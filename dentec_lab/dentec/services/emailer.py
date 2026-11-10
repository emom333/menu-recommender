"""Optional e-mailing of invoice PDFs through the lab's own e-mail account (SMTP).

Only used when a person clicks "Email"; nothing is sent automatically.
The SMTP password is never stored - it is asked for once per session.
"""
from __future__ import annotations

import mimetypes
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path


def send_email(settings: dict, password: str, to: str, subject: str, body: str,
               attachments: list[Path] | None = None) -> None:
    host = settings.get("smtp_host", "").strip()
    if not host:
        raise ValueError("E-mail is not set up. Enter your SMTP server in Backup & Settings.")
    port = int(settings.get("smtp_port") or 587)
    user = settings.get("smtp_user", "").strip()
    sender = settings.get("smtp_from", "").strip() or user
    if not to or "@" not in to:
        raise ValueError("Enter a valid recipient e-mail address.")
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    for p in attachments or []:
        p = Path(p)
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        msg.add_attachment(p.read_bytes(), maintype=maintype, subtype=subtype, filename=p.name)
    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as smtp:
            if user:
                smtp.login(user, password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            if settings.get("smtp_tls", "1") == "1":
                smtp.starttls(context=ctx)
            if user:
                smtp.login(user, password)
            smtp.send_message(msg)
