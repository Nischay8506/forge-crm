"""Connecting, checking and disconnecting the company's mailbox."""

import hashlib
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import chain
from app.config import get_settings
from app.core import secrets
from app.core.errors import AppError
from app.enquiries.mail import gmail, imap, microsoft, oauth
from app.enquiries.mail.base import MailError, seal, unseal
from app.models import Mailbox, Tenant
from app.steward.identifiers import normalize_email

log = logging.getLogger(__name__)

PROVIDER_NAMES = {"imap": "IMAP", "gmail": "Gmail", "microsoft": "Microsoft 365"}
FAILURES_TO_ALERT = 5
RENEW_BEFORE = timedelta(hours=12)


def view(mb: Mailbox | None) -> dict[str, Any] | None:
    if mb is None:
        return None
    connected = mb.secret_hint != "-"
    return {"id": str(mb.id), "provider": mb.provider,
            "provider_name": PROVIDER_NAMES.get(mb.provider, mb.provider),
            "address": mb.address, "status": mb.status if connected else "disconnected",
            "imap_host": mb.imap_host, "imap_port": mb.imap_port,
            "imap_security": mb.imap_security, "username": mb.username, "folder": mb.folder,
            "secret_hint": mb.secret_hint if connected else None,
            "last_poll_at": mb.last_poll_at, "last_ok_at": mb.last_ok_at,
            "last_error": mb.last_error, "last_error_at": mb.last_error_at,
            "failures": mb.failures, "push": bool(mb.push_ref),
            "push_expires_at": mb.push_expires_at, "updated_by": mb.updated_by,
            "updated_at": mb.updated_at}


async def get(session: AsyncSession) -> Mailbox | None:
    return await session.scalar(select(Mailbox))


async def _audit(session: AsyncSession, mb: Mailbox, kind: str, actor: str, **data: Any) -> None:
    await chain.append(session, mb.tenant_id, kind, actor=actor, record_type="mailbox",
                       record_id=mb.id, data={"mailbox_id": mb.id, "provider": mb.provider,
                                              **data})


async def _row(session: AsyncSession, tenant_id: uuid.UUID, provider: str, address: str,
               actor: str) -> Mailbox:
    """The company's one mailbox row, reset for a new connection."""
    now = datetime.now(UTC)
    mb = await get(session)
    if mb is None:
        mb = Mailbox(id=uuid.uuid4(), tenant_id=tenant_id, provider=provider, address=address,
                     status="active", folder="INBOX", secret_enc="", secret_hint="-",
                     cursor={}, failures=0, created_by=actor, updated_by=actor,
                     created_at=now, updated_at=now)
        session.add(mb)
    mb.provider, mb.address, mb.status = provider, address, "active"
    mb.cursor, mb.failures, mb.last_error, mb.last_error_at = {}, 0, None, None
    mb.push_ref = mb.push_expires_at = mb.push_secret_hash = None
    mb.poll_requested_at, mb.updated_by, mb.updated_at = now, actor, now
    # Read from now on: the first check notes where the mailbox is.
    mb.created_at = now
    return mb


async def connect_imap(session: AsyncSession, tenant_id: uuid.UUID, data: dict[str, Any],
                       actor: str) -> Mailbox:
    address = normalize_email(data.get("address"))
    host = str(data.get("imap_host") or "").strip()
    security = data.get("imap_security") or "ssl"
    password = str(data.get("password") or "")
    problems = []
    if not address:
        problems.append("Give the mailbox's email address")
    if not host or len(host) > 255 or " " in host:
        problems.append("Give the IMAP server name, e.g. imap.example.com")
    if security not in ("ssl", "starttls"):
        problems.append("Security is SSL or STARTTLS")
    port = int(data.get("imap_port") or (993 if security == "ssl" else 143))
    if not 1 <= port <= 65535:
        problems.append("The port is between 1 and 65535")
    existing = await get(session)
    keep_password = not password and existing is not None and existing.provider == "imap" \
        and existing.secret_hint != "-"
    if not password and not keep_password:
        problems.append("Give the mailbox password (an app password where the provider "
                        "asks for one)")
    if problems:
        raise AppError("; ".join(problems))
    if keep_password:
        password = unseal(existing).get("password") or ""
    probe = Mailbox(id=existing.id if existing else uuid.uuid4(), tenant_id=tenant_id,
                    provider="imap", address=address, imap_host=host, imap_port=port,
                    imap_security=security, username=(data.get("username") or "").strip()
                    or address, folder=(data.get("folder") or "INBOX").strip()[:100])
    try:
        await imap.test(probe, password)
    except MailError as e:
        raise AppError(str(e)) from e
    mb = await _row(session, tenant_id, "imap", address, actor)
    mb.id = mb.id or probe.id
    await session.flush()
    mb.imap_host, mb.imap_port, mb.imap_security = host, port, security
    mb.username, mb.folder = probe.username, probe.folder
    seal(mb, {"password": password})
    mb.secret_hint = secrets.hint(password)
    await _audit(session, mb, "mailbox.connected", actor, address=address, host=host)
    return mb


