import base64
import struct
import zlib
from io import BytesIO

from hris import app, liability_cutoff_deduction
from models import Employee, EmployeeLiability, HRDocument, PayslipVerification, db


def log_in(client, employee):
    with client.session_transaction() as session:
        session['_user_id'] = str(employee.id)
        session['_fresh'] = True


def tiny_signature_png():
    def chunk(kind, data):
        return (
            struct.pack('>I', len(data))
            + kind
            + data
            + struct.pack('>I', zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    header = struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0)
    pixels = zlib.compress(b'\x00\x10\x10\x10\xff')
    return (
        b'\x89PNG\r\n\x1a\n'
        + chunk(b'IHDR', header)
        + chunk(b'IDAT', pixels)
        + chunk(b'IEND', b'')
    )


def test_admin_issues_private_pdf_and_staff_can_typed_sign_once():
    with app.app_context():
        admin = Employee(first_name='HR', last_name='Admin', role='admin')
        staff = Employee(first_name='HR', last_name='Staff', role='staff', company='Trece-Uno')
        other_staff = Employee(first_name='Other', last_name='Staff', role='staff', company='Trece-Uno')
        db.session.add_all([admin, staff, other_staff])
        db.session.flush()
        liability = EmployeeLiability(
            employee_id=staff.id,
            category='Cash Shortage',
            description='Test deduction',
            total_amount=1000,
            deduction_per_cutoff=250,
            status='Active',
            acknowledgment_status='Pending',
        )
        db.session.add(liability)
        db.session.commit()
        admin_id, staff_id, other_staff_id = admin.id, staff.id, other_staff.id
        liability_id = liability.id
    document_id = None
    try:
        admin_client = app.test_client()
        with app.app_context():
            log_in(admin_client, db.session.get(Employee, admin_id))
        response = admin_client.post(
            '/hr-documents',
            data={
                'employee_id': str(staff_id),
                'document_type': 'Liability Agreement',
                'subject': 'Deduction agreement',
                'body': 'Agreed deduction schedule: PHP 250 per cutoff.',
                'liability_id': str(liability_id),
                'signature_required': '1',
                'issue_now': 'true',
                'attachment': (BytesIO(b'%PDF-1.4\nTest agreement PDF'), 'deduction-agreement.pdf'),
            },
            content_type='multipart/form-data',
        )
        assert response.status_code == 302
        with app.app_context():
            document = HRDocument.query.filter_by(employee_id=staff_id).one()
            document_id = document.id
            assert document.status == 'Issued'
            assert document.attachment_sha256
            assert document.attachment_filename.endswith('.pdf')
            assert liability_cutoff_deduction(staff_id) == 0

        staff_client = app.test_client()
        with app.app_context():
            log_in(staff_client, db.session.get(Employee, staff_id))
        response = staff_client.get('/hr-documents')
        assert response.status_code == 200
        assert b'Deduction agreement' in response.data
        assert b'voluntarily authorize' in response.data
        assert staff_client.get(f'/hr-documents/{document_id}/attachment').status_code == 200

        other_client = app.test_client()
        with app.app_context():
            log_in(other_client, db.session.get(Employee, other_staff_id))
        assert other_client.get(f'/hr-documents/{document_id}/attachment').status_code == 403
        assert other_client.post(
            '/hr-documents',
            data={
                'action': 'sign',
                'document_id': str(document_id),
                'signature_method': 'typed',
                'signature_name': 'HR Staff',
                'signature_confirmation': '1',
            },
        ).status_code == 403

        drawn_document = admin_client.post(
            '/hr-documents',
            data={
                'employee_id': str(other_staff_id),
                'document_type': 'Memo',
                'subject': 'Drawn-signature memo',
                'signature_required': '1',
                'issue_now': 'true',
            },
        )
        assert drawn_document.status_code == 302
        with app.app_context():
            drawn_record = HRDocument.query.filter_by(subject='Drawn-signature memo').one()
            drawn_document_id = drawn_record.id
        image_data = 'data:image/png;base64,' + base64.b64encode(tiny_signature_png()).decode('ascii')
        response = other_client.post(
            '/hr-documents',
            data={
                'action': 'sign',
                'document_id': str(drawn_document_id),
                'signature_method': 'drawn',
                'signature_name': 'Other Staff',
                'signature_confirmation': '1',
                'signature_image': image_data,
            },
        )
        assert response.status_code == 302
        with app.app_context():
            drawn_record = db.session.get(HRDocument, drawn_document_id)
            assert drawn_record.employee_signature_method == 'drawn'
            assert drawn_record.employee_signature_image == image_data
        assert other_client.get(f'/hr-documents/{drawn_document_id}/download').status_code == 200

        response = staff_client.post(
            '/hr-documents',
            data={
                'action': 'sign',
                'document_id': str(document_id),
                'signature_method': 'typed',
                'signature_name': 'HR Staff',
                'signature_confirmation': '1',
            },
        )
        assert response.status_code == 302
        with app.app_context():
            document = db.session.get(HRDocument, document_id)
            assert document.status == 'Signed'
            assert document.employee_signature_method == 'typed'
            assert document.employee_signature_name == 'HR Staff'
            assert document.employee_signed_at is not None
            assert 'voluntarily authorize' in document.employee_signature_statement
            assert document.liability.acknowledgment_status == 'Acknowledged'
            assert liability_cutoff_deduction(staff_id) == 250
        assert staff_client.post(
            '/hr-documents',
            data={
                'action': 'sign',
                'document_id': str(document_id),
                'signature_method': 'typed',
                'signature_name': 'HR Staff',
                'signature_confirmation': '1',
            },
        ).status_code == 409
        signed_pdf = staff_client.get(f'/hr-documents/{document_id}/download')
        assert signed_pdf.status_code == 200
        assert signed_pdf.mimetype == 'application/pdf'

        wet_document = admin_client.post(
            '/hr-documents',
            data={
                'employee_id': str(other_staff_id),
                'document_type': 'Memo',
                'subject': 'Paper-signed memo',
                'signature_required': '1',
                'issue_now': 'true',
            },
        )
        assert wet_document.status_code == 302
        with app.app_context():
            wet_record = HRDocument.query.filter_by(subject='Paper-signed memo').one()
            wet_document_id = wet_record.id
        response = admin_client.post(
            '/hr-documents',
            data={
                'action': 'record_wet_signature',
                'document_id': str(wet_document_id),
                'wet_signed_copy': (BytesIO(b'%PDF-1.4\nWet-signed memo PDF'), 'signed-memo.pdf'),
            },
            content_type='multipart/form-data',
        )
        assert response.status_code == 302
        with app.app_context():
            wet_record = db.session.get(HRDocument, wet_document_id)
            assert wet_record.employee_signature_method == 'wet'
            assert wet_record.wet_signed_sha256
            assert wet_record.wet_signed_by == admin_id
        assert admin_client.get(f'/hr-documents/{wet_document_id}/wet-signed').status_code == 200
    finally:
        with app.app_context():
            db.session.rollback()
            for document in HRDocument.query.filter(
                HRDocument.employee_id.in_((staff_id, other_staff_id))
            ).all():
                db.session.delete(document)
            PayslipVerification.query.filter(
                PayslipVerification.employee_id.in_((staff_id, other_staff_id, admin_id))
            ).delete(synchronize_session=False)
            liability = db.session.get(EmployeeLiability, liability_id)
            if liability:
                db.session.delete(liability)
            for employee_id in (staff_id, other_staff_id, admin_id):
                employee = db.session.get(Employee, employee_id)
                if employee:
                    db.session.delete(employee)
            db.session.commit()


def test_hr_documents_do_not_show_drafts_to_staff_and_reject_bad_uploads():
    with app.app_context():
        admin = Employee(first_name='Draft', last_name='Admin', role='admin')
        staff = Employee(first_name='Draft', last_name='Staff', role='staff', company='Auto Expert')
        db.session.add_all([admin, staff])
        db.session.flush()
        draft = HRDocument(
            employee_id=staff.id,
            document_type='Memo',
            subject='Unissued memo',
            body='Draft only',
            status='Draft',
            signature_required=True,
        )
        db.session.add(draft)
        db.session.commit()
        admin_id, staff_id, document_id = admin.id, staff.id, draft.id
    try:
        staff_client = app.test_client()
        with app.app_context():
            log_in(staff_client, db.session.get(Employee, staff_id))
        response = staff_client.get('/hr-documents')
        assert response.status_code == 200
        assert b'Unissued memo' not in response.data

        admin_client = app.test_client()
        with app.app_context():
            log_in(admin_client, db.session.get(Employee, admin_id))
        response = admin_client.post(
            '/hr-documents',
            data={
                'employee_id': str(staff_id),
                'document_type': 'Memo',
                'subject': 'Invalid upload',
                'issue_now': 'true',
                'signature_required': '1',
                'attachment': (BytesIO(b'not a pdf'), 'not-a-pdf.pdf'),
            },
            content_type='multipart/form-data',
        )
        assert response.status_code == 302
        with app.app_context():
            assert HRDocument.query.filter_by(subject='Invalid upload').count() == 0
    finally:
        with app.app_context():
            db.session.rollback()
            db.session.delete(db.session.get(HRDocument, document_id))
            db.session.delete(db.session.get(Employee, staff_id))
            db.session.delete(db.session.get(Employee, admin_id))
            db.session.commit()
