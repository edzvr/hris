import importlib.util
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from hris import app, calendar_reminder_email_configured, db, send_calendar_reminder_emails
from models import AuditLog, CalendarReminder, CalendarReminderDelivery, Employee
from utils.calendar_reminders import dispatch_reminders, local_time, occurrences


@pytest.mark.parametrize("year,expected", [(2027, 28), (2028, 29)])
def test_monthly_anchor_clamps_short_months_without_drifting(year, expected):
    reminder = SimpleNamespace(scheduled_at=datetime(year, 1, 31, 1, 30), recurrence="monthly")
    values = list(occurrences(reminder, date(year, 1, 1), date(year, 3, 31)))
    assert [local_time(value).day for value in values] == [31, expected, 31]
    assert all(local_time(value).hour == 9 for value in values)


def test_one_time_year_boundary_and_inclusive_local_dates():
    reminder = SimpleNamespace(scheduled_at=datetime(2026, 12, 31, 16, 5), recurrence="once")
    assert list(occurrences(reminder, date(2026, 12, 31), date(2026, 12, 31))) == []
    assert list(occurrences(reminder, date(2027, 1, 1), date(2027, 1, 1))) == [reminder.scheduled_at]
    reminder.recurrence = "monthly"
    assert len(list(occurrences(reminder, date(2026, 1, 1), date(2027, 1, 31)))) == 1


@pytest.fixture
def people():
    with app.app_context():
        users = [
            Employee(first_name="Reminder", last_name="Admin", role="admin",
                     company="Auto Expert", email="admin@example.invalid"),
            Employee(first_name="Reminder", last_name="Staff", role="staff",
                     company="Trece-Uno", email="staff@example.invalid"),
            Employee(first_name="Reminder", last_name="Other", role="staff",
                     company="Auto Expert", email="other@example.invalid"),
        ]
        db.session.add_all(users)
        db.session.commit()
        ids = tuple(user.id for user in users)
    yield ids
    with app.app_context():
        db.session.rollback()
        reminders = CalendarReminder.query.filter(CalendarReminder.created_by.in_(ids)).all()
        reminder_ids = [reminder.id for reminder in reminders]
        CalendarReminderDelivery.query.filter(CalendarReminderDelivery.reminder_id.in_(reminder_ids)).delete(synchronize_session=False)
        CalendarReminder.query.filter(CalendarReminder.id.in_(reminder_ids)).delete(synchronize_session=False)
        AuditLog.query.filter(AuditLog.employee_id.in_(ids)).delete(synchronize_session=False)
        Employee.query.filter(Employee.id.in_(ids)).delete(synchronize_session=False)
        db.session.commit()


def client_for(employee_id):
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(employee_id)
        session["_fresh"] = True
        session["calendar_reminder_csrf"] = "test-token"
    return client


def add_reminder(admin_id, **kwargs):
    reminder = CalendarReminder(
        title="Company planning", notes="Synthetic notes", company="Trece-Uno",
        scheduled_at=datetime(2027, 1, 31, 1), recurrence="once",
        created_by=admin_id, updated_at=datetime(2027, 1, 1),
        **kwargs,
    )
    db.session.add(reminder)
    db.session.commit()
    return reminder.id


def valid_form(**kwargs):
    return {
        "title": "Company planning", "notes": "Synthetic notes",
        "company": "Trece-Uno", "scheduled_at": "2090-01-31T09:00",
        "recurrence": "monthly", "csrf_token": "test-token", **kwargs,
    }


def test_admin_create_edit_cancel_and_calendar_wiring(people):
    client = client_for(people[0])
    assert client.get("/calendar/reminders/new").status_code == 200
    assert client.post("/calendar/reminders/new", data=valid_form()).status_code == 302
    with app.app_context():
        reminder = CalendarReminder.query.filter_by(created_by=people[0]).one()
        reminder_id = reminder.id
        assert reminder.scheduled_at == datetime(2090, 1, 31, 1)
        assert reminder.revision == 1
    assert client.post(f"/calendar/reminders/{reminder_id}/edit", data=valid_form(
        revision="1", title="Updated planning",
    )).status_code == 302
    response = client.get("/dashboard_admin?calendar_month=2090-02")
    assert response.status_code == 200
    assert b"Updated planning" in response.data
    assert b"Add company reminder" in response.data
    assert client.post(f"/calendar/reminders/{reminder_id}/cancel", data={
        "csrf_token": "test-token", "revision": "2",
    }).status_code == 302
    with app.app_context():
        reminder = db.session.get(CalendarReminder, reminder_id)
        assert not reminder.active
        assert reminder.revision == 3
    assert b"Updated planning" not in client.get("/dashboard_admin?calendar_month=2090-02").data