async def finish_oauth(session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID,
                       provider: str, code: str) -> Mailbox:
    tokens = await oauth.exchange(provider, code)
    if provider == "gmail":
        address = normalize_email((await gmail.profile(tokens["access_token"]))
                                  .get("emailAddress"))
    else:
        me = await microsoft.me(tokens["access_token"])
        address = normalize_email(me.get("mail") or me.get("userPrincipalName"))
    if not address:
        raise MailError("Could not tell which mailbox was signed in")
    actor = f"user:{user_id}"
    mb = await _row(session, tenant_id, provider, address, actor)
    await session.flush()
    mb.imap_host = mb.imap_port = mb.imap_security = mb.username = None
    mb.folder = "INBOX"
    seal(mb, tokens)
    mb.secret_hint = secrets.hint(tokens["refresh_token"])
    await _audit(session, mb, "mailbox.connected", actor, address=address)
    try:
        await _renew_push(mb, tokens["access_token"])
    except MailError as e:  # push is a bonus; checking every minute still works
        log.info("push not started for %s: %s", provider, e)
    return mb


async def disconnect(session: AsyncSession, mb: Mailbox, actor: str) -> None:
    seal(mb, {})
    mb.secret_hint, mb.status, mb.cursor = "-", "paused", {}
    mb.push_ref = mb.push_expires_at = mb.push_secret_hash = None
    mb.updated_by, mb.updated_at = actor, datetime.now(UTC)
    await _audit(session, mb, "mailbox.disconnected", actor)


async def pause(session: AsyncSession, mb: Mailbox, actor: str, on: bool) -> None:
    if mb.secret_hint == "-":
        raise AppError("Connect the mailbox first")
    mb.status = "paused" if on else "active"
    if not on:
        mb.failures, mb.poll_requested_at = 0, datetime.now(UTC)
    mb.updated_by, mb.updated_at = actor, datetime.now(UTC)
    await _audit(session, mb, "mailbox.paused" if on else "mailbox.resumed", actor)


async def _renew_push(mb: Mailbox, token: str) -> None:
    if mb.provider == "gmail":
        ref, expires = await gmail.watch(token)
        if ref:
            mb.push_ref, mb.push_expires_at = f"gmail:{mb.address}", expires
    elif mb.provider == "microsoft":
        got = await microsoft.subscribe(token, mb.tenant_id)
        if got:
            sub_id, expires, secret = got
            mb.push_ref, mb.push_expires_at = sub_id, expires
            mb.push_secret_hash = hashlib.sha256(secret.encode()).hexdigest()


async def _alert(session: AsyncSession, mb: Mailbox, why: str) -> None:
    from app.approvals.chain import people
    from app.approvals.notify import tell
    from app.core.permissions import ALL

    everyone = await people(session)
    to = {uid for uid, p in everyone.items()
          if ALL in p.permissions or "enquiries.manage" in p.permissions}
    if to:
        await tell(session, mb.tenant_id, to, "Forge cannot read the sales mailbox", why,
                   "/enquiries/setup?tab=mailbox", f"mailbox-{mb.id}")


