from types import SimpleNamespace
from unittest.mock import patch

from hris import app


def test_forgot_password_sends_case_insensitive_registered_email():
    app.config["TESTING"] = True
    employee = SimpleNamespace(id=456, email="owner@example.com")
    reset_token = SimpleNamespace(token="test-reset-token")

    with app.app_context():
        with (
            patch("hris.Employee.query") as employee_query,
            patch("hris.PasswordResetToken", return_value=reset_token),
            patch("hris.db.session.add"),
            patch("hris.db.session.commit"),
            patch("hris.db.session.delete") as delete,
            patch("hris.secrets.token_urlsafe", return_value="test-reset-token"),
            patch("hris.send_notification_email", return_value=True) as send_email,
        ):
            employee_query.filter.return_value.first.return_value = employee
            response = app.test_client().post(
                "/forgot_password",
                data={"email": " OWNER@EXAMPLE.COM "},
                base_url="https://hris.example.com",
                follow_redirects=False,
            )

    assert response.status_code == 302
    assert employee_query.filter.called
    assert send_email.called
    assert "https://hris.example.com/reset_password/test-reset-token" in send_email.call_args.args[2]
    assert not delete.called


def test_forgot_password_removes_token_when_email_delivery_fails():
    app.config["TESTING"] = True
    employee = SimpleNamespace(id=789, email="owner@example.com")
    reset_token = SimpleNamespace(token="undelivered-reset-token")

    with app.app_context():
        with (
            patch("hris.Employee.query") as employee_query,
            patch("hris.PasswordResetToken", return_value=reset_token),
            patch("hris.db.session.add"),
            patch("hris.db.session.commit"),
            patch("hris.db.session.delete") as delete,
            patch("hris.send_notification_email", return_value=False),
        ):
            employee_query.filter.return_value.first.return_value = employee
            response = app.test_client().post(
                "/forgot_password",
                data={"email": "owner@example.com"},
                follow_redirects=False,
            )

    assert response.status_code == 302
    delete.assert_called_once_with(reset_token)


def test_forgot_password_brevo_timeout_redirects_and_removes_token():
    employee = SimpleNamespace(id=789, email="staff@example.com")
    reset_token = SimpleNamespace(token="undelivered-token")
    with app.app_context():
        with (
            patch.dict(app.config, {
                "TESTING": True,
                "MAIL_PROVIDER": "brevo",
                "BREVO_API_KEY": "test-api-key",
                "MAIL_DEFAULT_SENDER": "sender@example.com",
                "MAIL_SERVER": "",
            }),
            patch("hris.Employee.query") as employee_query,
            patch("hris.PasswordResetToken", return_value=reset_token),
            patch("hris.db.session.add"),
            patch("hris.db.session.commit"),
            patch("hris.db.session.delete") as delete,
            patch("utils.email_delivery.urlopen", side_effect=TimeoutError()),
        ):
            employee_query.filter.return_value.first.return_value = employee
            response = app.test_client().post(
                "/forgot_password", data={"email": employee.email},
                follow_redirects=False,
            )
    assert response.status_code == 302
    delete.assert_called_once_with(reset_token)


def test_brevo_notification_does_not_require_flask_mail_or_smtp():
    from hris import send_notification_email

    with (
        patch.dict(app.config, {
            "MAIL_PROVIDER": "brevo",
            "BREVO_API_KEY": "test-api-key",
            "MAIL_DEFAULT_SENDER": "sender@example.com",
            "MAIL_SERVER": "",
        }),
        patch("hris.mail", None),
        patch("hris.send_brevo_email", return_value=True) as send,
    ):
        assert send_notification_email(["", "staff@example.com"], "Subject", "Body")
    send.assert_called_once_with(
        "test-api-key", "sender@example.com", ["staff@example.com"],
        "Subject", "Body", None,
    )


def test_smtp_notification_retains_existing_delivery_path():
    from hris import send_notification_email

    with (
        patch.dict(app.config, {
            "MAIL_PROVIDER": "smtp",
            "MAIL_SERVER": "smtp.example.com",
            "MAIL_DEFAULT_SENDER": "sender@example.com",
        }),
        patch("hris.mail") as mail,
        patch("hris.Message") as message,
        patch("hris.send_brevo_email") as brevo,
    ):
        assert send_notification_email(
            ["staff@example.com"], "Subject", "Body",
            [("payslip.pdf", b"pdf", "application/pdf")],
        )
    mail.send.assert_called_once_with(message.return_value)
    message.return_value.attach.assert_called_once_with("payslip.pdf", "application/pdf", b"pdf")
    brevo.assert_not_called()


def test_invalid_provider_and_empty_recipients_do_not_send():
    from hris import send_notification_email

    with (
        patch.dict(app.config, {"MAIL_PROVIDER": "invalid"}),
        patch("hris.mail") as mail,
        patch("hris.send_brevo_email") as brevo,
    ):
        assert not send_notification_email(["staff@example.com"], "Subject", "Body")
        assert not send_notification_email([""], "Subject", "Body")
    mail.send.assert_not_called()
    brevo.assert_not_called()
