"""Local user accounts with salted PBKDF2 password hashes."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import User
from .common import BusinessError, CurrentUser, audit, require_admin

_ITER = 200_000
MIN_PASSWORD = 6


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _ITER)
    return f"pbkdf2_sha256${_ITER}${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt, digest = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iters))
        return hmac.compare_digest(dk.hex(), digest)
    except (ValueError, AttributeError):
        return False


def has_users(s: Session) -> bool:
    return (s.scalar(select(func.count(User.id))) or 0) > 0


def _check_pw(password: str) -> None:
    if len(password or "") < MIN_PASSWORD:
        raise BusinessError(f"Password must be at least {MIN_PASSWORD} characters.")


def create_user(s: Session, username: str, password: str, role: str = "staff",
                full_name: str = "", must_change: bool = False, _bootstrap: bool = False) -> User:
    if not _bootstrap:
        require_admin("user management")
    username = (username or "").strip().lower()
    if not username:
        raise BusinessError("Username is required.")
    if role not in ("admin", "staff"):
        raise BusinessError("Role must be admin or staff.")
    _check_pw(password)
    if s.scalar(select(User).where(User.username == username)):
        raise BusinessError(f"User '{username}' already exists.")
    u = User(username=username, full_name=full_name, password_hash=hash_password(password),
             role=role, must_change_password=must_change)
    s.add(u)
    s.flush()
    audit(s, "create_user", "user", u.id, f"{username} ({role})")
    return u


def authenticate(s: Session, username: str, password: str) -> User | None:
    u = s.scalar(select(User).where(User.username == (username or "").strip().lower()))
    if u is None or not u.active or not verify_password(password, u.password_hash):
        _audit_login(s, username, False)
        return None
    u.last_login = datetime.now().replace(microsecond=0)
    _audit_login(s, u.username, True)
    return u


def _audit_login(s: Session, username: str, ok: bool) -> None:
    from ..db.models import AuditLog
    s.add(AuditLog(username=(username or "?")[:64], action="login" if ok else "login_failed",
                   entity="user", details=""))


def change_password(s: Session, user_id: int, old: str | None, new: str) -> None:
    u = s.get(User, user_id)
    if u is None:
        raise BusinessError("User not found.")
    if old is not None and not verify_password(old, u.password_hash):
        raise BusinessError("Current password is incorrect.")
    _check_pw(new)
    u.password_hash = hash_password(new)
    u.must_change_password = False
    audit(s, "change_password", "user", u.id, u.username)


def reset_password(s: Session, user_id: int, new: str) -> None:
    require_admin("password resets")
    u = s.get(User, user_id)
    if u is None:
        raise BusinessError("User not found.")
    _check_pw(new)
    u.password_hash = hash_password(new)
    u.must_change_password = True
    audit(s, "reset_password", "user", u.id, u.username)


def set_user_active(s: Session, user_id: int, active: bool) -> None:
    require_admin("user management")
    u = s.get(User, user_id)
    if u is None:
        raise BusinessError("User not found.")
    if not active and u.role == "admin":
        admins = s.scalar(select(func.count(User.id)).where(User.role == "admin", User.active.is_(True)))
        if admins <= 1:
            raise BusinessError("Cannot deactivate the last active administrator.")
    u.active = active
    audit(s, "activate_user" if active else "deactivate_user", "user", u.id, u.username)


def verify_admin_credentials(s: Session, username: str, password: str) -> bool:
    u = s.scalar(select(User).where(User.username == (username or "").strip().lower()))
    return bool(u and u.active and u.role == "admin" and verify_password(password, u.password_hash))


def to_current(u: User) -> CurrentUser:
    return CurrentUser(id=u.id, username=u.username, role=u.role)
