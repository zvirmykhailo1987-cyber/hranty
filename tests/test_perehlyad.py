# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Перегляд документів у порталі: PDF inline + сторінки PNG, текст рендериться, HTML — у пісочниці."""
from app.models import Document, Potreba
from app.services import docstore

PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj "
       b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF")


def _dok(s, dani, imya):
    p = Potreba(nazva="Т"); s.add(p); s.commit(); s.refresh(p)
    m = docstore.zberehty(dani, imya, "zapyty")
    d = Document(potreba_id=p.id, nazva=imya, stored_name=m["stored_name"], rozmir=m["rozmir"]); s.add(d); s.commit(); s.refresh(d)
    return d


def test_pdf_i_storinky(s, marko):
    d = _dok(s, PDF, "a.pdf")
    html = marko.get(f"/perehlyad/dok/{d.id}").text
    assert "perehlyad-ramka" in html
    r = marko.get(f"/perehlyad/dok/{d.id}/vmist")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and "inline" in r.headers["content-disposition"]
    import importlib.util
    if importlib.util.find_spec("pymupdf"):          # optional page-PNG renderer for phones
        assert "storinka/1.png" in html
        assert marko.get(f"/perehlyad/dok/{d.id}/storinka/1.png").headers["content-type"] == "image/png"


def test_tekst_i_html(s, marko):
    d = _dok(s, "# Заголовок\n\nТекст <script>x</script>".encode(), "a.md")
    html = marko.get(f"/perehlyad/dok/{d.id}").text
    assert "<h1>Заголовок</h1>" in html and "<script>x</script>" not in html
    h = _dok(s, b"<p>hi</p><script>alert(1)</script>", "b.html")
    r = marko.get(f"/perehlyad/dok/{h.id}/vmist")
    assert "sandbox" in r.headers["content-security-policy"]


def test_preview_content_may_be_framed_by_its_own_page(s, marko):
    """The global policy forbids framing; the preview page frames /vmist from its own origin."""
    h = _dok(s, b"<p>hi</p>", "c.html")
    r = marko.get(f"/perehlyad/dok/{h.id}/vmist")
    assert "frame-ancestors 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "SAMEORIGIN"
    from app.web import perehlyad
    assert perehlyad._RAMKA["X-Frame-Options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in perehlyad._RAMKA["Content-Security-Policy"]