def test_staff_sees_own_company_only_and_cannot_write_or_read_delivery_status(people):
    with app.app_context():
        reminder_id = add_reminder(people[0])
    client = client_for(people[1])
    response = client.get("/calendar/reminders?month=2027-01")
    assert response.status_code == 200
    assert b"Company planning" in response.data
    assert b"Email delivery status" not in response.data
    assert b"Edit reminder" not in response.data
    for path in ("/calendar/reminders/new", f"/calendar/reminders/{reminder_id}/edit",
                 f"/calendar/reminders/{reminder_id}/cancel"):
        assert client.post(path, data=valid_form()).status_code == 403
    assert client.get(f"/calendar/reminders/{reminder_id}/edit").status_code == 403
    other = client_for(people[2])
    assert b"Company planning" not in other.get("/calendar/reminders?month=2027-01").data
    assert b"Company planning" not in other.get(f"/calendar/reminders?month=2027-01&company=Trece-Uno&employee_id={people[1]}").data
    with app.app_context():
        db.session.get(Employee, people[1]).company = "Trece"
        db.session.commit()
    assert b"Company planning" in client.get("/calendar/reminders?month=2027-01").data
    with app.app_context():
        db.session.get(Employee, people[1]).company = None
        db.session.commit()
    assert b"Company planning" not in client.get("/calendar/reminders?month=2027-01").data
    assert app.test_client().get("/calendar/reminders").status_code == 302


def test_staff_dashboard_displays_upcoming_company_reminders_only(people):
    with app.app_context():
        reminder_id = add_reminder(people[0])
        reminder = db.session.get(CalendarReminder, reminder_id)
        reminder.scheduled_at = datetime.utcnow() + timedelta(days=2)
        db.session.commit()
    own = client_for(people[1]).get("/dashboard_staff")
    other = client_for(people[2]).get("/dashboard_staff")
    assert own.status_code == other.status_code == 200
    assert b"Company planning" in own.data
    assert b"Company planning" not in other.data
    assert b"View company reminder calendar" in own.data


def test_monthly_existing_anchor_can_be_edited_without_resetting_start_date(people):
    with app.app_context():
        reminder_id = add_reminder(people[0])
        reminder = db.session.get(CalendarReminder, reminder_id)
        reminder.scheduled_at = datetime(2000, 1, 31, 1)
        reminder.recurrence = "monthly"
        db.session.commit()
    response = client_for(people[0]).post(f"/calendar/reminders/{reminder_id}/edit", data=valid_form(
        scheduled_at="2000-01-31T09:00", title="Updated recurring reminder", revision="1",
    ))
    assert response.status_code == 302
    with app.app_context():
        reminder = db.session.get(CalendarReminder, reminder_id)
        assert reminder.scheduled_at == datetime(2000, 1, 31, 1)
        assert reminder.title == "Updated recurring reminder"


def test_reminder_form_csrf_stale_edit_and_validation(people):
    client = client_for(people[0])
    assert client.post("/calendar/reminders/new", data=valid_form(csrf_token="")).status_code == 400
    for fields in (
        {"title": ""}, {"title": "x" * 151}, {"title": "line\nbreak"},
        {"notes": "x" * 5001}, {"company": "unknown"}, {"recurrence": "weekly"},
        {"scheduled_at": "invalid"}, {"scheduled_at": "2000-01-01T09:00"},
    ):
        response = client.post("/calendar/reminders/new", data=valid_form(**fields))
        assert response.status_code == 400, fields
    with app.app_context():
        assert CalendarReminder.query.filter_by(created_by=people[0]).count() == 0
        reminder_id = add_reminder(people[0])
    assert client.post(f"/calendar/reminders/{reminder_id}/cancel", data={"revision": "1"}).status_code == 400
    response = client.post(f"/calendar/reminders/{reminder_id}/edit", data=valid_form(revision="9"))
    assert response.status_code == 302
    assert client.post(f"/calendar/reminders/{reminder_id}/cancel", data={
        "csrf_token": "test-token", "revision": "9",
    }).status_code == 302
    with app.app_context():
        assert db.session.get(CalendarReminder, reminder_id).title == "Company planning"
        assert db.session.get(CalendarReminder, reminder_id).active
    assert client.get("/calendar/reminders?month=invalid").status_code == 302
    assert client.get("/calendar/reminders?month=0001-01").status_code == 302


