"""IMAP (any provider that is not Gmail or Microsoft 365, decision N3 A).

The cursor is the folder's UIDVALIDITY and the last UID read. The first check only
notes where the folder is (mail from before the connection is left alone). Messages
are fetched with BODY.PEEK[] so they stay unread for the people using the mailbox.
"""

import asyncio
import imaplib
import re
import ssl
from collections.abc import Callable
from typing import Any

from app.enquiries.mail.base import Batch, Fetched, MailError
from app.models import Mailbox

PER_CHECK = 25
TIMEOUT = 30

# Tests replace this with a fake server.
connect: Callable[..., Any] | None = None


def _open(mb: Mailbox, password: str) -> Any:
    if connect is not None:
        return connect(mb.imap_host, mb.imap_port, mb.imap_security)
    ctx = ssl.create_default_context()
    if mb.imap_security == "ssl":
        conn = imaplib.IMAP4_SSL(mb.imap_host, mb.imap_port or 993, ssl_context=ctx,
                                 timeout=TIMEOUT)
    else:
        conn = imaplib.IMAP4(mb.imap_host, mb.imap_port or 143, timeout=TIMEOUT)
        conn.starttls(ssl_context=ctx)
    try:
        conn.login(mb.username or mb.address, password)
    except imaplib.IMAP4.error as e:
        raise MailError("The mail server did not accept the user name and password",
                        signed_out=True) from e
    return conn


def _status(conn: Any, folder: str) -> tuple[int, int]:
    typ, data = conn.status(_quote(folder), "(UIDVALIDITY UIDNEXT)")
    if typ != "OK" or not data:
        raise MailError(f"The folder {folder} was not found")
    text = data[0].decode() if isinstance(data[0], bytes) else str(data[0])
    validity = int(re.search(r"UIDVALIDITY (\d+)", text).group(1))
    nxt = int(re.search(r"UIDNEXT (\d+)", text).group(1))
    return validity, nxt


def _quote(folder: str) -> str:
    return '"' + folder.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _check(mb: Mailbox, password: str, cursor: dict[str, Any]) -> Batch:
    conn = _open(mb, password)
    try:
        validity, nxt = _status(conn, mb.folder or "INBOX")
        if not cursor or cursor.get("uidvalidity") != validity:
            # First check, or the server renumbered the folder: start from now.
            return Batch([], {"uidvalidity": validity, "last_uid": nxt - 1})
        last = int(cursor.get("last_uid") or 0)
        if nxt - 1 <= last:
            return Batch([], cursor)
        typ, _ = conn.select(_quote(mb.folder or "INBOX"), readonly=True)
        if typ != "OK":
            raise MailError(f"The folder {mb.folder} could not be opened")
        typ, data = conn.uid("SEARCH", None, f"UID {last + 1}:*")
        uids = sorted(int(u) for u in (data[0] or b"").split() if int(u) > last)
        batch = Batch([], dict(cursor), more=len(uids) > PER_CHECK)
        for uid in uids[:PER_CHECK]:
            typ, parts = conn.uid("FETCH", str(uid), "(BODY.PEEK[])")
            raw = next((p[1] for p in parts or [] if isinstance(p, tuple) and len(p) > 1), None)
            if typ == "OK" and raw:
                batch.emails.append(Fetched(f"{validity}:{uid}", raw))
            batch.cursor["last_uid"] = uid
        return batch
    finally:
        try:
            conn.logout()
        except Exception:  # noqa: BLE001
            pass


async def check(mb: Mailbox, secret: dict[str, Any], cursor: dict[str, Any]) -> Batch:
    try:
        return await asyncio.to_thread(_check, mb, secret.get("password") or "", cursor)
    except MailError:
        raise
    except (OSError, imaplib.IMAP4.error, ssl.SSLError, AttributeError, ValueError) as e:
        raise MailError(f"Could not read the mailbox: {type(e).__name__}: {e}"[:300]) from e


async def test(mb: Mailbox, password: str) -> str:
    """Sign in and look at the folder, without reading anything."""
    def run() -> str:
        conn = _open(mb, password)
        try:
            _status(conn, mb.folder or "INBOX")
            return "Signed in and found the folder"
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001
                pass
    try:
        return await asyncio.to_thread(run)
    except MailError:
        raise
    except (OSError, imaplib.IMAP4.error, ssl.SSLError) as e:
        raise MailError(f"Could not reach the mail server: {type(e).__name__}: {e}"[:300]) from e
