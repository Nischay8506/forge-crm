"""ERP connections (F12.6): stored credentials, the adapter for a connection, the
minute connection check, and the health that drives alerts (decision E7, safeguard 5).

Credentials are encrypted with app/core/secrets.py, bound to the tenant, connection
and field. They are decrypted only to build an adapter, and never logged, audited or
returned: audit records and screens carry the key's last four characters.
"""

import hashlib
import secrets as _random
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import chain
from app.config import get_settings
from app.core import secrets
from app.core.errors import AppError, ValidationFailed
from app.integrations.adapter import CAUSES, RETRYABLE, ConnectionCheck, ConnectorError
from app.integrations.alerts import Problem, sync_alert
from app.integrations.contracts import ENTITY_LABELS, Entity
from app.integrations.erpnext.adapter import ERPNextAdapter, ERPNextSettings
from app.models import ErpConnection, SyncFlow

KINDS: dict[str, type[ERPNextAdapter]] = {"erpnext": ERPNextAdapter}
# Customers first, so orders and invoices find their account on the first sync.
ENTITY_ORDER = (Entity.customer, Entity.item, Entity.price, Entity.order, Entity.invoice,
                Entity.receipt, Entity.stock, Entity.workstation, Entity.bom)
SETTING_KEYS = ("time_zone", "company", "host_header")

# Plain words for alert titles, and what a person can do about each cause.
CAUSE_TITLES = {
    "auth_rejected": "ERPNext rejected Forge's API key",
    "permission_denied": "Forge's ERPNext user is missing a permission",
    "unreachable": "ERPNext cannot be reached",
    "timeout": "ERPNext is not answering in time",
    "rate_limited": "ERPNext is asking Forge to slow down",
    "server_error": "ERPNext is failing on Forge's requests",
    "bad_response": "ERPNext sent something Forge could not read",
    "rejected": "ERPNext refused a record",
    "not_configured": "The connection is missing a setting",
}
CAUSE_FIXES = {
    "auth_rejected": "In ERPNext open the integration user, generate a new API key and secret "
                     "(API Access), and enter them on this connection's Settings and login tab.",
    "permission_denied": "Give the integration user the roles Sales User, Accounts User, Stock "
                         "User, Sales Master Manager and Manufacturing User in ERPNext.",
    "unreachable": "Check that ERPNext is running and that the address on this connection is "
                   "right.",
    "timeout": "Check that ERPNext is running normally; Forge keeps retrying.",
    "server_error": "Check ERPNext's error log; Forge keeps retrying.",
    "not_configured": "Open the connection and fill in the missing setting.",
}

# Failures that are about reaching or logging in to the ERP, not about the records.
CONNECTION_CAUSES = frozenset({"auth_rejected", "permission_denied", "unreachable", "timeout",
                               "rate_limited", "server_error", "not_configured"})

# Test hook: base_url -> transport, so tests can point a stored connection at a fake.
_TRANSPORTS: dict[str, httpx.AsyncBaseTransport] = {}


def use_transport(base_url: str, transport: httpx.AsyncBaseTransport | None) -> None:
    if transport is None:
        _TRANSPORTS.pop(base_url.rstrip("/"), None)
    else:
        _TRANSPORTS[base_url.rstrip("/")] = transport


# --- credentials ----------------------------------------------------------------------

def _context(conn: ErpConnection, field: str) -> str:
    return f"{conn.tenant_id}:{conn.id}:{field}"


def _set_credentials(conn: ErpConnection, api_key: str, api_secret: str) -> None:
    api_key, api_secret = api_key.strip(), api_secret.strip()
    if not api_key or not api_secret:
        raise ValidationFailed(["API key and API secret are both required"])
    try:
        conn.api_key_enc = secrets.encrypt(api_key, context=_context(conn, "api_key"))
        conn.api_secret_enc = secrets.encrypt(api_secret, context=_context(conn, "api_secret"))
    except secrets.CredentialKeyError as e:
        raise AppError(f"Forge cannot store ERP credentials yet: {e}") from e
    conn.secret_hint = secrets.hint(api_key)


def _credentials(conn: ErpConnection) -> tuple[str, str]:
    return (secrets.decrypt(conn.api_key_enc, context=_context(conn, "api_key")),
            secrets.decrypt(conn.api_secret_enc, context=_context(conn, "api_secret")))


def _clean_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValidationFailed(["Address must start with http:// or https://, for example "
                                "https://erp.example.com"])
    if parsed.username or parsed.password:
        raise ValidationFailed(["Put the API key and secret in their own fields, not in the "
                                "address"])
    return url


