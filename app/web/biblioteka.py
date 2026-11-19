# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Документи програм: положення, форми, шаблони, бюджети, прецеденти — з походженням (URL, дата, sha256).
Етап A: ручне завантаження; етап B: збирач агента кладе файли сюди ж."""
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlmodel import Session, or_, select

from .. import audit
from ..db import get_session
from ..models import Opportunity, ProgramFile, User, utcnow
from ..services import docstore
from ..slovnyky import VYDY_DOK
from ..models.people import CAP_DATA
from .deps import current_user, redirect, render, require_cap

router = APIRouter()


@router.get("/biblioteka")
def biblioteka(request: Request, q: str = "", vyd: str = "", user: User = Depends(current_user),
               s: Session = Depends(get_session)):
    st = select(ProgramFile, Opportunity).join(Opportunity, ProgramFile.opportunity_id == Opportunity.id, isouter=True)
    if vyd:
        st = st.where(ProgramFile.vyd == vyd)
    if q.strip():
        like = f"%{q.strip()}%"
        st = st.where(or_(ProgramFile.nazva.ilike(like), ProgramFile.tekst.ilike(like), Opportunity.title.ilike(like)))
    poryadok = {"forma": 0, "polozhennya": 1, "shablon": 2, "byudzhet": 3, "faq": 4, "logframe": 5, "pretsedent": 6}
    ryadky = sorted(s.exec(st.order_by(ProgramFile.id.desc()).limit(300)),
                    key=lambda r: (r[0].nazva.startswith("Сторінка програми"), poryadok.get(r[0].vyd, 9)))
    from .reyestr import _kartka_faylu
    return render(request, "biblioteka.html", user=user, ryadky=[(_kartka_faylu(f), o) for f, o in ryadky],
                  q=q, vyd=vyd, vydy=VYDY_DOK)


@router.post("/biblioteka")
async def zavantazhyty(opportunity_id: int = Form(...), vyd: str = Form("inshe"), source_url: str = Form(""),
                       fayl: UploadFile = File(...), user: User = Depends(require_cap(CAP_DATA)), s: Session = Depends(get_session)):
    if not s.get(Opportunity, opportunity_id):
        raise HTTPException(404, "немає такої можливості")
    dani = await fayl.read()
    try:
        meta = docstore.zberehty(dani, fayl.filename or "", "prohramy")
    except docstore.FaylPomylka as e:
        raise HTTPException(400, str(e)) from None
    if s.exec(select(ProgramFile).where(ProgramFile.sha256 == meta["sha256"],
                                        ProgramFile.opportunity_id == opportunity_id)).first():
        return redirect(f"/reyestr/{opportunity_id}#fayly")          # той самий файл уже є
    tekst = ""
    try:
        import asyncio

        from ..services.extract import read_file
        tekst = (await asyncio.to_thread(read_file, docstore.shlyakh(meta["stored_name"])))[:200000]
    except Exception as e:  # noqa: BLE001 — extracted text is optional
        import logging
        logging.getLogger("uvicorn.error").info("text extraction skipped: %s", type(e).__name__)
    pf = ProgramFile(opportunity_id=opportunity_id, nazva=(fayl.filename or "")[:200],
                     vyd=vyd if vyd in VYDY_DOK else "inshe", source_url=source_url.strip()[:1000],
                     fetched_at=utcnow(), tekst=tekst, created_by=user.name, **meta)
    s.add(pf); audit.log(s, user.name, "programfile.upload", entity="opportunity", entity_id=opportunity_id); s.commit()
    return redirect(f"/reyestr/{opportunity_id}#fayly")


@router.get("/biblioteka/{fid}/fayl")
def fayl(fid: int, user: User = Depends(current_user), s: Session = Depends(get_session)):
    pf = s.get(ProgramFile, fid)
    if not pf:
        raise HTTPException(404)
    return FileResponse(docstore.shlyakh(pf.stored_name), filename=pf.nazva or "fayl")
