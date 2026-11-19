"""Детермінований розбір тексту можливості: теги, типи заявників, суми, дати.

Нічого не вигадує: не розпізнано → None / "" (відображається як «немає даних»).
"""
import hashlib
import re
from datetime import date, datetime

from ..slovnyky import TEGY

_SLOVO = re.compile(r"[\wʼ'’-]+", re.U)


def norm(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").lower().replace("’", "ʼ").replace("'", "ʼ")).strip()


def tegy_z_tekstu(*teksty: str) -> list[str]:
    """Тег ставиться, якщо ключ знайдено НА ПОЧАТКУ слова (стем), а не будь-де всередині."""
    t = " " + norm(" ".join(x for x in teksty if x)) + " "
    out = []
    for kod, (_, kluchi) in TEGY.items():
        for k in kluchi:
            if re.search(r"(?<![\wʼ])" + re.escape(norm(k)), t):
                out.append(kod)
                break
    return out


ZAYAV_KLUCHI = {
    "hromada": ["громад", "омс", "муніцип", "municipal", "місцев самоврядуван", "сільськ рад", "міськ рад"],
    "kp": ["комунальн підприєм", "кп ", "водоканал", "utility", "utilities"],
    "biznes": ["мсп", "мсб", "фоп", "sme", "бізнес", "підприєм", "компан", "business", "юрос", "приватн"],
    "fermer": ["фермер", "агровиробник", "сільгосп", "farmer", "аграрі"],
    "go": ["го ", "го,", "громадськ організац", "благодійн", "ногс", "огс", "ngo", "cso", "неприбутков"],
    "naukova": ["університет", "наук", "заклад освіт", "академ", "university", "research"],
}


def zayavnyky_z_tekstu(t: str) -> list[str]:
    tt = " " + norm(t) + " "
    if not tt.strip():
        return []
    return [k for k, kl in ZAYAV_KLUCHI.items() if any(norm(x) in tt or x in tt for x in kl)]


_VALYUTY = [("EUR", r"€|eur|євро"), ("USD", r"\$|usd|дол"), ("CHF", r"chf|франк"), ("GBP", r"£|gbp|фунт"),
            ("UAH", r"грн|uah|гривн")]
_CHYSLO = re.compile(r"(\d[\d\s  .,]*\d|\d)\s*(млрд|млн|тис|k|m|bn)?", re.I)


def _v_chyslo(s: str, mnozh: str) -> float | None:
    s = s.replace(" ", "").replace(" ", "").replace(" ", "")
    if s.count(",") == 1 and s.count(".") == 0:
        s = s.replace(",", ".")
    s = s.replace(",", "")
    if s.count(".") > 1:
        s = s.replace(".", "")
    try:
        x = float(s)
    except ValueError:
        return None
    m = (mnozh or "").lower()
    return x * {"млрд": 1e9, "bn": 1e9, "млн": 1e6, "m": 1e6, "тис": 1e3, "k": 1e3}.get(m, 1)


def suma(t: str) -> tuple[float | None, float | None, str | None]:
    """(min, max, валюта) з тексту суми. Один показник із «до» → лише max. Не розпізнано → None."""
    tt = norm(t)
    if not tt or tt.startswith("немає даних"):
        return None, None, None
    val = next((k for k, rx in _VALYUTY if re.search(rx, tt)), None)
    chysla = [_v_chyslo(a, b) for a, b in _CHYSLO.findall(tt)]
    chysla = [x for x in chysla if x is not None and x >= 100]   # відсікти «20%», «3 роки», «1 грант»
    if not chysla:
        return None, None, val
    if len(chysla) >= 2 and re.search(r"(\d)\s*[–—-]\s*(\d)", tt):
        return min(chysla[:2]), max(chysla[:2]), val
    return (None, chysla[0], val) if re.search(r"\bдо\b|up to|max", tt) or len(chysla) == 1 else (None, max(chysla), val)


def kursy() -> dict[str, float]:
    """Курси до EUR лише з налаштування HRANTY_KURSY («USD=0.92,UAH=0.0208»), без вигаданих дефолтів.
    Немає курсу валюти → суму не порівнюємо (компонент «сума» = немає даних)."""
    import os
    out = {"EUR": 1.0}
    for chast in os.getenv("HRANTY_KURSY", "").split(","):
        if "=" in chast:
            k, v = chast.split("=", 1)
            try:
                out[k.strip().upper()] = float(v)
            except ValueError:
                pass
    return out


def v_eur(x: float | None, val: str | None) -> float | None:
    k = kursy()
    if x is None or not val or val not in k:
        return None
    return x * k[val]


def data(t) -> date | None:
    if not t:
        return None
    if isinstance(t, date):
        return t
    t = str(t).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(t[:10], fmt).date()
        except ValueError:
            continue
    return None


def url_hash(url: str) -> str | None:
    u = (url or "").strip().lower().rstrip("/")
    u = re.sub(r"^https?://(www\.)?", "", u)
    u = re.sub(r"[?#].*$", "", u) if "utm_" in u else u
    return hashlib.sha1(u.encode("utf-8"), usedforsecurity=False).hexdigest() if u else None


def dedup_key(title: str, org: str = "") -> str:
    slova = _SLOVO.findall(norm(title))[:10]
    return hashlib.sha1((" ".join(slova) + "|" + norm(org)[:40]).encode("utf-8"), usedforsecurity=False).hexdigest()
