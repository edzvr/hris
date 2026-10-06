import base64
import json
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from utils.email_delivery import send_brevo_email


def accepted_response():
    response = MagicMock()
    response.__enter__.return_value = response
    response.status = 201
    response.read.return_value = b'{"messageId":"test-message"}'
    return response


def test_brevo_sends_https_payload_with_timeout_and_attachments():
    with patch("utils.email_delivery.urlopen", return_value=accepted_response()) as send:
        assert send_brevo_email(
            "test-api-key", "sender@example.com", ["staff@example.com"],
            "Payslip", "Email body", [("payslip.pdf", b"pdf-content", "application/pdf")]
        )
    request = send.call_args.args[0]
    assert request.full_url == "https://api.brevo.com/v3/smtp/email"
    assert request.get_method() == "POST"
    assert request.get_header("Api-key") == "test-api-key"
    assert send.call_args.kwargs["timeout"] == 10
    payload = json.loads(request.data)
    assert payload["sender"] == {"email": "sender@example.com"}
    assert payload["to"] == [{"email": "staff@example.com"}]
    assert payload["textContent"] == "Email body"
    assert base64.b64decode(payload["attachment"][0]["content"]) == b"pdf-content"
    assert payload["attachment"][0]["name"] == "payslip.pdf"


def test_brevo_missing_configuration_does_not_send():
    with patch("utils.email_delivery.urlopen") as send:
        assert not send_brevo_email("", "sender@example.com", ["staff@example.com"], "Test", "Body")
        assert not send_brevo_email("key", "", ["staff@example.com"], "Test", "Body")
    send.assert_not_called()


def test_brevo_preserves_plain_text_and_clickable_html():
    html = '<a href="https://hris.example.com/reset_password/test-token">Reset Password</a>'
    with patch("utils.email_delivery.urlopen", return_value=accepted_response()) as send:
        assert send_brevo_email(
            "key", "sender@example.com", ["staff@example.com"],
            "Reset", "Plain text fallback", html_body=html,
        )
    payload = json.loads(send.call_args.args[0].data)
    assert payload["htmlContent"] == html
    assert payload["textContent"] == "Plain text fallback"


def test_brevo_network_failures_return_false():
    for error in (TimeoutError(), URLError("unreachable"), OSError("connection failed")):
        with patch("utils.email_delivery.urlopen", side_effect=error):
            assert not send_brevo_email("key", "sender@example.com", ["staff@example.com"], "Test", "Body")


def test_brevo_rejected_requests_do_not_log_credentials_or_reset_links():
    for status in (400, 401, 403, 429, 500):
        error = HTTPError("https://api.brevo.com", status, "Rejected", {}, None)
        with (
            patch("utils.email_delivery.urlopen", side_effect=error),
            patch("utils.email_delivery.logger") as logger,
        ):
            assert not send_brevo_email(
                "private-key", "sender@example.com", ["staff@example.com"],
                "Reset", "https://hris.example.com/reset_password/private-token"
            )
        logged = str(logger.method_calls)
        assert "private-key" not in logged
        assert "private-token" not in logged


def test_brevo_invalid_or_unconfirmed_response_is_not_success():
    for status, body in (
        (202, b'{"messageId":"scheduled"}'),
        (201, b"not-json"),
        (201, b"{}"),
        (201, b"[]"),
    ):
        response = accepted_response()
        response.status = status
        response.read.return_value = body
        with patch("utils.email_delivery.urlopen", return_value=response):
            assert not send_brevo_email("key", "sender@example.com", ["staff@example.com"], "Test", "Body")
