# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

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


def test_new_version_at_submission_sends_application_back_to_documents(s, halyna):
    a = _zayavka_na_podachi(s)
    r = halyna.post(f"/zayavky/{a.id}/dokument", data={"group_uid": "gb", "nazva": "Budget v2"},
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
    doc = tmp_path / "a.doc"                     # legacy binary format: no zip guard
    doc.write_bytes(b"not really a doc")
    assert perehlyad.pdf_dlya(doc) is None
    assert {"--headless", "--convert-to", "pdf"} <= set(calls[0])
    assert perehlyad.pdf_dlya(doc) is None and len(calls) == 1      # the failure is remembered


def test_demo_seed_never_touches_a_database_with_data(s, monkeypatch):
    from app import seed_demo
    monkeypatch.setattr(config, "IS_PROD", False)
    _potreba(s)
    assert seed_demo.seed(s) == {"skipped": True}
    assert json.dumps(True)


# ── verification round ───────────────────────────────────────────────────────

@pytest.mark.parametrize("stary,novyi,dl_dniv,aktyvna", [
    ("open", "closed", 30, False),       # feed closes it explicitly
    ("closed", "open", 30, True),        # feed reopens it
])
def test_active_flag_follows_explicit_feed_status(s, stary, novyi, dl_dniv, aktyvna):
    dl = (utcnow().date() + timedelta(days=dl_dniv)).isoformat()
    ingest.importuvaty_zapysy(s, [_zapys(20, status=stary, deadline=dl)], "t")
    ingest.importuvaty_zapysy(s, [_zapys(20, status=novyi, deadline=dl)], "t")
    o = s.exec(select(Opportunity)).one()
    assert o.active is aktyvna


@pytest.mark.parametrize("tekst,ochik", [
    ("від 50 тис до 2 млн євро", (50000.0, 2000000.0, "EUR")),
    ("€300-500k", (300000.0, 500000.0, "EUR")),
    ("100 тис. - 1 млн грн", (100000.0, 1000000.0, "UAH")),
    ("€1.5 million", (None, 1500000.0, "EUR")),
])
def test_amount_ranges_and_words(tekst, ochik):
    assert rozbir.suma(tekst) == ochik


def test_municipal_utility_is_not_a_business():
    assert rozbir.zayavnyky_z_tekstu("комунальні підприємства") == ["kp"]


def test_libreoffice_timeout_is_cached_and_missing_soffice_is_not(tmp_path, monkeypatch):
    import subprocess as sp

    from app.services import perehlyad
    monkeypatch.setattr(perehlyad, "KESH", tmp_path / "kesh")
    doc = tmp_path / "a.doc"
    doc.write_bytes(b"x")
    monkeypatch.setattr(perehlyad, "soffice", lambda: None)
    assert perehlyad.pdf_dlya(doc) is None
    assert not list((tmp_path / "kesh").glob("*.failed")) if (tmp_path / "kesh").exists() else True
    calls = []

    def hang(argv, **kw):
        calls.append(argv)
        raise sp.TimeoutExpired(argv, 180)
    monkeypatch.setattr(perehlyad, "soffice", lambda: "soffice")
    monkeypatch.setattr(perehlyad.subprocess, "run", hang)
    assert perehlyad.pdf_dlya(doc) is None and perehlyad.pdf_dlya(doc) is None
    assert len(calls) == 1


def test_upload_during_submission_needs_send_right(s, oksana):
    a = _zayavka_na_podachi(s)
    r = oksana.post(f"/zayavky/{a.id}/dokument", data={"group_uid": "gb", "nazva": "v2"},
                    files={"fayl": ("b.txt", b"x", "text/plain")}, follow_redirects=False)
    assert r.status_code == 403
    s.refresh(a)
    assert a.stadiya == "podacha"


def test_rollback_below_submission_clears_it(s, halyna):
    a = _zayavka_na_podachi(s)
    a.podano_at, a.podano_kto = utcnow(), "Halyna"; s.add(a); s.commit()
    r = halyna.post(f"/zayavky/{a.id}/stadiya", data={"nova": "dokumenty"}, follow_redirects=False)
    assert r.status_code == 303
    s.refresh(a)
    assert a.podano_at is None and a.podano_kto == ""


# ── final narrow review ──────────────────────────────────────────────────────

@pytest.mark.parametrize("tekst,ochik", [
    ("€50 000 – 1 млн", (50000.0, 1000000.0, "EUR")),
    ("€20 000 - 100k", (20000.0, 100000.0, "EUR")),
    ("від 500 000 грн до 1,5 млн грн у 2026-2027", (500000.0, 1500000.0, "UAH")),
    ("2026–2027: 200 000 грн – 2 млн грн", (200000.0, 2000000.0, "UAH")),
])
def test_amount_multiplier_is_not_shared_with_full_numbers(tekst, ochik):
    assert rozbir.suma(tekst) == ochik


def test_merged_duplicate_stays_hidden_on_reingest(s):
    ingest.importuvaty_zapysy(s, [_zapys(30), _zapys(31)], "t")
    dubl, kanon = s.exec(select(Opportunity).order_by(Opportunity.id)).all()
    dubl.active, dubl.zlyto_v = False, kanon.id
    s.add(dubl); s.commit()
    ingest.importuvaty_zapysy(s, [_zapys(30)], "t")
    s.refresh(dubl)
    assert dubl.active is False


def test_zip_bomb_with_a_doc_extension_is_refused_before_conversion(tmp_path, monkeypatch):
    import zipfile

    from app.services import extract, perehlyad
    calls = []
    monkeypatch.setattr(perehlyad, "soffice", lambda: "soffice")
    monkeypatch.setattr(perehlyad, "KESH", tmp_path / "kesh")
    monkeypatch.setattr(perehlyad.subprocess, "run", lambda argv, **kw: calls.append(argv))
    f = tmp_path / "bomb.doc"
    with zipfile.ZipFile(f, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("content.xml", b"0" * (extract.MAX_ROZPAKOVKA + 1))
    assert perehlyad.pdf_dlya(f) is None and calls == []


# ── final independent review ─────────────────────────────────────────────────

@pytest.mark.parametrize("tekst,ochik", [
    ("до 250.000 EUR", (None, 250000.0, "EUR")),
    ("fino a 100.000 €", (None, 100000.0, "EUR")),
    ("1.500,50 EUR", (None, 1500.5, "EUR")),
    ("до 2000 євро", (None, 2000.0, "EUR")),
    ("у 2026 році до 50 000 грн", (None, 50000.0, "UAH")),
    ("1.5 млн грн", (None, 1500000.0, "UAH")),
])
def test_european_thousands_and_four_digit_amounts(tekst, ochik):
    assert rozbir.suma(tekst) == ochik


def test_far_deadline_is_no_longer_closing():
    from datetime import date, timedelta

    from app.services.ingest import status_z_dedlainu
    d0 = date(2026, 11, 1)
    assert status_z_dedlainu("closing", d0 + timedelta(days=60), d0) == "open"
    assert status_z_dedlainu("rolling", d0 + timedelta(days=60), d0) == "rolling"
    assert status_z_dedlainu("open", d0 + timedelta(days=10), d0) == "closing"


def test_reopened_call_and_changed_amount_on_reingest(s):
    from datetime import date, timedelta
    d0 = date.today()
    ingest.importuvaty_zapysy(s, [_zapys(40, deadline=(d0 + timedelta(days=5)).isoformat())], "t")
    o = s.exec(select(Opportunity)).one()
    o.status, o.active = "closed", False
    s.add(o); s.commit()
    ingest.importuvaty_zapysy(s, [_zapys(40, deadline=(d0 + timedelta(days=60)).isoformat(),
                                         amount="up to €100 000")], "t")
    s.refresh(o)
    assert o.status == "open" and o.active is True
    assert o.amount_max == 100000.0