async def check(session: AsyncSession, mb: Mailbox, now: datetime | None = None
                ) -> dict[str, Any]:
    """Read what arrived since the last check and keep it (app/enquiries/intake.py)."""
    from app.enquiries import intake

    now = now or datetime.now(UTC)
    mb.last_poll_at = now
    try:
        secret = unseal(mb)
        if mb.provider == "imap":
            batch = await imap.check(mb, secret, dict(mb.cursor or {}))
        else:
            token, fresh = await oauth.access_token(mb.provider, secret)
            if fresh is not secret:
                seal(mb, fresh)
            mod = gmail if mb.provider == "gmail" else microsoft
            batch = await mod.check(mb, token, dict(mb.cursor or {}))
            if mb.push_ref and mb.push_expires_at and mb.push_expires_at - now < RENEW_BEFORE:
                try:
                    await _renew_push(mb, token)
                except MailError:
                    mb.push_ref = None
    except (MailError, secrets.CredentialKeyError) as e:
        signed_out = isinstance(e, secrets.CredentialKeyError) or getattr(e, "signed_out", False)
        mb.failures = min(mb.failures + 1, 999)
        mb.last_error, mb.last_error_at = str(e)[:500], now
        if signed_out and mb.status != "broken":
            mb.status = "broken"
            await _alert(session, mb, str(e)[:300])
        elif mb.failures == FAILURES_TO_ALERT:
            await _alert(session, mb, f"The last {FAILURES_TO_ALERT} checks failed: "
                                      f"{str(e)[:200]}")
        return {"error": str(e)[:300]}
    kept = 0
    for f in batch.emails:
        _, new = await intake.ingest(session, mb.tenant_id, f.raw, provider=mb.provider,
                                     provider_id=f.provider_id, mailbox_id=mb.id,
                                     received_at=f.received_at)
        kept += int(new)
    mb.cursor = batch.cursor
    mb.last_ok_at, mb.failures, mb.last_error = now, 0, None
    mb.poll_requested_at = now if batch.more else None
    return {"kept": kept, "more": batch.more}


async def check_all(now: datetime | None = None) -> dict[str, int]:
    from app.db import get_sessionmaker, tenant_session

    now = now or datetime.now(UTC)
    every = timedelta(seconds=get_settings().mail_poll_seconds)
    async with get_sessionmaker()() as s:
        tenant_ids = list(await s.scalars(select(Tenant.id)))
    total = {"checked": 0, "kept": 0, "errors": 0}
    for tid in tenant_ids:
        try:
            async with tenant_session(tid) as s:
                mb = await s.scalar(select(Mailbox).with_for_update(skip_locked=True))
                if mb is None or mb.status != "active" or mb.secret_hint == "-":
                    continue
                due = mb.poll_requested_at is not None or mb.last_poll_at is None or \
                    mb.last_poll_at <= now - every
                if not due:
                    continue
                out = await check(s, mb, now)
                total["checked"] += 1
                total["kept"] += out.get("kept", 0)
                total["errors"] += int("error" in out)
        except Exception:  # one company's failure must not stop the others
            log.exception("mailbox check for tenant %s failed", tid)
            total["errors"] += 1
    return total


async def pushed_gmail(address: str) -> None:
    """Gmail says mail arrived for this address: check it soon (nothing else trusted)."""
    from app.db import get_sessionmaker, tenant_session

    address = normalize_email(address) or ""
    async with get_sessionmaker()() as s:
        tenant_ids = list(await s.scalars(select(Tenant.id)))
    for tid in tenant_ids:
        async with tenant_session(tid) as s:
            mb = await s.scalar(select(Mailbox).where(Mailbox.provider == "gmail",
                                                      Mailbox.address == address))
            if mb is not None and mb.status == "active" and mb.poll_requested_at is None:
                mb.poll_requested_at = datetime.now(UTC)


async def pushed_microsoft(tenant_id: uuid.UUID, subscription_id: str, client_state: str
                           ) -> bool:
    from app.db import tenant_session

    async with tenant_session(tenant_id) as s:
        mb = await s.scalar(select(Mailbox).where(Mailbox.provider == "microsoft",
                                                  Mailbox.push_ref == subscription_id))
        if mb is None or not mb.push_secret_hash or hashlib.sha256(
                (client_state or "").encode()).hexdigest() != mb.push_secret_hash:
            return False
        if mb.status == "active" and mb.poll_requested_at is None:
            mb.poll_requested_at = datetime.now(UTC)
        return True


def rotate(mb: Mailbox) -> bool:
    """Re-encrypt the stored secret under the current CREDENTIAL_KEYS key."""
    if not mb.secret_enc or not secrets.needs_rotation(mb.secret_enc):
        return False
    seal(mb, unseal(mb))
    return True


async def rotate_all() -> int:
    """Re-encrypt every company's stored mailbox secret (CLI rotate-credentials)."""
    from app.db import get_sessionmaker, tenant_session

    async with get_sessionmaker()() as s:
        tenant_ids = list(await s.scalars(select(Tenant.id)))
    n = 0
    for tid in tenant_ids:
        async with tenant_session(tid) as s:
            mb = await s.scalar(select(Mailbox).with_for_update())
            if mb is not None and rotate(mb):
                n += 1
                await _audit(s, mb, "mailbox.credentials.reencrypted",
                             "system:rotate-credentials",
                             key_id=secrets.key_ring().current)
    return n
