"""Regression tests for the independent code and security review (one test per finding)."""
import json
from datetime import timedelta

import pytest
from sqlmodel import select

from app import config
from app.models import Application, Document, Match, Opportunity, Potreba, Task, Zmina, utcnow
from app.services import gates, ingest, matching, rozbir, zhyvyi


def _zapys(i, **kw):
    r = {"title": f"Biogas pilots for farms {i}", "url": f"https://funder.example/p/{i}", "kind": "grant",
         "source_org": "Demo Fund", "for_whom": "SME, farmer", "notes": "biogas manure", "amount": "up to €400 000"}
    r.update(kw)
    return r


def _potreba(s):
    p = Potreba(nazva="Biogas 1 MW", klient="Farm", opys="biogas plant on manure", tags="biometan",
                zayavnyk="biznes", byudzhet_eur=300000)
    s.add(p); s.commit(); s.refresh(p)
    return p


# ── code review ──────────────────────────────────────────────────────────────

def test_import_that_closes_a_programme_still_emits_event_and_task(s):
    sohodni = utcnow().date()
    ingest.importuvaty_zapysy(s, [_zapys(1, deadline=(sohodni + timedelta(days=40)).isoformat())], "t")
    o = s.exec(select(Opportunity)).one()
    p = _potreba(s)
    a = Application(potreba_id=p.id, opportunity_id=o.id, stadiya="dokumenty"); s.add(a); s.commit()
    ingest.importuvaty_zapysy(s, [_zapys(1, deadline=(sohodni - timedelta(days=1)).isoformat())], "t")
    s.commit()
    assert s.exec(select(Zmina).where(Zmina.typ == "prohrama_zakryta")).first()
    assert s.exec(select(Task).where(Task.application_id == a.id)).first()


def test_programme_imported_already_closing_gets_its_event(s):
    sohodni = utcnow().date()
    ingest.importuvaty_zapysy(s, [_zapys(2, deadline=(sohodni + timedelta(days=5)).isoformat())], "t")
    s.commit()
    assert s.exec(select(Zmina).where(Zmina.typ == "zakryvayetsya")).first()


def test_new_strong_match_is_announced_even_if_computed_by_an_import(s):
    _potreba(s)
    ingest.importuvaty_zapysy(s, [_zapys(3)], "t")
    matching.pererakhuvaty_vse(s)                     # what the ingest API does
    zhyvyi.obslugovuvannya(s)
    assert s.exec(select(Zmina).where(Zmina.typ == "novyi_zbih")).first()


@pytest.mark.parametrize("tekst,ochik", [
    ("Для органів місцевого самоврядування", ["hromada"]),
    ("для малого і середнього бізнесу", ["biznes"]),
    ("громадські організації", ["go"]),
    ("ГО, БО", ["go"]),
])
def test_applicant_type_detection(tekst, ochik):
    assert rozbir.zayavnyky_z_tekstu(tekst) == ochik


@pytest.mark.parametrize("tekst,ochik", [
    ("до €100 000 3 роки", (None, 100000.0, "EUR")),
    ("EUR 100,000 maximum", (None, 100000.0, "EUR")),
    ("€50 000 per project, 12 months", (None, 50000.0, "EUR")),
    ("2026-2027: до 500 000 грн", (None, 500000.0, "UAH")),
    ("€10 000 – €50 000", (10000.0, 50000.0, "EUR")),
])
def test_amount_parsing(tekst, ochik):
    assert rozbir.suma(tekst) == ochik


def test_matches_on_closed_programmes_are_dropped(s):
    p = _potreba(s)
    ingest.importuvaty_zapysy(s, [_zapys(4)], "t")
    matching.pererakhuvaty(s, p)
    o = s.exec(select(Opportunity)).one()
    assert s.exec(select(Match).where(Match.opportunity_id == o.id)).first()
    o.status, o.active = "closed", False
    s.add(o); s.commit()
    matching.pererakhuvaty(s, p)
    assert not s.exec(select(Match).where(Match.opportunity_id == o.id)).first()


def test_same_title_different_url_is_a_different_programme(s):
    ingest.importuvaty_zapysy(s, [_zapys(5), {**_zapys(5), "url": "https://funder.example/other"}], "t")
    assert len(s.exec(select(Opportunity)).all()) == 2


def test_reingest_without_reliability_keeps_the_curated_value(s):
    ingest.importuvaty_zapysy(s, [_zapys(6, reliability="confirmed", status="rolling")], "t")
    z = _zapys(6); z.pop("reliability", None)
    ingest.importuvaty_zapysy(s, [z], "t")
    o = s.exec(select(Opportunity)).one()
    assert o.reliability == "confirmed" and o.status == "rolling"


def test_rows_rejected_by_the_gate_are_counted():
    from app.db import engine
    from sqlmodel import Session
    with Session(engine) as s:
        log = ingest.importuvaty_zapysy(s, [_zapys(7), {"title": "no url"}], "t")
        assert log.nevalidni >= 1


