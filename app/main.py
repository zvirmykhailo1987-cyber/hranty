# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""«Гранти під запит» — оперативний грантовий портал Проєктного офісу."""
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import authkit, config, i18n
from .db import init_db
from .security import KIT
from .web import api, auth, biblioteka, perehlyad, potreby, reyestr, syogodni, zayavky, znannya


async def _planuvalnyk():
    """Фоновий планувальник «живого порталу»: раз на добу (після 05:00 UTC) — обслуговування на сервері."""
    import asyncio
    import logging
    from sqlmodel import Session
    from .db import engine
    from .services import zhyvyi
    log = logging.getLogger("uvicorn.error")
    while True:
        try:
            if utcnow_hour() >= 5:
                with Session(engine) as s:
                    if not zhyvyi.vzhe_sohodni(s):
                        r = await asyncio.to_thread(_obslugovuvannya)
                        log.info("живий портал: щоденне обслуговування %s", r)
        except Exception as e:  # noqa: BLE001 — планувальник не має валити застосунок
            log.warning("живий портал: збій обслуговування: %s", e)
        await asyncio.sleep(1800)


def utcnow_hour() -> int:
    from .models import utcnow
    return utcnow().hour


def _obslugovuvannya() -> dict:
    from sqlmodel import Session
    from .db import engine
    from .services import zhyvyi
    with Session(engine) as s:
        return zhyvyi.obslugovuvannya(s)


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    import os
    config.check_prod_ready()
    init_db()
    zadacha = None
    if config.IS_PROD or config.IS_DEMO or os.getenv("HRANTY_PLANUVALNYK") == "1":
        zadacha = asyncio.create_task(_planuvalnyk())
    yield
    if zadacha:
        zadacha.cancel()


_DOCS = config.ENV == "dev"
app = FastAPI(title="Grants on demand", lifespan=lifespan,
              dependencies=[Depends(authkit.csrf_protect)],
              docs_url="/docs" if _DOCS else None, redoc_url=None,
              openapi_url="/openapi.json" if _DOCS else None)
app.mount("/static", StaticFiles(directory=str(config.BASE_DIR / "static")), name="static")
for r in (auth, syogodni, potreby, perehlyad, reyestr, zayavky, biblioteka, znannya, api):
    app.include_router(r.router)


@app.middleware("http")
async def _mova_i_noindex(request: Request, call_next):
    """Interface language from the `lang` cookie; internal tool → not indexed."""
    i18n.set_lang(i18n.pick(request.cookies.get(i18n.COOKIE)))
    resp = await call_next(request)
    resp.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return resp


authkit.install(app, KIT)


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 307:
        # 303, never 307: a 307 would re-send an expired form (or a whole upload) to /login
        return RedirectResponse((exc.headers or {}).get("Location", "/login"), status_code=303)
    return _error_page(request, exc.status_code, str(exc.detail or ""))


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError):
    return _error_page(request, 422, i18n.translate("Некоректні дані форми або адреси."))


@app.exception_handler(Exception)
async def _server_error(request: Request, exc: Exception):
    import logging
    logging.getLogger("uvicorn.error").exception("unhandled error on %s", request.url.path)
    return _error_page(request, 500, "")


def _error_page(request: Request, code: int, detail: str):
    if request.url.path.startswith("/api/") or "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"detail": detail or str(code)}, status_code=code)
    from .web.deps import render
    try:
        return render(request, "pomylka.html", kod=code, detail=i18n.translate(detail), status_code=code)
    except Exception:                                       # noqa: BLE001 — last resort
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(f"Error {code}", status_code=code)


@app.get("/lang/{code}")
def change_language(code: str, next: str = "/"):
    """Switch the interface language. `next` must be a local path (no open redirect)."""
    if not next.startswith("/") or next.startswith("//") or "\\" in next:
        next = "/"
    resp = RedirectResponse(next, status_code=303)
    if code in i18n.LANGS:
        resp.set_cookie(i18n.COOKIE, code, max_age=60 * 60 * 24 * 365, path="/",
                        samesite="lax", secure=config.SECURE_COOKIES)
    return resp


@app.get("/robots.txt", include_in_schema=False)
def robots():
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse("User-agent: *\nDisallow: /\n")


@app.get("/health")
def health():
    from sqlalchemy import text
    from sqlmodel import Session

    from .db import engine
    try:
        with Session(engine) as s:
            lyudej = s.exec(text("select count(*) from user")).one()[0]
            mozhl = s.exec(text("select count(*) from opportunity")).one()[0]
    except Exception as e:  # noqa: BLE001
        detail = {"baza": type(e).__name__} if config.ENV == "dev" else {}
        return JSONResponse({"ok": False, **detail}, status_code=503)
    if config.ENV != "dev":
        return {"ok": True}
    return {"ok": True, "env": config.ENV, "lyudej": lyudej, "mozhlyvostei": mozhl}
