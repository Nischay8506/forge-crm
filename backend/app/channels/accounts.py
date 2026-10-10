"""The company's email account (decision L2 A) and which adapter sends.

The password (an app password, for Google Workspace or Microsoft 365) is encrypted
with CREDENTIAL_KEYS like ERP credentials (app/core/secrets.py), bound to this
company and field, and never returned, logged or audited; screens see its last four
characters. Messages go through the simulator until the company switches to live,
which needs an account that passed its check.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import chain
from app.channels.adapters.base import EmailAdapter
from app.channels.adapters.simulator import FROM_ADDRESS, SimulatorAdapter
from app.channels.adapters.smtp import SmtpAdapter
from app.core import secrets
from app.core.errors import AppError
from app.models import EmailAccount
from app.steward.identifiers import normalize_email

# Common providers, so people pick one instead of typing host and port.
PRESETS = {
    "google": {"label": "Google Workspace / Gmail", "host": "smtp.gmail.com", "port": 587,
               "security": "starttls",
               "help": "Turn on 2-step verification, then make an app password at "
                       "myaccount.google.com/apppasswords and paste it here."},
    "microsoft": {"label": "Microsoft 365 / Outlook", "host": "smtp.office365.com",
                  "port": 587, "security": "starttls",
                  "help": "Your Microsoft 365 admin must allow SMTP sending (SMTP AUTH) for "
                          "this mailbox; use an app password if sign-in needs two steps."},
    "zoho": {"label": "Zoho Mail", "host": "smtp.zoho.in", "port": 465, "security": "ssl",
             "help": "Make an app-specific password in Zoho Mail's security settings."},
    "other": {"label": "Another mail server", "host": "", "port": 587,
              "security": "starttls", "help": "Ask whoever runs your mail for the SMTP "
                                              "server, port and login."},
}
SECURITY = ("starttls", "ssl", "none")
FIELDS = ("host", "port", "security", "username", "from_address", "from_name", "reply_to")


def _context(tenant_id: uuid.UUID) -> str:
    return f"email_account:{tenant_id}:password"


async def get(session: AsyncSession) -> EmailAccount | None:
    return await session.scalar(select(EmailAccount))


def view(a: EmailAccount | None) -> dict[str, Any] | None:
    if a is None:
        return None
    return {"id": str(a.id), "host": a.host, "port": a.port, "security": a.security,
            "username": a.username, "password_hint": a.secret_hint,
            "from_address": a.from_address, "from_name": a.from_name, "reply_to": a.reply_to,
            "status": a.status, "last_check_at": a.last_check_at,
            "last_check_ok": a.last_check_ok, "last_check_message": a.last_check_message,
            "updated_by": a.updated_by, "updated_at": a.updated_at}


async def save(session: AsyncSession, tenant_id: uuid.UUID, data: dict[str, Any],
               actor: str) -> EmailAccount:
    """Create or change the account. A missing password keeps the stored one."""
    if not secrets.configured():
        raise AppError("CREDENTIAL_KEYS is not set in backend/.env, so Forge cannot store "
                       "the email password safely. Generate one with "
                       "`uv run python -m app.cli new-credential-key`.")
    host = str(data.get("host") or "").strip()
    port = data.get("port")
    security = data.get("security")
    sender = normalize_email(data.get("from_address"))
    reply_to = normalize_email(data.get("reply_to")) if data.get("reply_to") else None
    problems = []
    if not host or " " in host or "/" in host:
        problems.append("Give the mail server's name, e.g. smtp.gmail.com")
    if not isinstance(port, int) or not 1 <= port <= 65535:
        problems.append("Port must be a number such as 587 or 465")
    if security not in SECURITY:
        problems.append("Security must be STARTTLS, SSL or none")
    if not sender:
        problems.append("The sender address is not a valid email address")
    if data.get("reply_to") and not reply_to:
        problems.append("The reply-to address is not a valid email address")
    a = await get(session)
    password = data.get("password") or None
    if a is None and not password:
        problems.append("Give the mailbox's password or app password")
    if problems:
        raise AppError("; ".join(problems))
    now = datetime.now(UTC)
    if a is None:
        a = EmailAccount(tenant_id=tenant_id, created_by=actor, password_enc="", secret_hint="")
        session.add(a)
    before = {k: getattr(a, k, None) for k in FIELDS}
    a.host, a.port, a.security = host, port, security
    a.username = str(data.get("username") or "").strip()
    a.from_address, a.reply_to = sender, reply_to
    a.from_name = (str(data.get("from_name") or "").strip() or None)
    changed = [k for k in FIELDS if before[k] != getattr(a, k)]
    if password:
        a.password_enc = secrets.encrypt(password, context=_context(tenant_id))
        a.secret_hint = secrets.hint(password)
        changed.append("password")
    a.status = "active"
    # A changed account must pass its check again before live sending uses it (the
    # worker checks it within a minute; see send.deliver_due).
    a.last_check_ok, a.last_check_message = None, None
    a.updated_by, a.updated_at = actor, now
    await session.flush()
    await chain.append(session, tenant_id, "channels.email_account_saved", actor=actor,
                       data={"email_account_id": a.id, "changed": changed,
                             "host": host, "port": port})
    return a


def smtp_adapter(a: EmailAccount) -> SmtpAdapter:
    password = secrets.decrypt(a.password_enc, context=_context(a.tenant_id))
    return SmtpAdapter(a.host, a.port, a.security, a.username, password)


async def check(session: AsyncSession, a: EmailAccount, actor: str) -> tuple[bool, str]:
    """Connect and log in without sending anything."""
    try:
        ok, msg = await smtp_adapter(a).check()
    except secrets.CredentialKeyError as e:
        ok, msg = False, str(e)
    a.last_check_at, a.last_check_ok, a.last_check_message = datetime.now(UTC), ok, msg[:500]
    await chain.append(session, a.tenant_id, "channels.email_account_checked", actor=actor,
                       data={"email_account_id": a.id, "ok": ok})
    return ok, msg


async def set_paused(session: AsyncSession, a: EmailAccount, paused: bool, actor: str) -> None:
    a.status, a.updated_by, a.updated_at = ("paused" if paused else "active"), actor, \
        datetime.now(UTC)
    await chain.append(session, a.tenant_id, "channels.email_account_" +
                       ("paused" if paused else "resumed"), actor=actor,
                       data={"email_account_id": a.id})


async def note_problem(session: AsyncSession, reason: str) -> None:
    """The mail server refused the login or the sender while sending."""
    a = await get(session)
    if a is not None:
        a.last_check_at, a.last_check_ok = datetime.now(UTC), False
        a.last_check_message = reason[:500]


async def sender(session: AsyncSession, mode: str, force_simulator: bool = False
                 ) -> tuple[EmailAdapter | None, dict[str, Any]]:
    """The adapter for a message now, and who it is from. None when live sending is
    on but the account is missing, paused or failed its last check."""
    if mode != "live" or force_simulator:
        return SimulatorAdapter(), {"from_address": FROM_ADDRESS, "from_name": None,
                                    "reply_to": None}
    a = await get(session)
    if a is None or a.status != "active" or a.last_check_ok is not True:
        return None, {}
    try:
        adapter = smtp_adapter(a)
    except secrets.CredentialKeyError:
        return None, {}
    return adapter, {"from_address": a.from_address, "from_name": a.from_name,
                     "reply_to": a.reply_to}
