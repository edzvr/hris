import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hris import app, Employee


STAFF_ROUTES = [
    "/dashboard_staff",
    "/staff-guide",
    "/attendance/{staff_id}",
    "/profile/{staff_id}",
    "/profile/{staff_id}/resume",
    "/attendance/correction",
    "/payroll/{staff_id}",
    "/payroll/{staff_id}/monthly",
    "/apply_ot",
    "/leave",
    "/loan",
    "/peer_evaluation",
    "/merit_demerit/{staff_id}",
    "/files",
    "/bulletin",
]

ADMIN_ROUTES = [
    "/dashboard_admin",
    "/admin/audit-logs",
    "/admin/monthly-deductions",
    "/admin/employee_201",
    "/admin/incidents",
    "/payroll_dashboard",
    "/holiday_overtime",
    "/evaluation_dashboard",
    "/manage_questions",
    "/admin/files",
    "/backup",
]


def login(client, employee):
    with client.session_transaction() as session:
        session["_user_id"] = str(employee.id)
        session["_fresh"] = True


def check_routes(client, routes, replacements):
    failures = []
    for route in routes:
        response = client.get(route.format(**replacements), follow_redirects=False)
        print(response.status_code, route)
        if response.status_code >= 500:
            failures.append((route, response.status_code))
    return failures


app.config["TESTING"] = False
with app.app_context():
    staff = Employee.query.filter(Employee.role.ilike("%staff%")).first()
    admin = Employee.query.filter(Employee.role.ilike("%admin%")).first()
    if not staff or not admin:
        raise SystemExit("A staff and admin account are required for the smoke test.")

    staff_client = app.test_client()
    login(staff_client, staff)
    failures = check_routes(staff_client, STAFF_ROUTES, {"staff_id": staff.id})

    admin_client = app.test_client()
    login(admin_client, admin)
    failures += check_routes(admin_client, ADMIN_ROUTES, {"staff_id": staff.id})

    if failures:
        raise SystemExit(f"Route failures: {failures}")

print("Authenticated route smoke test passed.")