def _clean_settings(settings: dict[str, Any] | None) -> dict[str, Any]:
    out = {k: (v.strip() if isinstance(v, str) else v) for k, v in (settings or {}).items()
           if k in SETTING_KEYS and v not in (None, "")}
    if "time_zone" in out:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(out["time_zone"])
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValidationFailed([f"Unknown time zone {out['time_zone']!r}"]) from e
    return out


def make_adapter(kind: str, base_url: str, api_key: str, api_secret: str,
                 settings: dict[str, Any] | None = None) -> ERPNextAdapter:
    if kind not in KINDS:
        raise ValidationFailed([f"Unknown ERP kind {kind!r}"])
    s = _clean_settings(settings)
    url = _clean_url(base_url)
    return KINDS[kind](
        ERPNextSettings(url, api_key.strip(), api_secret.strip(),
                        time_zone=s.get("time_zone", "Asia/Kolkata"), company=s.get("company"),
                        host_header=s.get("host_header")),
        transport=_TRANSPORTS.get(url),
    )


def adapter_for(conn: ErpConnection) -> ERPNextAdapter:
    try:
        key, secret = _credentials(conn)
    except secrets.CredentialKeyError as e:
        raise ConnectorError("not_configured", f"Stored credentials cannot be used: {e}") from e
    return make_adapter(conn.kind, conn.base_url, key, secret, conn.settings)


def public(conn: ErpConnection) -> dict[str, Any]:
    """What screens and audit records may show about a connection."""
    return {"id": str(conn.id), "name": conn.name, "kind": conn.kind, "base_url": conn.base_url,
            "settings": conn.settings, "key_hint": conn.secret_hint, "status": conn.status}


# --- creating and changing --------------------------------------------------------------

async def test_login(kind: str, base_url: str, api_key: str, api_secret: str,
                     settings: dict[str, Any] | None = None) -> ConnectionCheck:
    """The wizard's "Test connection": a full check before anything is saved."""
    adapter = make_adapter(kind, base_url, api_key, api_secret, settings)
    try:
        return await adapter.check(full=True)
    finally:
        await adapter.aclose()


async def create_connection(
    session: AsyncSession, tenant_id: uuid.UUID, *, kind: str, name: str, base_url: str,
    api_key: str, api_secret: str, settings: dict[str, Any] | None, actor: str,
    entities: list[str] | None = None,
) -> ErpConnection:
    name = (name or "").strip()
    if not name:
        raise ValidationFailed(["Give the connection a name, for example 'ERPNext Pune'"])
    if kind not in KINDS:
        raise ValidationFailed([f"Unknown ERP kind {kind!r}"])
    wanted = [Entity(e) for e in entities] if entities else list(ENTITY_ORDER)
    conn = ErpConnection(id=uuid.uuid4(), tenant_id=tenant_id, kind=kind, name=name,
                         base_url=_clean_url(base_url), settings=_clean_settings(settings),
                         status="active", created_by=actor, updated_by=actor)
    _set_credentials(conn, api_key, api_secret)
    session.add(conn)
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as e:
        raise ValidationFailed([f"A connection named {name!r} already exists"]) from e
    for entity in ENTITY_ORDER:
        session.add(SyncFlow(tenant_id=tenant_id, connection_id=conn.id, entity=entity.value,
                             enabled=entity in wanted, state={}))
    await session.flush()
    await chain.append(session, tenant_id, "integration.connection.created", actor=actor,
                       record_type="erp_connection", record_id=conn.id,
                       data={**public(conn), "entities": [e.value for e in wanted]})
    return conn


async def update_connection(session: AsyncSession, conn: ErpConnection, changes: dict[str, Any],
                            actor: str) -> ErpConnection:
    before = public(conn)
    if "name" in changes and changes["name"] is not None:
        if not changes["name"].strip():
            raise ValidationFailed(["Name cannot be empty"])
        conn.name = changes["name"].strip()
    if changes.get("base_url"):
        conn.base_url = _clean_url(changes["base_url"])
    if "settings" in changes and changes["settings"] is not None:
        conn.settings = _clean_settings(changes["settings"])
    if changes.get("status"):
        if changes["status"] not in ("active", "paused"):
            raise ValidationFailed(["status must be active or paused"])
        conn.status = changes["status"]
    after = public(conn)
    if after == before:
        return conn
    conn.updated_by = actor
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as e:
        raise ValidationFailed([f"A connection named {conn.name!r} already exists"]) from e
    await chain.append(session, conn.tenant_id, "integration.connection.changed", actor=actor,
                       record_type="erp_connection", record_id=conn.id,
                       data={"before": {k: v for k, v in before.items() if after[k] != v},
                             "after": {k: v for k, v in after.items() if before[k] != v}})
    return conn


