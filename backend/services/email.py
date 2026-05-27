"""Send books to e-readers via SMTP email."""
import logging
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from backend.core.config import settings
from backend.services.organizer import sanitize_name

log = logging.getLogger(__name__)

MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024

MIME_TYPES = {
    "epub": "application/epub+zip",
    "pdf": "application/pdf",
    "mobi": "application/x-mobipocket-ebook",
    "cbz": "application/x-cbz",
    "cbr": "application/x-cbz",
}


class SendToDeviceError(Exception):
    pass


class SmtpNotConfiguredError(SendToDeviceError):
    pass


class FileTooLargeError(SendToDeviceError):
    pass


class SmtpSendError(SendToDeviceError):
    pass


def _connect() -> smtplib.SMTP:
    """Open and authenticate an SMTP connection using app settings."""
    if not settings.smtp_configured:
        raise SmtpNotConfiguredError("SMTP is not configured")
    try:
        if settings.smtp_use_ssl:
            conn = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=30)
        else:
            conn = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30)
            if settings.smtp_use_tls:
                conn.starttls()
        conn.login(settings.smtp_user, settings.smtp_password)
        return conn
    except Exception as exc:
        raise SmtpSendError(f"SMTP connection failed: {exc}") from exc


def _build_book_message(
    to_email: str,
    book_title: str,
    file_path: Path,
    file_format: str,
) -> MIMEMultipart:
    """Build a MIME message with the book file attached."""
    file_size = file_path.stat().st_size
    if file_size > MAX_ATTACHMENT_BYTES:
        size_mb = file_size / (1024 * 1024)
        raise FileTooLargeError(f"File is {size_mb:.1f} MB — exceeds 25 MB email limit")

    msg = MIMEMultipart()
    msg["From"] = settings.smtp_from_address
    msg["To"] = to_email
    msg["Subject"] = book_title
    msg.attach(MIMEText("Sent from Tome", "plain"))

    mime_type = MIME_TYPES.get(file_format, "application/octet-stream")
    maintype, subtype = mime_type.split("/", 1)

    filename = f"{sanitize_name(book_title)}.{file_format}"
    with open(file_path, "rb") as f:
        attachment = MIMEApplication(f.read(), _subtype=subtype)
    attachment.add_header("Content-Disposition", "attachment", filename=filename)
    msg.attach(attachment)

    return msg


def send_book_to_device(
    to_email: str,
    book_title: str,
    file_path: Path,
    file_format: str,
) -> None:
    """Send a single book to a device email address."""
    msg = _build_book_message(to_email, book_title, file_path, file_format)
    conn = _connect()
    try:
        conn.sendmail(settings.smtp_from_address, [to_email], msg.as_string())
    except Exception as exc:
        raise SmtpSendError(f"Failed to send email: {exc}") from exc
    finally:
        conn.quit()
    log.info("Sent '%s' to %s", book_title, to_email)


def send_books_bulk(
    to_email: str,
    books: list[tuple[str, Path, str]],
) -> list[tuple[str, str | None]]:
    """Send multiple books over a single SMTP connection.

    Args:
        to_email: device email address
        books: list of (book_title, file_path, file_format) tuples

    Returns:
        list of (book_title, error_or_none) — None means success
    """
    conn = _connect()
    results: list[tuple[str, str | None]] = []
    try:
        for title, path, fmt in books:
            try:
                msg = _build_book_message(to_email, title, path, fmt)
                conn.sendmail(settings.smtp_from_address, [to_email], msg.as_string())
                results.append((title, None))
                log.info("Bulk send: sent '%s' to %s", title, to_email)
            except FileTooLargeError as exc:
                results.append((title, str(exc)))
            except Exception as exc:
                results.append((title, f"Send failed: {exc}"))
    finally:
        conn.quit()
    return results


def send_test_email(to_email: str) -> None:
    """Send a plain-text test email to verify SMTP config."""
    if not settings.smtp_configured:
        raise SmtpNotConfiguredError("SMTP is not configured")

    msg = MIMEText("This is a test email from Tome. Your SMTP configuration is working.", "plain")
    msg["From"] = settings.smtp_from_address
    msg["To"] = to_email
    msg["Subject"] = "Tome SMTP Test"

    conn = _connect()
    try:
        conn.sendmail(settings.smtp_from_address, [to_email], msg.as_string())
    except Exception as exc:
        raise SmtpSendError(f"Failed to send test email: {exc}") from exc
    finally:
        conn.quit()
    log.info("Test email sent to %s", to_email)
