from hris import app
from models import Employee, db


def log_in(client, employee_id):
    with client.session_transaction() as session:
        session['_user_id'] = str(employee_id)
        session['_fresh'] = True


def render_dashboard(loan_balance, deduction):
    with app.app_context():
        staff = Employee(
            first_name='Loan',
            last_name='Card',
            role='staff',
            company='Trece-Uno',
            loan_balance=loan_balance,
            loan_deduction_per_cutoff=deduction,
        )
        db.session.add(staff)
        db.session.commit()
        staff_id = staff.id
    try:
        client = app.test_client()
        log_in(client, staff_id)
        response = client.get('/dashboard_staff')
        assert response.status_code == 200
        return response.get_data(as_text=True)
    finally:
        with app.app_context():
            db.session.rollback()
            db.session.delete(db.session.get(Employee, staff_id))
            db.session.commit()


def test_staff_dashboard_shows_remaining_loan_balance_card():
    html = render_dashboard(2750.0, 500.0)
    card = html[html.index('Remaining Loan Balance'):html.index('Summary &amp; Reports')]
    assert 'PHP 2,750.00' in card
    assert 'PHP 500.00' in card
    assert '<strong>6</strong> cutoff(s) remaining' in card


def test_staff_dashboard_loan_card_without_balance():
    html = render_dashboard(0.0, 500.0)
    card = html[html.index('Remaining Loan Balance'):html.index('Summary &amp; Reports')]
    assert 'PHP 0.00' in card
    assert 'You have no outstanding loan.' in card


def test_staff_dashboard_puts_clock_and_shortcuts_first_for_mobile():
    html = render_dashboard(0.0, 500.0)
    order = [html.index(marker) for marker in (
        'id="attendanceForm"', 'aria-label="Staff shortcuts"', 'Remaining Loan Balance',
        'aria-label="Company reminders"', 'Summary &amp; Reports', '<details class="dashboard-tools">',
        'View My Analytics',
    )]
    assert order == sorted(order)
    assert 'Quick Actions' not in html
    shortcuts = html[order[1]:order[2]]
    assert shortcuts.count('class="btn btn-primary"') == 6
