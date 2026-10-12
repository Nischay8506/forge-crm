"""Signing in to Gmail and Microsoft 365 with OAuth (decisions N3 A, N4 A).

Forge has one app registration with Google and one with Microsoft (CognitBotz owns
them; their ids and secrets go in backend/.env). A company admin presses "Connect",
signs in at Google or Microsoft and allows Forge to read mail; Forge keeps the
refresh token encrypted (CREDENTIAL_KEYS) and never shows, logs or audits it.
Forge asks only to read mail: it sends through the company's email account (Step 6).

Until Google reviews the app for its restricted Gmail scope, Google keeps it in
testing mode: at most 100 users, and they sign in again every 7 days.
"""

import secrets as _random
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import get_settings
from app.core.security import _create_token, verify_token
from app.enquiries.mail.base import MailError

# Tests replace this with httpx.MockTransport.
transport: httpx.AsyncBaseTransport | None = None


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=30, transport=transport)


def _endpoints(provider: str) -> dict[str, Any]:
    s = get_settings()
    if provider == "gmail":
        return {"auth": "https://accounts.google.com/o/oauth2/v2/auth",
                "token": "https://oauth2.googleapis.com/token",
                "scope": "https://www.googleapis.com/auth/gmail.readonly openid email",
                "extra": {"access_type": "offline", "prompt": "consent"},
                "id": s.google_client_id, "secret": s.google_client_secret}
    if provider == "microsoft":
        base = f"https://login.microsoftonline.com/{s.microsoft_tenant}/oauth2/v2.0"
        return {"auth": f"{base}/authorize", "token": f"{base}/token",
                "scope": "offline_access https://graph.microsoft.com/Mail.Read "
                         "https://graph.microsoft.com/User.Read openid email",
                "extra": {"prompt": "select_account"},
                "id": s.microsoft_client_id, "secret": s.microsoft_client_secret}
    raise MailError("Unknown provider")


def configured(provider: str) -> bool:
    e = _endpoints(provider)
    return bool(e["id"] and e["secret"])


def missing(provider: str) -> str:
    name = {"gmail": "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET",
            "microsoft": "MICROSOFT_CLIENT_ID and MICROSOFT_CLIENT_SECRET"}[provider]
    return (f"Connecting {'Gmail' if provider == 'gmail' else 'Microsoft 365'} needs Forge's "
            f"app registration: {name} in backend/.env (decision N4 A). IMAP works without it.")


def redirect_uri(provider: str) -> str:
    base = get_settings().public_base_url.rstrip("/")
    return f"{base}/api/v1/public/mail/oauth/{provider}/callback"


def state_for(tenant_id: uuid.UUID, user_id: uuid.UUID, provider: str) -> str:
    """A signed, short-lived note of who started signing in (checked on the way back)."""
    return _create_token({"sub": str(user_id), "tid": str(tenant_id), "prv": provider,
                          "n": _random.token_hex(8)}, "mail_oauth", timedelta(minutes=15))


def read_state(state: str, provider: str) -> tuple[uuid.UUID, uuid.UUID] | None:
    p = verify_token(state, "mail_oauth")
    if p is None or p.get("prv") != provider:
        return None
    return uuid.UUID(p["tid"]), uuid.UUID(p["sub"])


def auth_url(provider: str, state: str) -> str:
    e = _endpoints(provider)
    if not configured(provider):
        raise MailError(missing(provider))
    return e["auth"] + "?" + urlencode({
        "client_id": e["id"], "redirect_uri": redirect_uri(provider), "response_type": "code",
        "scope": e["scope"], "state": state, **e["extra"]})


def _tokens(data: dict[str, Any], old: dict[str, Any] | None = None) -> dict[str, Any]:
    out = dict(old or {})
    out["access_token"] = data["access_token"]
    out["expires_at"] = int(time.time()) + int(data.get("expires_in") or 3600) - 60
    if data.get("refresh_token"):
        out["refresh_token"] = data["refresh_token"]
    return out


async def exchange(provider: str, code: str) -> dict[str, Any]:
    e = _endpoints(provider)
    async with client() as c:
        r = await c.post(e["token"], data={
            "client_id": e["id"], "client_secret": e["secret"], "code": code,
            "redirect_uri": redirect_uri(provider), "grant_type": "authorization_code",
            "scope": e["scope"]})
    if r.status_code != 200:
        raise MailError(f"{'Google' if provider == 'gmail' else 'Microsoft'} did not accept "
                        f"the sign-in ({r.status_code})")
    data = r.json()
    if not data.get("refresh_token"):
        raise MailError("The sign-in did not allow Forge to keep reading; connect again and "
                        "allow access")
    return _tokens(data)


async def access_token(provider: str, secret: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """A working access token, refreshing it when it is about to run out. Returns the
    (possibly new) secret to keep."""
    if secret.get("access_token") and int(secret.get("expires_at") or 0) > time.time():
        return secret["access_token"], secret
    e = _endpoints(provider)
    if not configured(provider):
        raise MailError(missing(provider))
    async with client() as c:
        r = await c.post(e["token"], data={
            "client_id": e["id"], "client_secret": e["secret"],
            "refresh_token": secret.get("refresh_token") or "", "grant_type": "refresh_token"})
    if r.status_code in (400, 401):
        raise MailError("The sign-in to the mailbox has run out or was withdrawn; connect "
                        "it again", signed_out=True)
    if r.status_code != 200:
        raise MailError(f"Could not renew the sign-in ({r.status_code})")
    new = _tokens(r.json(), secret)
    return new["access_token"], new


def now() -> datetime:
    return datetime.now(UTC)
