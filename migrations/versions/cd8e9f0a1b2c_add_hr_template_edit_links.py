"""Add the workbook's online editable template links."""

from alembic import op
import sqlalchemy as sa


revision = "cd8e9f0a1b2c"
down_revision = "bc7d8e9f0a1b"
branch_labels = None
depends_on = None


TEMPLATE_LINKS = {
    "1_Employee_Handbook.docx": "https://docs.google.com/document/d/19PVtbCDB10_NUDcXET4ztkniIcWnJvLI/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
    "Code_of_Conduct_Policy_Template.docx": "https://docs.google.com/document/d/1hYLqCGw_UNca_K1_qYflwEzvl6sE6TT0/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
    "Data_Privacy_Policy.docx": "https://docs.google.com/document/d/15A9zTdD-P_P_R4qgBTKyRZduhL7oKeHR/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
    "Anti_Sexual_Harassment_Policy.docx": "https://docs.google.com/document/d/14iBvy5vj-a0WscRZ8o8I6NJA4tNX3oMa/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
    "Leave_Policy.docx": "https://docs.google.com/document/d/1PShEi7S2Wy39f4SVlfgybRH0NLC1bk8O/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
    "Job_Requisition_Form_Template.docx": "https://docs.google.com/document/d/1eiOtUSAG3zOVwIg9p2dC9QsYvfqJ7sYy/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
    "Candidate_Evaluation_Form_Template.docx": "https://docs.google.com/document/d/1rpPoOzqVlwZRJ6dRiF5d7S7nrRIoAcXS/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
    "Pre_Employment_Checklist_Template.docx": "https://docs.google.com/document/d/1HfyZIekb-CObu6RFmhkT_cmLn1oJi_Wq/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
    "Employment_Offer_Sheet_Template.docx": "https://docs.google.com/document/d/1H9c5auTheOubyWUj4MSDBfL6cJJJNS_-/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
    "Standardized_Job_Posting_Template.docx": "https://docs.google.com/document/d/1nVlyyObz3htmr2SuGrgzhMkTYY8sR5Go/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
    "Internal_Job_Posting_Template.docx": "https://docs.google.com/document/d/1K3kN07xeUSzkzoam4qoOZJe9BomGJ1GP/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
    "Structured_Interview_Guide_Template.docx": "https://docs.google.com/document/d/16IHU3c8SQngPLPxCMrefSJ5wi24XZtU5/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
    "STAR_Method_Behavioral_Interview_Template.docx": "https://docs.google.com/document/d/1CZYmNIAqpaVnK3mqZ-ZFSRZ8Va6bt3k0/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
    "Panel_Interview_Consolidated_Evaluation_Form.docx": "https://docs.google.com/document/d/19N9HSwEGaBglIGPfw7s-OR84zLXbLIVj/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
    "Skills_Matrix_Template.docx": "https://docs.google.com/document/d/1OLLEu4Xf-NVAtUSaszUVkqls1O6h1UY4/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
    "Technical_Competency_Framework.docx": "https://docs.google.com/document/d/1NSr8N4BlIkTpQkd_KCT2lqhSi6QlBEYN/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
    "Role_Specific_KPI_Performance_Template.docx": "https://docs.google.com/document/d/1bhNYCjaHnPdEU0Jy6QsQ0BBvryZidoEV/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
    "Performance_Appraisal_Form.docx": "https://docs.google.com/document/d/1CJVZdb_WaQ2XLzQl4jrIEkK9EisyDfc6/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
    "360_Degree_Feedback_Form.docx": "https://docs.google.com/document/d/10dwQcytQsl_tVy1Hf1W5AE_24R_PV9MR/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
    "Performance_Improvement_Plan_Template.docx": "https://docs.google.com/document/d/1FlorGUzjGGOKOIYh1Xy4ezTp-F888ILe/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
    "Training_Evaluation_Form.docx": "https://docs.google.com/document/d/1Yjd12PmOoOjIsE7pMht8UmEPGt9XVAJA/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
    "Training_Attendance_Sheet.docx": "https://docs.google.com/document/d/11LUptiKUKIEenmo6Kgrnmimqc9LjBp13/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
    "Training_Needs_Analysis_Template.docx": "https://docs.google.com/document/d/1bJIxq9qQhKTSBQU9LovZlqrBa2-QiiDT/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
    "Employee_Engagement_Survey.docx": "https://docs.google.com/document/d/1BAONg-WVOPuswnsx5iEqMLAnGROnZotk/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
    "Employee_Suggestion_Form.docx": "https://docs.google.com/document/d/1U2rkewYgrcOOYnIB6sGS7BeBD3F9hA17/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
    "Benefits_Enrollment_Form.docx": "https://docs.google.com/document/d/1kHfgDYv_1aNsVPOC3TnkItaNAt1B59ue/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
    "Leave_Credits_Tracker.docx": "https://docs.google.com/document/d/1PFujqVReF5CMUQL-_S0Pwyr22KnChxmJ/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
    "Reimbursement_Claim_Form.docx": "https://docs.google.com/document/d/1PUaKeg_h_OHGGDvPnbgGUYr_1qEJXCCM/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
    "HMO_Enrollment_Form_V2.docx": "https://docs.google.com/document/d/15Ee-jvmNxzNgNrj3CK8LI-R5PJTRkRNF/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
    "Medical_Claim_Form.docx": "https://docs.google.com/document/d/1ksDSk6rd9HH-svrsU-G0cg3aQ1yiqGwJ/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
    "Health_Declaration_Form.docx": "https://docs.google.com/document/d/16LFWdMdcXPf51YMpt7N0Pbyjp5jxr-UC/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
    "Reference_Check_Form.docx": "https://docs.google.com/document/d/1Jdw6a-6PP9iQ2nk4qVaY7JPvicJ_7ANd/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
    "Employment_Verification_Request_Letter.docx": "https://docs.google.com/document/d/1bexPXV0OJDyjnixlMBuZuUSqZ5wzfvkb/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
    "Employee_201_File_Checklist.docx": "https://docs.google.com/document/d/1HAIBkemsnu91LQ468IvRGRcyAt1ahoYR/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
    "Personal_Data_Sheet.docx": "https://docs.google.com/document/d/1pddOQslTVpI1qG_uf3Jrf1DcdkZBN_v-/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
    "Leave_Application_Form.docx": "https://docs.google.com/document/d/1Z2EHa10Hs2vAmwFsSdtW9bsQAAX2VH1B/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
    "Overtime_Authorization_Form.docx": "https://docs.google.com/document/d/1NnDCqR9Pqc3oAPyBmtzaj3a52XDKqgxo/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
    "Disciplinary_Action_Notice_Template.docx": "https://docs.google.com/document/d/1L7vuA1rrNAlagFwm0Ud3IEirv4O44BMJ/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
    "Incident_Report_Form.docx": "https://docs.google.com/document/d/1wAMDheWKWneVt3hFGksDeqfTY171GnmA/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
    "Notice_to_Explain_Template.docx": "https://docs.google.com/document/d/130nutBQ6bR6uWsSU77CykvtD4oTo0IJF/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
    "Employment_Contract_Template.docx": "https://docs.google.com/document/d/1XSAG_vcvysasz2ZzWccaWocNPPxdMctL/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
    "Probationary_Evaluation_Form.docx": "https://docs.google.com/document/d/1QTgLTG54lknUTgf75yyNGjlthwo2BmHk/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
    "Employee_Exit_Clearance_Form.docx": "https://docs.google.com/document/d/1QyzhjkQYcLky3xE8dV4a77v9NsWTN76y/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
    "Exit_Interview_Form.docx": "https://docs.google.com/document/d/1dg4j1GHsG1YzuHuZ0KVfQHokvEYhhMF2/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
    "Termination_Notice_Template.docx": "https://docs.google.com/document/d/1KDPV68SrclbgpgnwsRd79h7_gGOhu6kE/edit?usp=drive_link&ouid=112419035597301080727&rtpof=true&sd=true",
}


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "hr_templates" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("hr_templates")}
    if not {"source_filename", "online_url"}.issubset(columns):
        return

    templates = sa.table(
        "hr_templates",
        sa.column("source_filename", sa.String),
        sa.column("online_url", sa.String),
    )
    connection = op.get_bind()
    for filename, url in TEMPLATE_LINKS.items():
        connection.execute(
            templates.update()
            .where(templates.c.source_filename == filename)
            .where(sa.or_(templates.c.online_url.is_(None), templates.c.online_url == ""))
            .values(online_url=url)
        )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if "hr_templates" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("hr_templates")}
    if not {"source_filename", "online_url"}.issubset(columns):
        return

    templates = sa.table(
        "hr_templates",
        sa.column("source_filename", sa.String),
        sa.column("online_url", sa.String),
    )
    connection = op.get_bind()
    for filename, url in TEMPLATE_LINKS.items():
        connection.execute(
            templates.update()
            .where(templates.c.source_filename == filename)
            .where(templates.c.online_url == url)
            .values(online_url=None)
        )
