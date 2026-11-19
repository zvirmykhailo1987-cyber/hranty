# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Client requests, the grants register, the programme library, knowledge and document preview —
the actions behind each screen, including their refusals."""
import json
import sys
import types
from datetime import timedelta

import pytest
from sqlmodel import select

from app.models import (Application, Document, Match, Opportunity, Potreba, ProgramFile, Znannya, utcnow)
from app.services import docstore, perehlyad
from app.web.potreby import _float

SOHODNI = utcnow().date()


def _opp(s, **kw):
    base = dict(title="Biogas call", kind="grant", status="open", reliability="confirmed", active=True,
                tags="biometan", zayavnyky="biznes", geo="Україна", deadline=SOHODNI + timedelta(days=60))
    base.update(kw)
    o = Opportunity(**base)
    s.add(o); s.commit(); s.refresh(o)
    return o


# ── client requests ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,ochik", [("", None), ("1 200 000", 1_200_000.0), ("1,200,000", 1_200_000.0),
                                       ("1200,5", 1200.5), ("1 500", 1500.0)])
def test_budget_parsing(raw, ochik):
    assert _float(raw) == ochik


@pytest.mark.parametrize("raw", ["abc", "-5", "inf"])
def test_budget_rejects_non_numbers(raw):
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        _float(raw)


def test_request_lifecycle_create_edit_status_and_matches(s, marko):
    o = _opp(s)
    r = marko.post("/potreby/nova", data={"nazva": "Biogas plant", "opys": "біогаз на гної", "zayavnyk": "biznes",
                                          "oblast": "Тернопільська", "byudzhet_eur": "300 000",
                                          "potribno_do": (SOHODNI + timedelta(days=200)).isoformat()},
                   follow_redirects=False)
    assert r.status_code == 303
    p = s.exec(select(Potreba)).one()
    assert p.tags == "biometan" and p.byudzhet_eur == 300_000          # tags inferred from the text
    m = s.exec(select(Match).where(Match.potreba_id == p.id)).one()
    assert m.opportunity_id == o.id
    assert "Biogas call" in marko.get(f"/potreby/{p.id}").text
    assert marko.get("/potreby").status_code == 200 and marko.get("/potreby/nova").status_code == 200

    r = marko.post(f"/potreby/{p.id}/redaguvaty", data={"nazva": "Biogas plant v2", "tags": ["osvita", "bogus"]},
                   follow_redirects=False)
    assert r.status_code == 303
    s.refresh(p)
    assert (p.nazva, p.tags) == ("Biogas plant v2", "osvita")
    assert marko.post(f"/potreby/{p.id}/redaguvaty", data={"nazva": " "}, follow_redirects=False).status_code == 400

    assert marko.post(f"/potreby/{p.id}/status", data={"status": "deleted"}, follow_redirects=False).status_code == 400
    assert marko.post(f"/potreby/{p.id}/status", data={"status": "pauza"}, follow_redirects=False).status_code == 303
    s.refresh(p)
    assert p.status == "pauza"


def test_create_needs_a_name_and_missing_requests_are_404(marko):
    assert marko.post("/potreby/nova", data={"nazva": ""}, follow_redirects=False).status_code == 400
    assert marko.get("/potreby/999999").status_code == 404
    assert marko.post("/potreby/999999/redaguvaty", data={"nazva": "x"}, follow_redirects=False).status_code == 404
    assert marko.post("/potreby/999999/status", data={"status": "pauza"}, follow_redirects=False).status_code == 400
    assert marko.post("/zbihy/999999/vidkhylyty", follow_redirects=False).status_code == 404
    assert marko.post("/zbihy/999999/v-robotu", follow_redirects=False).status_code == 404


def test_rejecting_a_match_keeps_the_reason_and_taking_it_twice_reuses_the_application(s, marko):
    p = Potreba(nazva="Biogas", tags="biometan", zayavnyk="biznes"); s.add(p)
    o1, o2 = _opp(s, title="A"), _opp(s, title="B")
    s.commit()
    m1 = Match(potreba_id=p.id, opportunity_id=o1.id, score=0.8)
    m2 = Match(potreba_id=p.id, opportunity_id=o2.id, score=0.8)
    s.add_all([m1, m2]); s.commit()
    marko.post(f"/zbihy/{m1.id}/vidkhylyty", data={"prychyna": ""})
    s.refresh(m1)
    assert (m1.stan, m1.prychyna) == ("vidkhyleno", "не підходить")
    first = marko.post(f"/zbihy/{m2.id}/v-robotu", follow_redirects=False).headers["location"]
    again = marko.post(f"/zbihy/{m2.id}/v-robotu", follow_redirects=False).headers["location"]
    assert first == again and len(s.exec(select(Application)).all()) == 1


def test_request_documents_show_only_the_latest_version(s, marko):
    p = Potreba(nazva="Biogas"); s.add(p); s.commit()
    assert marko.post(f"/potreby/{p.id}/dokument", data={"vyd": "statut", "nazva": "Statute"},
                      files={"fayl": ("st.txt", b"v1", "text/plain")}, follow_redirects=False).status_code == 303
    d = s.exec(select(Document).where(Document.potreba_id == p.id)).one()
    assert (d.vyd, d.avtor, d.group_uid) == ("statut", "Марко", d.uid)
    s.add(Document(potreba_id=p.id, nazva="Statute v2", vyd="statut", group_uid=d.group_uid, versiya=2))
    s.add(Document(potreba_id=p.id, nazva="Statute v0", vyd="statut", group_uid=d.group_uid, versiya=0))
    s.commit()
    html = marko.get(f"/potreby/{p.id}").text
    assert "Statute v2" in html and "Statute v0" not in html
    assert marko.post(f"/potreby/{p.id}/dokument", data={"nazva": "x"},
                      files={"fayl": ("x.exe", b"MZ", "application/x-msdownload")},
                      follow_redirects=False).status_code == 400
    assert marko.post(f"/potreby/{p.id}/dokument", data={"nazva": "no file"}, follow_redirects=False).status_code == 400
    assert marko.post("/potreby/999999/dokument", data={"nazva": "x"},
                      files={"fayl": ("x.txt", b"x", "text/plain")}, follow_redirects=False).status_code == 404


# ── grants register ──────────────────────────────────────────────────────────

def test_register_filters(s, marko):
    _opp(s, title="Biogas loan", kind="loan", reliability="plausible", zayavnyky="biznes")
    _opp(s, title="School grant", tags="osvita", zayavnyky="hromada", notes="кошти для шкіл")
    _opp(s, title="Old call", status="closed", active=False)
    assert "School grant" in marko.get("/reyestr?q=шкіл").text
    r = marko.get("/reyestr?kind=loan").text
    assert "Biogas loan" in r and "School grant" not in r
    r = marko.get("/reyestr?zayavnyk=hromada&nadiynist=confirmed").text
    assert "School grant" in r and "Biogas loan" not in r
    r = marko.get("/reyestr?status=closed").text
    assert "Old call" in r and "School grant" not in r
    assert marko.get("/reyestr?storinka=0").status_code == 200


def test_manual_classification_sticks_and_recomputes_matches(s, marko):
    p = Potreba(nazva="Schools", tags="osvita", zayavnyk="hromada"); s.add(p); s.commit()
    o = _opp(s, tags="biometan", zayavnyky="biznes")
    r = marko.post(f"/reyestr/{o.id}/klasyfikatsiya", data={"tags": ["osvita", "nonsense"],
                                                            "zayavnyky": ["hromada", "martians"]},
                   follow_redirects=False)
    assert r.status_code == 303
    s.refresh(o)
    assert (o.tags, o.zayavnyky, o.tags_avto) == ("osvita", "hromada", False)
    assert s.exec(select(Match).where(Match.potreba_id == p.id, Match.opportunity_id == o.id)).one()
    assert marko.post("/reyestr/999999/klasyfikatsiya", data={"tags": "osvita"}, follow_redirects=False).status_code == 404


def test_verdict_must_be_a_known_value(s, marko):
    o = _opp(s)
    assert marko.post(f"/reyestr/{o.id}/verdykt", data={"verdykt": "yes!"}, follow_redirects=False).status_code == 400
    assert marko.post(f"/reyestr/{o.id}/verdykt", data={"verdykt": "уточнити"}, follow_redirects=False).status_code == 303
    s.refresh(o)
    assert o.verdykt == "уточнити" and o.notes == ""
    assert marko.get("/reyestr/999999").status_code == 404


# ── programme library ────────────────────────────────────────────────────────

def test_library_upload_dedup_search_and_download(s, marko):
    o = _opp(s)
    dani = "Положення програми: подаються громади та підприємства з бюджетом проєкту".encode()
    data = {"opportunity_id": str(o.id), "vyd": "polozhennya", "source_url": " https://funder.example/rules.txt "}
    for _ in range(2):
        r = marko.post("/biblioteka", data=data, files={"fayl": ("rules.txt", dani, "text/plain")},
                       follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == f"/reyestr/{o.id}#fayly"
    pf = s.exec(select(ProgramFile)).one()                    # the second upload was a duplicate
    assert (pf.vyd, pf.source_url, pf.created_by) == ("polozhennya", "https://funder.example/rules.txt", "Марко")
    assert "громади" in pf.tekst
    assert "rules.txt" in marko.get("/biblioteka?q=громади").text
    assert "rules.txt" not in marko.get("/biblioteka?vyd=forma").text
    assert "rules.txt" in marko.get(f"/reyestr/{o.id}").text
    r = marko.get(f"/biblioteka/{pf.id}/fayl")
    assert r.status_code == 200 and r.content == dani
    assert marko.get("/biblioteka/999999/fayl").status_code == 404


def test_library_refusals_and_unreadable_files(s, marko):
    o = _opp(s)
    assert marko.post("/biblioteka", data={"opportunity_id": "999999"}, files={"fayl": ("a.txt", b"x", "text/plain")},
                      follow_redirects=False).status_code == 404
    assert marko.post("/biblioteka", data={"opportunity_id": str(o.id)},
                      files={"fayl": ("a.exe", b"MZ", "application/x-msdownload")},
                      follow_redirects=False).status_code == 400
    # a broken PDF: text extraction fails, the file is still stored without text
    r = marko.post("/biblioteka", data={"opportunity_id": str(o.id), "vyd": "nonsense"},
                   files={"fayl": ("broken.pdf", b"%PDF-1.4 garbage", "application/pdf")}, follow_redirects=False)
    assert r.status_code == 303
    pf = s.exec(select(ProgramFile)).one()
    assert (pf.tekst, pf.vyd) == ("", "inshe")


# ── knowledge ────────────────────────────────────────────────────────────────

def test_knowledge_import_updates_by_slug_and_skips_incomplete_rows(s, marko):
    from app import znannya
    z = {"slug": "eucf-8", "typ": "bryf", "title": "EUCF call 8", "dek": "short", "body_md": "# Body",
         "dzherela": [{"title": "EUCF", "url": "https://eucf.example"}], "data": SOHODNI.isoformat()}
    assert znannya.importuvaty([z, {"slug": "", "title": "x"}, {"slug": "no-title"}]) == \
        {"novi": 1, "onovleno": 0, "bez_zmin": 0}
    assert znannya.importuvaty([z]) == {"novi": 0, "onovleno": 0, "bez_zmin": 1}
    assert znannya.importuvaty([{**z, "title": "EUCF call 8 (updated)", "typ": "weird"}]) == \
        {"novi": 0, "onovleno": 1, "bez_zmin": 0}
    zn = s.exec(select(Znannya)).one()
    assert (zn.title, zn.typ) == ("EUCF call 8 (updated)", "stattya")
    assert "EUCF call 8" in marko.get("/znannya?typ=stattya").text
    assert "EUCF call 8" not in marko.get("/znannya?typ=bryf").text
    assert marko.get("/znannya/eucf-8").status_code == 200
    assert marko.get("/znannya/nothing-here").status_code == 404


def test_knowledge_command_line(tmp_path, monkeypatch, capsys):
    import runpy
    f = tmp_path / "znannya.json"
    f.write_text(json.dumps([{"slug": "cli-1", "title": "From the CLI"}]), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["app.znannya", "--fayl", str(f)])
    sys.modules.pop("app.znannya", None)
    runpy.run_module("app.znannya", run_name="__main__")
    assert "'novi': 1" in capsys.readouterr().out


# ── document preview ─────────────────────────────────────────────────────────

def _dok(s, dani, imya):
    p = Potreba(nazva="T"); s.add(p); s.commit()
    m = docstore.zberehty(dani, imya, "zapyty")
    d = Document(potreba_id=p.id, nazva=imya, stored_name=m["stored_name"], rozmir=m["rozmir"])
    s.add(d); s.commit(); s.refresh(d)
    return d


def test_preview_refuses_unknown_kinds_ids_and_missing_files(s, marko):
    assert marko.get("/perehlyad/nope/1").status_code == 404
    assert marko.get("/perehlyad/dok/abc").status_code == 404
    assert marko.get("/perehlyad/pf/999999").status_code == 404
    d = _dok(s, b"x", "gone.txt")
    docstore.shlyakh(d.stored_name).unlink()
    assert marko.get(f"/perehlyad/dok/{d.id}").status_code == 404


def test_preview_of_images_archives_and_office_files_without_libreoffice(s, marko, monkeypatch):
    img = _dok(s, b"\x89PNG\r\n\x1a\n fake", "pic.png")
    r = marko.get(f"/perehlyad/dok/{img.id}/vmist")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    zipf = _dok(s, b"PK\x05\x06" + b"\0" * 18, "pack.zip")
    assert marko.get(f"/perehlyad/dok/{zipf.id}/vmist").status_code == 404
    monkeypatch.setattr(perehlyad, "soffice", lambda: None)
    docx = _dok(s, b"not really a docx", "plan.docx")
    assert marko.get(f"/perehlyad/dok/{docx.id}").status_code == 200
    assert marko.get(f"/perehlyad/dok/{docx.id}/vmist").status_code == 404
    assert marko.get(f"/perehlyad/dok/{docx.id}/storinka/1.png").status_code == 404


def test_preview_of_a_programme_file(s, marko):
    o = _opp(s)
    m = docstore.zberehty(b"plain rules", "rules.txt", "prohramy")
    pf = ProgramFile(opportunity_id=o.id, nazva="rules.txt", vyd="polozhennya", stored_name=m["stored_name"],
                     sha256=m["sha256"], rozmir=m["rozmir"])
    s.add(pf); s.commit(); s.refresh(pf)
    r = marko.get(f"/perehlyad/pf/{pf.id}")
    assert r.status_code == 200 and "plain rules" in r.text and f"/reyestr/{o.id}" in r.text


def _fake_pymupdf(monkeypatch, pages=2):
    saved = []

    class Pix:
        def save(self, path):
            saved.append(path)
            with open(path, "wb") as f:
                f.write(b"\x89PNG fake")

    class Page:
        def get_pixmap(self, dpi):
            assert dpi in (40, 110)
            return Pix()

    class Doc:
        page_count = pages

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def __getitem__(self, i):
            return Page()
    monkeypatch.setitem(sys.modules, "pymupdf", types.SimpleNamespace(open=lambda path: Doc()))
    return saved


def test_page_png_is_rendered_once_and_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(perehlyad, "KESH", tmp_path / "kesh")
    saved = _fake_pymupdf(monkeypatch)
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4 x")
    first = perehlyad.png_storinky(pdf, 1, 40)
    assert first and first.exists() and perehlyad.png_storinky(pdf, 1, 40) == first
    assert len(saved) == 1
    assert perehlyad.png_storinky(pdf, 3) is None and perehlyad.png_storinky(pdf, 0) is None


def test_page_png_route_with_a_renderer(s, marko, monkeypatch, tmp_path):
    from tests.test_perehlyad import PDF
    monkeypatch.setattr(perehlyad, "KESH", tmp_path / "kesh")
    _fake_pymupdf(monkeypatch)
    d = _dok(s, PDF, "a.pdf")
    r = marko.get(f"/perehlyad/dok/{d.id}/storinka/1.png?dpi=20")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert "private" in r.headers["cache-control"]


def test_libreoffice_output_lands_in_the_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(perehlyad, "KESH", tmp_path / "kesh")
    monkeypatch.setattr(perehlyad, "soffice", lambda: "soffice")

    def fake_run(argv, **kw):
        out = argv[argv.index("--outdir") + 1]
        import pathlib
        pathlib.Path(out).mkdir()
        (pathlib.Path(out) / "00000.pdf").write_bytes(b"%PDF-1.4 converted")
        (pathlib.Path(out) / "stray.pdf").write_bytes(b"%PDF-1.4 not ours")
    monkeypatch.setattr(perehlyad.subprocess, "run", fake_run)
    doc = tmp_path / "plan.doc"
    doc.write_bytes(b"binary doc")
    pdf = perehlyad.pdf_dlya(doc)
    assert pdf == perehlyad.kesh_pdf(doc) and pdf.read_bytes() == b"%PDF-1.4 converted"
    assert perehlyad.pdf_dlya(tmp_path / "missing.doc") is None
    perehlyad._konvertuvaty([])                        # nothing to do is not an error


def test_finding_libreoffice(tmp_path, monkeypatch):
    exe = tmp_path / "soffice.exe"
    exe.write_bytes(b"")
    monkeypatch.setenv("HRANTY_SOFFICE", str(exe))
    assert perehlyad.soffice() == str(exe)
    monkeypatch.setenv("HRANTY_SOFFICE", str(tmp_path / "nope.exe"))
    monkeypatch.setattr(perehlyad.shutil, "which", lambda name: None)
    monkeypatch.setattr(perehlyad, "Path", lambda p: tmp_path / "never" / str(len(p)))
    assert perehlyad.soffice() is None


@pytest.mark.parametrize("imya,vyd", [("a.PDF", "pdf"), ("b.xlsx", "ofisnyi"), ("c.csv", "tekst"),
                                      ("d.webp", "zobrazhennya"), ("e.htm", "html"), ("f.zip", "inshe")])
def test_preview_kind_by_extension(imya, vyd):
    from pathlib import Path
    assert perehlyad.vyd(Path(imya)) == vyd


def test_damaged_pdf_has_zero_pages(tmp_path):
    f = tmp_path / "bad.pdf"
    f.write_bytes(b"not a pdf")
    assert perehlyad.storinky(f) == 0
