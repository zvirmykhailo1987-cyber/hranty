"""Запити клієнтів: на що шукаємо гроші + ранжований список грантів із поясненням."""
import json

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlmodel import Session, select

from .. import audit
from ..db import get_session
from ..models import Application, Document, Match, Opportunity, Potreba, User, utcnow
from ..services import matching, rozbir
from ..slovnyky import TEGY
from .deps import current_user, redirect, render

router = APIRouter()


def _float(x: str) -> float | None:
    try:
        return float(str(x).replace(" ", "").replace(",", ".")) if str(x).strip() else None
    except ValueError:
        return None


@router.get("/potreby")
def spysok(request: Request, user: User = Depends(current_user), s: Session = Depends(get_session)):
    potreby = list(s.exec(select(Potreba).order_by(Potreba.status, Potreba.id.desc())))
    lichba = {}
    for m in s.exec(select(Match).where(Match.stan != "vidkhyleno", Match.score >= 0.5)):
        lichba[m.potreba_id] = lichba.get(m.potreba_id, 0) + 1
    users = {u.id: u for u in s.exec(select(User))}
    return render(request, "potreby.html", user=user, potreby=potreby, lichba=lichba, users=users)


@router.get("/potreby/nova")
def nova(request: Request, user: User = Depends(current_user), s: Session = Depends(get_session)):
    users = list(s.exec(select(User).where(User.active == True)))  # noqa: E712
    return render(request, "potreba_forma.html", user=user, p=None, users=users)


def _zapovnyty(p: Potreba, form) -> None:
    p.nazva = (form.get("nazva") or "").strip()[:200]
    p.klient = (form.get("klient") or "").strip()[:200]
    p.edrpou = (form.get("edrpou") or "").strip()[:20]
    p.opys = (form.get("opys") or "").strip()[:4000]
    tags = [t for t in form.getlist("tags") if t in TEGY]
    if not tags:      # теги не відмічено — пробуємо з опису
        tags = rozbir.tegy_z_tekstu(p.nazva, p.opys)
    p.tags = ",".join(tags)
    p.oblast = (form.get("oblast") or "").strip()
    p.zayavnyk = (form.get("zayavnyk") or "").strip()
    p.byudzhet_eur = _float(form.get("byudzhet_eur", ""))
    p.potribno_do = rozbir.data(form.get("potribno_do"))
    p.updated_at = utcnow()


@router.post("/potreby/nova")
async def stvoryty(request: Request, user: User = Depends(current_user), s: Session = Depends(get_session)):
    form = await request.form()
    p = Potreba(created_by=user.name)
    _zapovnyty(p, form)
    if not p.nazva:
        raise HTTPException(400, "Вкажіть назву запиту")
    s.add(p); s.commit(); s.refresh(p)
    audit.log(s, user.name, "potreba.create", entity="potreba", entity_id=p.id); s.commit()
    matching.pererakhuvaty(s, p)
    return redirect(f"/potreby/{p.id}")


@router.get("/potreby/{pid}")
def kartka(pid: int, request: Request, user: User = Depends(current_user), s: Session = Depends(get_session)):
    p = s.get(Potreba, pid)
    if not p:
        raise HTTPException(404)
    zbihy = list(s.exec(select(Match, Opportunity).join(Opportunity, Match.opportunity_id == Opportunity.id)
                        .where(Match.potreba_id == pid).order_by(Match.stan == "vidkhyleno", Match.score.desc())))
    rozbyvky = {m.id: json.loads(m.breakdown_json or "{}") for m, _ in zbihy}
    zayavky = list(s.exec(select(Application, Opportunity).join(Opportunity, Application.opportunity_id == Opportunity.id,
                                                                  isouter=True).where(Application.potreba_id == pid)))
    users = list(s.exec(select(User).where(User.active == True)))  # noqa: E712
    ost = {}
    for d in s.exec(select(Document).where(Document.potreba_id == pid)):
        g = d.group_uid or d.uid
        if g not in ost or d.versiya > ost[g].versiya:
            ost[g] = d                              # лише остання версія кожного документа
    doky = sorted(ost.values(), key=lambda d: (d.vyd, d.id))
    prorobka = json.loads(p.prorobka_json) if p.prorobka_json else None
    return render(request, "potreba.html", prorobka=prorobka, user=user, p=p, zbihy=zbihy, rozbyvky=rozbyvky, zayavky=zayavky,
                  users=users, v_roboti={a.opportunity_id for a, _ in zayavky}, doky=doky)


