from datetime import date, datetime, timedelta
from unittest.mock import patch

from hris import (
    app,
    generate_monthly_peer_eval_reminder,
    monthly_utc_window,
    send_month_end_reminders,
)
from models import Bulletin, Employee, Evaluation, QuizResult, db


def test_month_end_emails_only_pending_items_and_bulletins_do_not_name_staff():
    month_start = date.today().replace(day=1)
    next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    month_end = next_month - timedelta(days=1)
    reminder_day = month_end - timedelta(days=3)
    result_date = datetime.combine(month_start + timedelta(days=10), datetime.min.time())
    quiz_pending_email = 'quiz-pending@example.com'
    peer_pending_email = 'peer-pending@example.com'
    completed_email = 'all-done@example.com'
    test_bulletin_titles = [
        f'Monthly reminders due by {month_end}',
        'Monthly peer evaluation reminder',
    ]
    with app.app_context():
        preexisting_bulletin_ids = {
            row.id for row in Bulletin.query.filter(
                Bulletin.author == 'System',
                Bulletin.title.in_(test_bulletin_titles),
            ).all()
        }
        quiz_pending = Employee(
            first_name='Quiz', last_name='Pending', role='staff',
            company='Trece-Uno', email=quiz_pending_email,
        )
        peer_pending = Employee(
            first_name='Peer', last_name='Pending', role='staff',
            company='Trece-Uno', email=peer_pending_email,
        )
        completed = Employee(
            first_name='All', last_name='Done', role='staff',
            company='Trece-Uno', email=completed_email,
        )
        peer_target = Employee(
            first_name='Peer', last_name='Target', role='staff',
            company='Trece-Uno',
        )
        db.session.add_all([quiz_pending, peer_pending, completed, peer_target])
        db.session.flush()
        employee_ids = [quiz_pending.id, peer_pending.id, completed.id, peer_target.id]
        db.session.add_all([
            Evaluation(
                employee_id=peer_target.id,
                evaluator_id=quiz_pending.id,
                rating=4,
                category='peer_monthly',
                date=result_date,
            ),
            QuizResult(
                employee_id=peer_pending.id,
                score=8,
                total_points=10,
                date_taken=result_date,
                is_official=True,
            ),
            QuizResult(
                employee_id=quiz_pending.id,
                score=10,
                total_points=10,
                date_taken=datetime(month_start.year - 1, month_start.month, 10),
                is_official=True,
            ),
            QuizResult(
                employee_id=completed.id,
                score=9,
                total_points=10,
                date_taken=result_date,
                is_official=True,
            ),
            Evaluation(
                employee_id=peer_target.id,
                evaluator_id=completed.id,
                rating=5,
                category='peer_monthly',
                date=result_date,
            ),
        ])
        db.session.commit()

    try:
        with patch('hris.send_notification_email') as send_email:
            send_month_end_reminders(reminder_day)
            notifications = {
                call.args[0][0]: call.args[2]
                for call in send_email.call_args_list
            }
            assert quiz_pending_email in notifications
            assert peer_pending_email in notifications
            assert completed_email not in notifications
            assert 'official monthly quiz' in notifications[quiz_pending_email]
            assert 'monthly peer evaluation' in notifications[peer_pending_email]

            with app.app_context():
                bulletin = Bulletin.query.filter_by(
                    author='System',
                    title=f'Monthly reminders due by {month_end}',
                ).one()
            assert 'Quiz Pending' not in bulletin.content
            assert 'Peer Pending' not in bulletin.content
            assert bulletin.content == (
                f'Monthly quiz and peer evaluation are due by {month_end}. '
                'Check the reminder above the Clock In panel on your staff dashboard.'
            )

            send_month_end_reminders(reminder_day)
            assert send_email.call_count == len(notifications)

        with patch('hris.peer_evaluation_is_due', return_value=True), \
                patch('hris.send_notification_email'):
            generate_monthly_peer_eval_reminder(month_start)
            bulletin_start, bulletin_end = monthly_utc_window(month_start)
            with app.app_context():
                bulletin = Bulletin.query.filter_by(
                    author='System', title='Monthly peer evaluation reminder',
                ).filter(
                    Bulletin.created_at >= bulletin_start,
                    Bulletin.created_at < bulletin_end,
                ).one()
            assert 'Quiz Pending' not in bulletin.content
            assert 'Peer Pending' not in bulletin.content
            assert bulletin.content == (
                f'Please complete your monthly peer-to-peer evaluation by {month_end}. '
                'Open Monthly Peer Evaluation from your staff dashboard.'
            )
    finally:
        with app.app_context():
            db.session.rollback()
            for bulletin in Bulletin.query.filter(
                Bulletin.author == 'System',
                Bulletin.title.in_(test_bulletin_titles),
            ).all():
                if bulletin.id not in preexisting_bulletin_ids:
                    db.session.delete(bulletin)
            QuizResult.query.filter(QuizResult.employee_id.in_(employee_ids)).delete(
                synchronize_session=False,
            )
            Evaluation.query.filter(Evaluation.evaluator_id.in_(employee_ids)).delete(
                synchronize_session=False,
            )
            Evaluation.query.filter(Evaluation.employee_id == employee_ids[3]).delete(
                synchronize_session=False,
            )
            Employee.query.filter(Employee.id.in_(employee_ids)).delete(
                synchronize_session=False,
            )
            db.session.commit()