def test_day_before_due_email_exact_timing_and_persistent_dedup(people):
    send = Mock(return_value=True)
    with app.app_context():
        reminder_id = add_reminder(people[0])
        due = db.session.get(CalendarReminder, reminder_id).scheduled_at
        dispatch_reminders(send, due - timedelta(days=1, seconds=1))
        assert not send.called
        dispatch_reminders(send, due - timedelta(days=1))
        assert send.call_count == 2  # matching staff and all admins, not other-company staff
        assert {call.args[0][0] for call in send.call_args_list} == {
            "staff@example.invalid", "admin@example.invalid",
        }
        assert all(len(call.args[0]) == 1 for call in send.call_args_list)
        dispatch_reminders(send, due - timedelta(hours=12))
        assert send.call_count == 2
        db.session.remove()
        dispatch_reminders(send, due)
        assert send.call_count == 4
        dispatch_reminders(send, due + timedelta(minutes=1))
        assert send.call_count == 4
        deliveries = CalendarReminderDelivery.query.filter_by(reminder_id=reminder_id).all()
        assert len(deliveries) == 4
        assert all(delivery.status == "accepted" for delivery in deliveries)
        assert any("09:00 AM" in call.args[2] and "PHT" in call.args[2] for call in send.call_args_list)


def test_email_unconfirmed_and_missing_address_are_visible_not_retried(people):
    send = Mock(return_value=False)
    with app.app_context():
        reminder_id = add_reminder(people[0])
        db.session.get(Employee, people[1]).email = None
        db.session.commit()
        due = db.session.get(CalendarReminder, reminder_id).scheduled_at
        dispatch_reminders(send, due)
        dispatch_reminders(send, due + timedelta(minutes=1))
        assert send.call_count == 1
        deliveries = CalendarReminderDelivery.query.filter_by(reminder_id=reminder_id).all()
        assert len(deliveries) == 2
        assert all(delivery.status == "unconfirmed" for delivery in deliveries)
        assert any(delivery.detail == "Recipient has no email address." for delivery in deliveries)
    response = client_for(people[0]).get("/calendar/reminders")
    assert b"unconfirmed" in response.data
    assert b"Recipient has no email address." in response.data


def test_new_or_edited_reminder_skips_already_passed_stage_and_cancellation(people):
    send = Mock(return_value=True)
    with app.app_context():
        reminder_id = add_reminder(people[0])
        reminder = db.session.get(CalendarReminder, reminder_id)
        due = reminder.scheduled_at
        reminder.updated_at = due - timedelta(hours=12)
        db.session.commit()
        dispatch_reminders(send, due - timedelta(hours=10))
        assert not send.called
        dispatch_reminders(send, due)
        assert send.call_count == 2
        reminder = db.session.get(CalendarReminder, reminder_id)
        reminder.active = False
        reminder.scheduled_at += timedelta(days=1)
        db.session.commit()
        dispatch_reminders(send, due + timedelta(days=1))
        assert send.call_count == 2


def test_edit_does_not_resend_same_occurrence_and_old_company_loses_visibility(people):
    send = Mock(return_value=True)
    with app.app_context():
        reminder_id = add_reminder(people[0])
        reminder = db.session.get(CalendarReminder, reminder_id)
        due = reminder.scheduled_at
        dispatch_reminders(send, due - timedelta(days=1))
        reminder = db.session.get(CalendarReminder, reminder_id)
        reminder.revision += 1
        reminder.company = "Auto Expert"
        reminder.updated_at = due - timedelta(hours=12)
        db.session.commit()
        dispatch_reminders(send, due)
        assert send.call_count == 4
        assert send.call_args_list[-1].args[0] == ["other@example.invalid"]
        dispatch_reminders(send, due + timedelta(minutes=1))
        assert send.call_count == 4
    assert b"Company planning" not in client_for(people[1]).get("/calendar/reminders?month=2027-01").data


