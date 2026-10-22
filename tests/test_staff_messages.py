from hris import app
from models import Employee, StaffMessage, db


def login(client, employee_id):
    with client.session_transaction() as session:
        session['_user_id'] = str(employee_id)
        session['_fresh'] = True


def test_admin_sends_private_messages_and_announcements_only_to_staff():
    with app.app_context():
        admin = Employee(first_name='Message', last_name='Admin', role='admin')
        staff_one = Employee(first_name='Message', last_name='One', role='staff')
        staff_two = Employee(first_name='Message', last_name='Two', role='staff')
        db.session.add_all([admin, staff_one, staff_two])
        db.session.commit()
        admin_id, staff_one_id, staff_two_id = admin.id, staff_one.id, staff_two.id

    try:
        admin_client = app.test_client()
        login(admin_client, admin_id)

        response = admin_client.post(
            '/staff-messages',
            data={
                'message_type': 'direct',
                'recipient_id': str(staff_one_id),
                'subject': 'Private note',
                'body': 'Please see me.',
            },
        )
        assert response.status_code == 302

        response = admin_client.post(
            '/staff-messages',
            data={
                'message_type': 'announcement',
                'send_to_all': '1',
                'subject': 'Schedule update',
                'body': 'The office opens at 9 AM.',
            },
        )
        assert response.status_code == 302

        with app.app_context():
            messages = StaffMessage.query.filter_by(sender_id=admin_id).all()
            assert len(messages) == 3
            assert {message.recipient_id for message in messages} == {
                staff_one_id, staff_two_id,
            }

        staff_one_client = app.test_client()
        login(staff_one_client, staff_one_id)
        inbox_one = staff_one_client.get('/staff-messages')
        assert inbox_one.status_code == 200
        assert b'Private note' in inbox_one.data
        assert b'Schedule update' in inbox_one.data

        staff_two_client = app.test_client()
        login(staff_two_client, staff_two_id)
        inbox_two = staff_two_client.get('/staff-messages')
        assert inbox_two.status_code == 200
        assert b'Schedule update' in inbox_two.data
        assert b'Private note' not in inbox_two.data
        assert staff_two_client.post('/staff-messages', data={}).status_code == 403

        with app.app_context():
            private_message = StaffMessage.query.filter_by(
                recipient_id=staff_one_id,
                subject='Private note',
            ).one()
            assert private_message.read_at is not None
    finally:
        with app.app_context():
            StaffMessage.query.filter(
                db.or_(
                    StaffMessage.sender_id == admin_id,
                    StaffMessage.recipient_id.in_([staff_one_id, staff_two_id]),
                )
            ).delete(synchronize_session=False)
            for employee_id in (staff_one_id, staff_two_id, admin_id):
                employee = db.session.get(Employee, employee_id)
                if employee:
                    db.session.delete(employee)
            db.session.commit()


def test_admin_cannot_send_private_message_to_another_admin():
    with app.app_context():
        admin = Employee(first_name='Sender', last_name='Admin', role='admin')
        other_admin = Employee(first_name='Other', last_name='Admin', role='admin')
        db.session.add_all([admin, other_admin])
        db.session.commit()
        admin_id, other_admin_id = admin.id, other_admin.id

    try:
        client = app.test_client()
        login(client, admin_id)
        response = client.post(
            '/staff-messages',
            data={
                'message_type': 'direct',
                'recipient_id': str(other_admin_id),
                'subject': 'No',
                'body': 'This should not send.',
            },
        )
        assert response.status_code == 302
        with app.app_context():
            assert StaffMessage.query.filter_by(sender_id=admin_id).count() == 0
    finally:
        with app.app_context():
            db.session.delete(db.session.get(Employee, other_admin_id))
            db.session.delete(db.session.get(Employee, admin_id))
            db.session.commit()
