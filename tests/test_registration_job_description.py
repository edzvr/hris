from unittest.mock import patch

from hris import app


def test_registration_saves_job_description_separately_from_system_role():
    app.config["TESTING"] = True
    client = app.test_client()
    form_data = {
        "first_name": "Jamie",
        "last_name": "Sample",
        "dob": "1995-01-01",
        "role": "staff",
        "job_description": "Office Staff",
        "employment_status": "Probationary",
        "company": "Trece-Uno",
        "email": "jamie.sample@example.com",
        "password": "StrongPass1!",
        "contact_no": "09123456789",
        "date_started": "2020-01-01",
        "emergency_person": "Alex Sample",
        "emergency_contact": "09987654321",
    }

    with (
        patch("hris.db.session.add") as add,
        patch("hris.db.session.commit"),
    ):
        response = client.post("/register", data=form_data, follow_redirects=False)

    assert response.status_code == 302
    employee = add.call_args.args[0]
    assert employee.role == "staff"
    assert employee.job_description == "Office Staff"
    assert employee.employment_status == "Probationary"


def test_registration_requires_job_description():
    app.config["TESTING"] = True
    client = app.test_client()
    form_data = {
        "first_name": "Jamie",
        "last_name": "Sample",
        "dob": "1995-01-01",
        "role": "staff",
        "employment_status": "Regular",
        "company": "Trece-Uno",
        "email": "jamie.sample@example.com",
        "password": "StrongPass1!",
        "contact_no": "09123456789",
        "date_started": "2020-01-01",
        "emergency_person": "Alex Sample",
        "emergency_contact": "09987654321",
    }

    with patch("hris.db.session.add") as add:
        response = client.post("/register", data=form_data, follow_redirects=False)

    assert response.status_code == 302
    assert not add.called


def test_registration_rejects_unknown_employment_status():
    app.config["TESTING"] = True
    client = app.test_client()
    form_data = {
        "first_name": "Jamie",
        "last_name": "Sample",
        "dob": "1995-01-01",
        "role": "staff",
        "job_description": "Office Staff",
        "employment_status": "Manager",
        "company": "Trece-Uno",
        "email": "jamie.sample@example.com",
        "password": "StrongPass1!",
        "contact_no": "09123456789",
        "date_started": "2020-01-01",
        "emergency_person": "Alex Sample",
        "emergency_contact": "09987654321",
    }

    with patch("hris.db.session.add") as add:
        response = client.post("/register", data=form_data, follow_redirects=False)

    assert response.status_code == 302
    assert not add.called
