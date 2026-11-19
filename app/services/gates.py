"""Гейти стадій заявки: що потрібно, щоб УВІЙТИ в стадію. Назад — завжди можна.

Ідея форку — ofis_crm/app/services/gates.py (див. FORK.md), правила — під гранти.
Агент не може просунути заявку в «подачу» чи «результат»: ці кроки вимагають людини.
"""
from sqlmodel import Session, select

from ..models import Application, Document, Match, Opportunity, StageRule, User
from ..models.people import CAP_APPROVE, CAP_SEND
from ..slovnyky import STADII, VYDY_DOK

PORYADOK = list(STADII)


def aktualni_dokumenty(s: Session, app_id: int) -> list[Document]:
    """Остання версія кожної групи документів."""
    vse = list(s.exec(select(Document).where(Document.application_id == app_id)))
    ost: dict[str, Document] = {}
    for d in vse:
        g = d.group_uid or d.uid
        if g not in ost or d.versiya > ost[g].versiya:
            ost[g] = d
    return sorted(ost.values(), key=lambda d: (d.vyd, d.nazva))


def perevirka(s: Session, app: Application, nova: str, user: User | None = None) -> list[str]:
    """Порожній список = можна. Інакше — причини українською."""
    if nova not in STADII:
        return [f"невідома стадія «{nova}»"]
    if PORYADOK.index(nova) <= PORYADOK.index(app.stadiya):
        return []                                     # назад або на місці — без гейту
    if PORYADOK.index(nova) - PORYADOK.index(app.stadiya) > 1:
        return ["стадії проходять по черзі — не можна перестрибнути"]
    prych: list[str] = []
    if nova == "skryninh":
        m = s.exec(select(Match).where(Match.potreba_id == app.potreba_id,
                                       Match.opportunity_id == app.opportunity_id)).first()
        if not ((m and m.score >= 0.5) or app.prychyna_vidboru.strip()):
            prych.append("бал збігу нижче 0.5 — вкажіть, чому беремо в роботу")
    elif nova == "dokumenty":
        o = s.get(Opportunity, app.opportunity_id) if app.opportunity_id else None
        if (app.verdykt or (o.verdykt if o else "")) != "подаватися":
            prych.append("немає вердикту скринінгу «подаватися» (агент або вручну)")
    elif nova == "podacha":
        rule = s.exec(select(StageRule).where(StageRule.stadiya == "podacha")).first()
        potribni = [x for x in (rule.potribni_dok if rule else "").split(",") if x]
        doky = aktualni_dokumenty(s, app.id)
        nayavni = {d.vyd for d in doky}
        for v in potribni:
            if v not in nayavni:
                prych.append(f"бракує документа: {VYDY_DOK.get(v, v)}")
        for d in doky:
            if not d.pohodzheno_kym:
                prych.append(f"не погоджено: {d.nazva or VYDY_DOK.get(d.vyd, d.vyd)}"
                             + (" (AI-чернетка)" if d.ai_chernetka else ""))
        if not doky:
            prych.append("у заявці немає жодного документа")
    elif nova == "rezultat":
        for d in aktualni_dokumenty(s, app.id):
            if not d.pohodzheno_kym:
                prych.append(f"не погоджено: {d.nazva or VYDY_DOK.get(d.vyd, d.vyd)} (нова версія після подачі?)")
        if not (app.podano_at and app.podano_kto):
            prych.append("позначте, хто й коли подав заявку (подає лише людина)")
        if user is not None and not user.can(CAP_SEND):
            prych.append("немає права позначати подачу")
    return prych


def mozhe_pohodzhuvaty(user: User, doc: Document) -> str:
    """'' = можна; інакше причина. Автор не погоджує сам себе; AI-чернетку погоджує людина з правом."""
    if not user.can(CAP_APPROVE):
        return "немає права погоджувати"
    # Identity is the account id (names are not unique); the name check covers rows
    # created before avtor_id existed.
    if (doc.avtor_id is not None and doc.avtor_id == user.id) or (doc.avtor_id is None and doc.avtor == user.name):
        return "автор не може погодити власний документ"
    return ""
