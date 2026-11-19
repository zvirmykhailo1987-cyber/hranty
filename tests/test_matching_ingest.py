# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Match scoring (unknown is "no data", never zero) and the ingest gate (invalid rows are counted)."""
import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app import config
from app.models import IngestLog, Match, Opportunity, Potreba, utcnow
from app.services import ingest, matching

SOHODNI = utcnow().date()


def _o(**kw):
    base = dict(title="Programme", kind="grant", status="open", reliability="confirmed", active=True,
                deadline=SOHODNI + timedelta(days=60))
    base.update(kw)
    return Opportunity(**base)


def _k(r, kod):
    return next(k for k in r.komponenty if k.kod == kod)


# ── scoring ──────────────────────────────────────────────────────────────────

def test_a_request_with_nothing_known_scores_neutral_not_zero():
    r = matching.score(Potreba(nazva="?"), _o(deadline=None, status="closing", reliability="mystery"))
    bez_danykh = {k.kod for k in r.komponenty if k.nemaye_danykh}
    assert bez_danykh == {"tegy", "zayavnyk", "suma", "dedlain", "geo"}
    assert all(k.znach == 0.5 for k in r.komponenty if k.nemaye_danykh)
    assert _k(r, "nadiynist").znach == 0.3 and _k(r, "nadiynist").prychyna == "невідомо"
    assert r.score > 0.4 and not r.vidsiyano
    assert _k(r, "tegy").nazva == "Напрям"


def test_programme_side_unknowns_are_no_data_too():
    p = Potreba(nazva="x", tags="biometan", zayavnyk="biznes", byudzhet_eur=1000, oblast="Львівська")
    r = matching.score(p, _o())
    for kod in ("tegy", "zayavnyk", "suma", "geo"):
        assert _k(r, kod).nemaye_danykh and _k(r, kod).znach == 0.5, kod


def test_open_to_anyone_counts_as_an_allowed_applicant():
    r = matching.score(Potreba(nazva="x", zayavnyk="ho"), _o(zayavnyky="bud_khto"))
    assert _k(r, "zayavnyk").znach == 1.0


@pytest.mark.parametrize("byudzhet,mn,mx,ochik", [
    (100_000, None, 1_000_000, 1.0),       # within
    (1_000_000, None, 500_000, 0.5),       # covers half
    (10_000_000, None, 100_000, 0.2),      # floor: a tiny programme is still shown, low
    (1_000, 50_000, 500_000, 0.3),         # below the minimum
])
def test_amount_component(byudzhet, mn, mx, ochik):
    r = matching.score(Potreba(nazva="x", byudzhet_eur=byudzhet), _o(amount_min=mn, amount_max=mx, currency="EUR"))
    assert _k(r, "suma").znach == ochik and not _k(r, "suma").nemaye_danykh


@pytest.mark.parametrize("kw,potribno,ochik", [
    (dict(status="rolling", deadline=None), None, 0.8),
    (dict(status="open", deadline=None), None, 0.8),
    (dict(deadline=SOHODNI + timedelta(days=3)), None, 0.2),
    (dict(deadline=SOHODNI + timedelta(days=60)), SOHODNI + timedelta(days=30), 0.4),
    (dict(deadline=SOHODNI + timedelta(days=10)), None, 0.6),
    (dict(deadline=SOHODNI + timedelta(days=21)), None, 1.0),
])
def test_deadline_component(kw, potribno, ochik):
    r = matching.score(Potreba(nazva="x", potribno_do=potribno), _o(**kw), sohodni=SOHODNI)
    assert _k(r, "dedlain").znach == ochik


@pytest.mark.parametrize("geo,ochik,nemaye", [
    ("Львівська область", 1.0, False),
    ("уся Україна", 0.9, False),
    ("Волинська та Рівненська області", 0.1, False),
    ("Sahel", 0.5, True),
])
def test_geography_component(geo, ochik, nemaye):
    r = matching.score(Potreba(nazva="x", oblast="Львівська"), _o(geo=geo))
    assert (_k(r, "geo").znach, _k(r, "geo").nemaye_danykh) == (ochik, nemaye)


def test_a_passed_deadline_filters_the_programme_out():
    r = matching.score(Potreba(nazva="x"), _o(deadline=SOHODNI - timedelta(days=1)))
    assert (r.score, r.vidsiyano, r.komponenty) == (0.0, "програма закрита", [])
    assert json.loads(r.json())["vidsiyano"] == "програма закрита"


def test_recompute_drops_a_new_match_that_became_weak_but_keeps_work_and_rejections(s):
    p = Potreba(nazva="Biogas", tags="biometan", zayavnyk="biznes"); s.add(p)
    o1, o2, o3 = (_o(title=f"P{i}", tags="biometan", zayavnyky="biznes") for i in range(3))
    s.add_all([o1, o2, o3]); s.commit(); s.refresh(p)
    assert matching.pererakhuvaty(s, p) == 3
    m = {x.opportunity_id: x for x in s.exec(select(Match))}
    m[o2.id].stan = "u_roboti"; m[o3.id].stan = "vidkhyleno"; s.add_all(m.values()); s.commit()
    for o in (o1, o2, o3):
        o.tags = "osvita"; s.add(o)           # direction no longer matches → below the threshold
    s.commit()
    assert matching.pererakhuvaty(s, p) == 0
    stany = {x.opportunity_id: x.stan for x in s.exec(select(Match))}
    assert stany == {o2.id: "u_roboti", o3.id: "vidkhyleno"}


