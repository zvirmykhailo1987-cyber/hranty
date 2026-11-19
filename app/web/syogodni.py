"""«Сьогодні»: що горить — дедлайни, нові збіги, задачі команди, збої агента."""
from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from sqlmodel import Session, func, select

from ..db import get_session
from ..models import AgentRun, Application, IngestLog, Match, Opportunity, Potreba, Task, User, Zmina, Znannya, utcnow
from ..slovnyky import STADII, TEGY_NAZVY
from .deps import current_user, render

router = APIRouter()


@router.get("/")
def syogodni(request: Request, user: User = Depends(current_user), s: Session = Depends(get_session)):
    sohodni = utcnow().date()
    # дедлайни програм, з якими ми вже працюємо (заявки) або які є у збігах
    v_roboti = {a.opportunity_id for a in s.exec(select(Application).where(Application.arkhiv == False))}  # noqa: E712
    zi_zbihiv = {m.opportunity_id for m in s.exec(select(Match).where(Match.stan != "vidkhyleno"))}
    dedlainy = list(s.exec(select(Opportunity).where(
        Opportunity.active == True, Opportunity.deadline != None,  # noqa: E711,E712
        Opportunity.deadline >= sohodni, Opportunity.deadline <= sohodni + timedelta(days=14)
    ).order_by(Opportunity.deadline)))
    dedlainy = sorted(dedlainy, key=lambda o: (o.id not in v_roboti, o.id not in zi_zbihiv, o.deadline))[:12]
    novi = list(s.exec(select(Match, Potreba, Opportunity).join(Potreba, Match.potreba_id == Potreba.id)
                       .join(Opportunity, Match.opportunity_id == Opportunity.id)
                       .where(Match.stan == "novyi", Match.score >= 0.5, Opportunity.active == True)  # noqa: E712
                       .order_by(Match.score.desc()).limit(8)))
    zadachi = list(s.exec(select(Task).where(Task.done == False)  # noqa: E712
                          .order_by(Task.due).limit(12)))
    po_stadiyakh = dict(s.exec(select(Application.stadiya, func.count()).where(Application.arkhiv == False)  # noqa: E712
                               .group_by(Application.stadiya)).all())
    zboi = list(s.exec(select(AgentRun).where(AgentRun.status.in_(["pomylka", "gejt_ne_projdeno"]))
                       .order_by(AgentRun.id.desc()).limit(5)))
    kpi = {
        "zapyty": s.exec(select(func.count()).select_from(Potreba).where(Potreba.status == "aktyvnyi")).one(),
        "mozhlyvosti": s.exec(select(func.count()).select_from(Opportunity).where(Opportunity.active == True)).one(),  # noqa: E712
        "zayavky": sum(po_stadiyakh.values()),
        "dedlainy14": len(dedlainy),
    }
    # класифікація чинних програм за напрямами — вхід у реєстр з головної
    za_napryamom = dict.fromkeys(TEGY_NAZVY, 0)
    for tags in s.exec(select(Opportunity.tags).where(Opportunity.active == True)):  # noqa: E712
        for t in (tags or "").split(","):
            if t in za_napryamom:
                za_napryamom[t] += 1
    import_ = s.exec(select(IngestLog).where(IngestLog.feed != "obslugovuvannya").order_by(IngestLog.id.desc())).first()
    obsl = s.exec(select(IngestLog).where(IngestLog.feed == "obslugovuvannya").order_by(IngestLog.id.desc())).first()
    vid = utcnow() - timedelta(days=7)
    zminy = list(s.exec(select(Zmina).where(Zmina.created_at >= vid, Zmina.typ != "nove_znannya")  # знання — окремий блок
                        .order_by(Zmina.vazhlyvist.desc(), Zmina.created_at.desc()).limit(14)))
    znannya = list(s.exec(select(Znannya).order_by(Znannya.data.desc(), Znannya.id.desc()).limit(4)))
    return render(request, "syogodni.html", user=user, dedlainy=dedlainy, v_roboti=v_roboti, novi=novi,
                  zadachi=zadachi, po_stadiyakh=po_stadiyakh, stadii=STADII, zboi=zboi, kpi=kpi, import_=import_,
                  zminy=zminy, znannya=znannya, obsl=obsl, za_napryamom=za_napryamom)
