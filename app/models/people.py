# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Користувачі, ролі, аудит. Форк ofis_crm/app/models/people.py (див. FORK.md):
ті самі 4 ролі й підписи; права адаптовано під гранти (CAP_AGENT замість CAP_CRM)."""
from datetime import datetime

from sqlmodel import Field, SQLModel

from .base import BaseRow, utcnow

ROLES = {
    "rozrobnyk": "Розробка ПЗ й аналітика",
    "perehovorschyk_dok": "Інвестори й переговори",
    "perehovory_kontakt": "Громади й підприємства",
    "zapyty_lysty": "Запити й кореспонденція",
}
ROLE_HINTS = {
    "rozrobnyk": "аналітика й пошук даних, підбір грантів, агент і бази",
    "perehovorschyk_dok": "напрямки нових проєктів, переговори, погодження заявок",
    "perehovory_kontakt": "запити громад і підприємств, перший контакт, звʼязки в регіонах",
    "zapyty_lysty": "підготовка заявок і листів, документи, супровід подачі",
}

CAP_ADMIN = "admin"        # користувачі, налаштування
CAP_DATA = "data"          # імпорти, ваги підбору
CAP_DELETE = "delete"      # архівування записів
CAP_SEND = "send"          # позначити «подано» (дію виконує людина поза системою)
CAP_AGENT = "agent"        # запускати AI-агента
# Погодження — СВІДОМО не в адміністратора: погоджує Богдан, автор не погоджує сам себе.
CAP_APPROVE = "approve"

# Roles are enforced: with individual accounts every action is checked against the
# role's capabilities on the route (deny by default), and the author of a document
# can never approve it.
ROLI_UVIMKNENO = True
VSI_PRAVA = {CAP_ADMIN, CAP_DATA, CAP_DELETE, CAP_SEND, CAP_AGENT, CAP_APPROVE}

ROLE_CAPS: dict[str, set[str]] = {
    "rozrobnyk": {CAP_ADMIN, CAP_DATA, CAP_DELETE, CAP_SEND, CAP_AGENT},
    "perehovorschyk_dok": {CAP_APPROVE, CAP_AGENT},
    "perehovory_kontakt": {CAP_AGENT},
    "zapyty_lysty": {CAP_SEND, CAP_AGENT},
}


class User(BaseRow, table=True):
    name: str = Field(index=True)
    login: str = Field(default="", index=True)
    password_hash: str = ""
    role: str = Field(default="perehovory_kontakt", index=True)
    active: bool = True
    last_login_at: datetime | None = None

    @property
    def caps(self) -> set[str]:
        return ROLE_CAPS.get(self.role, set()) if ROLI_UVIMKNENO else VSI_PRAVA

    def can(self, cap: str) -> bool:
        return cap in self.caps

    @property
    def role_label(self) -> str:
        return ROLES.get(self.role, self.role)

    @property
    def initsialy(self) -> str:
        return (self.name[:1] or "?").upper()


class AuditEntry(SQLModel, table=True):
    """Журнал дій. Tier: 0 читання · 1 зміна · 2 масова · 3 незворотна."""
    id: int | None = Field(default=None, primary_key=True)
    at: datetime = Field(default_factory=utcnow, index=True)
    actor: str = Field(default="", index=True)
    action: str = Field(default="", index=True)
    entity: str = ""
    entity_id: int | None = None
    tier: int = Field(default=1, index=True)
    detail: str = ""
