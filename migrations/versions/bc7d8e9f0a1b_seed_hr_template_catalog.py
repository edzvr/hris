"""Seed the HR template catalog from the TaxEase workbook."""

from alembic import op
import sqlalchemy as sa


revision = "bc7d8e9f0a1b"
down_revision = "ba6c7d8e9f0a"
branch_labels = None
depends_on = None


TEMPLATES = [
    ("a.) HR Essentials", "Employee Handbook", "Policies, code of conduct, benefits overview", "1_Employee_Handbook.docx"),
    ("b.) HR Policies and Documentation Templates", "Code of Conduct Policy", "Employee behavior standards", "Code_of_Conduct_Policy_Template.docx"),
    ("b.) HR Policies and Documentation Templates", "Data Privacy Policy", "Compliance with Data Privacy Act of 2012", "Data_Privacy_Policy.docx"),
    ("b.) HR Policies and Documentation Templates", "Anti-Sexual Harassment Policy", "Required under Safe Spaces Act", "Anti_Sexual_Harassment_Policy.docx"),
    ("b.) HR Policies and Documentation Templates", "Leave Policy", "Vacation, sick, maternity/paternity leave rules", "Leave_Policy.docx"),
    ("c.) Recruitment and Hiring", "Job Requisition Form", "Internal request to open a position", "Job_Requisition_Form_Template.docx"),
    ("c.) Recruitment and Hiring", "Candidate Evaluation Form", "Standardized scoring of applicants", "Candidate_Evaluation_Form_Template.docx"),
    ("c.) Recruitment and Hiring", "Pre-employment Checklist", "Requirements (NBI, SSS, Pag-IBIG, PhilHealth)", "Pre_Employment_Checklist_Template.docx"),
    ("c.) Recruitment and Hiring", "Employment Offer Sheet", "Summary of compensation package", "Employment_Offer_Sheet_Template.docx"),
    ("d.) Job Announcement Templates", "Job Posting Template", "Standard format for job ads", "Standardized_Job_Posting_Template.docx"),
    ("d.) Job Announcement Templates", "Internal Job Posting Template", "For promotions/transfers", "Internal_Job_Posting_Template.docx"),
    ("e.) Interview Templates", "Interview Guide (Structured)", "Pre-set questions per role", "Structured_Interview_Guide_Template.docx"),
    ("e.) Interview Templates", "Behavioral Interview Template", "STAR method-based questions", "STAR_Method_Behavioral_Interview_Template.docx"),
    ("e.) Interview Templates", "Panel Interview Evaluation Form", "Consolidated interviewer feedback", "Panel_Interview_Consolidated_Evaluation_Form.docx"),
    ("f.) Employees Skills and Proficiency Measuring Matrix", "Skills Matrix Template", "Tracks employee competency levels", "Skills_Matrix_Template.docx"),
    ("f.) Employees Skills and Proficiency Measuring Matrix", "Technical Competency Framework", "Role-based skill requirements", "Technical_Competency_Framework.docx"),
    ("g.) Performance Management", "KPI Template", "Role-specific performance metrics", "Role_Specific_KPI_Performance_Template.docx"),
    ("g.) Performance Management", "Performance Appraisal Form", "Quarterly/annual evaluation", "Performance_Appraisal_Form.docx"),
    ("g.) Performance Management", "360-Degree Feedback Form", "Multi-source performance review", "360_Degree_Feedback_Form.docx"),
    ("g.) Performance Management", "Performance Improvement Plan (PIP)", "For underperforming employees", "Performance_Improvement_Plan_Template.docx"),
    ("h.) Professional Development and Training", "Training Evaluation Form", "Post-training feedback", "Training_Evaluation_Form.docx"),
    ("h.) Professional Development and Training", "Training Attendance Sheet", "Tracks participation", "Training_Attendance_Sheet.docx"),
    ("h.) Professional Development and Training", "Training Needs Assessment", "Identifies training priorities", "Training_Needs_Analysis_Template.docx"),
    ("i.) Managing & Motivating Employees", "Employee Engagement Survey", "Measures satisfaction and morale", "Employee_Engagement_Survey.docx"),
    ("i.) Managing & Motivating Employees", "Employee Suggestion Form", "Collects ideas/feedback", "Employee_Suggestion_Form.docx"),
    ("j.) HR Benefits Management", "Benefits Enrollment Form", "Employee benefit selection", "Benefits_Enrollment_Form.docx"),
    ("j.) HR Benefits Management", "Leave Credits Tracker", "VL/SL balances", "Leave_Credits_Tracker.docx"),
    ("j.) HR Benefits Management", "Reimbursement Form", "Claims (medical, travel, etc.)", "Reimbursement_Claim_Form.docx"),
    ("k.) Health Insurance", "HMO Enrollment Form", "Employee & dependent registration", "HMO_Enrollment_Form_V2.docx"),
    ("k.) Health Insurance", "Medical Claim Form", "Insurance claims", "Medical_Claim_Form.docx"),
    ("k.) Health Insurance", "Health Declaration Form", "Pre-employment/annual", "Health_Declaration_Form.docx"),
    ("l.) Employee References", "Reference Check Form", "Verifies applicant background", "Reference_Check_Form.docx"),
    ("l.) Employee References", "Employment Verification Letter", "Confirms employment", "Employment_Verification_Request_Letter.docx"),
    ("m.) Employee Record Management", "201 File Checklist", "Employee document tracking", "Employee_201_File_Checklist.docx"),
    ("m.) Employee Record Management", "Personal Data Sheet", "Employee information form", "Personal_Data_Sheet.docx"),
    ("n.) Time and Attendance Templates", "Leave Application Form", "Employee leave requests", "Leave_Application_Form.docx"),
    ("n.) Time and Attendance Templates", "Overtime Authorization Form", "Approval for OT", "Overtime_Authorization_Form.docx"),
    ("o.) Employees Compliance Management", "Disciplinary Action Form", "Documentation of violations", "Disciplinary_Action_Notice_Template.docx"),
    ("o.) Employees Compliance Management", "Incident Report Form", "Workplace incidents", "Incident_Report_Form.docx"),
    ("o.) Employees Compliance Management", "Notice to Explain (NTE)", "Due process requirement", "Notice_to_Explain_Template.docx"),
    ("p.) Hiring and Firing Forms", "Employment Contract Template", "PH-compliant employment agreement", "Employment_Contract_Template.docx"),
    ("p.) Hiring and Firing Forms", "Probationary Evaluation Form", "Regularization assessment", "Probationary_Evaluation_Form.docx"),
    ("p.) Hiring and Firing Forms", "Clearance Form", "Exit clearance process", "Employee_Exit_Clearance_Form.docx"),
    ("p.) Hiring and Firing Forms", "Exit Interview Form", "Feedback from departing employees", "Exit_Interview_Form.docx"),
    ("p.) Hiring and Firing Forms", "Termination Notice Template", "Employer-initiated separation", "Termination_Notice_Template.docx"),
]


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "hr_templates" not in inspector.get_table_names():
        return

    templates = sa.table(
        "hr_templates",
        sa.column("title", sa.String),
        sa.column("category", sa.String),
        sa.column("description", sa.Text),
        sa.column("source_filename", sa.String),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )
    connection = op.get_bind()
    if connection.execute(sa.select(sa.func.count()).select_from(templates)).scalar_one():
        return
    connection.execute(
        templates.insert().values(
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        ),
        [
            {
                "category": category,
                "title": title,
                "description": description,
                "source_filename": filename,
            }
            for category, title, description, filename in TEMPLATES
        ],
    )


def downgrade():
    if "hr_templates" in sa.inspect(op.get_bind()).get_table_names():
        op.execute(sa.text("DELETE FROM hr_templates"))
