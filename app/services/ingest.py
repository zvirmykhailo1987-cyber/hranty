# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Opportunity import (radar format). Idempotent: a repeated run creates no duplicates.

Input is a JSON list of opportunities (title, url, kind, deadline, amount, …) — sent by
the ingest worker to ``POST /api/ingest/radar`` or loaded by ``app.seed_demo``.

Rules: closed programmes are never deleted (active=False); manual tags are not
overwritten; unknown values stay None ("no data"), never zero.
"""
import json
import re
from datetime import date
from pathlib import Path

from sqlmodel import Session, select

from ..models import Funder, IngestLog, Opportunity, utcnow
from ..slovnyky import KATEGORIYI, KINDS, RELIABILITY, STATUS
from . import rozbir

POLYA_ONOVLENNYA = ("deadline", "status", "amount", "reliability", "kategoriya", "for_whom", "geo", "notes")


def status_z_dedlainu(st: str, dl: date | None, sohodni: date | None = None) -> str:
    sohodni = sohodni or utcnow().date()
    if dl:
        dn = (dl - sohodni).days
        if dn < 0:
            return "closed"
        if dn <= 14:
            return "closing"
        if st == "closing":
            return "open"          # deadline moved out: no longer closing
    return st if st in STATUS else "open"


def _zapys_v_mozhlyvist(r: dict, feed: str) -> dict:
    """Запис радару/порталу → поля Opportunity (без збереження)."""
    dl = rozbir.data(r.get("deadline"))
    amount = (r.get("amount") or "").strip()
    mn, mx, val = rozbir.suma(amount)
    tekst = " ".join(str(r.get(k) or "") for k in ("title", "notes", "for_whom", "sectors", "amount"))
    kat = r.get("kategoriya")
    return {
        "title": (r.get("title") or "").strip()[:500],
        "source_org": (r.get("source_org") or "").strip(),
        "kind": r.get("kind") if r.get("kind") in KINDS else "grant",
        "kategoriya": kat if kat in KATEGORIYI else None,
        "url": (r.get("url") or "").strip(),
        "amount": amount,
        "amount_min": mn, "amount_max": mx, "currency": val,
        "deadline": dl,
        "deadline_typ": "hard" if dl else "",
        "status": status_z_dedlainu(r.get("status") or "open", dl),
        "for_whom": str(r.get("for_whom") or "")[:1000],
        "geo": str(r.get("geo") or "")[:300],
        "reliability": r.get("reliability") if r.get("reliability") in RELIABILITY else "unverified",
        "notes": str(r.get("notes") or "")[:4000],
        "first_seen": rozbir.data(r.get("first_seen")),
        "tags": ",".join(rozbir.tegy_z_tekstu(tekst)),
        "zayavnyky": ",".join(rozbir.zayavnyky_z_tekstu(str(r.get("for_whom") or "") + " " + str(r.get("notes") or ""))),
        "source_feed": feed,
        "_yavni": {k for k in ("status", "reliability") if r.get(k)},
    }


def upsert(s: Session, polya: dict, sohodni: date | None = None) -> str:
    """'dodano' | 'onovleno' | 'dubl' | 'nevalidnyi'."""
    if not polya["title"]:
        return "nevalidnyi"
    uh = rozbir.url_hash(polya["url"])
    dk = rozbir.dedup_key(polya["title"], polya["source_org"])
    yavni = polya.pop("_yavni", set())
    o = s.exec(select(Opportunity).where(Opportunity.url_hash == uh)).first() if uh else None
    if not o:
        # Title+funder fallback only when a URL cannot decide: two calls with the same
        # generic title but different URLs are different programmes.
        kand = s.exec(select(Opportunity).where(Opportunity.dedup_key == dk)).first()
        if kand and (not uh or not kand.url_hash):
            o = kand
    sohodni = sohodni or utcnow().date()
    if not o:
        o = Opportunity(**{**polya, "first_seen": polya.get("first_seen") or sohodni},
                        url_hash=uh, dedup_key=dk, last_checked=sohodni)
        o.active = o.status != "closed"
        s.add(o)
        s.flush()
        from .zhyvyi import podiya, zastosuvaty_status
        if o.active and polya.get("source_feed", "").startswith(("radar", "portal", "grant_market")):
            podiya(s, f"nova:{o.id}", "nova_mozhlyvist",
                   f"Нова можливість у реєстрі{(' (дедлайн ' + o.deadline.strftime('%d.%m.%Y') + ')') if o.deadline else ''}: {o.title}",
                   1, opportunity_id=o.id)
        zastosuvaty_status(s, o, sohodni)        # e.g. imported already "closing soon"
        return "dodano"
    zminy = False
    stary_dl = o.deadline
    for k in POLYA_ONOVLENNYA:
        v = polya.get(k)
        if k in ("status", "reliability") and k not in yavni:
            continue                 # absent in the feed → keep the curated value, no default
        if v not in (None, "") and getattr(o, k) != v:
            setattr(o, k, v)
            zminy = True
    if o.tags_avto and polya["tags"] and o.tags != polya["tags"]:
        o.tags, zminy = polya["tags"], True
    if not o.zayavnyky and polya["zayavnyky"]:
        o.zayavnyky, zminy = polya["zayavnyky"], True
    suma_zminena = polya.get("amount") not in (None, "") and o.amount == polya["amount"] and \
        (o.amount_min, o.amount_max, o.currency) != (polya.get("amount_min"), polya.get("amount_max"), polya.get("currency"))
    for k in ("amount_min", "amount_max", "currency"):
        if suma_zminena or (polya.get(k) is not None and getattr(o, k) is None):
            setattr(o, k, polya.get(k)); zminy = True       # the text is the source of the numbers
    if o.status == "closed" and "status" not in yavni and stary_dl != o.deadline and o.deadline \
            and o.deadline >= (sohodni or utcnow().date()):
        o.status, zminy = "open", True          # the call was reopened with a new deadline
    if stary_dl and o.deadline and stary_dl != o.deadline:
        from .zhyvyi import podiya
        from ..models import Application
        v_roboti = s.exec(select(Application).where(Application.opportunity_id == o.id, Application.arkhiv == False)).first()  # noqa: E712
        podiya(s, f"dl:{o.id}:{o.deadline}", "dedlain_zmineno",
               f"Змінено дедлайн {stary_dl:%d.%m.%Y} → {o.deadline:%d.%m.%Y}: {o.title}", 3 if v_roboti else 1, opportunity_id=o.id)
    from .zhyvyi import zastosuvaty_status
    zastosuvaty_status(s, o, sohodni)
    o.last_checked = sohodni
    o.updated_at = utcnow()
    s.add(o)
    return "onovleno" if zminy else "dubl"


def _rakhuvaty(s: Session, zapysy, feed: str, fayl: str, peretvoryty) -> IngestLog:
    lich = {"dodano": 0, "onovleno": 0, "dubl": 0, "nevalidnyi": 0}
    for r in zapysy:
        try:
            polya = peretvoryty(r)
        except Exception:  # noqa: BLE001 — один кривий запис не валить імпорт
            lich["nevalidnyi"] += 1
            continue
        if polya is None:
            lich["nevalidnyi"] += 1
            continue
        lich[upsert(s, polya)] += 1
        s.flush()
    log = IngestLog(feed=feed, fayl=fayl, dodano=lich["dodano"], onovleno=lich["onovleno"],
                    dubli=lich["dubl"], nevalidni=lich["nevalidnyi"])
    s.add(log)
    s.commit()
    return log


REQUIRED = ("title", "url", "kind")


def validuvaty_radar(zapysy: object) -> tuple[bool, str]:
    """Built-in gate for an ingest file: a list of objects with title, url and a known kind."""
    if not isinstance(zapysy, list):
        return False, "expected a JSON list of opportunities"
    pogani = [i for i, r in enumerate(zapysy)
              if not isinstance(r, dict) or not all(r.get(k) for k in REQUIRED) or r.get("kind") not in KINDS
              or not str(r.get("url", "")).startswith(("http://", "https://"))]
    if pogani:
        return False, f"{len(pogani)} invalid records (first at index {pogani[0]})"
    return True, ""


def importuvaty_zapysy(s: Session, zapysy: object, feed: str, dzherelo: str = "", gejt: bool = True) -> IngestLog:
    """Import a list of opportunity dicts. Invalid rows are counted and reported, not dropped silently."""
    ok, zvit = validuvaty_radar(zapysy) if gejt else (True, "")
    spysok = zapysy if isinstance(zapysy, list) else []
    if not ok:
        spysok = [r for r in spysok if isinstance(r, dict) and all(r.get(k) for k in REQUIRED)
                  and r.get("kind") in KINDS and str(r.get("url", "")).startswith(("http://", "https://"))]
    feed = "radar:" + re.sub(r"[^0-9A-Za-z_.-]", "", feed)[:40]
    log = _rakhuvaty(s, spysok, feed, dzherelo[:200], lambda r: _zapys_v_mozhlyvist(r, feed))
    if not ok:
        log.prymitka = "ingest gate: " + zvit[-500:]
        log.nevalidni += (len(zapysy) if isinstance(zapysy, list) else 1) - len(spysok)
        s.add(log); s.commit()
    return log


def importuvaty_radar(s: Session, fayl: Path, gejt: bool = True) -> IngestLog:
    """Import an opportunities JSON file."""
    zapysy = json.loads(Path(fayl).read_text(encoding="utf-8"))
    return importuvaty_zapysy(s, zapysy, Path(fayl).stem, Path(fayl).name, gejt)


def zvyazaty_funderiv(s: Session) -> int:
    """Можливість → грантодавець за входженням короткої назви в source_org (лише якщо однозначно)."""
    funders = list(s.exec(select(Funder)))
    n = 0
    for o in s.exec(select(Opportunity).where(Opportunity.funder_id == None)):  # noqa: E711
        org = rozbir.norm(o.source_org + " " + o.title)
        zbihy = [f for f in funders if len(f.name) >= 3 and rozbir.norm(f.name) in org.split()
                 or (len(f.name) >= 5 and rozbir.norm(f.name) in org)]
        if len(zbihy) == 1:
            o.funder_id = zbihy[0].id
            s.add(o); n += 1
    s.commit()
    return n
