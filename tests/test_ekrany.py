"""Every reachable screen, on the synthetic demo data, as a signed-in user.

* each GET page linked from the app answers 200 (no 404/500 anywhere);
* in English, no Ukrainian text is left in visible text or in title / aria-label /
  placeholder / alt attributes — the i18n gate for the whole UI;
* in Ukrainian, the same pages render too (the second language stays whole).
"""
import html
import re

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session


CYR = re.compile(r"[А-Яа-яІіЇїЄєҐґ]")
SKIP = ("/static", "/logout", "/lang/", "/fayl", ".docx", ".pdf", ".zip", "/vmist", ".png", "export")


@pytest.fixture()
def demo():
    from sqlmodel import select

    from app import seed_demo
    from app.db import engine
    from app.models import User
    with Session(engine) as s:
        for u in s.exec(select(User)):          # a fresh install: no passwords yet
            u.password_hash = ""
            s.add(u)
        s.commit()
        assert seed_demo.seed(s) != {"skipped": True}
    return engine


def _klient(engine, lang):
    from app.main import app
    c = TestClient(app, follow_redirects=False)
    c.cookies.set("lang", lang)
    tok = re.search(r'name="csrf_token" value="([^"]+)"', c.get("/login").text).group(1)
    r = c.post("/login", data={"login": "marko", "password": "marko-demo-2026", "csrf_token": tok})
    assert r.status_code == 303
    return c


def obkhid(c, limit=400):
    seen, queue, bad, left = set(), ["/"], [], set()
    while queue and len(seen) < limit:
        u = queue.pop(0)
        if u in seen:
            continue
        seen.add(u)
        r = c.get(u)
        if r.status_code in (303, 307):
            loc = r.headers.get("location", "")
            if loc.startswith("/"):
                queue.append(loc)
            continue
        if r.status_code != 200:
            bad.append((u, r.status_code))
            continue
        if "html" not in r.headers.get("content-type", ""):
            continue
        body = re.sub(r"<script\b(?![^>]*application/json).*?</script>|<style.*?</style>|<!--.*?-->",
                      "", r.text, flags=re.S)
        for x in re.findall(r">([^<]+)<", body) + re.findall(
                r'(?:title|aria-label|placeholder|alt|data-p)="([^"]*)"', body):
            x = html.unescape(x).strip()
            if CYR.search(x) and x != "Українська":
                left.add((re.sub(r"/\d+", "/N", u), x[:80]))
        for h in re.findall(r'href="(/[^"#]*)"', r.text):
            h = html.unescape(h)
            if h.startswith(SKIP[:3]) or any(s in h for s in SKIP[3:]):
                continue
            key = re.sub(r"/\d+", "/N", h.split("?")[0])
            if sum(1 for s in seen if re.sub(r"/\d+", "/N", s.split("?")[0]) == key) < 2:
                queue.append(h)
    return seen, bad, left


def test_every_screen_in_english_is_200_and_fully_translated(demo):
    seen, bad, left = obkhid(_klient(demo, "en"))
    assert len(seen) > 40, f"crawler reached only {len(seen)} pages"
    assert not bad, f"non-200 screens: {bad[:10]}"
    assert not left, f"untranslated UI strings: {sorted(left)[:15]}"


def test_every_screen_in_ukrainian_is_200(demo):
    seen, bad, _ = obkhid(_klient(demo, "uk"), limit=150)
    assert not bad, f"non-200 screens: {bad[:10]}"


def test_demo_seed_is_idempotent(demo):
    from app import seed_demo
    with Session(demo) as s:
        assert seed_demo.seed(s) == {"skipped": True}