def test_result_gate_rechecks_signoff(s):
    p = _potreba(s)
    ingest.importuvaty_zapysy(s, [_zapys(8)], "t")
    o = s.exec(select(Opportunity)).one()
    a = Application(potreba_id=p.id, opportunity_id=o.id, stadiya="podacha", podano_at=utcnow(), podano_kto="X")
    s.add(a); s.commit()
    s.add(Document(application_id=a.id, nazva="Budget v2", vyd="byudzhet", group_uid="g", versiya=2)); s.commit()
    assert gates.perevirka(s, a, "rezultat")


def test_unknown_foreign_keys_give_400(s, marko):
    r = marko.post("/zadachi", data={"title": "x", "application_id": "999999"}, follow_redirects=False)
    assert r.status_code == 400


def test_budget_must_be_a_finite_number(marko):
    r = marko.post("/potreby/nova", data={"nazva": "X", "byudzhet_eur": "nan"}, follow_redirects=False)
    assert r.status_code == 400


# ── security review ──────────────────────────────────────────────────────────

def _zayavka_na_podachi(s):
    p = _potreba(s)
    ingest.importuvaty_zapysy(s, [_zapys(9)], "t")
    o = s.exec(select(Opportunity)).one()
    a = Application(potreba_id=p.id, opportunity_id=o.id, stadiya="podacha"); s.add(a); s.commit()
    s.add(Document(application_id=a.id, nazva="Budget", vyd="byudzhet", group_uid="gb", versiya=1,
                   pohodzheno_kym="Bohdan", pohodzheno_kym_id=2)); s.commit()
    s.refresh(a)
    return a


def test_new_version_at_submission_sends_application_back_to_documents(s, oksana):
    a = _zayavka_na_podachi(s)
    r = oksana.post(f"/zayavky/{a.id}/dokument", data={"group_uid": "gb", "nazva": "Budget v2"},
                    files={"fayl": ("b.txt", b"altered", "text/plain")}, follow_redirects=False)
    assert r.status_code == 303
    s.refresh(a)
    assert a.stadiya == "dokumenty"


def test_submission_refused_with_unsigned_current_version(s, halyna):
    a = _zayavka_na_podachi(s)
    s.add(Document(application_id=a.id, nazva="Budget v2", vyd="byudzhet", group_uid="gb", versiya=2)); s.commit()
    r = halyna.post(f"/zayavky/{a.id}/podano", data={"data_podachi": utcnow().date().isoformat()},
                    follow_redirects=False)
    assert r.status_code == 409


def test_no_uploads_after_submission(s, marko):
    a = _zayavka_na_podachi(s)
    a.podano_at, a.podano_kto = utcnow(), "Halyna"; s.add(a); s.commit()
    r = marko.post(f"/zayavky/{a.id}/dokument", data={"nazva": "late"},
                   files={"fayl": ("l.txt", b"late", "text/plain")}, follow_redirects=False)
    assert r.status_code == 409


def test_verdict_needs_data_right(s, oksana):
    ingest.importuvaty_zapysy(s, [_zapys(10)], "t")
    o = s.exec(select(Opportunity)).one()
    assert oksana.post(f"/reyestr/{o.id}/verdykt", data={"verdykt": "подаватися"},
                       follow_redirects=False).status_code == 403


def test_moving_back_from_submission_needs_send_right(s, oksana):
    a = _zayavka_na_podachi(s)
    assert oksana.post(f"/zayavky/{a.id}/stadiya", data={"nova": "ideya"},
                       follow_redirects=False).status_code == 403


def test_zip_bomb_docx_is_not_parsed(tmp_path):
    import zipfile
    from app.services import extract
    f = tmp_path / "bomb.docx"
    with zipfile.ZipFile(f, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", b"0" * (extract.MAX_ROZPAKOVKA + 1))
    assert extract.read_file(f) == ""


def test_libreoffice_argv_and_failure_cache(tmp_path, monkeypatch):
    from app.services import perehlyad
    calls = []
    monkeypatch.setattr(perehlyad, "soffice", lambda: "soffice")
    monkeypatch.setattr(perehlyad, "KESH", tmp_path / "kesh")
    monkeypatch.setattr(perehlyad.subprocess, "run", lambda argv, **kw: calls.append(argv))
    doc = tmp_path / "a.docx"
    doc.write_bytes(b"not really a docx")
    assert perehlyad.pdf_dlya(doc) is None
    assert {"--headless", "--convert-to", "pdf"} <= set(calls[0])
    assert perehlyad.pdf_dlya(doc) is None and len(calls) == 1      # the failure is remembered


def test_demo_seed_never_touches_a_database_with_data(s, monkeypatch):
    from app import seed_demo
    monkeypatch.setattr(config, "IS_PROD", False)
    _potreba(s)
    assert seed_demo.seed(s) == {"skipped": True}
    assert json.dumps(True)
