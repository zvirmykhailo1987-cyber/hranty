"""Перегляд документів у порталі без завантаження.

PDF показується як є; DOCX/DOC/ODT/RTF/XLSX/XLS/ODS/PPTX конвертуються в PDF через LibreOffice (headless)
один раз — результат кешується за sha256 вмісту в DATA_DIR/perehlyad/<sha>.pdf, тож повторне відкриття миттєве.
Текст (MD/TXT/CSV/JSON) рендериться в HTML; зображення — як <img>; HTML — у пісочниці без скриптів.
Попередня масова конвертація: `python -m app.perehlyad` (усе, що є в порталі).
"""
import hashlib
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from .. import config

KESH = config.DATA_DIR / "perehlyad"
OFISNI = {".docx", ".doc", ".odt", ".rtf", ".xlsx", ".xls", ".ods", ".pptx", ".ppt", ".odp"}
TEKSTOVI = {".md", ".txt", ".csv", ".json"}
ZOBRAZHENNYA = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_ZAMOK = threading.Lock()


def vyd(path: Path) -> str:
    r = path.suffix.lower()
    if r == ".pdf":
        return "pdf"
    if r in OFISNI:
        return "ofisnyi"
    if r in TEKSTOVI:
        return "tekst"
    if r in ZOBRAZHENNYA:
        return "zobrazhennya"
    if r in (".html", ".htm"):
        return "html"
    return "inshe"


def soffice() -> str | None:
    kandydaty = [os.getenv("HRANTY_SOFFICE", ""), shutil.which("soffice") or "", shutil.which("libreoffice") or "",
                 r"C:\Program Files\LibreOffice\program\soffice.exe", r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"]
    return next((k for k in kandydaty if k and Path(k).exists()), None)


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for ch in iter(lambda: f.read(1 << 20), b""):
            h.update(ch)
    return h.hexdigest()


def kesh_pdf(path: Path) -> Path:
    return KESH / f"{_sha(path)}.pdf"


def _konvertuvaty(fayly: list[Path], taimaut: int = 600) -> None:
    """Пакетна конвертація в PDF у кеш (один запуск LibreOffice на групу файлів)."""
    so = soffice()
    if not so or not fayly:
        return
    KESH.mkdir(parents=True, exist_ok=True)
    with _ZAMOK, tempfile.TemporaryDirectory(prefix="hranty_lo_") as td:
        td = Path(td)
        vkhid = td / "in"
        vkhid.mkdir()
        mapa = {}
        for i, f in enumerate(fayly):
            kopiya = vkhid / f"{i:05d}{f.suffix.lower()}"      # латинські імена без колізій
            shutil.copyfile(f, kopiya)
            mapa[kopiya.stem] = kesh_pdf(f)
        profil = (td / "profil").as_uri()
        # fixed argv, renamed copies of our own files, no shell
        subprocess.run([so, f"-env:UserInstallation={profil}", "--headless", "--norestore", "--convert-to", "pdf",  # noqa: S603  # nosec B603
                        "--outdir", str(td / "out"), *[str(p) for p in sorted(vkhid.iterdir())]],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=taimaut, check=False)
        for pdf in (td / "out").glob("*.pdf") if (td / "out").exists() else []:
            cil = mapa.get(pdf.stem)
            if cil:
                shutil.move(str(pdf), cil)


def pdf_dlya(path: Path) -> Path | None:
    """PDF для перегляду: сам файл, кешована конвертація або None (немає LibreOffice / не вдалося)."""
    path = Path(path)
    if not path.exists():
        return None
    v = vyd(path)
    if v == "pdf":
        return path
    if v != "ofisnyi":
        return None
    k = kesh_pdf(path)
    nevdacha = k.with_suffix(".failed")
    if not k.exists() and not nevdacha.exists() and soffice():
        from .extract import _zip_bezpechnyi
        if path.suffix.lower() in (".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp") and not _zip_bezpechnyi(path):
            nevdacha.parent.mkdir(parents=True, exist_ok=True)
            nevdacha.write_text("refused: archive too large", encoding="utf-8")
            return None
        try:
            _konvertuvaty([path], taimaut=180)
        except subprocess.TimeoutExpired:
            pass                                   # treated as a failure below
        if not k.exists():
            # Remember the failure: a file that cannot be converted must not hold the
            # conversion lock for 180 s on every view.
            KESH.mkdir(parents=True, exist_ok=True)
            nevdacha.write_text("conversion failed", encoding="utf-8")
    return k if k.exists() else None


def pidhotuvaty(fayly: list[Path], porciya: int = 40) -> dict:
    """Масова попередня конвертація всіх офісних файлів, яких ще немає в кеші."""
    treba = [Path(f) for f in fayly if Path(f).exists() and vyd(Path(f)) == "ofisnyi" and not kesh_pdf(Path(f)).exists()]
    for i in range(0, len(treba), porciya):
        _konvertuvaty(treba[i:i + porciya])
    hotovo = sum(1 for f in treba if kesh_pdf(f).exists())
    return {"potribno": len(treba), "hotovo": hotovo, "ne_vdalos": len(treba) - hotovo}


def storinky(pdf: Path) -> int:
    from pypdf import PdfReader
    try:
        return len(PdfReader(str(pdf)).pages)
    except Exception:  # noqa: BLE001 — a damaged PDF shows as "0 pages", not a 500
        return 0


def png_storinky(pdf: Path, n: int, dpi: int = 110) -> Path | None:
    """Сторінка PDF як PNG (кеш) — для телефонів, де браузер не показує PDF у рамці.

    Optional: needs PyMuPDF (AGPL, not bundled). Without it phones get the download link."""
    try:
        import pymupdf
    except ImportError:
        return None
    cil = KESH / "png" / _sha(pdf)[:32] / f"p{n:03d}_{dpi}.png"
    if cil.exists():
        return cil
    with pymupdf.open(str(pdf)) as d:
        if not 1 <= n <= d.page_count:
            return None
        cil.parent.mkdir(parents=True, exist_ok=True)
        d[n - 1].get_pixmap(dpi=dpi).save(str(cil))
    return cil
