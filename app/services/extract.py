"""Текст із PDF/XLSX/DOCX/TXT. Форк ofis_crm/app/services/extract.py::read_file (див. FORK.md);
повертає рядок (порожній, якщо тексту немає — скан без OCR)."""
from pathlib import Path


def read_file(path: Path) -> str:
    suf = Path(path).suffix.lower()
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
