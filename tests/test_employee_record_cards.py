from datetime import date, datetime
from html.parser import HTMLParser
from urllib.parse import quote_plus

import pytest
from flask import template_rendered

from hris import app
from models import Attendance, Employee, EmployeeLiability, Loan, Payroll, db


class EmployeeCards(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.cards = {}
        self.current = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'article' and 'employee-record' in attrs.get('class', '').split():
            assert self.current is None
            self.current = {'text': [], 'elements': []}
            employee_id = int(attrs['data-employee'])
            assert employee_id not in self.cards
            self.cards[employee_id] = self.current
        elif self.current is not None:
            self.current['elements'].append((tag, attrs))

    def handle_endtag(self, tag):
        if tag == 'article':
            self.current = None

    def handle_data(self, data):
        if self.current is not None:
            self.current['text'].append(data)


@pytest.fixture
def people():
    with app.app_context():
        admin = Employee(first_name='Cards', last_name='Manager', role='admin', company='Auto Expert')
        auto = Employee(first_name='Same', last_name='Name', role='staff', company='Auto Expert', daily_rate=650, loan_balance=111)
        trece = Employee(first_name='Same', last_name='Name', role='staff', company='Trece', daily_rate=700, loan_balance=222)
        db.session.add_all([admin, auto, trece])
        db.session.flush()
        for employee, amount, reference in ((auto, 111, 'AUTO-ONLY'), (trece, 222, 'TRECE-ONLY')):
            db.session.add_all([
                Payroll(
                    employee_id=employee.id, cutoff_start=date(2026, 10, 3),
                    cutoff_end=date(2026, 10, 9), gross_income=amount * 10,
                    total_deductions=amount, net_pay=amount * 9, is_paid=True,
                    contribution_salary_base=amount * 10, sss=10,
                ),
                Loan(employee_id=employee.id, amount=amount, reason=reference, status='Pending', date_needed=date(2026, 10, 10)),
                EmployeeLiability(
                    employee_id=employee.id, description=reference, reference=reference,
                    total_amount=amount, deduction_per_cutoff=10,
                ),
                Attendance(
                    employee_id=employee.id, date=date(2026, 10, 5), status='Present',
                    clock_in=datetime(2026, 10, 5, 8), clock_out=datetime(2026, 10, 5, 17),
                ),
            ])
        db.session.add(Loan(employee_id=admin.id, amount=333, reason='ADMIN-LOAN', status='Pending', date_needed=date(2026, 10, 10)))
        db.session.commit()
        ids = admin.id, auto.id, trece.id
    try:
        yield ids
    finally:
        with app.app_context():
            db.session.rollback()
            for model in (Attendance, EmployeeLiability, Loan, Payroll):
                model.query.filter(model.employee_id.in_(ids)).delete(synchronize_session=False)
            Employee.query.filter(Employee.id.in_(ids)).delete(synchronize_session=False)
            db.session.commit()


def client_for(employee_id):
    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = str(employee_id)
        session['_fresh'] = True
    return client


@pytest.mark.parametrize('path', [
    '/admin/payroll-history?year=2026',
    '/admin/monthly-deductions?month=2026-10',
    '/admin/government-contributions?month=2026-10',
    '/loan',
    '/employee-liabilities',
])
def test_employee_cards_separate_duplicate_names_and_preserve_records(people, path):
    admin_id, auto_id, trece_id = people
    response = client_for(admin_id).get(path)
    assert response.status_code == 200
    cards = EmployeeCards(response.get_data(as_text=True)).cards
    assert auto_id in cards and trece_id in cards
    for employee_id, company, other_reference in (
        (auto_id, 'Auto', 'TRECE-ONLY'), (trece_id, 'Trece', 'AUTO-ONLY'),
    ):
        card = cards[employee_id]
        text = ' '.join(card['text'])
        assert 'Same Name' in text
        assert company in text
        assert other_reference not in text
        assert any(tag == 'details' for tag, _ in card['elements'])
        assert all('open' not in attrs for tag, attrs in card['elements'] if tag == 'details')
        if '/payroll-history' in path:
            assert '2026-10-03 to 2026-10-09' in text
            assert any(tag == 'a' and 'download' in attrs.get('href', '') for tag, attrs in card['elements'])
        elif '/monthly-deductions' in path:
            assert '2026-10-03 to 2026-10-09' in text
            assert 'Total EE Deductions' in text
        elif path == '/loan':
            assert any(tag == 'form' and 'approve' in attrs.get('action', '') for tag, attrs in card['elements'])
            assert 'Remaining Loan' in text
        elif path == '/employee-liabilities':
            assert any(tag == 'input' and attrs.get('name') == 'liability_id' for tag, attrs in card['elements'])
    if path == '/loan':
        assert 'ADMIN-LOAN' in ' '.join(cards[admin_id]['text'])
    with app.app_context():
        saved = Payroll.query.filter_by(employee_id=auto_id).one()
        assert (saved.gross_income, saved.total_deductions, saved.net_pay, saved.is_paid) == (1110, 111, 999, True)


def test_staff_cards_do_not_show_other_employee_records(people):
    _, auto_id, trece_id = people
    client = client_for(auto_id)
    for path in ('/loan', '/employee-liabilities'):
        response = client.get(path)
        assert response.status_code == 200
        cards = EmployeeCards(response.get_data(as_text=True)).cards
        assert set(cards) == {auto_id}
        assert trece_id not in cards
        assert b'TRECE-ONLY' not in response.data


def test_attendance_card_keeps_daily_details_and_filters(people):
    admin_id, auto_id, _ = people
    response = client_for(admin_id).get(f'/attendance/{auto_id}?cutoff_start=2026-10-03')
    assert response.status_code == 200
    cards = EmployeeCards(response.get_data(as_text=True)).cards
    assert set(cards) == {auto_id}
    text = ' '.join(cards[auto_id]['text'])
    for label in ('Daily Time Records', 'Clock In', 'Clock Out', 'Undertime', 'Branch', '2026-10-05'):
        assert label in text
    assert b"window.addEventListener('beforeprint'" in response.data


def test_company_attendance_filter_and_csv_keep_only_selected_employee(people):
    admin_id, auto_id, trece_id = people
    client = client_for(admin_id)
    for company, expected_id, excluded_id in (
        ('Auto Expert', auto_id, trece_id), ('Trece-Uno', trece_id, auto_id),
    ):
        response = client.get('/holiday_overtime', query_string={
            'cutoff_start': '2026-10-03', 'company': company,
        })
        assert response.status_code == 200
        assert f'/attendance/{expected_id}?'.encode() in response.data
        assert f'/attendance/{excluded_id}?'.encode() not in response.data
        assert b'name="company"' in response.data
        export = client.get('/holiday_overtime', query_string={
            'cutoff_start': '2026-10-03', 'company': company, 'export': 'attendance_csv',
        })
        assert export.status_code == 200
        assert export.get_data(as_text=True).count('Same Name') == 1


def test_company_dashboard_scopes_summary_directory_and_actions(people):
    admin_id, auto_id, trece_id = people
    client = client_for(admin_id)
    contexts = []

    def capture(sender, template, context, **extra):
        if template.name == 'dashboard_admin.html':
            contexts.append({
                **context,
                'auto_employees': [emp.id for emp in context['auto_employees']],
                'trece_employees': [emp.id for emp in context['trece_employees']],
            })

    template_rendered.connect(capture, app)
    try:
        for company, expected_id, loan_balance, net_pay in (
            ('Auto Expert', auto_id, 111, 999), ('Trece-Uno', trece_id, 222, 1998),
        ):
            response = client.get('/dashboard_admin', query_string={'company': company})
            assert response.status_code == 200
            assert response.data.count(b'images/autoxpertlogo.jpg') == 1
            assert response.data.count(b'images/trece-uno-logo.jpg') == 1
            assert response.data.count(b'aria-label="Open Auto Expert dashboard"') == 1
            assert response.data.count(b'aria-label="Open Trece-Uno dashboard"') == 1
            context = contexts[-1]
            assert context['selected_company'] == company
            assert context['total_employees'] == 1
            assert context['summary']['loan_balance'] == loan_balance
            assert context['summary']['net_pay'] == net_pay
            assert set(context['auto_employees'] + context['trece_employees']) >= {expected_id}
            assert not (context['auto_employees'] if company == 'Trece-Uno' else context['trece_employees'])
            assert b'All Companies' in response.data
            for path in ('/payroll_dashboard', '/holiday_overtime', '/loan', '/admin/government-contributions'):
                assert f'{path}?company={quote_plus(company)}'.encode() in response.data
        assert client.get('/dashboard_admin?company=invalid').status_code == 302
        client.get('/dashboard_admin')
        assert contexts[-1]['selected_company'] == ''
        assert contexts[-1]['summary']['loan_balance'] == 333
    finally:
        template_rendered.disconnect(capture, app)
