# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Конфіг з ENV. У проді — fail-fast, якщо секрети не задані.

Секрети НІКОЛИ не в git: локально — дефолти для розробки, у проді — `DATA_DIR/secrets.env`
або змінні оточення Coolify. (Той самий патерн, що в Project Office CRM.)
"""
import os
from pathlib import Path

ENV = os.getenv("HRANTY_ENV", "production")   # fail-closed: dev/demo must be chosen explicitly            # dev | demo | production
IS_PROD = ENV == "production"
# Demo = the public showcase: synthetic data (`python -m app.seed_demo`).
IS_DEMO = ENV == "demo"

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _load_secrets_file() -> None:
    p = DATA_DIR / "secrets.env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_secrets_file()

DB_PATH = Path(os.getenv("DB_PATH", DATA_DIR / "hranty.sqlite"))
FILES_DIR = Path(os.getenv("FILES_DIR", DATA_DIR / "fayly"))     # документи програм і заявок
FILES_DIR.mkdir(parents=True, exist_ok=True)

PORT = int(os.getenv("PORT", "8070"))
PAGE_SIZE = int(os.getenv("PAGE_SIZE", "50"))

SECRET = os.getenv("HRANTY_SECRET", "" if IS_PROD else "dev-secret-not-for-production")
# Password of the first admin on an EMPTY production database (no default).
BOOTSTRAP_PASSWORD = os.getenv("HRANTY_BOOTSTRAP_PASSWORD", "")
# Token of the ingest worker for /api/*. Empty → the API is disabled.
API_TOKEN = os.getenv("HRANTY_API_TOKEN", "dev-api-token" if ENV == "dev" else "")

# Sessions (server-side, see app/authkit.py)
# Secure + __Host- cookies everywhere except local dev (browsers treat http://localhost as secure).
SECURE_COOKIES = ENV != "dev" or os.getenv("HRANTY_SECURE_COOKIES") == "1"
SESSION_TTL = int(os.getenv("HRANTY_SESSION_TTL", str(60 * 60 * 24 * 7)))
SESSION_IDLE = int(os.getenv("HRANTY_SESSION_IDLE", str(60 * 60 * 8)))
MAX_FILE_MB = 25


def check_prod_ready() -> None:
    """Fail fast: production never starts with missing or development secrets."""
    if not IS_PROD:
        return
    missing = [k for k, v in {"HRANTY_SECRET": SECRET, "HRANTY_API_TOKEN": API_TOKEN}.items() if not v]
    if missing:
        raise RuntimeError("HRANTY_ENV=production, but secrets are missing: " + ", ".join(missing))
    if SECRET.startswith("dev-") or len(SECRET) < 32:
        raise RuntimeError("HRANTY_SECRET must be a random value of at least 32 characters.")
    if API_TOKEN.startswith("dev-") or len(API_TOKEN) < 32:
        raise RuntimeError("HRANTY_API_TOKEN must be a random value of at least 32 characters.")
