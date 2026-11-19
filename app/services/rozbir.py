# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

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
    # "громад" alone would also hit "громадська організація" (an NGO): the noun's case forms only
    "hromada": [r"re:громад(?:а|и|і|у|ою|ам|ами|ах)?(?![\wʼ])", "омс", "муніцип", "municipal", "місцев самоврядуван", "сільськ рад", "міськ рад"],
    "kp": ["комунальн підприєм", "кп ", "водоканал", "utility", "utilities"],
    "biznes": ["мсп", "мсб", "фоп", "sme", "бізнес", "підприєм", "компан", "business", "юрос", "приватн"],
    "fermer": ["фермер", "агровиробник", "сільгосп", "farmer", "аграрі"],
    "go": ["го ", "го,", "громадськ організац", "благодійн", "ногс", "огс", "ngo", "cso", "неприбутков"],
    "naukova": ["університет", "наук", "заклад освіт", "академ", "university", "research"],
}


def _frazovyi_rx(fraza: str) -> re.Pattern[str]:
    """Each word of a key phrase must START a word, in order; keys of ≤3 letters are whole words
    ("го" must not match the adjective ending "-го", "кп" must not match inside a word).
    A key starting with "re:" is an explicit regex anchored at a word start."""
    if fraza.startswith("re:"):
        return re.compile(r"(?<![\wʼ])" + fraza[3:])
    chastyny = []
    for slovo in norm(fraza).split():
        slovo = re.escape(slovo.strip(" ,"))
        chastyny.append(slovo + (r"(?![\wʼ])" if len(slovo) <= 3 else r"[\wʼ]*"))
    return re.compile(r"(?<![\wʼ])" + r"\s+".join(chastyny))


_ZAYAV_RX = {k: [_frazovyi_rx(x) for x in kl if x.strip(" ,")] for k, kl in ZAYAV_KLUCHI.items()}


def zayavnyky_z_tekstu(t: str) -> list[str]:
    tt = norm(t)
    if not tt:
        return []
    out = [k for k, rxs in _ZAYAV_RX.items() if any(rx.search(tt) for rx in rxs)]
    if "kp" in out and "biznes" in out:
        # "комунальне підприємство" is a utility; count business only if named separately
        bez_kp = re.sub(r"комунальн[\wʼ]*\s+підприєм[\wʼ]*", " ", tt)
        if not any(rx.search(bez_kp) for rx in _ZAYAV_RX["biznes"]):
            out.remove("biznes")
    return out


_VALYUTY = [("EUR", r"€|eur|євро"), ("USD", r"\$|usd|дол"), ("CHF", r"chf|франк"), ("GBP", r"£|gbp|фунт"),
            ("UAH", r"грн|uah|гривн")]
# A number: thousands groups of exactly three digits separated by one space/NBSP, comma or dot,
# or a plain number with an optional decimal part. The multiplier must end a word, so the
# "m" of "maximum" or "months" is not read as millions.
_CHYSLO = re.compile(r"(?<![\d\w])(\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d{1,3}(?:,\d{3})+(?:\.\d+)?"
                     r"|\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?)"
                     r"(?:\s*(млрд|млн|тис|bn|k|m|million|mln|thousand)(?![\w]))?", re.I)
_ROKY = re.compile(r"^(19|20)\d{2}$")


def _v_chyslo(s: str, mnozh: str) -> float | None:
    s = s.replace(" ", "").replace(" ", "").replace(" ", "")
    if not mnozh and re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", s):
        s = s.replace(".", "").replace(",", ".")   # "250.000" / "1.500,50": dot = thousands
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", s):
        s = s.replace(",", "")              # "100,000" / "1,250,000.50": comma = thousands
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
    return x * {"млрд": 1e9, "bn": 1e9, "млн": 1e6, "m": 1e6, "million": 1e6, "mln": 1e6,
                "тис": 1e3, "k": 1e3, "thousand": 1e3}.get(m, 1)


_VALYUTA_POR = re.compile(r"\s*(грн|uah|eur|євро|usd|дол|chf|gbp|[€$£])", re.I)


def _bilya_valyuty(tt: str, m: re.Match[str]) -> bool:
    """«2000 євро», «€2000»: a 4-digit number written next to a currency is an amount, not a year."""
    return bool(_VALYUTA_POR.match(tt, m.end()) or re.search(r"[€$£]\s*$", tt[:m.start()]))


def suma(t: str) -> tuple[float | None, float | None, str | None]:
    """(min, max, валюта) з тексту суми. Один показник із «до» → лише max. Не розпізнано → None."""
    tt = norm(t)
    if not tt or tt.startswith("немає даних"):
        return None, None, None
    val = next((k for k, rx in _VALYUTY if re.search(rx, tt)), None)
    zbihy = [m for m in _CHYSLO.finditer(tt)
             if m.group(2) or not _ROKY.match(m.group(1)) or _bilya_valyuty(tt, m)]   # years are not amounts
    pary = [(m.group(1), m.group(2) or "") for m in zbihy]
    # "€300-500k": a multiplier written only on the upper end applies to both ends — but only when
    # the first number is bare and small and the two numbers are joined directly by the dash.
    if len(zbihy) >= 2 and not pary[0][1] and pary[1][1] and re.fullmatch(r"\d{1,3}(?:[.,]\d+)?", pary[0][0]) \
            and re.fullmatch(r"\s*[–—-]\s*[€$£]?\s*", tt[zbihy[0].end():zbihy[1].start()]):
        pary[0] = (pary[0][0], pary[1][1])
    chysla = [_v_chyslo(a, b) for a, b in pary]
    chysla = [x for x in chysla if x is not None and x >= 100]   # відсікти «20%», «3 роки», «1 грант»
    if not chysla:
        return None, None, val
    diapazon = bool(re.search(r"\b(від|from)\b.*\d.*\b(до|to)\b", tt))
    if len(zbihy) >= 2 and not diapazon:
        # a dash directly between the first two amounts (not between two years elsewhere)
        mizh = tt[zbihy[0].end():zbihy[1].start()]
        diapazon = bool(re.fullmatch(r"\s*(млн|тис|k|m)?\.?\s*(грн|uah|eur|євро|usd|chf|[€$£])?\s*[–—-]\s*[€$£]?\s*", mizh))
    if len(chysla) >= 2 and diapazon:
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
