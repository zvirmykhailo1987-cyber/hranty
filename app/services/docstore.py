"""Безпечне сховище файлів (патерн portal_investytsiy/app/services/docstore.py, див. FORK.md):
білий список розширень, ліміт розміру, uuid-імена, захист від виходу за теку, sha256 для дедупу."""
import hashlib
import mimetypes
import uuid
from pathlib import Path

from .. import config

DOZVOLENI = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".odt", ".ods", ".rtf", ".txt", ".csv",
             ".zip", ".png", ".jpg", ".jpeg", ".pptx", ".md", ".html"}  # .md/.html віддаються лише як вкладення


class FaylPomylka(ValueError):
    pass


def _teka(pidteka: str) -> Path:
    t = (config.FILES_DIR / pidteka).resolve()
    if config.FILES_DIR.resolve() not in t.parents and t != config.FILES_DIR.resolve():
        raise FaylPomylka("недопустима тека")
    t.mkdir(parents=True, exist_ok=True)
    return t


def zberehty(dani: bytes, imya: str, pidteka: str = "zayavky") -> dict:
    rozsh = Path(imya or "").suffix.lower()
    if rozsh not in DOZVOLENI:
        raise FaylPomylka(f"тип файлу {rozsh or '(без розширення)'} не дозволено")
    if len(dani) > config.MAX_FILE_MB * 1024 * 1024:
        raise FaylPomylka(f"файл більший за {config.MAX_FILE_MB} МБ")
    if not dani:
        raise FaylPomylka("порожній файл")
    sha = hashlib.sha256(dani).hexdigest()
    stored = f"{pidteka}/{uuid.uuid4().hex}{rozsh}"
    (_teka(pidteka) / Path(stored).name).write_bytes(dani)
    return {"stored_name": stored, "sha256": sha, "rozmir": len(dani),
            "mime": mimetypes.guess_type(imya)[0] or "application/octet-stream"}


def shlyakh(stored_name: str) -> Path:
    p = (config.FILES_DIR / stored_name).resolve()
    if config.FILES_DIR.resolve() not in p.parents:
        raise FaylPomylka("недопустимий шлях")
    return p
