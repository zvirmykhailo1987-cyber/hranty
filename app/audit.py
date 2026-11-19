"""Risk-tier журнал дій (форк ofis_crm/app/audit.py, див. FORK.md)."""
from sqlmodel import Session

from .models import AuditEntry

TIER_READ, TIER_CHANGE, TIER_BULK, TIER_IRREVERSIBLE = 0, 1, 2, 3


def log(s: Session, actor: str, action: str, *, entity: str = "", entity_id: int | None = None,
        tier: int = TIER_CHANGE, detail: str = "") -> AuditEntry:
    e = AuditEntry(actor=actor, action=action, entity=entity, entity_id=entity_id, tier=tier, detail=detail[:2000])
    s.add(e)
    return e
