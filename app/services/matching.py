# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Пояснюваний підбір «запит клієнта ↔ можливість».

Скоринг розкладено на компоненти:
кожен компонент дає значення 0..1, вагу й причину українською; ВІДСУТНЄ поле = нейтрально 0.5
з позначкою «немає даних», не нуль. Жорсткі фільтри: закрито; тип заявника не підходить.
"""
import json
from dataclasses import asdict, dataclass, field
from datetime import date

from sqlmodel import Session, select

from ..models import Match, Opportunity, Potreba, utcnow
from ..slovnyky import TEGY_NAZVY, ZAYAVNYKY
from . import rozbir

VAHY = {"tegy": 0.35, "zayavnyk": 0.20, "suma": 0.15, "dedlain": 0.15, "geo": 0.10, "nadiynist": 0.05}
NAZVY = {"tegy": "Напрям", "zayavnyk": "Хто подає", "suma": "Сума", "dedlain": "Строки",
         "geo": "Географія", "nadiynist": "Надійність даних"}
POROH_ZBEREHTY = 0.35


@dataclass
class Komponent:
    kod: str
    znach: float
    vaha: float
    prychyna: str
    nemaye_danykh: bool = False

    @property
    def nazva(self) -> str:
        return NAZVY.get(self.kod, self.kod)


@dataclass
class Rezultat:
    score: float
    komponenty: list = field(default_factory=list)
    vidsiyano: str = ""          # причина жорсткого фільтра

    def json(self) -> str:
        return json.dumps({"score": self.score, "vidsiyano": self.vidsiyano,
                           "komponenty": [asdict(k) for k in self.komponenty]}, ensure_ascii=False)


def _tegy(p: Potreba, o: Opportunity) -> Komponent:
    pt, ot = set(p.tags_list), set(o.tags_list)
    if not pt:
        return Komponent("tegy", 0.5, VAHY["tegy"], "у запиті не вказано напрям", True)
    if not ot:
        return Komponent("tegy", 0.5, VAHY["tegy"], "напрям програми не визначено", True)
    spilni = pt & ot
    if not spilni:
        return Komponent("tegy", 0.0, VAHY["tegy"], "напрями не збігаються")
    znach = len(spilni) / len(pt)
    return Komponent("tegy", round(znach, 2), VAHY["tegy"],
                     "збіг: " + ", ".join(TEGY_NAZVY.get(t, t) for t in sorted(spilni)))


def _zayavnyk(p: Potreba, o: Opportunity) -> Komponent:
    if not p.zayavnyk:
        return Komponent("zayavnyk", 0.5, VAHY["zayavnyk"], "у запиті не вказано, хто подає", True)
    oz = o.zayavnyky_list
    if not oz:
        return Komponent("zayavnyk", 0.5, VAHY["zayavnyk"], "програма не називає типу заявника", True)
    if p.zayavnyk in oz or "bud_khto" in oz:
        return Komponent("zayavnyk", 1.0, VAHY["zayavnyk"], f"{ZAYAVNYKY.get(p.zayavnyk, p.zayavnyk)} — допустимий заявник")
    return Komponent("zayavnyk", 0.0, VAHY["zayavnyk"],
                     "заявник програми: " + ", ".join(ZAYAVNYKY.get(x, x) for x in oz))


def _suma(p: Potreba, o: Opportunity) -> Komponent:
    if p.byudzhet_eur is None:
        return Komponent("suma", 0.5, VAHY["suma"], "у запиті не вказано бюджет", True)
    mx = rozbir.v_eur(o.amount_max, o.currency)
    mn = rozbir.v_eur(o.amount_min, o.currency)
    if mx is None and mn is None:
        return Komponent("suma", 0.5, VAHY["suma"], "сума програми: немає даних (або немає курсу валюти)", True)
    b = p.byudzhet_eur
    if mx is not None and b > mx:
        chastka = mx / b
        return Komponent("suma", round(max(0.2, chastka), 2), VAHY["suma"],
                         f"покриває ≈{chastka:.0%} бюджету (макс. ≈€{mx:,.0f})".replace(",", " "))
    if mn is not None and b < mn:
        return Komponent("suma", 0.3, VAHY["suma"], f"бюджет менший за мінімум програми (≈€{mn:,.0f})".replace(",", " "))
    return Komponent("suma", 1.0, VAHY["suma"], "бюджет у межах програми")


def _dedlain(p: Potreba, o: Opportunity, sohodni: date) -> Komponent:
    if o.status == "rolling" or (not o.deadline and o.status in ("open", "rolling")):
        return Komponent("dedlain", 0.8, VAHY["dedlain"], "постійний прийом або без фіксованого дедлайну")
    if not o.deadline:
        return Komponent("dedlain", 0.5, VAHY["dedlain"], "дедлайн: немає даних", True)
    dn = (o.deadline - sohodni).days
    if dn < 7:
        return Komponent("dedlain", 0.2, VAHY["dedlain"], f"до дедлайну {dn} дн. — замало на якісну заявку")
    if p.potribno_do and o.deadline > p.potribno_do:
        return Komponent("dedlain", 0.4, VAHY["dedlain"], "рішення програми може бути пізніше, ніж потрібно клієнту")
    return Komponent("dedlain", 1.0 if dn >= 21 else 0.6, VAHY["dedlain"], f"до дедлайну {dn} дн.")


def _geo(p: Potreba, o: Opportunity) -> Komponent:
    if not p.oblast:
        return Komponent("geo", 0.5, VAHY["geo"], "у запиті не вказано область", True)
    g = rozbir.norm(o.geo + " " + o.for_whom)
    if not g.strip():
        return Komponent("geo", 0.5, VAHY["geo"], "географія програми: немає даних", True)
    stem = rozbir.norm(p.oblast)[:6]
    if stem and stem in g:
        return Komponent("geo", 1.0, VAHY["geo"], f"{p.oblast} у переліку")
    if any(x in g for x in ("україн", "усі регіон", "вся країн", "ukraine", "всі област")):
        return Komponent("geo", 0.9, VAHY["geo"], "уся Україна")
    if "област" in g or "oblast" in g:
        return Komponent("geo", 0.1, VAHY["geo"], f"{p.oblast} не згадано серед областей програми")
    return Komponent("geo", 0.5, VAHY["geo"], "географію не вдалося визначити", True)


def _nadiynist(o: Opportunity) -> Komponent:
    z = {"confirmed": (1.0, "першоджерело перевірено"), "plausible": (0.6, "вторинні джерела — звірити"),
         "unverified": (0.3, "не перевірено — обовʼязково звірити")}.get(o.reliability, (0.3, "невідомо"))
    return Komponent("nadiynist", z[0], VAHY["nadiynist"], z[1])


def score(p: Potreba, o: Opportunity, sohodni: date | None = None) -> Rezultat:
    sohodni = sohodni or utcnow().date()
    if not o.active or o.status == "closed" or (o.deadline and o.deadline < sohodni):
        return Rezultat(0.0, [], "програма закрита")
    k = [_tegy(p, o), _zayavnyk(p, o), _suma(p, o), _dedlain(p, o, sohodni), _geo(p, o), _nadiynist(o)]
    zayav = k[1]
    if zayav.znach == 0.0 and not zayav.nemaye_danykh:
        return Rezultat(0.0, k, "не той тип заявника")
    s = sum(x.znach * x.vaha for x in k) / sum(VAHY.values())
    if k[0].znach == 0.0 and not k[0].nemaye_danykh:
        s = min(s, 0.3)                  # без збігу напрямку вгору не пускаємо
    return Rezultat(round(s, 3), k)


def pererakhuvaty(s: Session, p: Potreba) -> int:
    """Перерахувати збіги одного запиту. Відхилені вручну лишаються відхиленими."""
    isnuyuchi = {m.opportunity_id: m for m in s.exec(select(Match).where(Match.potreba_id == p.id))}
    # A "new" match on a programme that has since closed is stale: drop it (taken-into-work and
    # manually rejected matches stay as history).
    zakryti = {o.id for o in s.exec(select(Opportunity).where(Opportunity.active == False))}  # noqa: E712
    for oid, m in list(isnuyuchi.items()):
        if oid in zakryti and m.stan == "novyi":
            s.delete(m)
            del isnuyuchi[oid]
    n = 0
    for o in s.exec(select(Opportunity).where(Opportunity.active == True)):  # noqa: E712
        r = score(p, o)
        m = isnuyuchi.get(o.id)
        if r.score < POROH_ZBEREHTY:
            if m and m.stan == "novyi":
                s.delete(m)
            continue
        if not m:
            m = Match(potreba_id=p.id, opportunity_id=o.id)
        m.score, m.breakdown_json, m.computed_at = r.score, r.json(), utcnow()
        s.add(m)
        n += 1
    s.commit()
    return n


def pererakhuvaty_vse(s: Session) -> int:
    return sum(pererakhuvaty(s, p) for p in s.exec(select(Potreba).where(Potreba.status == "aktyvnyi")))
