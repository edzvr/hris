from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from hris import app


def reset_record():
    return SimpleNamespace(
        used=False,
        expires_at=datetime.utcnow() + timedelta(hours=1),
        employee=SimpleNamespace(set_password=Mock()),
    )


def test_reset_page_has_independent_password_visibility_controls():
    with app.app_context(), patch("hris.PasswordResetToken.query") as query:
        query.filter_by.return_value.first.return_value = reset_record()
        response = app.test_client().get("/reset_password/test-token")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'data-password-toggle="password"' in html
    assert 'data-password-toggle="password_confirm"' in html
    assert html.count('minlength="8"') == 2
    assert 'aria-pressed="false"' in html


def test_success_confirmation_follows_saved_password_and_used_token():
    token = reset_record()
    with app.app_context():
        with (
            patch("hris.PasswordResetToken.query") as query,
            patch("hris.db.session.commit") as commit,
        ):
            query.filter_by.return_value.first.return_value = token
            response = app.test_client().post(
                "/reset_password/test-token",
                data={"password": "NewPassword123!", "password_confirm": "NewPassword123!"},
                follow_redirects=True,
            )
    assert response.status_code == 200
    token.employee.set_password.assert_called_once_with("NewPassword123!")
    assert token.used
    commit.assert_called_once()
    assert "Your password has been reset successfully." in response.get_data(as_text=True)
    assert 'role="status"' in response.get_data(as_text=True)


def test_invalid_passwords_show_error_without_success_or_save():
    for password, confirmation, expected in (
        ("short", "short", "Password must have at least 8 characters"),
        ("NewPassword123!", "Different123!", "Passwords do not match."),
    ):
        token = reset_record()
        with app.app_context():
            with (
                patch("hris.PasswordResetToken.query") as query,
                patch("hris.db.session.commit") as commit,
            ):
                query.filter_by.return_value.first.return_value = token
                response = app.test_client().post(
                    "/reset_password/test-token",
                    data={"password": password, "password_confirm": confirmation},
                )
        assert expected in response.get_data(as_text=True)
        assert 'role="alert"' in response.get_data(as_text=True)
        assert not token.used
        token.employee.set_password.assert_not_called()
        commit.assert_not_called()


def test_expired_or_used_link_cannot_reset_password():
    expired = reset_record()
    expired.expires_at = datetime.utcnow() - timedelta(seconds=1)
    for token in (None, expired):
        with app.app_context(), patch("hris.PasswordResetToken.query") as query:
            query.filter_by.return_value.first.return_value = token
            response = app.test_client().post(
                "/reset_password/test-token",
                data={"password": "NewPassword123!", "password_confirm": "NewPassword123!"},
            )
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/forgot_password")
        query.filter_by.assert_called_once_with(token="test-token", used=False)
