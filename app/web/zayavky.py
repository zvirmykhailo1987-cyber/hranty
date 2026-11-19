"""Заявки: дошка стадій, картка з гейтом наступної стадії, документи з версіями й погодженням,
задачі, позначка подачі (лише людина)."""
import re
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlmodel import Session, select

from .. import audit
from ..db import get_session
from ..models import Application, Document, Opportunity, Potreba, StageRule, Task, User, utcnow
from ..models.people import CAP_APPROVE, CAP_DELETE, CAP_SEND
from ..services import docstore, gates, rozbir
from ..slovnyky import REZULTATY, STADII, VYDY_DOK
from .deps import current_user, lokalnyi, redirect, render, require_cap

router = APIRouter()


@router.get("/zayavky")
def doshka(request: Request, user: User = Depends(current_user), s: Session = Depends(get_session)):
    zayavky = list(s.exec(select(Application, Opportunity, Potreba)
                          .join(Opportunity, Application.opportunity_id == Opportunity.id, isouter=True)
                          .join(Potreba, Application.potreba_id == Potreba.id, isouter=True)
                          .where(Application.arkhiv == False)))  # noqa: E712
    kolonky = {k: [] for k in STADII}
    for a, o, p in zayavky:
        kolonky.setdefault(a.stadiya, []).append((a, o, p))
    for k in kolonky:
        kolonky[k].sort(key=lambda t: (t[1].deadline if t[1] and t[1].deadline else rozbir.data("2999-12-31")))
    users = {u.id: u for u in s.exec(select(User))}
    return render(request, "zayavky.html", user=user, kolonky=kolonky, stadii=STADII, users=users)


@router.get("/zayavky/{aid}")
def kartka(aid: int, request: Request, user: User = Depends(current_user), s: Session = Depends(get_session)):
    a = s.get(Application, aid)
    if not a:
        raise HTTPException(404)
    o = s.get(Opportunity, a.opportunity_id) if a.opportunity_id else None
    p = s.get(Potreba, a.potreba_id) if a.potreba_id else None
    poryadok = list(STADII)
    i = poryadok.index(a.stadiya)
    nastupna = poryadok[i + 1] if i + 1 < len(poryadok) else None
    gejt = gates.perevirka(s, a, nastupna, user) if nastupna else []
    pravylo = s.exec(select(StageRule).where(StageRule.stadiya == nastupna)).first() if nastupna else None
    doky = gates.aktualni_dokumenty(s, aid)
    istoriya = {}
    for d in s.exec(select(Document).where(Document.application_id == aid).order_by(Document.versiya.desc())):
        istoriya.setdefault(d.group_uid or d.uid, []).append(d)
    zadachi = list(s.exec(select(Task).where(Task.application_id == aid).order_by(Task.done, Task.due)))
    users = list(s.exec(select(User).where(User.active == True)))  # noqa: E712
    import json as _json
    skryn = _json.loads(a.skryner_json) if a.skryner_json else None
    return render(request, "zayavka.html", skryn=skryn, user=user, a=a, o=o, p=p, nastupna=nastupna, gejt=gejt, pravylo=pravylo,
                  doky=doky, istoriya=istoriya, zadachi=zadachi, users=users, users_by={u.id: u for u in users},
                  stadii=STADII, rezultaty=REZULTATY, vydy=VYDY_DOK, poryadok=poryadok,
                  pohodzh={d.id: gates.mozhe_pohodzhuvaty(user, d) for d in doky})


