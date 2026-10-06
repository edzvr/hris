import base64
import json
import logging
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


logger = logging.getLogger(__name__)
BREVO_EMAIL_URL = "https://api.brevo.com/v3/smtp/email"
EMAIL_API_TIMEOUT_SECONDS = 10


def send_brevo_email(api_key, sender, recipients, subject, body, attachments=None):
    """Return whether Brevo accepted the email, not whether it reached the inbox."""
    if not api_key or not sender:
        logger.error("Brevo email send skipped: BREVO_API_KEY or MAIL_DEFAULT_SENDER is missing.")
        return False

    payload = {
        "sender": {"email": sender},
        "to": [{"email": email} for email in recipients],
        "subject": subject,
        "textContent": body,
    }
    if attachments:
        payload["attachment"] = [
            {
                "name": filename,
                "content": base64.b64encode(
                    content.encode("utf-8") if isinstance(content, str) else content
                ).decode("ascii"),
            }
            for filename, content, _mimetype in attachments
        ]

    request = Request(
        BREVO_EMAIL_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "api-key": api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=EMAIL_API_TIMEOUT_SECONDS) as response:
            if response.status != 201:
                logger.error("Brevo email was not accepted: HTTP %s.", response.status)
                return False
            result = json.loads(response.read())
            if not isinstance(result, dict) or not result.get("messageId"):
                logger.error("Brevo email response did not contain a message ID.")
                return False
    except HTTPError as error:
        # Do not log request headers, reset links, or provider response bodies.
        logger.error(
            "Brevo email rejected: HTTP %s. Check API key, verified sender, "
            "transactional account activation and sending quota in Brevo.",
            error.code,
        )
        error.close()
        return False
    except (URLError, TimeoutError, OSError):
        logger.error("Brevo email connection failed or timed out.")
        return False
    except (ValueError, UnicodeError):
        logger.error("Brevo email returned an invalid JSON response.")
        return False

    logger.info("Brevo accepted notification email for delivery.")
    return True
