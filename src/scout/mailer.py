"""Composing and "sending" email. Scout only talks to a capture SMTP server (Mailpit in docker compose): the mailer
refuses any other host unless capture-only mode is switched off in code, and every message carries a one-click
unsubscribe link (RFC 8058 headers), the sender's postal address and a stable Message-ID for reply threading."""

from __future__ import annotations

import datetime as dt
import smtplib
import uuid
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import format_datetime, formataddr, make_msgid, parseaddr
from typing import Protocol

CAPTURE_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "mailpit", "scout-mailpit"})


class CaptureOnlyError(RuntimeError):
    pass


@dataclass(frozen=True)
class Outgoing:
    to_email: str
    to_name: str
    subject: str
    body: str
    unsubscribe_url: str
    from_address: str
    postal_address: str
    company: str
    message_id: str = ""
    in_reply_to: str | None = None


def footer(company: str, postal_address: str, unsubscribe_url: str) -> str:
    return (
        f"\n\n--\n{company} · {postal_address}\n"
        f"You received this because your role matches who we help. Not interested? Unsubscribe in one click: {unsubscribe_url}"
    )


def compose(out: Outgoing, *, now: dt.datetime | None = None) -> EmailMessage:
    msg = EmailMessage()
    name, addr = parseaddr(out.from_address)
    msg["From"] = formataddr((name, addr))
    msg["To"] = formataddr((out.to_name, out.to_email))
    msg["Subject"] = out.subject
    msg["Date"] = format_datetime(now or dt.datetime.now(dt.UTC))
    msg["Message-ID"] = out.message_id or make_msgid(domain=addr.split("@")[-1] or "scout.example")
    if out.in_reply_to:
        msg["In-Reply-To"] = out.in_reply_to
        msg["References"] = out.in_reply_to
    unsubscribe_mailto = f"mailto:unsubscribe@{addr.split('@')[-1]}?subject=unsubscribe"
    msg["List-Unsubscribe"] = f"<{out.unsubscribe_url}>, <{unsubscribe_mailto}>"
    msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    msg["X-Scout-Capture"] = "true"
    msg.set_content(out.body.rstrip() + footer(out.company, out.postal_address, out.unsubscribe_url))
    return msg


class Mailer(Protocol):
    def send(self, msg: EmailMessage) -> None: ...


class SmtpMailer:
    def __init__(self, host: str, port: int, *, capture_only: bool = True, timeout: float = 10.0) -> None:
        if capture_only and host not in CAPTURE_HOSTS:
            raise CaptureOnlyError(
                f"capture-only mode: refusing to send through {host!r}; Scout sends only to a capture server such as Mailpit"
            )
        self.host, self.port, self.timeout = host, port, timeout

    def send(self, msg: EmailMessage) -> None:
        with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as smtp:
            smtp.send_message(msg)


@dataclass
class MemoryMailer:
    """For tests and the offline evaluation: keeps the composed messages."""

    sent: list[EmailMessage] = field(default_factory=list)

    def send(self, msg: EmailMessage) -> None:
        self.sent.append(msg)


def new_token() -> str:
    return uuid.uuid4().hex
