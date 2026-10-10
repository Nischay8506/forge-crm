"""Email over the company's own mailbox (decision L2 A): Google Workspace, Microsoft
365, Zoho or any SMTP server, with the username and an app password.

Sending is split so a failure says whether the email could have gone:
  1. connect, secure and log in: any failure here means nothing was sent (retry,
     or an account problem when the login or sender is refused);
  2. hand the email over: a clear refusal from the server means it did not go
     (bounced for a refused address, retry for a temporary 4xx); a dropped
     connection here means nobody can tell (unknown, never retried automatically).

Bounces the server reports later, by email, are read once Forge reads the sales
inbox (F1.1, Phase 2). smtplib blocks, so it runs in a thread.
"""

import asyncio
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate

from app.channels.adapters.base import (
    BOUNCED,
    FAILED,
    RETRY,
    SENT,
    UNKNOWN,
    OutgoingEmail,
    SendResult,
)

TIMEOUT = 30


class _NotSent(Exception):
    def __init__(self, result: SendResult):
        self.result = result


def build(mail: OutgoingEmail) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr((mail.from_name, mail.from_address)) if mail.from_name \
        else mail.from_address
    msg["To"] = mail.to
    msg["Subject"] = mail.subject
    msg["Date"] = formatdate(localtime=False)
    domain = mail.from_address.rsplit("@", 1)[-1]
    msg["Message-ID"] = f"<{mail.message_id}@{domain}>"
    if mail.reply_to:
        msg["Reply-To"] = mail.reply_to
    for k, v in mail.headers.items():
        msg[k] = v
    msg.set_content(mail.text)
    return msg


class SmtpAdapter:
    name = "smtp"

    def __init__(self, host: str, port: int, security: str, username: str, password: str):
        self.host, self.port, self.security = host, port, security
        self.username, self._password = username, password

    def __repr__(self) -> str:  # never show the password
        return f"<SmtpAdapter {self.host}:{self.port} {self.security}>"

    def _open(self) -> smtplib.SMTP:
        """Connected, secured and logged in, or _NotSent."""
        context = ssl.create_default_context()
        try:
            if self.security == "ssl":
                server: smtplib.SMTP = smtplib.SMTP_SSL(self.host, self.port, timeout=TIMEOUT,
                                                        context=context)
            else:
                server = smtplib.SMTP(self.host, self.port, timeout=TIMEOUT)
                server.ehlo()
                if self.security == "starttls":
                    server.starttls(context=context)
                    server.ehlo()
        except (OSError, smtplib.SMTPException) as e:
            raise _NotSent(SendResult(RETRY, reason=f"Could not reach the mail server "
                                                    f"({type(e).__name__})")) from e
        try:
            if self.username:
                server.login(self.username, self._password)
        except smtplib.SMTPAuthenticationError as e:
            server.close()
            raise _NotSent(SendResult(FAILED, reason="The mail server refused Forge's username "
                                                     "or password", account_problem=True)) from e
        except (OSError, smtplib.SMTPException) as e:
            server.close()
            raise _NotSent(SendResult(RETRY, reason=f"Could not log in to the mail server "
                                                    f"({type(e).__name__})")) from e
        return server

    def _send(self, mail: OutgoingEmail) -> SendResult:
        try:
            server = self._open()
        except _NotSent as e:
            return e.result
        msg = build(mail)
        try:
            server.send_message(msg)
        except smtplib.SMTPRecipientsRefused as e:
            code = next(iter(e.recipients.values()))[0] if e.recipients else 550
            if 400 <= code < 500:
                return SendResult(RETRY, reason=f"The mail server asked to try later ({code})")
            return SendResult(BOUNCED, reason=f"The mail server refused this address ({code})")
        except smtplib.SMTPSenderRefused as e:
            return SendResult(FAILED, reason=f"The mail server refused the sender address "
                                             f"({e.smtp_code})", account_problem=True)
        except smtplib.SMTPDataError as e:
            if 400 <= e.smtp_code < 500:
                return SendResult(RETRY, reason=f"The mail server asked to try later "
                                                f"({e.smtp_code})")
            return SendResult(FAILED, reason=f"The mail server refused the email "
                                             f"({e.smtp_code})")
        except (OSError, smtplib.SMTPException) as e:
            return SendResult(UNKNOWN, reason=f"The connection dropped while sending "
                                              f"({type(e).__name__})")
        finally:
            try:
                server.quit()
            except (OSError, smtplib.SMTPException):
                server.close()
        return SendResult(SENT, provider_ref=msg["Message-ID"])

    async def send(self, mail: OutgoingEmail) -> SendResult:
        return await asyncio.to_thread(self._send, mail)

    def _check(self) -> tuple[bool, str]:
        try:
            server = self._open()
        except _NotSent as e:
            return False, e.result.reason or "Could not connect"
        try:
            server.noop()
        finally:
            try:
                server.quit()
            except (OSError, smtplib.SMTPException):
                server.close()
        return True, "Connected and logged in"

    async def check(self) -> tuple[bool, str]:
        return await asyncio.to_thread(self._check)
