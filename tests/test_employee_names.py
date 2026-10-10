import pytest

from hris import app, normalize_employee_names
from models import Employee, db
from utils.names import format_person_name, format_suffix_name


@pytest.mark.parametrize("value,expected", [
    ("  MARY   JANE ", "Mary Jane"),
    ("aIdA eSPINOSA", "Aida Espinosa"),
    ("N/A", ""),
    (" n/a ", ""),
    ("ANA-MARIE O'NEIL", "Ana-Marie O'Neil"),
    ("Na", "Na"),
])
def test_person_name_format(value, expected):
    assert format_person_name(value) == expected


def test_name_assignment_and_full_name_exclude_placeholders():
    employee = Employee(
        first_name="AIDA", middle_name="N/A",
        last_name="ESPINOSA", suffix_name="n/a",
    )
    assert employee.first_name == "Aida"
    assert employee.middle_name is None
    assert employee.suffix_name is None
    assert employee.full_name() == "Aida Espinosa"
    employee.suffix_name = "iii"
    assert employee.full_name() == "Aida Espinosa III"
    assert format_suffix_name("JR") == "Jr."


def test_existing_names_are_cleaned_and_normalization_is_idempotent():
    with app.app_context():
        employee = Employee(first_name="Legacy", last_name="Name", role="staff")
        db.session.add(employee)
        db.session.commit()
        employee_id = employee.id
        try:
            db.session.execute(
                db.update(Employee).where(Employee.id == employee_id).values(
                    first_name="MARY JANE", middle_name="N/A",
                    last_name="ESPINOSA", suffix_name="N/A",
                )
            )
            db.session.commit()
            normalize_employee_names()
            db.session.expire_all()
            saved = db.session.get(Employee, employee_id)
            assert saved.first_name == "Mary Jane"
            assert saved.middle_name is None
            assert saved.suffix_name is None
            assert saved.full_name() == "Mary Jane Espinosa"
            normalize_employee_names()
            assert saved.full_name() == "Mary Jane Espinosa"
        finally:
            db.session.rollback()
            db.session.delete(db.session.get(Employee, employee_id))
            db.session.commit()
