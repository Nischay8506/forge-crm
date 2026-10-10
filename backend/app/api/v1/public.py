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
