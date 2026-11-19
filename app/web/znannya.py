"""«Знання»: статті й брифи редакції та статті каналу — автоматично щодня, з пошуком і фільтрами."""
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, or_, select

from ..db import get_session
from ..models import User, Znannya
from ..znannya import TYPY
from .deps import current_user, render

router = APIRouter()


@router.get("/znannya")
def spysok(request: Request, q: str = "", typ: str = "", user: User = Depends(current_user),
           s: Session = Depends(get_session)):
    st = select(Znannya)
    if typ in TYPY:
        st = st.where(Znannya.typ == typ)
    if q.strip():
        like = f"%{q.strip()}%"
        st = st.where(or_(Znannya.title.ilike(like), Znannya.dek.ilike(like), Znannya.body_md.ilike(like),
                          Znannya.tags.ilike(like)))
    zapysy = list(s.exec(st.order_by(Znannya.data.desc(), Znannya.id.desc()).limit(200)))
    return render(request, "znannya.html", user=user, zapysy=zapysy, q=q, typ=typ, typy=TYPY)


@router.get("/znannya/{slug}")
def stattya(slug: str, request: Request, user: User = Depends(current_user), s: Session = Depends(get_session)):
    zn = s.exec(select(Znannya).where(Znannya.slug == slug)).first()
    if not zn:
        raise HTTPException(404)
    dzherela = json.loads(zn.dzherela_json or "[]")
    return render(request, "znannya_stattya.html", user=user, zn=zn, dzherela=dzherela, typy=TYPY)