def test_expired_stages_are_not_sent_and_interrupted_send_is_flagged(people):
    send = Mock(return_value=True)
    with app.app_context():
        reminder_id = add_reminder(people[0])
        due = db.session.get(CalendarReminder, reminder_id).scheduled_at
        db.session.add(CalendarReminderDelivery(
            reminder_id=reminder_id, occurrence_at=due, stage="due", employee_id=people[1],
            revision=1, status="sending", attempted_at=due - timedelta(hours=1),
        ))
        db.session.commit()
        dispatch_reminders(send, due + timedelta(days=2))
        assert not send.called
        assert CalendarReminderDelivery.query.filter_by(reminder_id=reminder_id).one().status == "unconfirmed"


def test_email_disabled_job_does_not_claim_or_send(people):
    with app.app_context():
        add_reminder(people[0])
    with patch.dict(app.config, MAIL_PROVIDER="disabled"), patch("hris.dispatch_reminders") as dispatch:
        send_calendar_reminder_emails()
        dispatch.assert_not_called()


@pytest.mark.parametrize("config,expected", [
    ({"MAIL_PROVIDER": "smtp", "MAIL_SERVER": ""}, False),
    ({"MAIL_PROVIDER": "brevo", "BREVO_API_KEY": "", "MAIL_DEFAULT_SENDER": "sender@example.invalid"}, False),
    ({"MAIL_PROVIDER": "brevo", "BREVO_API_KEY": "synthetic-test", "MAIL_DEFAULT_SENDER": "sender@example.invalid"}, True),
    ({"MAIL_PROVIDER": "brevo", "BREVO_API_KEY": "synthetic-test", "MAIL_DEFAULT_SENDER": "sender@example.invalid", "MAIL_SUPPRESS_SEND": True}, False),
])
def test_worker_readiness_requires_actual_email_configuration(config, expected):
    with patch.dict(app.config, config):
        assert calendar_reminder_email_configured() is expected


def test_second_worker_cannot_duplicate_claim_even_during_provider_call(people):
    with app.app_context():
        reminder_id = add_reminder(people[0])
        due = db.session.get(CalendarReminder, reminder_id).scheduled_at
        nested_send = Mock(return_value=True)
        first_send = True

        def send(*args):
            nonlocal first_send
            if first_send:
                first_send = False
                with app.app_context():
                    dispatch_reminders(nested_send, due)
            return True

        outer_send = Mock(side_effect=send)
        dispatch_reminders(outer_send, due)
        assert outer_send.call_count + nested_send.call_count == 2
        assert CalendarReminderDelivery.query.filter_by(reminder_id=reminder_id).count() == 2


def test_cancel_after_first_claim_stops_remaining_recipients(people):
    with app.app_context():
        reminder_id = add_reminder(people[0])
        due = db.session.get(CalendarReminder, reminder_id).scheduled_at

        def send(*args):
            reminder = db.session.get(CalendarReminder, reminder_id)
            reminder.active = False
            db.session.commit()
            return True

        sender = Mock(side_effect=send)
        dispatch_reminders(sender, due)
        assert sender.call_count == 1


def test_monthly_next_occurrence_sends_again_without_reusing_previous_claim(people):
    with app.app_context():
        reminder_id = add_reminder(people[0])
        reminder = db.session.get(CalendarReminder, reminder_id)
        reminder.recurrence = "monthly"
        db.session.commit()
        sender = Mock(return_value=True)
        dispatch_reminders(sender, datetime(2027, 1, 31, 1))
        dispatch_reminders(sender, datetime(2027, 2, 28, 1))
        assert sender.call_count == 4
        assert CalendarReminderDelivery.query.filter_by(reminder_id=reminder_id).count() == 4


def test_migration_additive_and_repeatable_without_modifying_history():
    path = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "ad4e5f6a7b8c_add_calendar_reminders.py"
    spec = importlib.util.spec_from_file_location("calendar_reminder_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            connection.execute(sa.text("CREATE TABLE history (id INTEGER PRIMARY KEY, value TEXT)"))
            connection.execute(sa.text("INSERT INTO history VALUES (1, 'unchanged')"))
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                migration.upgrade()
            assert connection.execute(sa.text("SELECT value FROM history")).scalar() == "unchanged"
            assert set(sa.inspect(connection).get_table_names()) >= {
                "calendar_reminders", "calendar_reminder_deliveries",
            }
    finally:
        engine.dispose()
