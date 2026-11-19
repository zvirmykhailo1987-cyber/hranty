# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Small parsers and fallbacks whose wrong answer would quietly become a wrong number or date."""
import sys
import types
from datetime import date, timedelta

import pytest
from sqlmodel import select

from app import i18n
from app.models import Document, Opportunity, Potreba, utcnow
from app.services import extract, gates, perehlyad, rozbir


@pytest.mark.parametrize("raw,ochik", [(None, None), (date(2026, 1, 2), date(2026, 1, 2)), ("2026-03-04", date(2026, 3, 4)),
                                       ("04.03.2026", date(2026, 3, 4)), ("04/03/2026 10:00", date(2026, 3, 4)),
                                       ("soon", None)])
def test_dates(raw, ochik):
    assert rozbir.data(raw) == ochik


def test_exchange_rates_come_only_from_configuration(monkeypatch):
    monkeypatch.setenv("HRANTY_KURSY", "usd=0.9, UAH=abc,junk")
    assert rozbir.kursy() == {"EUR": 1.0, "USD": 0.9}
    assert rozbir.v_eur(None, "EUR") is None and rozbir.v_eur(10, "") is None and rozbir.v_eur(10, "UAH") is None


def test_amount_edge_numbers():
    assert rozbir.zayavnyky_z_tekstu("") == []
    assert rozbir._v_chyslo("1.2.3", "") == 123.0          # stray dots are thousands, not a crash
    assert rozbir._v_chyslo("1,5", "млн") == 1_500_000.0


def test_days_until_deadline():
    today = utcnow().date()
    assert Opportunity(title="x").dniv_do() is None
    assert Opportunity(title="x", deadline=today + timedelta(days=3)).dniv_do(today) == 3


def test_translation_fallbacks(monkeypatch, tmp_path):
    assert i18n.translate("  Заявки  ", "en") == "  " + i18n.translate("Заявки", "en") + "  "
    assert i18n.plural_en(1, "day", "days") == "day" and i18n.plural_en(None, "day", "days") == "days"
    i18n.catalog.cache_clear()
    monkeypatch.setattr(i18n, "_CATALOG_PATH", tmp_path / "missing.json")
    try:
        assert i18n.catalog() == {}
    finally:
        i18n.catalog.cache_clear()


def test_pdf_text_uses_pymupdf_when_it_is_installed(monkeypatch, tmp_path):
    class Page:
        def get_text(self):
            return "Programme rules, eligible costs and deadlines"

    class Doc(list):
        closed = False

        def close(self):
            Doc.closed = True
    monkeypatch.setitem(sys.modules, "fitz", types.SimpleNamespace(open=lambda p: Doc([Page(), Page()])))
    f = tmp_path / "a.pdf"
    f.write_bytes(b"%PDF")
    assert extract.read_file(f).count("Programme rules") == 2 and Doc.closed


def test_page_images_need_the_optional_renderer(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "pymupdf", None)       # import fails like on a server without it
    assert perehlyad.png_storinky(tmp_path / "a.pdf", 1) is None
    note = tmp_path / "notes.txt"
    note.write_text("x", encoding="utf-8")
    assert perehlyad.pdf_dlya(note) is None                 # text is shown as text, never converted


def test_an_older_version_listed_later_does_not_replace_the_newer(s):
    p = Potreba(nazva="x"); s.add(p); s.commit()
    from app.models import Application
    a = Application(potreba_id=p.id); s.add(a); s.commit()
    s.add(Document(application_id=a.id, vyd="byudzhet", nazva="v2", versiya=2, group_uid="g")); s.commit()
    s.add(Document(application_id=a.id, vyd="byudzhet", nazva="v1", versiya=1, group_uid="g")); s.commit()
    assert [d.nazva for d in gates.aktualni_dokumenty(s, a.id)] == ["v2"]


def test_signoff_on_a_missing_document_is_404_and_names_keep_their_extension(s, bohdan, marko):
    assert bohdan.post("/dokumenty/999999/pohodyty", follow_redirects=False).status_code == 404
    p = Potreba(nazva="x"); s.add(p); s.commit()
    from app.models import Application
    a = Application(potreba_id=p.id, stadiya="dokumenty"); s.add(a); s.commit()
    marko.post(f"/zayavky/{a.id}/dokument", data={"vyd": "byudzhet", "nazva": "budget.txt"},
               files={"fayl": ("b.txt", b"1", "text/plain")})
    d = s.exec(select(Document)).one()
    assert "filename=\"budget.txt\"" in marko.get(f"/dokumenty/{d.id}/fayl").headers["content-disposition"]
    assert marko.post(f"/zayavky/{a.id}/krok", data={"krok": "wait"}, follow_redirects=False).status_code == 303
    s.refresh(a)
    assert a.owner_id is None


def test_password_tool_entry_point(monkeypatch):
    import runpy
    monkeypatch.setattr(sys, "argv", ["app.parol"])
    sys.modules.pop("app.parol", None)
    with pytest.raises(SystemExit) as e:
        runpy.run_module("app.parol", run_name="__main__")
    assert e.value.code == 2
