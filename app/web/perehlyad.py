"""Сторінка перегляду будь-якого документа порталу (документи запитів і заявок, документи програм, пакети)."""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response
from sqlmodel import Session

from ..db import get_session
from ..models import Document, ProgramFile, User
from ..services import docstore, perehlyad
from ..slovnyky import VYDY_DOK
from .deps import current_user, render

router = APIRouter()
MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}


def _dzherelo(kind: str, key: str, s: Session) -> tuple[Path, dict]:
    """Шлях до файлу + метадані для сторінки. kind: dok | pf."""
    if kind == "dok":
        d = s.get(Document, int(key)) if key.isdigit() else None
        if not d or not d.stored_name:
            raise HTTPException(404)
        nazad = f"/zayavky/{d.application_id}#dokumenty" if d.application_id else f"/potreby/{d.potreba_id}#dokumenty"
        return docstore.shlyakh(d.stored_name), {"nazva": d.nazva, "vyd": VYDY_DOK.get(d.vyd, d.vyd), "nazad": nazad,
                                                 "zavantazhyty": f"/dokumenty/{d.id}/fayl", "ai": d.ai_chernetka,
                                                 "dzherelo": d.dzherelo, "versiya": d.versiya}
    if kind == "pf":
        pf = s.get(ProgramFile, int(key)) if key.isdigit() else None
        if not pf or not pf.stored_name:
            raise HTTPException(404)
        return docstore.shlyakh(pf.stored_name), {"nazva": pf.nazva, "vyd": VYDY_DOK.get(pf.vyd, pf.vyd),
                                                  "nazad": f"/reyestr/{pf.opportunity_id}", "zavantazhyty": f"/biblioteka/{pf.id}/fayl",
                                                  "dzherelo": pf.source_url, "ai": False, "versiya": None}
    raise HTTPException(404)


@router.get("/perehlyad/{kind}/{key}")
def storinka(kind: str, key: str, request: Request, user: User = Depends(current_user), s: Session = Depends(get_session)):
    path, meta = _dzherelo(kind, key, s)
    if not path.exists():
        raise HTTPException(404, "файл відсутній у сховищі")
    v = perehlyad.vyd(path)
    tekst = ""
    if v == "tekst":
        tekst = path.read_text(encoding="utf-8", errors="replace")[:400000]
    pdf = perehlyad.pdf_dlya(path) if v in ("pdf", "ofisnyi") else None
    ye_pdf = pdf is not None
    n_stor = perehlyad.storinky(pdf) if pdf else 0
    return render(request, "perehlyad.html", user=user, m=meta, v=v, tekst=tekst, ye_pdf=ye_pdf, n_stor=n_stor,
                  md=path.suffix.lower() == ".md", vmist=f"/perehlyad/{kind}/{key}/vmist", rozsh=path.suffix.lower())


@router.get("/perehlyad/{kind}/{key}/storinka/{n}.png")
def storinka_png(kind: str, key: str, n: int, dpi: int = 110, user: User = Depends(current_user),
                 s: Session = Depends(get_session)):
    path, _ = _dzherelo(kind, key, s)
    pdf = perehlyad.pdf_dlya(path)
    png = perehlyad.png_storinky(pdf, n, 40 if dpi <= 40 else 110) if pdf else None
    if not png:
        raise HTTPException(404)
    return FileResponse(png, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})


@router.get("/perehlyad/{kind}/{key}/vmist")
def vmist(kind: str, key: str, user: User = Depends(current_user), s: Session = Depends(get_session)):
    path, _ = _dzherelo(kind, key, s)
    v = perehlyad.vyd(path)
    if v in ("pdf", "ofisnyi"):
        pdf = perehlyad.pdf_dlya(path)
        if not pdf:
            raise HTTPException(404, "перегляд недоступний — завантажте файл")
        return FileResponse(pdf, media_type="application/pdf", content_disposition_type="inline",
                            filename="perehlyad.pdf", headers={"Cache-Control": "private, max-age=3600"})
    if v == "zobrazhennya":
        return FileResponse(path, media_type=MIME.get(path.suffix.lower(), "application/octet-stream"),
                            content_disposition_type="inline")
    if v == "html":                                       # чужий HTML — лише в пісочниці, без скриптів і форм
        return Response(path.read_bytes(), media_type="text/html; charset=utf-8",
                        headers={"Content-Security-Policy": "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'",
                                 "X-Content-Type-Options": "nosniff"})
    raise HTTPException(404)
