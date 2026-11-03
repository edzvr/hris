from io import BytesIO
from pathlib import Path
import runpy

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text

from hris import app
from models import Employee, HRTemplate, db


def log_in(client, employee):
    with client.session_transaction() as session:
        session['_user_id'] = str(employee.id)
        session['_fresh'] = True


def test_hr_template_migrations_seed_the_catalog_once():
    migration_dir = Path(__file__).resolve().parents[1] / 'migrations' / 'versions'
    migrations = [
        runpy.run_path(str(migration_dir / filename))
        for filename in (
            'af6b7c8d9e0f_add_hr_template_library.py',
            'ba6c7d8e9f0a_add_hr_template_source_filename.py',
            'bc7d8e9f0a1b_seed_hr_template_catalog.py',
        )
    ]
    engine = create_engine('sqlite://')
    with engine.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE employees (id INTEGER PRIMARY KEY)')
        for migration in migrations:
            with Operations.context(MigrationContext.configure(connection)):
                migration['upgrade']()
        with Operations.context(MigrationContext.configure(connection)):
            migrations[-1]['upgrade']()
        rows = connection.execute(
            text('SELECT category, title, source_filename, stored_filename FROM hr_templates ORDER BY id')
        ).all()
    engine.dispose()

    assert len(rows) == 45
    assert rows[0] == (
        'a.) HR Essentials',
        'Employee Handbook',
        '1_Employee_Handbook.docx',
        None,
    )
    assert all(row.stored_filename is None for row in rows)


def test_admin_hr_template_library_crud_and_access_control(tmp_path):
    with app.app_context():
        admin = Employee(first_name='Template', last_name='Admin', role='admin')
        staff = Employee(first_name='Template', last_name='Staff', role='staff')
        db.session.add_all([admin, staff])
        db.session.commit()
        admin_id, staff_id = admin.id, staff.id

    original_template_folder = app.config['HR_TEMPLATES_FOLDER']
    app.config['HR_TEMPLATES_FOLDER'] = str(tmp_path)
    template_id = None
    try:
        admin_client = app.test_client()
        with app.app_context():
            log_in(admin_client, db.session.get(Employee, admin_id))
        response = admin_client.post(
            '/admin/hr-templates',
            data={
                'action': 'create',
                'title': 'Employee Handbook',
                'category': 'HR Essentials',
                'description': 'Company policies',
                'online_url': 'https://docs.google.com/document/d/example/edit',
                'template_file': (BytesIO(b'original docx data'), 'handbook.docx'),
            },
            content_type='multipart/form-data',
        )
        assert response.status_code == 302
        with app.app_context():
            template = HRTemplate.query.filter_by(title='Employee Handbook').one()
            template_id = template.id
            old_filename = template.stored_filename
            assert template.online_url.endswith('/edit')

        page = admin_client.get('/admin/hr-templates')
        assert page.status_code == 200
        assert b'Open online editor' in page.data
        assert b'HR Templates' in page.data

        downloaded = admin_client.get(f'/admin/hr-templates/{template_id}/download')
        assert downloaded.status_code == 200
        assert downloaded.data == b'original docx data'
        assert 'handbook' in downloaded.headers['Content-Disposition']
        assert downloaded.headers['Content-Disposition'].endswith('.docx')
        downloaded.close()

        response = admin_client.post(
            '/admin/hr-templates',
            data={
                'action': 'update',
                'template_id': str(template_id),
                'title': 'Updated Handbook',
                'category': 'Policies',
                'description': 'Updated policies',
                'online_url': '',
                'template_file': (BytesIO(b'updated docx data'), 'handbook-v2.docx'),
            },
            content_type='multipart/form-data',
        )
        assert response.status_code == 302
        with app.app_context():
            template = db.session.get(HRTemplate, template_id)
            new_filename = template.stored_filename
            assert template.title == 'Updated Handbook'
            assert template.category == 'Policies'
            assert template.online_url is None
            assert not (tmp_path / old_filename).exists()
            assert (tmp_path / new_filename).read_bytes() == b'updated docx data'

            placeholder = HRTemplate(
                title='Placeholder Form',
                category='Recruitment',
                description='Needs the source file',
                source_filename='Candidate_Form.docx',
            )
            db.session.add(placeholder)
            db.session.commit()
            placeholder_id = placeholder.id
        response = admin_client.post(
            '/admin/hr-templates',
            data={
                'action': 'update',
                'template_id': str(placeholder_id),
                'title': 'Updated Placeholder Form',
                'category': 'Recruitment',
                'description': 'Still waiting for the source file',
                'source_filename': 'Candidate_Form_v2.docx',
                'online_url': '',
            },
        )
        assert response.status_code == 302
        with app.app_context():
            placeholder = db.session.get(HRTemplate, placeholder_id)
            assert placeholder.title == 'Updated Placeholder Form'
            assert placeholder.source_filename == 'Candidate_Form_v2.docx'

        staff_client = app.test_client()
        with app.app_context():
            log_in(staff_client, db.session.get(Employee, staff_id))
        assert staff_client.get('/admin/hr-templates').status_code == 403
        assert staff_client.get(f'/admin/hr-templates/{template_id}/download').status_code == 403
        assert staff_client.post('/admin/hr-templates').status_code == 403

        response = admin_client.post(
            '/admin/hr-templates',
            data={
                'action': 'create',
                'title': 'Invalid Link',
                'category': 'Policies',
                'online_url': 'javascript:alert(1)',
            },
        )
        assert response.status_code == 302
        with app.app_context():
            assert HRTemplate.query.filter_by(title='Invalid Link').count() == 0

        response = admin_client.post(
            '/admin/hr-templates',
            data={'action': 'delete', 'template_id': str(template_id)},
        )
        assert response.status_code == 302
        with app.app_context():
            assert db.session.get(HRTemplate, template_id) is None
            assert not (tmp_path / new_filename).exists()
    finally:
        app.config['HR_TEMPLATES_FOLDER'] = original_template_folder
        with app.app_context():
            db.session.rollback()
            if template_id:
                template = db.session.get(HRTemplate, template_id)
                if template:
                    db.session.delete(template)
            placeholder = HRTemplate.query.filter_by(title='Updated Placeholder Form').first()
            if placeholder:
                db.session.delete(placeholder)
            Employee.query.filter(Employee.id.in_([admin_id, staff_id])).delete(synchronize_session=False)
            db.session.commit()
