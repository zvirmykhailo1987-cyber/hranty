# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Break the code on purpose and check that the tests notice. Writes docs/mutation-results.json.

Coverage says a line ran. This says a test fails when the line is wrong. Each mutation removes one
guarantee from a temporary copy of the project, and the whole suite runs against that copy. A mutation
nothing catches is reported as "survived", not hidden: the fix is a missing test, never deleting the
mutation.

    python scripts/mutation_check.py           # all mutations (~1 suite run each, stops at the first failure)
    python scripts/mutation_check.py --check   # only verify every mutation still applies to the code
"""

import datetime as dt
import json
import os
import re
import shutil
import subprocess  # nosec B404 — runs this repo's own test suite only
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYTHON = sys.executable
SKIP = {".venv", "data", ".git", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", "docs", ".coverage"}

MUTATIONS = [
    # ── application stage gates ──────────────────────────────────────────────
    (
        "Screening accepts a weak match with no stated reason",
        "app/services/gates.py",
        "        if not ((m and m.score >= 0.5) or app.prychyna_vidboru.strip()):",
        "        if False:",
    ),
    (
        "Documents stage opens without the verdict \"apply\"",
        "app/services/gates.py",
        '        if (app.verdykt or (o.verdykt if o else "")) != "подаватися":',
        "        if False:",
    ),
    (
        "Stages can be skipped",
        "app/services/gates.py",
        "    if PORYADOK.index(nova) - PORYADOK.index(app.stadiya) > 1:",
        "    if False:",
    ),
    (
        "Submission opens with a required document missing",
        "app/services/gates.py",
        "            if v not in nayavni:",
        "            if False:",
    ),
    (
        "Submission opens with an unsigned document",
        "app/services/gates.py",
        "        for d in doky:\n            if not d.pohodzheno_kym:",
        "        for d in []:\n            if not d.pohodzheno_kym:",
    ),
    (
        "A date alone counts as a submission by a person",
        "app/services/gates.py",
        "        if not (app.podano_at and app.podano_kto):",
        "        if not app.podano_at:",
    ),
    (
        "Anyone may move an application to the result stage",
        "app/services/gates.py",
        "        if user is not None and not user.can(CAP_SEND):",
        "        if False:",
    ),
    # ── sign-off and submission ──────────────────────────────────────────────
    (
        "The author signs off their own document",
        "app/services/gates.py",
        "    if (doc.avtor_id is not None and doc.avtor_id == user.id) or (doc.avtor_id is None and doc.avtor == user.name):",
        "    if False:",
    ),
    (
        "Sign-off identity is the display name, not the account",
        "app/services/gates.py",
        "    if (doc.avtor_id is not None and doc.avtor_id == user.id) or (doc.avtor_id is None and doc.avtor == user.name):",
        "    if doc.avtor == user.name:",
    ),
    (
        "A submission is recorded over an unsigned current version",
        "app/web/zayavky.py",
        "    if nepohodzheni:\n",
        "    if False:\n",
    ),
    (
        "Anyone signed in may record a submission",
        "app/web/zayavky.py",
        "def podano(aid: int, data_podachi: str = Form(...), user: User = Depends(require_cap(CAP_SEND)),",
        "def podano(aid: int, data_podachi: str = Form(...), user: User = Depends(current_user),",
    ),
    (
        "A new version at submission keeps the application at submission",
        "app/web/zayavky.py",
        '        a.stadiya, a.updated_at = "dokumenty", utcnow()',
        "        a.updated_at = utcnow()",
    ),
    (
        "Documents can be changed after the submission",
        "app/web/zayavky.py",
        "    if a.podano_at:\n        raise HTTPException(409",
        "    if False:\n        raise HTTPException(409",
    ),
    # ── match scoring ────────────────────────────────────────────────────────
    (
        "An unknown budget scores zero instead of \"no data\"",
        "app/services/matching.py",
        'return Komponent("suma", 0.5, VAHY["suma"], "у запиті не вказано бюджет", True)',
        'return Komponent("suma", 0.0, VAHY["suma"], "у запиті не вказано бюджет", True)',
    ),
    (
        "An unknown programme geography scores zero",
        "app/services/matching.py",
        'return Komponent("geo", 0.5, VAHY["geo"], "географія програми: немає даних", True)',
        'return Komponent("geo", 0.0, VAHY["geo"], "географія програми: немає даних", True)',
    ),
    (
        "A programme past its deadline is still matched",
        "app/services/matching.py",
        '    if not o.active or o.status == "closed" or (o.deadline and o.deadline < sohodni):',
        '    if not o.active or o.status == "closed":',
    ),
    (
        "Recomputing deletes manually rejected matches",
        "app/services/matching.py",
        '            if m and m.stan == "novyi":\n                s.delete(m)',
        "            if m:\n                s.delete(m)",
    ),
    # ── ingest gate ──────────────────────────────────────────────────────────
    (
        "Rows refused by the ingest gate are not counted",
        "app/services/ingest.py",
        "        log.nevalidni += (len(zapysy) if isinstance(zapysy, list) else 1) - len(spysok)",
        "        log.nevalidni += 0",
    ),
    (
        "The ingest gate accepts a non-web URL",
        "app/services/ingest.py",
        '              or not str(r.get("url", "")).startswith(("http://", "https://"))]\n    if pogani:',
        "              ]\n    if pogani:",
    ),
    (
        "A feed without a status overwrites the curated one",
        "app/services/ingest.py",
        '        if k in ("status", "reliability") and k not in yavni:\n            continue',
        "        if False:\n            continue",
    ),
    (
        "A re-import overwrites tags a person corrected",
        "app/services/ingest.py",
        '    if o.tags_avto and polya["tags"] and o.tags != polya["tags"]:',
        '    if polya["tags"] and o.tags != polya["tags"]:',
    ),
    # ── daily self-maintenance ───────────────────────────────────────────────
    (
        "The daily job creates the same follow-up task twice",
        "app/services/zhyvyi.py",
        "                if not s.exec(select(Task).where(Task.application_id == a.id, Task.title == tytul)).first():",
        "                if True:",
    ),
    (
        "The daily job leaves no journal entry",
        "app/services/zhyvyi.py",
        "    s.add(IngestLog(feed=FEED, fayl=sohodni.isoformat()",
        "    (IngestLog(feed=FEED, fayl=sohodni.isoformat()",
    ),
    (
        "The scheduler runs the job on every wake-up",
        "app/main.py",
        "                    if not zhyvyi.vzhe_sohodni(s):",
        "                    if True:",
    ),
    # ── access, sessions, CSRF, lockout ──────────────────────────────────────
    (
        "A deactivated account keeps its session",
        "app/web/deps.py",
        "    if not user or not user.active:",
        "    if not user:",
    ),
    (
        "A role without the right still passes a guarded route",
        "app/web/deps.py",
        "        if not user.can(cap):\n            raise HTTPException(403",
        "        if False:\n            raise HTTPException(403",
    ),
    (
        "A return address may point to another site",
        "app/web/deps.py",
        '    if not url.startswith("/") or url.startswith("//") or',
        '    if not url.startswith("/") or',
    ),
    (
        "An idle session never expires",
        "app/authkit.py",
        "    if now >= sess.expires_at or now - sess.last_seen_at > timedelta(seconds=cfg.idle_timeout):",
        "    if now >= sess.expires_at:",
    ),
    (
        "A CSRF token from another page is accepted",
        "app/authkit.py",
        "    if not cookie or not sent or not hmac.compare_digest(cookie, sent) or not _same_origin(request):",
        "    if not cookie or not sent or not _same_origin(request):",
    ),
    (
        "Repeated failed sign-ins never lock",
        "app/authkit.py",
        "                if len(q) >= self._limit(k):",
        "                if False:",
    ),
]


def copy_project(target: Path) -> None:
    shutil.copytree(ROOT, target, ignore=lambda _d, names: [n for n in names if n in SKIP], dirs_exist_ok=True)


def run_tests(cwd: Path) -> tuple[bool, str]:
    env = {**os.environ, "UI_DEFAULT_LANG": "uk", "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("DATA_DIR", None)
    # fixed argv (this interpreter + pytest), no shell
    done = subprocess.run(  # nosec B603
        [PYTHON, "-m", "pytest", "-x", "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    found = re.findall(r"^FAILED (\S+)", done.stdout, re.M) or re.findall(r"^ERROR (\S+)", done.stdout, re.M)
    return done.returncode == 0, found[0] if found else ""


def apply(text: str, old: str, new: str, name: str) -> str:
    if text.count(old) != 1:
        raise SystemExit(f"Mutation no longer applies exactly once, fix the script: {name}")
    return text.replace(old, new, 1)


def main() -> None:
    for name, relative, old, new in MUTATIONS:
        apply((ROOT / relative).read_text(encoding="utf-8"), old, new, name)
    if "--check" in sys.argv:
        print(len(MUTATIONS), "mutations apply")
        return
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "copy"
        copy_project(work)
        passed, failing = run_tests(work)
        if not passed:
            raise SystemExit(f"The unmodified copy does not pass ({failing}), so nothing here could be trusted.")
        for name, relative, old, new in MUTATIONS:
            path = work / relative
            original = path.read_text(encoding="utf-8")
            path.write_text(apply(original, old, new, name), encoding="utf-8", newline="\n")
            passed, caught_by = run_tests(work)
            path.write_text(original, encoding="utf-8", newline="\n")
            results.append({"bug": name, "file": relative, "caught": not passed,
                            "caught_by": caught_by if not passed else None})
            print(("caught  " if not passed else "SURVIVED"), name, caught_by, flush=True)
    summary = {
        "measured": dt.date.today().isoformat(),
        "bugs_injected": len(results),
        "caught": sum(row["caught"] for row in results),
        "results": results,
    }
    (ROOT / "docs" / "mutation-results.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
                                                         encoding="utf-8", newline="\n")
    print(summary["caught"], "of", summary["bugs_injected"])


if __name__ == "__main__":
    main()
