"""Pages customers open without logging in (Step 6): the unsubscribe link.

GET shows one button, so mail scanners that open every link do not unsubscribe
anyone; POST does it (the button, and mail apps' one-click unsubscribe through the
List-Unsubscribe-Post header). The link names the company and message and carries a
random secret checked against a hash on the message.
"""

import html

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.channels import consent as consent_mod
from app.channels import send
from app.db import tenant_session
from app.models import Contact, Message, Tenant

router = APIRouter(prefix="/public", tags=["public"])

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title>
<style>body{{font-family:system-ui,sans-serif;max-width:32rem;margin:3rem auto;padding:0 1rem;
color:#1c1917;background:#fafaf9}}h1{{font-size:1.4rem}}button{{font-size:1rem;padding:.6rem
1.2rem;border-radius:.4rem;border:0;background:#1c1917;color:#fff;cursor:pointer}}
p{{line-height:1.5}}</style></head><body><h1>{title}</h1>{body}</body></html>"""


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(PAGE.format(title=html.escape(title), body=body), status_code=status)


async def _find(token: str) -> tuple[Message, Tenant] | None:
    parsed = consent_mod.parse_link_token(token)
    if parsed is None:
        return None
    tenant_id, message_id, secret = parsed
    async with tenant_session(tenant_id) as s:
        msg = await s.get(Message, message_id)
        tenant = await s.get(Tenant, tenant_id)
        if not consent_mod.link_token_ok(msg, secret) or tenant is None:
            return None
        s.expunge_all()
        return msg, tenant


NOT_FOUND = ("This link is not valid any more",
             "<p>If you still get promotional emails from us, reply to one of them with the "
             "word STOP.</p>")


@router.get("/unsubscribe/{token}", response_class=HTMLResponse)
async def unsubscribe_page(token: str) -> HTMLResponse:
    found = await _find(token)
    if found is None:
        return _page(*NOT_FOUND, status=404)
    _, tenant = found
    name = html.escape(tenant.name)
    return _page(f"Stop promotional emails from {tenant.name}",
                 f"<p>Press the button and {name} will stop sending you promotional emails "
                 "and messages. Emails about your orders, invoices and replies to you will "
                 "still reach you.</p><form method=\"post\"><button type=\"submit\">"
                 "Unsubscribe</button></form>")


@router.post("/unsubscribe/{token}", response_class=HTMLResponse)
async def unsubscribe(token: str) -> HTMLResponse:
    found = await _find(token)
    if found is None:
        return _page(*NOT_FOUND, status=404)
    msg, tenant = found
    async with tenant_session(tenant.id) as s:
        contact = await s.get(Contact, msg.contact_id) if msg.contact_id else None
        if contact is not None and contact.status == "active":
            await send.handle_stop(s, tenant.id, contact, None, how="link")
    return _page("You are unsubscribed",
                 f"<p>{html.escape(tenant.name)} will not send you promotional emails any "
                 "more. Emails about your orders, invoices and replies to you will still "
                 "reach you.</p>")


# The sales mailbox (M1, decisions N3 A, N4 A) ---------------------------------------

@router.get("/mail/oauth/{provider}/callback")
async def mail_oauth_callback(provider: str, code: str | None = None, state: str | None = None,
                              error: str | None = None):
    """Google or Microsoft sends the person back here after they sign in."""
    from urllib.parse import quote

    from fastapi.responses import RedirectResponse

    from app.config import get_settings
    from app.enquiries.mail import oauth
    from app.enquiries.mail import service as mail
    from app.enquiries.mail.base import MailError

    back = get_settings().app_base_url.rstrip("/") + "/enquiries/setup?tab=mailbox"
    if provider not in ("gmail", "microsoft"):
        return RedirectResponse(f"{back}&error=" + quote("Unknown provider"), 303)
    who = oauth.read_state(state or "", provider)
    if who is None:
        return RedirectResponse(f"{back}&error=" + quote(
            "The sign-in link ran out or was not started here; press Connect again"), 303)
    if error or not code:
        return RedirectResponse(f"{back}&error=" + quote(
            "The sign-in was cancelled or not allowed"), 303)
    tenant_id, user_id = who
    try:
        async with tenant_session(tenant_id) as s:
            await mail.finish_oauth(s, tenant_id, user_id, provider, code)
    except MailError as e:
        return RedirectResponse(f"{back}&error=" + quote(str(e)[:200]), 303)
    return RedirectResponse(f"{back}&connected={provider}", 303)


@router.post("/mail/push/gmail", status_code=204)
async def mail_push_gmail(body: dict) -> None:
    """Google Pub/Sub: mail arrived for an address. Only makes Forge check sooner."""
    import base64
    import json

    from app.enquiries.mail import service as mail

    try:
        data = json.loads(base64.b64decode(body["message"]["data"]))
        address = str(data["emailAddress"])
    except (KeyError, TypeError, ValueError):
        return None
    await mail.pushed_gmail(address)
    return None


@router.post("/mail/push/microsoft/{tenant_id}")
async def mail_push_microsoft(tenant_id: str, body: dict | None = None,
                              validationToken: str | None = None):  # noqa: N803
    """Microsoft Graph: new mail in a subscribed inbox. When a subscription starts, Graph
    first checks this address by sending a token to echo back."""
    import uuid as _uuid

    from fastapi.responses import PlainTextResponse, Response

    from app.enquiries.mail import service as mail

    if validationToken:
        return PlainTextResponse(validationToken[:500])
    try:
        tid = _uuid.UUID(tenant_id)
    except ValueError:
        return Response(status_code=202)
    for n in (body or {}).get("value") or []:
        if isinstance(n, dict):
            await mail.pushed_microsoft(tid, str(n.get("subscriptionId") or ""),
                                        str(n.get("clientState") or ""))
    return Response(status_code=202)
