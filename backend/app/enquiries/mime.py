"""Reading a raw email (RFC 822 bytes) into what Forge needs: who, what, the thread,
the text, the attachments, and whether it is automatic mail (a bounce, an
out-of-office, a newsletter) or a copy of our own.

Only the standard library is used. HTML-only emails are turned into plain text;
nothing in an email is ever run or rendered.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from html.parser import HTMLParser

MSGID = re.compile(r"<[^<>\s]+>")


@dataclass
class Attachment:
    filename: str
    content_type: str
    data: bytes


@dataclass
class Bounce:
    original_message_id: str | None
    recipient: str | None
    status: str | None
    reason: str | None


@dataclass
class ParsedEmail:
    message_id: str | None
    in_reply_to: str | None
    references: list[str]
    from_address: str | None
    from_name: str | None
    to: list[str]
    cc: list[str]
    subject: str
    date: datetime | None
    text: str
    attachments: list[Attachment] = field(default_factory=list)
    auto_kind: str | None = None  # bounce | auto_reply | bulk
    bounce: Bounce | None = None

    @property
    def thread_root(self) -> str | None:
        """The first message of the conversation, as far as the headers say."""
        return (self.references[0] if self.references else None) or self.in_reply_to \
            or self.message_id


class _Text(HTMLParser):
    BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table",
             "blockquote", "section"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in ("script", "style", "head", "title"):
            self.skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n")
        elif tag == "td":
            self.out.append("\t")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "head", "title"):
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.out.append(data)


def html_to_text(html: str) -> str:
    p = _Text()
    try:
        p.feed(html)
        p.close()
    except Exception:  # noqa: BLE001 - broken HTML still gives what was read
        pass
    text = "".join(p.out).replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _ids(value: str | None) -> list[str]:
    return MSGID.findall(value or "")


def _one_id(value: str | None) -> str | None:
    found = _ids(value)
    return found[0][:500] if found else None


def _addresses(values: list[str]) -> list[str]:
    out = []
    for _, addr in getaddresses(values):
        addr = addr.strip().lower()
        if addr and "@" in addr and addr not in out:
            out.append(addr[:320])
    return out


def _header(msg: EmailMessage, name: str) -> str:
    try:
        v = msg.get(name)
    except Exception:  # noqa: BLE001 - a malformed header reads as empty
        return ""
    return str(v) if v is not None else ""


def _part_text(part: EmailMessage) -> str:
    try:
        return part.get_content()
    except Exception:  # noqa: BLE001 - unknown charset: decode leniently
        raw = part.get_payload(decode=True) or b""
        return raw.decode("utf-8", errors="replace")


def _auto_kind(msg: EmailMessage, from_address: str | None, subject: str) -> str | None:
    ctype = msg.get_content_type()
    sender = (from_address or "").split("@")[0]
    if ctype == "multipart/report" and "delivery-status" in _header(msg, "Content-Type"):
        return "bounce"
    if sender in ("mailer-daemon", "postmaster") and re.search(
            r"undeliver|delivery (status|failure)|returned mail|failure notice|not delivered",
            subject, re.I):
        return "bounce"
    auto = _header(msg, "Auto-Submitted").strip().lower()
    if (auto and auto != "no") or _header(msg, "X-Autoreply") or \
            _header(msg, "X-Autorespond") or \
            _header(msg, "Precedence").strip().lower() == "auto_reply" or \
            re.match(r"^\s*(automatic reply|auto(matic)?[- ]?(reply|response)|out of (the )?"
                     r"office|auto:)", subject, re.I):
        return "auto_reply"
    if _header(msg, "Precedence").strip().lower() in ("bulk", "list", "junk") or \
            _header(msg, "List-Id") or _header(msg, "List-Unsubscribe"):
        return "bulk"
    return None


def _bounce(msg: EmailMessage) -> Bounce:
    original = recipient = status = reason = None
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype == "message/delivery-status":
            text = ""
            payload = part.get_payload()
            if isinstance(payload, list):
                text = "\n".join(str(p) for p in payload)
            else:
                text = _part_text(part) if isinstance(part, EmailMessage) else str(payload)
            m = re.search(r"^Final-Recipient:\s*[^;]*;\s*(\S+)", text, re.M | re.I)
            recipient = m.group(1).strip().lower() if m else recipient
            m = re.search(r"^Status:\s*(\S+)", text, re.M | re.I)
            status = m.group(1) if m else status
            m = re.search(r"^Diagnostic-Code:\s*[^;]*;\s*(.+)$", text, re.M | re.I)
            reason = m.group(1).strip()[:300] if m else reason
        elif ctype in ("message/rfc822", "text/rfc822-headers"):
            payload = part.get_payload()
            if isinstance(payload, list) and payload:
                original = original or _one_id(_header(payload[0], "Message-ID"))
            else:
                m = re.search(r"^Message-ID:\s*(<[^>]+>)", _part_text(part), re.M | re.I)
                original = original or (m.group(1) if m else None)
    if original is None:
        body = "\n".join(_part_text(p) for p in msg.walk()
                         if p.get_content_type() == "text/plain")
        m = re.search(r"Message-ID:\s*(<[^>]+>)", body, re.I)
        original = m.group(1) if m else None
    return Bounce(original, recipient, status, reason)


def parse(raw: bytes) -> ParsedEmail:
    msg = message_from_bytes(raw, policy=policy.default)
    assert isinstance(msg, EmailMessage)
    name, addr = parseaddr(_header(msg, "From"))
    from_address = addr.strip().lower()[:320] or None
    subject = re.sub(r"\s+", " ", _header(msg, "Subject")).strip()[:500]
    try:
        date = parsedate_to_datetime(_header(msg, "Date"))
        date = date if date.tzinfo else date.replace(tzinfo=UTC)
    except (TypeError, ValueError, IndexError):
        date = None
    plain, html, attachments = [], [], []
    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        disposition = (part.get_content_disposition() or "").lower()
        filename = part.get_filename()
        if ctype.startswith("message/") or ctype == "text/rfc822-headers":
            continue
        if disposition == "attachment" or (filename and disposition != "inline") or (
                not ctype.startswith("text/") and ctype != "multipart/alternative"):
            data = part.get_payload(decode=True) or b""
            if data:
                attachments.append(Attachment(filename or f"attachment-{len(attachments) + 1}",
                                              ctype, data))
            continue
        if ctype == "text/plain":
            plain.append(_part_text(part))
        elif ctype == "text/html":
            html.append(_part_text(part))
    text = "\n\n".join(t.strip() for t in plain if t.strip())
    if not text and html:
        text = "\n\n".join(html_to_text(h) for h in html)
    kind = _auto_kind(msg, from_address, subject)
    return ParsedEmail(
        message_id=_one_id(_header(msg, "Message-ID")),
        in_reply_to=_one_id(_header(msg, "In-Reply-To")),
        references=[r[:500] for r in _ids(_header(msg, "References"))][:50],
        from_address=from_address, from_name=(name or "").strip()[:200] or None,
        to=_addresses([_header(msg, "To")]), cc=_addresses([_header(msg, "Cc")]),
        subject=subject, date=date, text=text.replace("\r\n", "\n").strip(),
        attachments=attachments, auto_kind=kind,
        bounce=_bounce(msg) if kind == "bounce" else None)


def build(*, from_address: str, from_name: str | None, to: str, subject: str, body: str,
          attachments: list[Attachment] | None = None, message_id: str | None = None,
          in_reply_to: str | None = None, references: list[str] | None = None,
          date: datetime | None = None) -> bytes:
    """A raw email, as a customer's mail program would send it (the Test mailbox)."""
    from email.utils import format_datetime, formataddr, make_msgid

    m = EmailMessage()
    m["From"] = formataddr((from_name or "", from_address))
    m["To"] = to
    m["Subject"] = subject
    m["Date"] = format_datetime(date or datetime.now(UTC))
    m["Message-ID"] = message_id or make_msgid(domain="customer.test")
    if in_reply_to:
        m["In-Reply-To"] = in_reply_to
        m["References"] = " ".join(references or [in_reply_to])
    m.set_content(body)
    for a in attachments or []:
        maintype, _, subtype = a.content_type.partition("/")
        m.add_attachment(a.data, maintype=maintype or "application",
                         subtype=subtype or "octet-stream", filename=a.filename)
    return m.as_bytes()