async def change_credentials(session: AsyncSession, conn: ErpConnection, api_key: str,
                             api_secret: str, actor: str) -> ErpConnection:
    old_hint = conn.secret_hint
    _set_credentials(conn, api_key, api_secret)
    conn.updated_by = actor
    await chain.append(session, conn.tenant_id, "integration.credentials.changed", actor=actor,
                       record_type="erp_connection", record_id=conn.id,
                       data={"connection": conn.name, "key_hint_before": old_hint,
                             "key_hint_after": conn.secret_hint})
    return conn


async def set_flow(session: AsyncSession, flow: SyncFlow, enabled: bool, actor: str) -> None:
    if flow.enabled == enabled:
        return
    flow.enabled = enabled
    await chain.append(session, flow.tenant_id, "integration.flow.changed", actor=actor,
                       record_type="erp_connection", record_id=flow.connection_id,
                       data={"entity": flow.entity, "enabled": enabled})


def new_webhook_token() -> tuple[str, str]:
    token = "fwh_" + _random.token_urlsafe(32)
    return token, hashlib.sha256(token.encode()).hexdigest()


async def issue_webhook_token(session: AsyncSession, conn: ErpConnection, actor: str) -> str:
    """A new secret for ERPNext's webhooks (shown once). The old one stops working."""
    token, digest = new_webhook_token()
    conn.webhook_token_hash = digest
    await chain.append(session, conn.tenant_id, "integration.webhook.token_issued", actor=actor,
                       record_type="erp_connection", record_id=conn.id,
                       data={"connection": conn.name})
    return token


def webhook_token_ok(conn: ErpConnection, token: str | None) -> bool:
    if not token or not conn.webhook_token_hash:
        return False
    digest = hashlib.sha256(token.encode()).hexdigest()
    return _random.compare_digest(digest, conn.webhook_token_hash)


# --- checking and health ----------------------------------------------------------------

async def check_connection(session: AsyncSession, conn: ErpConnection, *, full: bool = False,
                           now: datetime | None = None) -> ConnectionCheck:
    """Log in and ask who we are (the minute check, decision E6), store the result and
    bring the connection's alert up to date."""
    now = now or datetime.now(UTC)
    try:
        adapter = adapter_for(conn)
    except ConnectorError as e:
        check = ConnectionCheck(ok=False, cause=e.cause, message=e.message)
    else:
        try:
            check = await adapter.check(full=full)
        finally:
            await adapter.aclose()
    conn.last_check_at, conn.last_check_ok = now, check.ok
    conn.last_check_cause = check.cause
    conn.last_check_message = (check.message or "")[:500]
    if check.ok:
        conn.last_ok_at = now
        conn.about = {**conn.about, **check.about}
        tz = check.about.get("time_zone")
        if (full and tz and check.about.get("time_zone_source") == "erpnext"
                and not (conn.settings or {}).get("time_zone")):
            # Nobody set one: use ERPNext's own, which its timestamps are written in.
            conn.settings = {**(conn.settings or {}), "time_zone": tz}
        # The login works again: re-run flows that failed only because it did not, so
        # their failures clear within a minute instead of at the next scheduled read.
        for f in await flows_of(session, conn):
            if (f.enabled and f.consecutive_failures and f.last_error_cause in CONNECTION_CAUSES
                    and f.run_requested_at is None and f.running_since is None):
                f.run_requested_at = now
    await refresh_health(session, conn, now=now)
    return check


def _since(then: datetime | None, now: datetime) -> timedelta | None:
    return now - then if then else None


