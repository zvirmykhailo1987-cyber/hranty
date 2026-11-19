"""Обʼєднання дублів програм: картка-дубль лишається (історія), але неактивна й переадресована.
Імпорти (proekty, prorobka) через kanon() завжди приходять у канонічну картку — дубль не воскресає."""

from sqlmodel import Session, select

from ..models import Application, Document, Match, Opportunity, Task, utcnow


def kanon(s: Session, o: Opportunity | None) -> Opportunity | None:
    bachyly = set()
    while o is not None and o.zlyto_v and o.id not in bachyly:
        bachyly.add(o.id)
        o = s.get(Opportunity, o.zlyto_v)
    return o


def zlyty(s: Session, dubl_id: int, kanon_id: int, avtor: str = "обʼєднання дублів") -> dict:
    """Заявки дубля → на канонічну програму. Якщо в запиту вже є заявка на канонічну — задачі й документи
    переносяться в неї, а заявка-дубль іде в архів із приміткою. Нічого не видаляється, крім збігів дубля."""
    if dubl_id == kanon_id:
        return {"perenese": 0, "zlyto": 0}
    d, k = s.get(Opportunity, dubl_id), s.get(Opportunity, kanon_id)
    if not d or not k:
        raise ValueError(f"немає картки {dubl_id} або {kanon_id}")
    r = {"perenese": 0, "zlyto": 0}
    for a in s.exec(select(Application).where(Application.opportunity_id == dubl_id)):
        isn = s.exec(select(Application).where(Application.potreba_id == a.potreba_id,
                                               Application.opportunity_id == kanon_id)).first()
        if not isn:
            a.opportunity_id = kanon_id
            s.add(a); r["perenese"] += 1
            continue
        for t in s.exec(select(Task).where(Task.application_id == a.id)):
            t.application_id = isn.id; s.add(t)
        for doc in s.exec(select(Document).where(Document.application_id == a.id)):
            doc.application_id = isn.id; s.add(doc)
        if not isn.verdykt and a.verdykt:
            isn.verdykt, isn.skryner_json = a.verdykt, a.skryner_json
        a.arkhiv = True
        a.nastupnyi_krok = f"Дубль: обʼєднано в заявку #{isn.id}"
        s.add(a); s.add(isn); r["zlyto"] += 1
    for m in s.exec(select(Match).where(Match.opportunity_id == dubl_id)):
        s.delete(m)
    d.zlyto_v, d.active = kanon_id, False
    d.zviryty = f"Дубль — обʼєднано в картку #{kanon_id}"
    d.updated_at = utcnow()
    s.add(d)
    return r
