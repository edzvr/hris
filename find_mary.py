import os
os.environ.setdefault('FLASK_ENV', 'development')
from hris import app, db, Employee

with app.app_context():
    # List all real employees
    all_staff = Employee.query.all()
    
    print("Real employees (with company):")
    for s in all_staff:
        full_name = s.full_name()
        # Skip templates
        if 'template' not in full_name.lower() and 'unselected' not in full_name.lower() and s.company:
            print(f'ID: {s.id}, Name: {full_name}, Company: {s.company}')