# ── ingest gate ──────────────────────────────────────────────────────────────

def _r(i, **kw):
    z = {"title": f"Call {i}", "url": f"https://funder.example/{i}", "kind": "grant", "source_org": "Fund"}
    z.update(kw)
    return z


def test_gate_refuses_anything_but_a_list(s):
    assert ingest.validuvaty_radar({"title": "x"}) == (False, "expected a JSON list of opportunities")
    log = ingest.importuvaty_zapysy(s, {"title": "x"}, "t")
    assert (log.dodano, log.nevalidni) == (0, 1) and log.prymitka.startswith("ingest gate: expected")


def test_every_invalid_row_is_counted_and_reported(s):
    rows = [_r(1), "not an object", _r(2, url=""), _r(3, kind="loan-shark"), _r(4, url="ftp://x.example/a"),
            _r(5, title="")]
    log = ingest.importuvaty_zapysy(s, rows, "feed/../x")
    assert (log.dodano, log.nevalidni) == (1, 5)
    assert log.prymitka == "ingest gate: 5 invalid records (first at index 1)"
    assert log.feed == "radar:feed..x"                       # the feed label is sanitised
    assert s.exec(select(IngestLog)).one().nevalidni == 5


def test_without_the_gate_broken_rows_are_still_counted_not_dropped(s):
    log = ingest.importuvaty_zapysy(s, ["not an object", {"title": ""}, {"title": 42}, _r(1)], "t", gejt=False)
    assert (log.dodano, log.nevalidni) == (1, 3)


def test_a_converter_that_yields_nothing_counts_as_invalid(s):
    log = ingest._rakhuvaty(s, [{"x": 1}], "t", "f", lambda r: None)
    assert (log.dodano, log.nevalidni) == (0, 1)


def test_without_urls_the_title_and_funder_decide_duplicates(s):
    for _ in range(2):
        ingest.importuvaty_zapysy(s, [_r(1, url="")], "t", gejt=False)
    assert len(s.exec(select(Opportunity)).all()) == 1


def test_reingest_refreshes_automatic_tags_and_fills_missing_applicants(s):
    ingest.importuvaty_zapysy(s, [_r(1, notes="школи та садочки")], "t")
    o = s.exec(select(Opportunity)).one()
    assert "osvita" in o.tags and o.zayavnyky == ""
    ingest.importuvaty_zapysy(s, [_r(1, notes="biogas on manure", for_whom="для МСП")], "t")
    s.refresh(o)
    assert "biometan" in o.tags and "osvita" not in o.tags and o.zayavnyky == "biznes"


def test_import_from_a_file(s, tmp_path):
    f = tmp_path / "radar_2026-11-19.json"
    f.write_text(json.dumps([_r(1)]), encoding="utf-8")
    log = ingest.importuvaty_radar(s, f)
    assert (log.dodano, log.fayl, log.feed) == (1, "radar_2026-11-19.json", "radar:radar_2026-11-19")


# ── ingest API ───────────────────────────────────────────────────────────────

@pytest.fixture
def api():
    from app.main import app
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {config.API_TOKEN}"
    return c


def test_api_rejects_malformed_bodies(api, monkeypatch):
    assert api.post("/api/ingest/radar", content=b"{not json").status_code == 400
    assert api.post("/api/ingest/radar", content=b"\xff\xfe").status_code == 400
    assert api.post("/api/ingest/radar", content=b'{"title": "x"}').status_code == 400
    assert api.post("/api/ingest/radar?data=19.11.2026", content=b"[]").status_code == 400
    from app.web import api as api_mod
    monkeypatch.setattr(api_mod, "MAX_BODY", 4)
    assert api.post("/api/ingest/radar", content=b"[1, 2]").status_code == 413


def test_api_reports_invalid_rows_and_a_wrong_token_is_401(api):
    r = api.post("/api/ingest/radar", content=json.dumps([_r(1), {"title": "no url"}]).encode())
    assert r.status_code == 200
    assert (r.json()["dodano"], r.json()["nevalidni"]) == (1, 1) and "invalid records" in r.json()["prymitka"]
    api.headers["Authorization"] = "Bearer wrong"
    assert api.post("/api/ingest/radar", content=b"[]").status_code == 401


def test_api_is_off_without_a_configured_token(api, monkeypatch):
    monkeypatch.setattr(config, "API_TOKEN", "")
    api.headers["Authorization"] = "Bearer "
    assert api.post("/api/ingest/radar", content=b"[]").status_code == 401
