from types import SimpleNamespace
from unittest.mock import patch

from hris import apply_approved_loan_balance


def test_approved_loan_updates_outstanding_once():
    employee = SimpleNamespace(loan_balance=1000.0)
    loan = SimpleNamespace(employee_id=7, amount=500.0, balance_applied=False)

    with patch("hris.db.session.get", return_value=employee):
        assert apply_approved_loan_balance(loan) is True
        assert apply_approved_loan_balance(loan) is False

    assert employee.loan_balance == 1500.0
    assert loan.balance_applied is True