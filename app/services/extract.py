# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Текст із PDF/XLSX/DOCX/TXT. Той самий читач, що в Project Office CRM;
повертає рядок (порожній, якщо тексту немає — скан без OCR)."""
from pathlib import Path


MAX_ROZPAKOVKA = 100 * 1024 * 1024     # uncompressed bytes allowed inside a DOCX/XLSX


def _zip_bezpechnyi(path: Path) -> bool:
    """DOCX/XLSX are ZIP archives: refuse decompression bombs before a parser opens them."""
    import zipfile
    try:
        with zipfile.ZipFile(path) as z:
            infos = z.infolist()
            return len(infos) <= 5000 and sum(i.file_size for i in infos) <= MAX_ROZPAKOVKA
    except zipfile.BadZipFile:
        return False


def read_file(path: Path) -> str:
    suf = Path(path).suffix.lower()
    if suf in (".docx", ".xlsx", ".xlsm") and not _zip_bezpechnyi(Path(path)):
        return ""
    if suf == ".pdf":
        try:
            import fitz
            doc = fitz.open(str(path))
            parts = [p.get_text() for p in doc]
            doc.close()
        except Exception:  # noqa: BLE001
            from pypdf import PdfReader
            parts = [(p.extract_text() or "") for p in PdfReader(str(path)).pages]
        t = "\n".join(parts)
        return t if len(t.strip()) >= 20 else ""
    if suf in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook
        wb = load_workbook(str(path), read_only=True, data_only=True)
        rows = []
        for ws in wb.worksheets:
            for r in ws.iter_rows(values_only=True):
                cells = [str(c) for c in r if c is not None]
                if cells:
                    rows.append(" | ".join(cells))
        wb.close()
        return "\n".join(rows)
    if suf == ".docx":
        from docx import Document as Docx
        d = Docx(str(path))
        parts = [p.text for p in d.paragraphs]
        for tbl in d.tables:
            for row in tbl.rows:
                parts.append(" | ".join(c.text for c in row.cells))
        return "\n".join(parts)
    if suf in (".txt", ".md", ".csv"):
        return Path(path).read_text(encoding="utf-8", errors="replace")
    return ""