@router.post("/potreby/{pid}/dokument")
async def dokument(pid: int, request: Request, user: User = Depends(current_user), s: Session = Depends(get_session)):
    from fastapi import UploadFile
    from ..services import docstore
    from ..slovnyky import VYDY_DOK
    if not s.get(Potreba, pid):
        raise HTTPException(404)
    form = await request.form()
    fayl = form.get("fayl")
    if not isinstance(fayl, UploadFile) and not hasattr(fayl, "read"):
        raise HTTPException(400, "оберіть файл")
    try:
        meta = docstore.zberehty(await fayl.read(), fayl.filename or "", "zapyty")
    except docstore.FaylPomylka as e:
        raise HTTPException(400, str(e)) from None
    vyd = form.get("vyd") or "inshe"
    d = Document(potreba_id=pid, nazva=(form.get("nazva") or fayl.filename or "")[:200], vyd=vyd if vyd in VYDY_DOK else "inshe",
                 stored_name=meta["stored_name"], rozmir=meta["rozmir"], avtor=user.name, avtor_id=user.id, created_by=user.name)
    d.group_uid = d.uid
    s.add(d); audit.log(s, user.name, "document.upload", entity="potreba", entity_id=pid, detail=d.nazva); s.commit()
    return redirect(f"/potreby/{pid}#dokumenty")


@router.post("/potreby/{pid}/redaguvaty")
async def redaguvaty(pid: int, request: Request, user: User = Depends(current_user), s: Session = Depends(get_session)):
    p = s.get(Potreba, pid)
    if not p:
        raise HTTPException(404)
    _zapovnyty(p, await request.form())
    s.add(p); audit.log(s, user.name, "potreba.edit", entity="potreba", entity_id=p.id); s.commit()
    matching.pererakhuvaty(s, p)
    return redirect(f"/potreby/{pid}")


@router.post("/potreby/{pid}/status")
def status(pid: int, status: str = Form(...), user: User = Depends(current_user), s: Session = Depends(get_session)):
    p = s.get(Potreba, pid)
    if not p or status not in ("aktyvnyi", "pauza", "zakrytyi"):
        raise HTTPException(400)
    p.status = status
    s.add(p); audit.log(s, user.name, f"potreba.status.{status}", entity="potreba", entity_id=pid); s.commit()
    return redirect(f"/potreby/{pid}")


@router.post("/zbihy/{mid}/vidkhylyty")
def vidkhylyty(mid: int, prychyna: str = Form(""), user: User = Depends(current_user), s: Session = Depends(get_session)):
    m = s.get(Match, mid)
    if not m:
        raise HTTPException(404)
    m.stan, m.prychyna = "vidkhyleno", prychyna.strip()[:300] or "не підходить"
    s.add(m); audit.log(s, user.name, "match.reject", entity="match", entity_id=mid, detail=m.prychyna); s.commit()
    return redirect(f"/potreby/{m.potreba_id}")


@router.post("/zbihy/{mid}/v-robotu")
def v_robotu(mid: int, user: User = Depends(current_user), s: Session = Depends(get_session)):
    m = s.get(Match, mid)
    if not m:
        raise HTTPException(404)
    a = s.exec(select(Application).where(Application.potreba_id == m.potreba_id,
                                         Application.opportunity_id == m.opportunity_id)).first()
    if not a:
        p, o = s.get(Potreba, m.potreba_id), s.get(Opportunity, m.opportunity_id)
        a = Application(nazva=f"{p.nazva} → {o.title[:80]}", potreba_id=p.id, opportunity_id=o.id,
                        created_by=user.name)
        s.add(a)
    m.stan = "u_roboti"
    s.add(m); s.commit(); s.refresh(a)
    audit.log(s, user.name, "application.create", entity="application", entity_id=a.id); s.commit()
    return redirect(f"/zayavky/{a.id}")
