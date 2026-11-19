# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Edge cases of the shared auth kit (app/authkit.py is a byte-identical copy shared with the
Project Office CRM, so it is only tested here, never edited): password policy, legacy hashes,
session touch and idle limits, failure windows, CSRF origins, body limits, HSTS, CSP reports."""
from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import authkit
from app.security import KIT
from tests.conftest import sesiya_dlya


@pytest.mark.parametrize("pw,ok", [("short1!", False), ("onlylowercaseletters", False),
                                   ("Long-enough-pass", True), ("longpassword12", True),
                                   ("OnlyLettersMixedCase", False)])
def test_password_policy(pw, ok):
    assert (authkit.password_problem(pw) is None) is ok


def test_legacy_hash_mismatch_and_empty_hash_fail_without_upgrade():
    legacy = authkit.legacy_sha256("salt", "right-password")
    assert authkit.verify_password(legacy, "right-password", legacy_secret="salt")[0]
    assert authkit.verify_password(legacy, "wrong-password", legacy_secret="salt") == (False, None)
    assert authkit.verify_password("", "anything", legacy_secret="salt") == (False, None)
    assert authkit.verify_password("$argon2id$broken", "x", legacy_secret="salt") == (False, None)


def test_session_is_touched_at_most_once_a_minute_and_long_tokens_are_ignored():
    from app.db import engine
    raw = sesiya_dlya("marko")
    with Session(engine) as s:
        row = s.exec(select(authkit.AuthSession)).one()
        row.last_seen_at = authkit.utcnow() - timedelta(minutes=5)
        s.add(row); s.commit()
        assert authkit.resolve_session(s, KIT, raw) is not None
        s.refresh(row)
        assert authkit.utcnow() - row.last_seen_at < timedelta(seconds=5)
        assert authkit.resolve_session(s, KIT, "x" * 129) is None
        authkit.revoke_session(s, "")                      # nothing to revoke is not an error


def test_old_failures_fall_out_of_the_window(monkeypatch):
    cfg = authkit.KitConfig(app_slug="t", secret="s", max_failures=3, failure_window=10)
    lim = authkit.LoginLimiter(cfg)
    clock = [1000.0]
    monkeypatch.setattr(authkit.time, "monotonic", lambda: clock[0])
    keys = lim.keys("10.0.0.1", "Marko ")
    assert keys[0] == "pair:10.0.0.1|marko"
    for _ in range(2):
        lim.failure(keys)
    clock[0] += 60                                          # the first two are now stale
    lim.failure(keys)
    assert lim.retry_after(keys) == 0
    lim.failure(keys); lim.failure(keys)
    assert lim.retry_after(keys) > 0


def _mini(secure=False, **kw):
    cfg = authkit.KitConfig(app_slug="t", secret="s", secure_cookies=secure, **kw)
    app = FastAPI(dependencies=[])
    authkit.install(app, cfg)

    @app.post("/echo")
    async def echo():
        return {"ok": True}
    return app, cfg


def test_hsts_only_with_secure_cookies_and_csp_extras_are_merged():
    app, _ = _mini(secure=True, csp_extra={"img-src": ["https://tiles.example"]})
    r = TestClient(app, base_url="https://testserver").get("/missing")
    assert r.headers["strict-transport-security"].startswith("max-age=63072000")
    assert "img-src 'self' data: blob: https://tiles.example" in r.headers["content-security-policy"]
    assert "__Host-t_csrf" in r.headers["set-cookie"]
    app, _ = _mini()
    assert "strict-transport-security" not in TestClient(app).get("/missing").headers


def test_a_malformed_content_length_is_413():
    app, _ = _mini()
    r = TestClient(app).post("/echo", content=b"{}", headers={"content-length": "abc"})
    assert r.status_code == 413


def test_csp_reports_are_accepted_even_when_garbled(caplog):
    app, _ = _mini()
    c = TestClient(app)
    assert c.post("/csp-report", content=b"not json").status_code == 204
    assert c.post("/csp-report", content=b"[1, 2]").status_code == 204
    r = c.post("/csp-report", content=b'{"csp-report": {"violated-directive": "script-src", "blocked-uri": "inline"}}')
    assert r.status_code == 204 and "csp-violation script-src" in caplog.text


def test_csrf_refuses_an_opaque_null_origin(marko):
    r = marko.post("/zadachi", data={"title": "x"}, headers={"origin": "null"}, follow_redirects=False)
    assert r.status_code == 403