@router.post("/zayavky/{aid}/stadiya")
def zminyty_stadiyu(aid: int, nova: str = Form(...), prychyna_vidboru: str = Form(""),
                    user: User = Depends(current_user), s: Session = Depends(get_session)):
    a = s.get(Application, aid)
    if not a:
        raise HTTPException(404)
    if prychyna_vidboru.strip():
        a.prychyna_vidboru = prychyna_vidboru.strip()[:500]
    prych = gates.perevirka(s, a, nova, user)
    if prych:
        raise HTTPException(409, "Гейт не пройдено: " + "; ".join(prych))
    if a.stadiya in ("podacha", "rezultat") and nova != a.stadiya and not user.can(CAP_SEND):
        raise HTTPException(403, "Повернути заявку з подачі може лише людина з правом подачі")
    stara, a.stadiya, a.updated_at = a.stadiya, nova, utcnow()
    s.add(a); audit.log(s, user.name, "application.stage", entity="application", entity_id=aid, detail=f"{stara}→{nova}")
    s.commit()
    return redirect(f"/zayavky/{aid}")


@router.post("/zayavky/{aid}/podano")
def podano(aid: int, data_podachi: str = Form(...), user: User = Depends(require_cap(CAP_SEND)),
           s: Session = Depends(get_session)):
    """Подача відбувається ПОЗА системою (портал грантодавця, пошта). Тут лише фіксуємо факт: хто й коли."""
    a = s.get(Application, aid)
    if not a or a.stadiya != "podacha":
        raise HTTPException(400, "Позначити подачу можна лише на стадії «Подача»")
    d = rozbir.data(data_podachi)
    if not d:
        raise HTTPException(400, "Вкажіть дату подачі")
    nepohodzheni = [x.nazva or x.vyd for x in gates.aktualni_dokumenty(s, a.id) if not x.pohodzheno_kym]
    if nepohodzheni:
        raise HTTPException(409, "Не погоджено: " + ", ".join(nepohodzheni))
    a.podano_at, a.podano_kto = utcnow().replace(year=d.year, month=d.month, day=d.day), user.name
    s.add(a); audit.log(s, user.name, "application.submitted_mark", entity="application", entity_id=aid, tier=3)
    s.commit()
    return redirect(f"/zayavky/{aid}")


@router.post("/zayavky/{aid}/rezultat")
def rezultat(aid: int, rezultat: str = Form(...), user: User = Depends(require_cap(CAP_SEND)), s: Session = Depends(get_session)):
    a = s.get(Application, aid)
    if not a or rezultat not in REZULTATY or a.stadiya != "rezultat":
        raise HTTPException(400)
    a.rezultat = rezultat
    s.add(a); audit.log(s, user.name, "application.result", entity="application", entity_id=aid, detail=rezultat); s.commit()
    return redirect(f"/zayavky/{aid}")


@router.post("/zayavky/{aid}/krok")
def nastupnyi_krok(aid: int, krok: str = Form(""), do: str = Form(""), owner_id: str = Form(""),
                   user: User = Depends(current_user), s: Session = Depends(get_session)):
    a = s.get(Application, aid)
    if not a:
        raise HTTPException(404)
    a.nastupnyi_krok, a.nastupnyi_krok_do = krok.strip()[:300], rozbir.data(do)
    if owner_id.isdigit():
        if not s.get(User, int(owner_id)):
            raise HTTPException(400, "Невідомий виконавець")
        a.owner_id = int(owner_id)
    s.add(a); s.commit()
    return redirect(f"/zayavky/{aid}")


@router.post("/zayavky/{aid}/arkhiv")
def arkhiv(aid: int, user: User = Depends(require_cap(CAP_DELETE)), s: Session = Depends(get_session)):
    a = s.get(Application, aid)
    if not a:
        raise HTTPException(404)
    a.arkhiv = not a.arkhiv
    s.add(a); audit.log(s, user.name, "application.archive", entity="application", entity_id=aid); s.commit()
    return redirect("/zayavky" if a.arkhiv else f"/zayavky/{aid}")


