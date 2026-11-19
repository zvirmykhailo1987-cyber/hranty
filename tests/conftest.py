import os
import sys
import tempfile
from pathlib import Path

# Окрема тимчасова база й тека файлів ДО імпорту застосунку
_TMP = tempfile.mkdtemp(prefix="hranty_test_")
os.environ["DATA_DIR"] = _TMP
os.environ["HRANTY_ENV"] = "dev"
os.environ.setdefault("UI_DEFAULT_LANG", "uk")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, SQLModel, select  # noqa: E402

from app.db import engine, init_db  # noqa: E402
from app.models import User  # noqa: E402
from app import authkit  # noqa: E402
from app.security import KIT, LIMITER, hash_pw  # noqa: E402

CSRF_TEST_TOKEN = "test-csrf-token-0123456789abcdef"
TEST_PAROL = "test-parol-12345"


@pytest.fixture(autouse=True)
def chysta_baza():
    SQLModel.metadata.drop_all(engine)
    init_db()
    with Session(engine) as s:
        for u in s.exec(select(User)):
            u.password_hash = hash_pw(TEST_PAROL)
            s.add(u)
        s.commit()
    LIMITER.reset_all()
    yield


@pytest.fixture
def s():
    with Session(engine) as sess:
        yield sess


def z_csrf(c: TestClient) -> TestClient:
    """Valid double-submit CSRF pair, as a browser that loaded a page would have."""
    c.cookies.set(KIT.csrf_cookie, CSRF_TEST_TOKEN)
    c.headers["X-CSRF-Token"] = CSRF_TEST_TOKEN
    return c


def sesiya_dlya(login: str) -> str:
    """Create a real server-side session row for a user and return the raw cookie token."""
    import secrets
    from datetime import timedelta
    raw = secrets.token_urlsafe(32)
    with Session(engine) as sess:
        u = sess.exec(select(User).where(User.login == login)).first()
        now = authkit.utcnow()
        sess.add(authkit.AuthSession(token_hash=authkit._token_hash(raw), user_id=u.id,
                                     created_at=now, last_seen_at=now,
                                     expires_at=now + timedelta(seconds=KIT.session_ttl)))
        sess.commit()
    return raw


def _klient(login: str) -> TestClient:
    from app.main import app
    c = z_csrf(TestClient(app))
    c.cookies.set(KIT.session_cookie, sesiya_dlya(login))
    return c


@pytest.fixture
def marko():
    return _klient("marko")


@pytest.fixture
def bohdan():
    return _klient("bohdan")


@pytest.fixture
def halyna():
    return _klient("halyna")


@pytest.fixture
def oksana():
    return _klient("oksana")
