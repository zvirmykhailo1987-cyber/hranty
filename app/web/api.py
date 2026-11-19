# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Machine API for the ingest worker (Bearer ``HRANTY_API_TOKEN``; no cookies, so no CSRF)."""
import json
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session

from ..db import get_session
from ..services import ingest, matching
from .deps import api_token

router = APIRouter(prefix="/api", dependencies=[Depends(api_token)])
MAX_BODY = 5 * 1024 * 1024


@router.post("/ingest/radar")
async def ingest_radar(request: Request, data: str = "", s: Session = Depends(get_session)):
    """Body: a JSON list of opportunities. ``?data=YYYY-MM-DD`` labels the feed run."""
    tilo = await request.body()
    if len(tilo) > MAX_BODY:
        raise HTTPException(413, "payload too large")
    try:
        zapysy = json.loads(tilo.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(400, "expected a JSON array of opportunities") from None
    if not isinstance(zapysy, list):
        raise HTTPException(400, "expected a JSON array of opportunities")
    if data and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", data):
        raise HTTPException(400, "data must be YYYY-MM-DD")
    log = ingest.importuvaty_zapysy(s, zapysy, data or "api", "api")
    ingest.zvyazaty_funderiv(s)
    zbihiv = matching.pererakhuvaty_vse(s)
    return {"dodano": log.dodano, "onovleno": log.onovleno, "dubli": log.dubli, "nevalidni": log.nevalidni,
            "prymitka": log.prymitka, "zbihiv": zbihiv}
