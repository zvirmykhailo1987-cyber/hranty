"""«Живий портал»: щоденне самообслуговування на сервері + стрічка «Що змінилось».

Щодня (фоновий планувальник у застосунку, без участі ПК):
  • статуси програм за дедлайнами: «закривається» (≤14 дн) → подія; минув → «закрито», неактивна → подія;
    якщо на закриту програму є заявка в роботі — подія важливості 3 і задача команді;
  • перерахунок збігів усіх активних запитів; нові сильні збіги (≥ POROH_NOVOGO_ZBIHU) → подія під конкретний проєкт.
Імпорт (ingest/znannya) пише події «нова можливість», «змінено дедлайн», «нове знання».
Події ідемпотентні (унікальний ключ), тож повторні прогони нічого не дублюють.
"""
from datetime import date, timedelta

from sqlmodel import Session, select

from ..models import Application, IngestLog, Match, Opportunity, Potreba, Task, Zmina, utcnow

POROH_NOVOGO_ZBIHU = 0.6
FEED = "obslugovuvannya"


def podiya(s: Session, kluch: str, typ: str, tekst: str, vazhlyvist: int = 1, **ids) -> bool:
    """Записати подію один раз (за ключем). True — нова."""
    if s.exec(select(Zmina).where(Zmina.kluch == kluch)).first():
        return False
    s.add(Zmina(kluch=kluch[:250], typ=typ, tekst=tekst[:600], vazhlyvist=vazhlyvist, created_by="портал", **ids))
    return True


def _zayavky_v_roboti(s: Session, oid: int) -> list[Application]:
    return list(s.exec(select(Application).where(Application.opportunity_id == oid, Application.arkhiv == False)))  # noqa: E712


def zastosuvaty_status(s: Session, o: Opportunity, sohodni: date, r: dict | None = None) -> None:
    """Recompute a programme's status from its deadline and emit the matching feed events.

    Single source of truth for status transitions: called by the daily job AND by every
    import, so a programme closed by a re-ingest still produces its "closed" event and the
    follow-up tasks. Events and tasks are idempotent (unique key / title check).
    """
    from .ingest import status_z_dedlainu
    r = r if r is not None else {"zakryto": 0, "zakryvayetsya": 0, "novi_zbihy": 0, "zadachi": 0}
    novyi = status_z_dedlainu(o.status, o.deadline, sohodni)
    aktyvna = novyi != "closed" and o.zlyto_v is None      # a merged duplicate stays hidden
    if novyi != o.status or o.active != aktyvna:     # the feed may have set the status directly
        o.status, o.active = novyi, aktyvna
        o.updated_at = utcnow()
        s.add(o)
    if o.id is None:
        s.flush()
    zayavky = _zayavky_v_roboti(s, o.id)
    if o.status == "closing" and o.deadline:
        dniv = (o.deadline - sohodni).days
        if podiya(s, f"zakryvayetsya:{o.id}:{o.deadline}", "zakryvayetsya",
                  f"Закривається за {dniv} дн. ({o.deadline:%d.%m}): {o.title}", 3 if zayavky else 2, opportunity_id=o.id):
            r["zakryvayetsya"] += 1
    elif o.status == "closed":
        if podiya(s, f"zakryta:{o.id}:{o.deadline}", "prohrama_zakryta",
                  f"Програму закрито (дедлайн {o.deadline:%d.%m.%Y} минув): {o.title}" if o.deadline else f"Програму закрито: {o.title}",
                  3 if zayavky else 1, opportunity_id=o.id):
            r["zakryto"] += 1
        for a in zayavky:
            if a.stadiya in ("ideya", "skryninh", "dokumenty"):
                tytul = f"Програма закрилась — вирішити долю заявки #{a.id}: перенести на наступне вікно чи в архів"
                if not s.exec(select(Task).where(Task.application_id == a.id, Task.title == tytul)).first():
                    s.add(Task(title=tytul, application_id=a.id, potreba_id=a.potreba_id, due=sohodni + timedelta(days=3),
                               dzherelo="живий портал", created_by="портал"))
                    r["zadachi"] += 1


def obslugovuvannya(s: Session, sohodni: date | None = None) -> dict:
    from .matching import pererakhuvaty
    sohodni = sohodni or utcnow().date()
    r = {"zakryto": 0, "zakryvayetsya": 0, "novi_zbihy": 0, "zadachi": 0}
    for o in s.exec(select(Opportunity).where(Opportunity.active == True)):  # noqa: E712
        zastosuvaty_status(s, o, sohodni, r)
    s.commit()
    # перерахунок збігів і нові сильні збіги під кожен активний запит
    for p in s.exec(select(Potreba).where(Potreba.status == "aktyvnyi")):
        pererakhuvaty(s, p)
        for m, o in s.exec(select(Match, Opportunity).join(Opportunity, Match.opportunity_id == Opportunity.id)
                           .where(Match.potreba_id == p.id, Match.score >= POROH_NOVOGO_ZBIHU, Match.stan == "novyi",
                                  Opportunity.active == True)):  # noqa: E712
            # The key makes this idempotent; matches computed earlier by an import or a
            # request edit are announced here too (they used to be skipped as "already seen").
            if podiya(s, f"zbih:{p.id}:{o.id}", "novyi_zbih",
                      f"Нова можливість під «{p.nazva[:80]}» (бал {round(m.score * 100)}): {o.title}", 2,
                      opportunity_id=o.id, potreba_id=p.id):
                r["novi_zbihy"] += 1
    s.add(IngestLog(feed=FEED, fayl=sohodni.isoformat(), dodano=r["novi_zbihy"], onovleno=r["zakryto"] + r["zakryvayetsya"],
                    prymitka=f"задач: {r['zadachi']}"))
    s.commit()
    return r


def vzhe_sohodni(s: Session, sohodni: date | None = None) -> bool:
    sohodni = sohodni or utcnow().date()
    return bool(s.exec(select(IngestLog).where(IngestLog.feed == FEED, IngestLog.fayl == sohodni.isoformat())).first())
