"""Реєстр грантів: фільтри, картка можливості, ручні правки тегів/заявників/вердикту."""
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from sqlmodel import Session, func, or_, select

from .. import audit, config
from ..db import get_session
from ..models import Application, Funder, Match, Opportunity, Potreba, ProgramFile, User, utcnow
from ..services import matching
from ..slovnyky import TEGY, ZAYAVNYKY
from ..models.people import CAP_DATA
from .deps import current_user, redirect, render, require_cap

router = APIRouter()
VERDYKTY = ("подаватися", "уточнити", "не подаватися")


@router.get("/reyestr")
def reyestr(request: Request, q: str = "", teg: list[str] = Query(default=[]), kind: str = "", status: str = "aktyvni",
            zayavnyk: str = "", nadiynist: str = "", storinka: int = 1,
            user: User = Depends(current_user), s: Session = Depends(get_session)):
    st = select(Opportunity)
    if status == "aktyvni":
        st = st.where(Opportunity.active == True)  # noqa: E712
    elif status:
        st = st.where(Opportunity.status == status)
    if q.strip():
        like = f"%{q.strip()}%"
        st = st.where(or_(Opportunity.title.ilike(like), Opportunity.source_org.ilike(like), Opportunity.notes.ilike(like)))
    teg = [t for t in teg if t in TEGY]
    if teg:  # кілька напрямів — АБО; точний збіг коду в CSV, не підрядок
        st = st.where(or_(*[("," + Opportunity.tags + ",").contains(f",{t},") for t in teg]))
    if kind:
        st = st.where(Opportunity.kind == kind)
    if zayavnyk:
        st = st.where(Opportunity.zayavnyky.contains(zayavnyk))
    if nadiynist:
        st = st.where(Opportunity.reliability == nadiynist)
    vsoho = s.exec(select(func.count()).select_from(st.subquery())).one()
    storinka = max(1, storinka)
    ryadky = list(s.exec(st.order_by(Opportunity.deadline.is_(None), Opportunity.deadline, Opportunity.id.desc())
                         .offset((storinka - 1) * config.PAGE_SIZE).limit(config.PAGE_SIZE)))
    filtr = dict(q=q, teg=teg, kind=kind, status=status, zayavnyk=zayavnyk, nadiynist=nadiynist)
    return render(request, "reyestr.html", user=user, ryadky=ryadky, vsoho=vsoho, storinka=storinka,
                  storinok=max(1, -(-vsoho // config.PAGE_SIZE)), filtr=filtr, qs=urlencode(filtr, doseq=True))


@router.get("/reyestr/{oid}")
def mozhlyvist(oid: int, request: Request, user: User = Depends(current_user), s: Session = Depends(get_session)):
    o = s.get(Opportunity, oid)
    if not o:
        raise HTTPException(404)
    funder = s.get(Funder, o.funder_id) if o.funder_id else None
    zbihy = list(s.exec(select(Match, Potreba).join(Potreba, Match.potreba_id == Potreba.id)
                        .where(Match.opportunity_id == oid).order_by(Match.score.desc())))
    zayavky = list(s.exec(select(Application).where(Application.opportunity_id == oid)))
    poryadok = {"forma": 0, "polozhennya": 1, "shablon": 2, "byudzhet": 3, "faq": 4, "logframe": 5, "pretsedent": 6}
    fayly = sorted(s.exec(select(ProgramFile).where(ProgramFile.opportunity_id == oid)),
                   key=lambda f: (f.nazva.startswith("Сторінка програми"), poryadok.get(f.vyd, 9), f.nazva))
    return render(request, "mozhlyvist.html", user=user, o=o, funder=funder, zbihy=zbihy, zayavky=zayavky,
                  fayly=[_kartka_faylu(f) for f in fayly], verdykty=VERDYKTY)


def _kartka_faylu(f: ProgramFile) -> dict:
    """Дані для картки документа програми: джерело людською мовою, уривок змісту, чи є мініатюра."""
    from pathlib import PurePosixPath
    from urllib.parse import unquote, urlparse
    u = urlparse(f.source_url or "")
    fayl = unquote(PurePosixPath(u.path).name) if u.path and "." in PurePosixPath(u.path).name else ""
    ryadky = [r.strip() for r in (f.tekst or "").splitlines()
              if len(r.strip()) > 25 and not r.startswith(("Джерело:", "Знято:"))]
    urivok = " ".join(ryadky)[:260]
    rozsh = PurePosixPath(f.stored_name or "").suffix.lower()
    return {"f": f, "host": u.netloc.replace("www.", ""), "fayl": fayl.replace("_", " ")[:80], "urivok": urivok,
            "mini": rozsh in (".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".odt", ".rtf"),
            "znimok": f.nazva.startswith("Сторінка програми")}


@router.post("/reyestr/{oid}/klasyfikatsiya")
async def klasyfikatsiya(oid: int, request: Request, user: User = Depends(require_cap(CAP_DATA)), s: Session = Depends(get_session)):
    """Ручне виправлення тегів і типів заявника — далі імпорт їх не перетирає."""
    o = s.get(Opportunity, oid)
    if not o:
        raise HTTPException(404)
    form = await request.form()
    o.tags = ",".join(t for t in form.getlist("tags") if t in TEGY)
    o.zayavnyky = ",".join(t for t in form.getlist("zayavnyky") if t in ZAYAVNYKY)
    o.tags_avto = False
    o.updated_at = utcnow()
    s.add(o); audit.log(s, user.name, "opportunity.classify", entity="opportunity", entity_id=oid); s.commit()
    for p in s.exec(select(Potreba).where(Potreba.status == "aktyvnyi")):
        matching.pererakhuvaty(s, p)
    return redirect(f"/reyestr/{oid}")


@router.post("/reyestr/{oid}/verdykt")
def verdykt(oid: int, verdykt: str = Form(...), prymitka: str = Form(""),
            user: User = Depends(current_user), s: Session = Depends(get_session)):
    """Вердикт скринінгу вручну (агент пише той самий вердикт автоматично)."""
    o = s.get(Opportunity, oid)
    if not o or verdykt not in VERDYKTY:
        raise HTTPException(400)
    o.verdykt = verdykt
    if prymitka.strip():
        o.notes = (o.notes + f"\n[{utcnow():%d.%m.%Y} {user.name}] скринінг: {prymitka.strip()}").strip()
    s.add(o); audit.log(s, user.name, "opportunity.verdict", entity="opportunity", entity_id=oid, detail=verdykt); s.commit()
    return redirect(f"/reyestr/{oid}")
