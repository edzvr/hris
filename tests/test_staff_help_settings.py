from hris import answer_staff_help, app
from models import Employee, db


def test_settings_questions_explain_customization():
    for question in ('settings', 'paano baguhin ang kulay', 'customise button colors',
                     'customize layout', 'sidebar color'):
        result = answer_staff_help(question)
        assert result['matched']
        assert result['topic']['fragment'] == '#staffSettingsPanel'
        for instruction in ('My Account > Settings', 'Save Settings', 'Reset Layout', 'browser/device'):
            assert instruction in result['answer']
    assert answer_staff_help('download payslip')['topic']['endpoint'] == 'payroll'


def test_help_route_links_to_settings_panel():
    with app.app_context():
        employee = Employee(first_name='Help', last_name='Test', role='staff')
        db.session.add(employee)
        db.session.commit()
        employee_id = employee.id
    try:
        client = app.test_client()
        with client.session_transaction() as session:
            session['_user_id'] = str(employee_id)
            session['_fresh'] = True
        for response in (client.get('/staff-help'), client.post('/staff-help', data={'question': 'settings'})):
            assert response.status_code == 200
            html = response.get_data(as_text=True)
            assert 'href="/dashboard_staff#staffSettingsPanel"' in html
            assert 'Customize your dashboard' in html
            assert 'Save Settings' in html
    finally:
        with app.app_context():
            db.session.delete(db.session.get(Employee, employee_id))
            db.session.commit()
