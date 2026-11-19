# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""The daily self-maintenance job: idempotent (a re-run adds nothing), journaled (one IngestLog
row per run), and run at most once a day by the in-app scheduler."""
import asyncio
from datetime import timedelta

import pytest
from sqlmodel import Session, select

from app import main
from app.models import Application, IngestLog, Opportunity, Potreba, Task, Zmina, utcnow
from app.services import zhyvyi

SOHODNI = utcnow().date()


def _svit(s):
    p = Potreba(nazva="Biogas", tags="biometan", zayavnyk="biznes", status="aktyvnyi")
    closing = Opportunity(title="Closing", tags="biometan", zayavnyky="biznes", status="open", active=True,
                          deadline=SOHODNI + timedelta(days=5))
    gone = Opportunity(title="Gone", tags="biometan", status="open", active=True, deadline=SOHODNI - timedelta(days=1))
    s.add_all([p, closing, gone]); s.commit()
    early = Application(potreba_id=p.id, opportunity_id=gone.id, stadiya="dokumenty")
    late = Application(potreba_id=p.id, opportunity_id=gone.id, stadiya="podacha")
    s.add_all([early, late]); s.commit()
    return p, closing, gone, early, late


def _stan(s):
    return (sorted((z.typ, z.kluch) for z in s.exec(select(Zmina))),
            sorted((t.application_id, t.title) for t in s.exec(select(Task))))


def test_job_closes_announces_and_creates_follow_up_tasks(s):
    p, closing, gone, early, late = _svit(s)
    r = zhyvyi.obslugovuvannya(s, SOHODNI)
    assert r == {"zakryto": 1, "zakryvayetsya": 1, "novi_zbihy": 1, "zadachi": 1}
    s.refresh(gone)
    assert (gone.status, gone.active) == ("closed", False)
    zminy = {z.typ: z for z in s.exec(select(Zmina))}
    assert zminy["prohrama_zakryta"].vazhlyvist == 3          # an application is in work on it
    assert zminy["zakryvayetsya"].vazhlyvist == 2
    zadachi = list(s.exec(select(Task)))
    assert [t.application_id for t in zadachi] == [early.id]   # a submitted application needs no "decide" task
    assert zadachi[0].due == SOHODNI + timedelta(days=3)


def test_job_is_idempotent_and_journals_every_run(s):
    _svit(s)
    assert not zhyvyi.vzhe_sohodni(s, SOHODNI)
    zhyvyi.obslugovuvannya(s, SOHODNI)
    pislya_pershogo = _stan(s)
    druhyi = zhyvyi.obslugovuvannya(s, SOHODNI)
    assert druhyi == {"zakryto": 0, "zakryvayetsya": 0, "novi_zbihy": 0, "zadachi": 0}
    assert _stan(s) == pislya_pershogo
    logy = list(s.exec(select(IngestLog).where(IngestLog.feed == zhyvyi.FEED)))
    assert [(x.fayl, x.prymitka) for x in logy] == [(SOHODNI.isoformat(), "задач: 1"), (SOHODNI.isoformat(), "задач: 0")]
    assert zhyvyi.vzhe_sohodni(s, SOHODNI) and not zhyvyi.vzhe_sohodni(s, SOHODNI + timedelta(days=1))


def test_status_of_a_programme_not_yet_flushed_is_still_applied(s):
    o = Opportunity(title="Fresh", status="open", deadline=SOHODNI - timedelta(days=2))
    zhyvyi.zastosuvaty_status(s, o, SOHODNI)
    assert o.id is not None and (o.status, o.active) == ("closed", False)
    assert s.exec(select(Zmina).where(Zmina.typ == "prohrama_zakryta")).one().tekst.startswith("Програму закрито (дедлайн")


def test_a_closed_programme_without_deadline_gets_a_plain_event(s):
    o = Opportunity(title="Ended", status="closed", active=True)
    s.add(o); s.commit()
    zhyvyi.zastosuvaty_status(s, o, SOHODNI)
    assert s.exec(select(Zmina)).one().tekst == "Програму закрито: Ended"


# ── the in-app scheduler ─────────────────────────────────────────────────────

class _Stop(Exception):
    pass


async def _no_sleep(_):
    raise _Stop


def _run_scheduler(monkeypatch, hour):
    monkeypatch.setattr(main, "utcnow_hour", lambda: hour)
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    with pytest.raises(_Stop):
        asyncio.run(main._planuvalnyk())


def _job_logs():
    from app.db import engine
    with Session(engine) as s:
        return len(list(s.exec(select(IngestLog).where(IngestLog.feed == zhyvyi.FEED))))


def test_scheduler_waits_until_five_utc(monkeypatch):
    _run_scheduler(monkeypatch, 4)
    assert _job_logs() == 0


def test_scheduler_runs_the_job_once_a_day(monkeypatch):
    assert main.utcnow_hour() == utcnow().hour
    _run_scheduler(monkeypatch, 6)
    _run_scheduler(monkeypatch, 7)
    assert _job_logs() == 1


def test_a_failing_job_does_not_kill_the_scheduler(monkeypatch, caplog):
    def boom():
        raise RuntimeError("disk full")
    monkeypatch.setattr(main, "_obslugovuvannya", boom)
    _run_scheduler(monkeypatch, 6)            # reaches the sleep: the loop survived
    assert "disk full" in caplog.text and _job_logs() == 0


def test_lifespan_starts_the_scheduler_only_when_asked(monkeypatch):
    from fastapi.testclient import TestClient
    started = []

    async def fake():
        started.append(True)
        await asyncio.Event().wait()           # runs until cancelled at shutdown
    monkeypatch.setattr(main, "_planuvalnyk", fake)
    monkeypatch.delenv("HRANTY_PLANUVALNYK", raising=False)
    with TestClient(main.app) as c:
        assert c.get("/health").json()["ok"]
    assert started == []
    monkeypatch.setenv("HRANTY_PLANUVALNYK", "1")
    with TestClient(main.app) as c:
        c.get("/health")
    assert started == [True]


def test_reapplying_a_closed_status_does_not_repeat_the_follow_up_task(s):
    """Every import re-applies the status of a programme it touches; the 'decide' task stays single."""
    _, _, gone, early, _ = _svit(s)
    for _ in range(3):
        zhyvyi.zastosuvaty_status(s, gone, SOHODNI)
        s.commit()
    assert [t.application_id for t in s.exec(select(Task))] == [early.id]
    assert len(s.exec(select(Zmina).where(Zmina.typ == "prohrama_zakryta")).all()) == 1
