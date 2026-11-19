# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Поточний користувач, гейт прав, рендер зі спільним контекстом."""
from fastapi import Depends, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from .. import authkit, config
from ..db import get_session
from ..i18n import _
from ..models import User
from ..security import KIT
from ..templating import templates

NAV = [("/", "Сьогодні", "i-den"), ("/potreby", "Запити клієнтів", "i-tsil"),
       ("/reyestr", "Реєстр грантів", "i-dovidnyk"), ("/zayavky", "Заявки", "i-kanban"),
       ("/znannya", "Знання", "i-knyha"), ("/biblioteka", "Документи програм", "i-dokument")]


def current_user(request: Request, s: Session = Depends(get_session)) -> User:
    """Deny by default: no live server-side session → redirect to /login."""
    sess = authkit.resolve_session(s, KIT, request.cookies.get(KIT.session_cookie, ""))
    user = s.get(User, sess.user_id) if sess else None
    if not user or not user.active:
        raise HTTPException(status_code=307, headers={"Location": "/login"})
    return user


def require_cap(cap: str):
    def _dep(user: User = Depends(current_user)) -> User:
        if not user.can(cap):
            raise HTTPException(403, _("Немає права на цю дію"))
        return user
    return _dep


def api_token(authorization: str = Header("")) -> None:
    """Воркер агента на ПК: Authorization: Bearer <HRANTY_API_TOKEN>."""
    import hmac
    tok = authorization.removeprefix("Bearer ").strip()
    if not config.API_TOKEN or not hmac.compare_digest(tok.encode(), config.API_TOKEN.encode()):
        raise HTTPException(401, "invalid API token")


def render(request: Request, name: str, **ctx):
    """Render with the shared context (user, nav, CSRF token, CSP nonce, language)."""
    from .. import i18n
    ctx.setdefault("user", None)
    status = ctx.pop("status_code", 200)
    return templates.TemplateResponse(request, name, {
        "request": request, "nav": NAV, "shlyakh": request.url.path,
        "csrf_token": authkit.csrf_token(request), "csp_nonce": getattr(request.state, "csp_nonce", ""),
        "lang": i18n.current(), "demo": config.IS_DEMO, **ctx}, status_code=status)


def lokalnyi(url: str, default: str = "/") -> str:
    """Only same-site paths: "/x" yes; "//host", backslash tricks, "https://..." fall back to default."""
    if not url.startswith("/") or url.startswith("//") or any(ch in url for ch in ("\\", "\n", "\r")):
        return default
    return url


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)
