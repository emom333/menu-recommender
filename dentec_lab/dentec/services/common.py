"""Shared service helpers: current user, audit trail, settings, errors."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..db.models import AuditLog, Setting


class BusinessError(Exception):
    """A rule violation that should be shown to the user verbatim."""


class PermissionDenied(BusinessError):
    pass


@dataclass
class CurrentUser:
    id: int | None = None
    username: str = "system"
    role: str = "admin"

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


_current = CurrentUser()


def current_user() -> CurrentUser:
    return _current


def set_current_user(user: CurrentUser) -> None:
    global _current
    _current = user


def require_admin(action: str = "this action") -> None:
    if not _current.is_admin:
        raise PermissionDenied(f"Only an administrator can perform {action}.")


def audit(s: Session, action: str, entity: str = "", entity_id: int | None = None, details: str = "") -> None:
    s.add(AuditLog(username=_current.username, action=action, entity=entity,
                   entity_id=entity_id, details=details[:4000]))


def get_setting(s: Session, key: str, default: str = "") -> str:
    row = s.get(Setting, key)
    return row.value if row is not None and row.value is not None else default


def get_settings(s: Session) -> dict[str, str]:
    return {r.key: r.value for r in s.query(Setting).all()}


def set_setting(s: Session, key: str, value) -> None:
    row = s.get(Setting, key)
    value = "" if value is None else str(value)
    if row is None:
        s.add(Setting(key=key, value=value))
    elif row.value != value:
        row.value = value
