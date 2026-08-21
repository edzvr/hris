import os
import sys
import subprocess
from datetime import date

# 1. Compile hris.py
print("=== 1. Compiling hris.py ===")
compile_res = subprocess.run([sys.executable, "-m", "compileall", "hris.py"], capture_output=True, text=True)
print("Return code:", compile_res.returncode)
print("Stdout:", compile_res.stdout)
print("Stderr:", compile_res.stderr)

try:
    from hris import app, db
    from models import Employee
except Exception as e:
    print("Failed to import app/models:", e)
    sys.exit(1)

# 2. Parse templates with Jinja2
print("\n=== 2. Parsing templates with Jinja ===")
templates_to_test = ['leave.html', 'loan.html']
parse_status = {}
with app.app_context():
    for name in templates_to_test:
        try:
            app.jinja_env.get_template(name)
            print(f"Template {name} successfully parsed by Jinja.")
            parse_status[name] = True
        except Exception as e:
            print(f"Failed to parse template {name}: {e}")
            parse_status[name] = False

# 3. GET /leave and /loan with a staff test client
print("\n=== 3. GET /leave and /loan with staff client ===")
client = app.test_client()
with app.app_context():
    staff_emp = Employee.query.filter_by(role='staff').first()
    if not staff_emp:
        print("No staff member found in database! Trying first employee.")
        staff_emp = Employee.query.first()
    
    if staff_emp:
        print(f"Using employee: ID={staff_emp.id}, Email={staff_emp.email}, Role={staff_emp.role}")
        with client.session_transaction() as sess:
            sess['user_id'] = staff_emp.id
            sess['_user_id'] = str(staff_emp.id)
            sess['user_role'] = staff_emp.role
            sess['logged_in'] = True
    else:
        print("No employee found in database at all!")

for route in ['/leave', '/loan']:
    try:
        resp = client.get(route)
        print(f"GET {route} status code: {resp.status_code}")
    except Exception as e:
        print(f"GET {route} failed with exception: {e}")

# 4. Form tag balance checks
print("\n=== 4. HTML Form Tag Balance Check ===")
for prefix in ['templates', os.path.join('hris', 'templates')]:
    for name in templates_to_test:
        path = os.path.join(prefix, name)
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    content = f.read()
                form_open = content.lower().count('<form')
                form_close = content.lower().count('</form>')
                print(f"File {path}:")
                print(f"  <form occurrences: {form_open}")
                print(f"  </form> occurrences: {form_close}")
                if form_open == form_close:
                    print("  Status: BALANCED")
                else:
                    print("  Status: UNBALANCED (!!!)")
            except Exception as e:
                print(f"Error reading {path}: {e}")
