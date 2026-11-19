# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Задати пароль користувачеві там, де живе система (той самий інструмент, що в Project Office CRM).

    python -m app.parol bohdan      # приховане введення
    python -m app.parol --usim          # усім випадкові, показ один раз
"""
from __future__ import annotations

import getpass
import secrets
import string
import sys

from sqlmodel import Session, select

from .db import engine, init_db
from .models import User
from .authkit import password_problem
from .security import hash_pw

ABETKA = "".join(c for c in string.ascii_letters + string.digits if c not in "lI1O0")


def vypadkovyi(n: int = 14) -> str:
    return "".join(secrets.choice(ABETKA) for _ in range(n))


def zadaty(login: str, parol: str) -> bool:
    with Session(engine) as s:
        u = s.exec(select(User).where(User.login == login)).first()
        if not u:
            return False
        u.password_hash = hash_pw(parol)
        s.add(u)
        s.commit()
    return True


def main(argv: list[str]) -> int:
    init_db()
    if not argv:
        print(__doc__)
        return 2
    if argv[0] == "--usim":
        with Session(engine) as s:
            lohiny = sorted(u.login for u in s.exec(select(User)) if u.login)
        print("Нові паролі — показані ОДИН раз, роздати особисто:\n")
        for lg in lohiny:
            p = vypadkovyi()
            zadaty(lg, p)
            print(f"  {lg:12} {p}")
        return 0
    p1 = getpass.getpass(f"новий пароль для {argv[0]}: ")
    problem = password_problem(p1)
    if problem:
        print(problem, file=sys.stderr)
        return 2
    if p1 != getpass.getpass("ще раз: "):
        print("не збіглись", file=sys.stderr)
        return 2
    print("змінено" if zadaty(argv[0], p1) else "немає такого логіна")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
