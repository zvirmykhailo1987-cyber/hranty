# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Start-up and plumbing: production fail-fast, schema migration, error pages, the password
tool, duplicate merging, text extraction, rendering filters, file storage."""
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlmodel import Session, select

from app import config, db, main, parol, templating
from app.models import Application, Document, Match, Opportunity, Potreba, Task, User
from app.services import docstore, extract, zlyttya

# ── configuration ────────────────────────────────────────────────────────────

GOOD = "x" * 40


@pytest.mark.parametrize("secret,token,problem", [
    ("", GOOD, "secrets are missing: HRANTY_SECRET"),
    (GOOD, "", "secrets are missing: HRANTY_API_TOKEN"),
    ("dev-" + GOOD, GOOD, "HRANTY_SECRET must be"),
    ("short", GOOD, "HRANTY_SECRET must be"),
    (GOOD, "dev-" + GOOD, "HRANTY_API_TOKEN must be"),
    (GOOD, "short", "HRANTY_API_TOKEN must be"),
])
def test_production_refuses_to_start_with_weak_secrets(monkeypatch, secret, token, problem):
    monkeypatch.setattr(config, "IS_PROD", True)
    monkeypatch.setattr(config, "SECRET", secret)
    monkeypatch.setattr(config, "API_TOKEN", token)
    with pytest.raises(RuntimeError, match=problem):
        config.check_prod_ready()


def test_production_starts_with_strong_secrets(monkeypatch):
    monkeypatch.setattr(config, "IS_PROD", True)
    monkeypatch.setattr(config, "SECRET", GOOD)
    monkeypatch.setattr(config, "API_TOKEN", "y" * 40)
    config.check_prod_ready()


def test_secrets_file_fills_the_environment_without_overriding_it(tmp_path, monkeypatch):
    (tmp_path / "secrets.env").write_text("# comment\n\nNOEQUALS\nHRANTY_T1 = 'one'\nHRANTY_T2=\"two\"\n"
                                          "HRANTY_T3=from-file\n", encoding="utf-8")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    for k in ("HRANTY_T1", "HRANTY_T2"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HRANTY_T3", "from-env")
    config._load_secrets_file()
    import os
    assert (os.environ["HRANTY_T1"], os.environ["HRANTY_T2"], os.environ["HRANTY_T3"]) == ("one", "two", "from-env")
    for k in ("HRANTY_T1", "HRANTY_T2"):
        monkeypatch.delenv(k)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "empty")
    config._load_secrets_file()                  # no file is fine


# ── database ─────────────────────────────────────────────────────────────────

def test_empty_production_database_needs_a_bootstrap_password(tmp_path, monkeypatch):
    eng = create_engine(f"sqlite:///{tmp_path / 'p.sqlite'}")
    monkeypatch.setattr(config, "IS_PROD", True)
    monkeypatch.setattr(config, "BOOTSTRAP_PASSWORD", "short")
    with pytest.raises(RuntimeError, match="HRANTY_BOOTSTRAP_PASSWORD"):
        db.init_db(eng)
    monkeypatch.setattr(config, "BOOTSTRAP_PASSWORD", "a-long-enough-password")
    db.init_db(eng)
    with Session(eng) as s:
        hashed = {u.login: bool(u.password_hash) for u in s.exec(select(User))}
    assert hashed == {"marko": True, "bohdan": False, "oksana": False, "halyna": False}


def test_light_migration_adds_columns_and_rebuilds_an_empty_stale_table(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'old.sqlite'}")
    with eng.begin() as c:
        c.execute(text('CREATE TABLE "potreba" (id INTEGER PRIMARY KEY, nazva VARCHAR NOT NULL)'))
        c.execute(text("INSERT INTO potreba (id, nazva) VALUES (1, 'kept')"))
        # `deadline` is optional in the model; an old NOT NULL on an empty table → rebuilt
        c.execute(text('CREATE TABLE "opportunity" (id INTEGER PRIMARY KEY, title VARCHAR NOT NULL, '
                       'deadline DATE NOT NULL)'))
    db._dopysaty_kolonky(eng)
    insp = inspect(eng)
    assert {"klient", "byudzhet_eur", "tags"} <= {k["name"] for k in insp.get_columns("potreba")}
    deadline = next(k for k in insp.get_columns("opportunity") if k["name"] == "deadline")
    assert deadline["nullable"]
    with eng.connect() as c:
        assert c.execute(text("SELECT nazva FROM potreba")).scalar() == "kept"
    db.init_db(eng)                               # a full init on top is a no-op for the data


# ── error pages and plain endpoints ──────────────────────────────────────────

def test_validation_errors_and_crashes_get_a_page_not_a_traceback(marko, monkeypatch):
    r = marko.get("/zayavky/not-a-number")
    assert r.status_code == 422 and "Traceback" not in r.text
    assert marko.get("/zayavky/not-a-number", headers={"accept": "application/json"}).json() == \
        {"detail": "Некоректні дані форми або адреси."}

    from app.db import get_session

    def broken():
        raise RuntimeError("db exploded")
        yield  # generator shape for a FastAPI dependency; never reached
    main.app.dependency_overrides[get_session] = broken
    try:
        c = TestClient(main.app, raise_server_exceptions=False)
        r = c.get("/robots.txt")                          # no DB needed: still fine
        assert r.text == "User-agent: *\nDisallow: /\n"
        r = c.get("/reyestr", headers={"accept": "application/json"})
        assert r.status_code == 500 and r.json() == {"detail": "500"} and "exploded" not in r.text
    finally:
        main.app.dependency_overrides.clear()


def test_error_page_falls_back_to_plain_text_when_rendering_fails(marko, monkeypatch):
    from app.web import deps
    monkeypatch.setattr(deps, "render", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("template broke")))
    r = marko.get("/potreby/999999")
    assert (r.status_code, r.text) == (404, "Error 404")


def test_health_is_503_when_the_database_is_unreachable(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "engine", create_engine(f"sqlite:///{tmp_path / 'missing' / 'x.sqlite'}"))
    c = TestClient(main.app)
    r = c.get("/health")
    assert r.status_code == 503 and r.json() == {"ok": False, "baza": "OperationalError"}
    monkeypatch.setattr(config, "ENV", "production")
    assert c.get("/health").json() == {"ok": False}


def test_unknown_language_sets_no_cookie():
    c = TestClient(main.app, follow_redirects=False)
    r = c.get("/lang/xx?next=/reyestr")
    assert r.headers["location"] == "/reyestr" and "lang=" not in r.headers.get("set-cookie", "")
    assert "lang=en" in c.get("/lang/en").headers["set-cookie"]


# ── password tool ────────────────────────────────────────────────────────────

def test_password_tool(monkeypatch, capsys, s):
    from app.security import check_pw
    assert parol.main([]) == 2
    assert len(parol.vypadkovyi()) == 14 and not set(parol.vypadkovyi(200)) & set("lI1O0")
    answers = iter(["new-secret-123", "new-secret-123"])
    monkeypatch.setattr(parol.getpass, "getpass", lambda prompt: next(answers))
    assert parol.main(["bohdan"]) == 0 and "змінено" in capsys.readouterr().out
    s.expire_all()
    assert check_pw(s.exec(select(User).where(User.login == "bohdan")).one().password_hash, "new-secret-123")[0]
    answers = iter(["short", "short"])
    assert parol.main(["bohdan"]) == 2
    answers = iter(["long-enough-1", "different-22"])
    assert parol.main(["bohdan"]) == 2
    answers = iter(["long-enough-1", "long-enough-1"])
    assert parol.main(["nobody"]) == 0 and "немає такого логіна" in capsys.readouterr().out


def test_password_tool_resets_everyone_once(monkeypatch, capsys, s):
    before = {u.login: u.password_hash for u in s.exec(select(User))}
    assert parol.main(["--usim"]) == 0
    out = capsys.readouterr().out
    s.expire_all()
    after = {u.login: u.password_hash for u in s.exec(select(User))}
    assert all(after[k] != before[k] for k in before) and all(k in out for k in before)


# ── duplicate merging ────────────────────────────────────────────────────────

def test_merging_duplicates_moves_work_and_never_deletes_it(s):
    kanon, dubl = Opportunity(title="Canonical"), Opportunity(title="Duplicate")
    p1, p2 = Potreba(nazva="R1"), Potreba(nazva="R2")
    s.add_all([kanon, dubl, p1, p2]); s.commit()
    na_kanoni = Application(potreba_id=p2.id, opportunity_id=kanon.id)
    tilky_dubl = Application(potreba_id=p1.id, opportunity_id=dubl.id)
    konflikt = Application(potreba_id=p2.id, opportunity_id=dubl.id, verdykt="подаватися", skryner_json="{}")
    s.add_all([na_kanoni, tilky_dubl, konflikt]); s.commit()
    s.add_all([Task(title="t", application_id=konflikt.id), Document(application_id=konflikt.id, nazva="d"),
               Match(potreba_id=p1.id, opportunity_id=dubl.id, score=0.9)]); s.commit()
    assert zlyttya.zlyty(s, kanon.id, kanon.id) == {"perenese": 0, "zlyto": 0}
    with pytest.raises(ValueError):
        zlyttya.zlyty(s, dubl.id, 999999)
    assert zlyttya.zlyty(s, dubl.id, kanon.id) == {"perenese": 1, "zlyto": 1}
    s.commit()
    s.refresh(tilky_dubl); s.refresh(konflikt); s.refresh(na_kanoni); s.refresh(dubl)
    assert tilky_dubl.opportunity_id == kanon.id
    assert konflikt.arkhiv and f"#{na_kanoni.id}" in konflikt.nastupnyi_krok
    assert na_kanoni.verdykt == "подаватися"
    assert s.exec(select(Task)).one().application_id == na_kanoni.id
    assert s.exec(select(Document)).one().application_id == na_kanoni.id
    assert s.exec(select(Match)).all() == []
    assert (dubl.active, dubl.zlyto_v) == (False, kanon.id)
    assert zlyttya.kanon(s, dubl).id == kanon.id and zlyttya.kanon(s, None) is None


def test_canonical_lookup_survives_a_merge_loop(s):
    a, b = Opportunity(title="A"), Opportunity(title="B")
    s.add_all([a, b]); s.commit()
    a.zlyto_v, b.zlyto_v = b.id, a.id
    s.add_all([a, b]); s.commit()
    assert zlyttya.kanon(s, a).id in (a.id, b.id)          # terminates


# ── text extraction ──────────────────────────────────────────────────────────

def test_text_from_docx_xlsx_txt_and_unknown(tmp_path):
    from docx import Document as Docx
    from openpyxl import Workbook
    d = Docx()
    d.add_paragraph("Concept of the project")
    t = d.add_table(rows=1, cols=2)
    t.rows[0].cells[0].text, t.rows[0].cells[1].text = "Budget", "€100 000"
    d.save(tmp_path / "a.docx")
    assert extract.read_file(tmp_path / "a.docx") == "Concept of the project\nBudget | €100 000"
    wb = Workbook()
    wb.active.append(["Item", None, 42])
    wb.active.append([None, None])
    wb.save(tmp_path / "b.xlsx")
    assert extract.read_file(tmp_path / "b.xlsx") == "Item | 42"
    (tmp_path / "c.md").write_text("# Rules", encoding="utf-8")
    assert extract.read_file(tmp_path / "c.md") == "# Rules"
    (tmp_path / "d.bin").write_bytes(b"\0")
    assert extract.read_file(tmp_path / "d.bin") == ""
    (tmp_path / "e.docx").write_bytes(b"not a zip")
    assert extract.read_file(tmp_path / "e.docx") == ""


def _pdf(tekst: str) -> bytes:
    """A minimal valid one-page PDF with a text line (correct xref offsets)."""
    stream = f"BT /F1 12 Tf 20 100 Td ({tekst}) Tj ET".encode()
    objs = [b"<</Type/Catalog/Pages 2 0 R>>", b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
            b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 200]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
            b"<</Length %d>>stream\n" % len(stream) + stream + b"\nendstream",
            b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>"]
    out, offs = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj" % i + o + b"endobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % x for x in offs)
    out += b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, xref)
    return bytes(out)


def test_text_from_pdf_needs_real_text(tmp_path):
    (tmp_path / "rules.pdf").write_bytes(_pdf("Eligible applicants: municipalities and SMEs"))
    assert "Eligible applicants: municipalities and SMEs" in extract.read_file(tmp_path / "rules.pdf")
    (tmp_path / "scan.pdf").write_bytes(_pdf("p. 1"))
    assert extract.read_file(tmp_path / "scan.pdf") == ""            # under 20 characters: treated as a scan


def test_zip_with_too_many_members_is_refused(tmp_path):
    f = tmp_path / "many.xlsx"
    with zipfile.ZipFile(f, "w") as z:
        for i in range(5001):
            z.writestr(f"x/{i}", b"")
    assert extract._zip_bezpechnyi(f) is False


# ── rendering filters ────────────────────────────────────────────────────────

def test_markdown_links_cannot_run_script():
    html = str(templating.f_md("[ok](https://a.example) [bad](javascript:alert(1)) [rel](/x) "
                               "[proto](//evil.example) ![i](data:text/html,x) <b>raw</b>"))
    assert 'href="https://a.example"' in html and 'href="/x"' in html
    assert "javascript:" not in html and 'href="//evil' not in html and "data:text" not in html
    assert "<b>" not in html and "&lt;b&gt;" in html


def test_date_and_money_filters():
    from datetime import date, datetime, timedelta

    from app.models import utcnow
    today = utcnow().date()
    assert templating.f_data(None) == "—" and templating.f_data(datetime(2026, 1, 2, 3, 4)) == "02.01.2026 03:04"
    assert templating.f_dniv(None) == "без дедлайну"
    assert templating.f_dniv(today) == "сьогодні" and templating.f_dniv(today + timedelta(days=1)) == "завтра"
    assert templating.f_dniv(today - timedelta(days=2)) == "минув 2 дн. тому"
    assert templating.f_dniv(datetime.combine(today + timedelta(days=5), datetime.min.time())) == "за 5 дн."
    assert templating.f_hroshi(None) == "немає даних" and templating.f_hroshi(1234567, "EUR") == "1 234 567 EUR"
    assert templating.f_bal(None) == "—" and templating.f_bal(0.456) == "46"
    assert templating.f_tegy("biometan,,x") == [("biometan", templating.slovnyky.TEGY_NAZVY["biometan"]), ("x", "x")]
    assert str(templating.ik('i-den"><script>')).endswith('ikony.svg#i-denscript"/></svg>')  # no markup escapes
    assert isinstance(date.today(), date)


# ── file storage ─────────────────────────────────────────────────────────────

def test_storage_refuses_escapes_and_oversized_files(monkeypatch):
    with pytest.raises(docstore.FaylPomylka, match="тека"):
        docstore._teka("../outside")
    monkeypatch.setattr(config, "MAX_FILE_MB", 0)
    with pytest.raises(docstore.FaylPomylka, match="більший"):
        docstore.zberehty(b"x", "a.txt")
