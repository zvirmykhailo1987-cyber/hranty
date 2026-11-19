# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Спільна база рядка (форк ofis_crm/app/models/base.py, див. FORK.md)."""
import uuid
from datetime import datetime, UTC

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class BaseRow(SQLModel):
    id: int | None = Field(default=None, primary_key=True)
    uid: str = Field(default_factory=lambda: uuid.uuid4().hex, index=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    created_by: str = ""
