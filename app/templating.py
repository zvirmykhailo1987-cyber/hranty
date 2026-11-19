# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Jinja2 + фільтри відображення (дати, гроші, словники)."""
from datetime import date, datetime

from fastapi.templating import Jinja2Templates

from . import config, i18n, slovnyky
from .models import utcnow

templates = Jinja2Templates(directory=str(config.BASE_DIR / "templates"))
MISYATSI = ["січ", "лют", "бер", "квіт", "трав", "черв", "лип", "серп", "вер", "жовт", "лист", "груд"]


def f_data(d) -> str:
    if not d:
        return "—"
    if isinstance(d, datetime):
        return d.strftime("%d.%m.%Y %H:%M")
    return d.strftime("%d.%m.%Y")


def f_dniv(d) -> str:
    _ = i18n.translate
    if not d:
        return _("без дедлайну")
    dn = ((d.date() if isinstance(d, datetime) else d) - utcnow().date()).days
    if dn < 0:
        return _(f"минув {-dn} дн. тому")
    if dn == 0:
        return _("сьогодні")
    if dn == 1:
        return _("завтра")
    return _(f"за {dn} дн.")


def f_hroshi(x, val=None) -> str:
    if x is None:
        return i18n.translate("немає даних")
    s = f"{x:,.0f}".replace(",", " ")
    return f"{s} {val}" if val else s


def f_bal(x) -> str:
    return "—" if x is None else f"{x * 100:.0f}"


def f_tegy(csv: str) -> list[tuple[str, str]]:
    return [(t, i18n.translate(slovnyky.TEGY_NAZVY.get(t, t))) for t in (csv or "").split(",") if t]


def ik(i: str):
    from markupsafe import Markup
    safe = "".join(ch for ch in i if ch.isalnum() or ch == "-")      # icon ids only
    return Markup(f'<svg class="ik" aria-hidden="true"><use href="/static/bib/ikony.svg#{safe}"/></svg>')  # noqa: S704  # nosec B704


def f_md(tekst: str):
    """Markdown → HTML для робочих документів пакета. Сирий HTML у джерелі екранується."""
    import markdown
    from markupsafe import Markup
    bezpechno = (tekst or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    bezpechno = "\n".join(("> " + ln[5:]) if ln.startswith("&gt; ") else ln for ln in bezpechno.splitlines())
    html = markdown.markdown(bezpechno, extensions=["tables", "sane_lists"])
    return Markup(_bezpechni_posylannya(html))  # noqa: S704 — source HTML-escaped, links sanitised  # nosec B704


def _bezpechni_posylannya(html: str) -> str:
    """Neutralise non-http(s) link targets: markdown turns [x](javascript:…) into a live link."""
    import re

    def fix(m):
        url = m.group(2).strip().lower().replace("&#", "")
        ok = url.startswith(("http://", "https://", "/", "#", "mailto:")) and not url.startswith("//")
        return f'{m.group(1)}="{m.group(2) if ok else "#"}"'
    return re.sub(r'\b(href|src)="([^"]*)"', fix, html)


templates.env.filters.update(data=f_data, dniv=f_dniv, hroshi=f_hroshi, bal=f_bal, tegy=f_tegy, md=f_md)
templates.env.filters["t"] = i18n.translate
templates.env.finalize = i18n.finalize
templates.env.globals.update(ik=ik, S=slovnyky, ENV=config.ENV, sohodni=lambda: utcnow().date(), date=date,
                             _=i18n.translate, LANGS=i18n.LANGS, js_i18n=i18n.js_catalog)
