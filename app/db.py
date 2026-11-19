# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""БД: SQLite + SQLModel. Створення таблиць, команда (4 людини), гейти стадій."""
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select

from . import config
from .models import StageRule, User
from .security import hash_pw

engine = create_engine(f"sqlite:///{config.DB_PATH}", connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _pragma(dbapi_conn, _):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


KOMANDA = [
    ("Марко", "marko", "rozrobnyk"),
    ("Богдан", "bohdan", "perehovorschyk_dok"),
    ("Оксана", "oksana", "perehovory_kontakt"),
    ("Галина", "halyna", "zapyty_lysty"),
]

# Що потрібно, щоб УВІЙТИ в стадію (документи — види з VYDY_DOK).
GEJTY = {
    "ideya": ("", "Запит і можливість звʼязано."),
    "skryninh": ("", "Бал збігу ≥ 0.5 або вказано причину взяти в роботу."),
    "dokumenty": ("", "Скринінг дав вердикт «подаватися»."),
    "podacha": ("kontseptsiya,byudzhet", "Є обовʼязкові документи; жодної непогодженої AI-чернетки; "
                                         "погоджено людиною з правом погодження (не автором)."),
    "rezultat": ("", "Подано людиною: дата й хто подав."),
}


def bootstrap(eng=None) -> None:
    eng = eng or engine
    with Session(eng) as s:
        if not s.exec(select(User)).first():
            pw = config.BOOTSTRAP_PASSWORD
            if config.IS_PROD and len(pw) < 12:
                raise RuntimeError("Empty database in production: set HRANTY_BOOTSTRAP_PASSWORD "
                                   "(12+ characters) for the first admin account.")
            for name, login, role in KOMANDA:
                s.add(User(name=name, login=login, role=role,
                           password_hash=hash_pw(pw) if pw and login == "marko" else ""))
        for st, (dok, opys) in GEJTY.items():
            if not s.exec(select(StageRule).where(StageRule.stadiya == st)).first():
                s.add(StageRule(stadiya=st, potribni_dok=dok, opys=opys))
        s.commit()


def _dopysaty_kolonky(eng) -> None:
    """Легка міграція SQLite: нові поля моделей → ALTER TABLE ADD COLUMN (без даних за замовчуванням).
    Порожня таблиця зі старим NOT NULL, який став необовʼязковим, — перестворюється."""
    from sqlalchemy import inspect, text
    insp = inspect(eng)
    with eng.begin() as c:
        for t in SQLModel.metadata.sorted_tables:
            if not insp.has_table(t.name):
                continue
            isnuyuchi = {k["name"]: k for k in insp.get_columns(t.name)}
            zastarili = [k for k in t.columns if k.name in isnuyuchi and k.nullable
                         and not isnuyuchi[k.name]["nullable"]]
            if zastarili and c.execute(text(f'SELECT COUNT(*) FROM "{t.name}"')).scalar() == 0:  # noqa: S608 — name from model metadata  # nosec B608
                t.drop(c); t.create(c); continue
            for k in t.columns:
                if k.name not in isnuyuchi:
                    typ = k.type.compile(dialect=eng.dialect)
                    c.execute(text(f'ALTER TABLE "{t.name}" ADD COLUMN "{k.name}" {typ}'))


def init_db(eng=None) -> None:
    eng = eng or engine
    SQLModel.metadata.create_all(eng)
    _dopysaty_kolonky(eng)
    bootstrap(eng)


def get_session():
    with Session(engine) as s:
        yield s
