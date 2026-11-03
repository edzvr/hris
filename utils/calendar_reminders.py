"""Company reminder occurrences and durable, at-most-once email attempts."""

import logging
from calendar import monthrange
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError

from models import CalendarReminder, CalendarReminderDelivery, Employee, db

logger = logging.getLogger(__name__)
MANILA = ZoneInfo("Asia/Manila")


def local_time(utc_time):
    return utc_time.replace(tzinfo=ZoneInfo("UTC")).astimezone(MANILA)


def occurrences(reminder, start, end):
    """Yield UTC occurrences within an inclusive local-date range."""
    anchor = local_time(reminder.scheduled_at)
    if reminder.recurrence == "once":
        if start <= anchor.date() <= end:
            yield reminder.scheduled_at
        return
    year, month = max((start.year, start.month), (anchor.year, anchor.month))
    while (year, month) <= (end.year, end.month):
        day = min(anchor.day, monthrange(year, month)[1])
        candidate = anchor.replace(year=year, month=month, day=day)
        if candidate >= anchor and start <= candidate.date() <= end:
            yield candidate.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def visible_reminders(user):
    query = CalendarReminder.query
    if "admin" not in str(user.role or "").lower():
        if not user.company:
            return query.filter(db.false())
        company = "Trece-Uno" if user.company == "Trece" else user.company
        query = query.filter(CalendarReminder.company == company)
    return query


def reminder_events(user, start, end):
    events = []
    for reminder in visible_reminders(user).filter_by(active=True).order_by(CalendarReminder.id).all():
        for occurrence in occurrences(reminder, start, end):
            events.append({"reminder": reminder, "at": local_time(occurrence)})
    return sorted(events, key=lambda event: (event["at"], event["reminder"].id))


def _lock_reminder(reminder_id):
    if db.session.get_bind().dialect.name == "sqlite":
        db.session.execute(db.update(CalendarReminder).where(
            CalendarReminder.id == reminder_id
        ).values(revision=CalendarReminder.revision))
    return CalendarReminder.query.filter_by(id=reminder_id).with_for_update().populate_existing().first()


def dispatch_reminders(send_email, now=None):
    """No automatic retries after an unconfirmed provider result or interrupted send."""
    now = now or datetime.utcnow()
    stale = CalendarReminderDelivery.query.filter(
        CalendarReminderDelivery.status == "sending",
        CalendarReminderDelivery.attempted_at < now - timedelta(minutes=15),
    ).all()
    for delivery in stale:
        delivery.status = "unconfirmed"
        delivery.detail = "Send interrupted; provider acceptance is unknown. Check email provider logs."
    db.session.commit()
    start = local_time(now - timedelta(days=1)).date()
    end = local_time(now + timedelta(days=1)).date()
    reminder_ids = [row.id for row in CalendarReminder.query.filter_by(active=True).all()]
    for reminder_id in reminder_ids:
        reminder = db.session.get(CalendarReminder, reminder_id)
        candidates = list(occurrences(reminder, start, end))
        for occurrence in candidates:
            for stage, advance in (("day_before", timedelta(days=1)), ("due", timedelta(0))):
                trigger = occurrence - advance
                if not trigger <= now < trigger + timedelta(days=1):
                    continue
                company_names = ['Trece', 'Trece-Uno'] if reminder.company == 'Trece-Uno' else [reminder.company]
                recipient_ids = [
                    employee.id for employee in Employee.query.filter(
                        db.or_(
                            Employee.role.ilike("%admin%"),
                            db.and_(Employee.role.ilike("%staff%"), Employee.company.in_(company_names)),
                        )
                    ).all()
                ]
                for employee_id in recipient_ids:
                    current = _lock_reminder(reminder_id)
                    if (
                        current is None or not current.active
                        or occurrence not in list(occurrences(current, start, end))
                        or current.updated_at > trigger
                    ):
                        db.session.rollback()
                        continue
                    employee = db.session.get(Employee, employee_id, populate_existing=True)
                    if employee is None or not (
                        "admin" in str(employee.role or "").lower()
                        or ("staff" in str(employee.role or "").lower() and (
                            "Trece-Uno" if employee.company == "Trece" else employee.company
                        ) == current.company)
                    ):
                        db.session.rollback()
                        continue
                    delivery = CalendarReminderDelivery(
                        reminder_id=current.id, occurrence_at=occurrence, stage=stage,
                        employee_id=employee.id, revision=current.revision, status="sending",
                        attempted_at=now,
                    )
                    db.session.add(delivery)
                    try:
                        db.session.flush()
                    except IntegrityError:
                        db.session.rollback()
                        duplicate = CalendarReminderDelivery.query.filter_by(
                            reminder_id=reminder_id, occurrence_at=occurrence,
                            stage=stage, employee_id=employee_id,
                        ).first()
                        if duplicate is None:
                            raise
                        continue
                    title, notes, company = current.title, current.notes, current.company
                    email = employee.email
                    delivery_id = delivery.id
                    db.session.commit()
                    accepted = False
                    if email:
                        label = "Tomorrow" if stage == "day_before" else "Due now"
                        accepted = send_email(
                            [email], f"HRIS reminder: {title}",
                            f"{label}: {title}\nCompany: {company}\n"
                            f"When: {local_time(occurrence):%Y-%m-%d %I:%M %p} PHT\n\n{notes}\n\n"
                            "View company reminders in your HRIS dashboard. A recently edited or cancelled "
                            "reminder may supersede this email.",
                        )
                    delivery = db.session.get(CalendarReminderDelivery, delivery_id)
                    delivery.status = "accepted" if accepted else "unconfirmed"
                    delivery.detail = (
                        "Provider accepted; inbox delivery is not guaranteed." if accepted
                        else "Provider acceptance not confirmed; check email configuration/provider logs."
                        if email else "Recipient has no email address."
                    )
                    delivery.completed_at = datetime.utcnow()
                    db.session.commit()
                    if not accepted:
                        logger.error("Calendar reminder %s delivery %s unconfirmed: %s",
                                     reminder_id, delivery_id, delivery.detail)