def _ago(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 90:
        return f"{minutes} minutes"
    hours = minutes // 60
    return f"{hours} hours" if hours < 48 else f"{hours // 24} days"


def problems_for(conn: ErpConnection, flows: list[SyncFlow], now: datetime) -> list[Problem]:
    """Everything wrong with a connection right now (empty when healthy or paused)."""
    if conn.status != "active":
        return []
    cfg = get_settings()
    out: list[Problem] = []
    healthy_since = conn.last_ok_at or conn.created_at
    if conn.last_check_ok is False and conn.last_check_cause:
        cause = conn.last_check_cause
        down_for = _since(healthy_since, now) or timedelta(0)
        # A revoked key or a missing permission is reported at once; a network blip
        # only after it has lasted a few minutes.
        if (cause not in RETRYABLE
                or down_for >= timedelta(minutes=cfg.integration_unreachable_alert_minutes)):
            out.append(Problem(
                "check_failed", 100 if cause not in RETRYABLE else 80,
                CAUSE_TITLES.get(cause, CAUSES.get(cause, cause)),
                conn.last_check_message or CAUSES.get(cause, cause),
                CAUSE_FIXES.get(cause), {"cause": cause}))
    lag = timedelta(minutes=cfg.integration_lag_alert_minutes)
    stuck = timedelta(minutes=cfg.integration_stuck_run_minutes)
    for f in flows:
        if not f.enabled:
            continue
        label = ENTITY_LABELS[Entity(f.entity)]
        if f.running_since and now - f.running_since > stuck:
            out.append(Problem("stuck", 60, f"{label} sync has been running too long",
                               f"{label}: a sync started {_ago(now - f.running_since)} ago "
                               "and has not finished.", "Forge restarts it automatically; if "
                               "this stays, check the worker is running.",
                               {"entity": f.entity}))
        failing = f.last_error_cause and (
            f.last_error_cause not in RETRYABLE
            or f.consecutive_failures >= cfg.integration_failures_before_alert)
        if failing and f.last_error_cause:
            out.append(Problem(
                "flow_failing", 90 if f.last_error_cause not in RETRYABLE else 70,
                f"{label} sync is failing",
                f"{label}: the last {f.consecutive_failures} sync(s) failed. "
                f"{f.last_error_message or CAUSES.get(f.last_error_cause, '')}",
                CAUSE_FIXES.get(f.last_error_cause), {"entity": f.entity,
                                                      "cause": f.last_error_cause}))
        behind = _since(f.last_success_at or f.created_at, now)
        if behind is not None and behind > lag:
            out.append(Problem(
                "lagging", 50, f"{label} are out of date",
                f"{label}: last brought up to date {_ago(behind)} ago" if f.last_success_at
                else f"{label}: not read from ERPNext yet ({_ago(behind)} since set up)",
                None, {"entity": f.entity}))
    # A rejected key or missing permission explains the flows failing and falling
    # behind for the same reason; keep the card to the cause.
    top = next((p for p in out if p.code == "check_failed"), None)
    if top is not None and top.severity == 100:
        out = [p for p in out if p.code in ("check_failed", "stuck")
               or (p.code == "flow_failing" and p.data.get("cause") != top.data["cause"])]
    return out


async def flows_of(session: AsyncSession, conn: ErpConnection) -> list[SyncFlow]:
    order = {e.value: n for n, e in enumerate(ENTITY_ORDER)}
    flows = list(await session.scalars(select(SyncFlow).where(
        SyncFlow.connection_id == conn.id)))
    return sorted(flows, key=lambda f: order.get(f.entity, 99))


async def refresh_health(session: AsyncSession, conn: ErpConnection, *,
                         now: datetime | None = None) -> list[Problem]:
    now = now or datetime.now(UTC)
    problems = problems_for(conn, await flows_of(session, conn), now)
    await sync_alert(session, conn.tenant_id, key=f"conn:{conn.id}",
                     record_type="erp_connection", record_id=conn.id, subject=conn.name,
                     problems=problems)
    return problems


# --- key rotation -------------------------------------------------------------------------

async def rotate_all_credentials() -> dict[str, Any]:
    """Re-encrypt every stored credential under the current key (CLI rotate-credentials).
    Each tenant's audit chain records that its connections were re-encrypted."""
    from app.db import system_session, tenant_session

    ring = secrets.key_ring()
    counts = {"rotated": 0, "already": 0, "current": ring.current}
    async with system_session() as s:
        rows = [(c.id, c.tenant_id) for c in await s.scalars(select(ErpConnection))]
    for conn_id, tenant_id in rows:
        async with tenant_session(tenant_id) as s:
            conn = await s.get(ErpConnection, conn_id, with_for_update=True)
            if conn is None:
                continue
            changed = False
            for field in ("api_key", "api_secret"):
                token = getattr(conn, f"{field}_enc")
                if secrets.needs_rotation(token):
                    setattr(conn, f"{field}_enc",
                            secrets.rotate(token, context=_context(conn, field)))
                    counts["rotated"] += 1
                    changed = True
                else:
                    counts["already"] += 1
            if changed:
                await chain.append(s, tenant_id, "integration.credentials.reencrypted",
                                   actor="system:rotate-credentials",
                                   record_type="erp_connection", record_id=conn.id,
                                   data={"connection": conn.name, "key_id": ring.current})
    return counts
