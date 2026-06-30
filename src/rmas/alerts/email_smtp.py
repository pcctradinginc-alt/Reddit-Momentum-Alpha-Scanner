"""SMTP email sender (Gmail App-Password friendly).

Credentials come ONLY from the environment. If SMTP isn't configured, the
report is written to ``reports/`` and logged instead of failing — so dry runs
never crash.
"""

from __future__ import annotations

import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from rmas.config import ROOT, Secrets
from rmas.logging_setup import get_logger

log = get_logger("alerts.email")
REPORTS = ROOT / "reports"


def send_email(subject: str, text_body: str, html_body: str | None = None,
               secrets: Secrets | None = None, dry_run: bool | None = None) -> bool:
    """Send an alert email. Returns True if actually sent.

    ``dry_run=None`` -> auto: dry-run when SMTP not configured. Always also
    writes a copy to reports/ for the audit trail.
    """
    secrets = secrets or Secrets()
    _write_report_copy(subject, text_body, html_body)

    auto_dry = not secrets.smtp_ready
    if dry_run is None:
        dry_run = auto_dry
    if dry_run:
        log.info("EMAIL dry-run (SMTP not configured or dry_run=True). Subject: %s", subject)
        return False

    host = secrets.get("SMTP_HOST", "smtp.gmail.com")
    port = int(secrets.get("SMTP_PORT", "587"))
    user = secrets.get("SMTP_USER")
    pw = secrets.get("SMTP_PASSWORD")
    sender = secrets.get("ALERT_EMAIL_FROM", user)
    to = secrets.get("ALERT_EMAIL_TO", user)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    msg.attach(MIMEText(text_body, "plain"))
    if html_body:
        msg.attach(MIMEText(html_body, "html"))

    try:  # pragma: no cover - network
        with smtplib.SMTP(host, port, timeout=20) as server:
            server.starttls()
            server.login(user, pw)
            server.sendmail(sender, [addr.strip() for addr in to.split(",")], msg.as_string())
        log.info("EMAIL sent to %s", to)
        return True
    except Exception as exc:  # pragma: no cover
        log.error("EMAIL send failed (%s); report saved to disk", exc)
        return False


def _write_report_copy(subject: str, text_body: str, html_body: str | None) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    Path(REPORTS / f"alert_{stamp}.txt").write_text(f"{subject}\n\n{text_body}")
    if html_body:
        Path(REPORTS / f"alert_{stamp}.html").write_text(html_body)