# ── документи ────────────────────────────────────────────────────────────────
@router.post("/zayavky/{aid}/dokument")
async def dokument(aid: int, vyd: str = Form("inshe"), nazva: str = Form(""), group_uid: str = Form(""),
                   fayl: UploadFile = File(...), user: User = Depends(current_user), s: Session = Depends(get_session)):
    a = s.get(Application, aid)
    if not a:
        raise HTTPException(404)
    if a.podano_at:
        raise HTTPException(409, "Заявку вже подано — документи змінювати не можна")
    try:
        meta = docstore.zberehty(await fayl.read(), fayl.filename or "", "zayavky")
    except docstore.FaylPomylka as e:
        raise HTTPException(400, str(e)) from None
    versiya = 1
    if group_uid:
        poperedni = list(s.exec(select(Document).where(Document.group_uid == group_uid,
                                                       Document.application_id == aid)))
        versiya = max((d.versiya for d in poperedni), default=0) + 1
        if poperedni and not vyd:
            vyd = poperedni[0].vyd
    d = Document(application_id=aid, nazva=(nazva or fayl.filename or "")[:200], vyd=vyd if vyd in VYDY_DOK else "inshe",
                 versiya=versiya, stored_name=meta["stored_name"], rozmir=meta["rozmir"], avtor=user.name, avtor_id=user.id,
                 created_by=user.name)
    d.group_uid = group_uid or d.uid
    if a.stadiya == "podacha":
        # A new, unsigned version invalidates the submission gate: back to "documents".
        a.stadiya, a.updated_at = "dokumenty", utcnow()
        s.add(a)
    s.add(d); audit.log(s, user.name, "document.upload", entity="application", entity_id=aid, detail=d.nazva); s.commit()
    return redirect(f"/zayavky/{aid}#dokumenty")


@router.post("/dokumenty/{did}/pohodyty")
def pohodyty(did: int, user: User = Depends(require_cap(CAP_APPROVE)), s: Session = Depends(get_session)):
    d = s.get(Document, did)
    if not d:
        raise HTTPException(404)
    prych = gates.mozhe_pohodzhuvaty(user, d)
    if prych:
        raise HTTPException(403, prych)
    d.pohodzheno_kym, d.pohodzheno_kym_id, d.pohodzheno_at = user.name, user.id, utcnow()
    s.add(d); audit.log(s, user.name, "document.approve", entity="document", entity_id=did, tier=2); s.commit()
    return redirect(f"/zayavky/{d.application_id}#dokumenty")


@router.get("/dokumenty/{did}/fayl")
def zavantazhyty(did: int, user: User = Depends(current_user), s: Session = Depends(get_session)):
    d = s.get(Document, did)
    if not d or not d.stored_name:
        raise HTTPException(404)
    rozsh = Path(d.stored_name).suffix
    imya = re.sub(r"[\/:*?\"<>|]+", "-", d.nazva or "dokument")
    if rozsh and not imya.lower().endswith(rozsh):
        imya += rozsh
    return FileResponse(docstore.shlyakh(d.stored_name), filename=imya,
                        content_disposition_type="attachment")


# ── задачі ────────────────────────────────────────────────────────────────────
@router.post("/zadachi")
def zadacha(title: str = Form(...), application_id: str = Form(""), assignee_id: str = Form(""), due: str = Form(""),
            povernutys: str = Form("/"), user: User = Depends(current_user), s: Session = Depends(get_session)):
    if not title.strip():
        raise HTTPException(400, "Вкажіть назву задачі")
    aid = int(application_id) if application_id.isdigit() else None
    if aid is not None and not s.get(Application, aid):
        raise HTTPException(400, "Невідома заявка")
    vid = int(assignee_id) if assignee_id.isdigit() else None
    if vid is not None and not s.get(User, vid):
        raise HTTPException(400, "Невідомий виконавець")
    t = Task(title=title.strip()[:300], application_id=aid, assignee_id=vid, due=rozbir.data(due),
             dzherelo="ручна", created_by=user.name)
    s.add(t); s.commit()
    return redirect(lokalnyi(povernutys))


@router.post("/zadachi/{tid}/hotovo")
def zadacha_hotovo(tid: int, povernutys: str = Form("/"), user: User = Depends(current_user), s: Session = Depends(get_session)):
    t = s.get(Task, tid)
    if not t:
        raise HTTPException(404)
    t.done = not t.done
    s.add(t); s.commit()
    return redirect(lokalnyi(povernutys))
