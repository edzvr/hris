from sqlalchemy import inspect

from hris import Evaluation, app, db, generate_auto_quiz


def test_evaluation_schema_and_quiz_generator():
    with app.app_context():
        Evaluation.query.filter_by(employee_id=1).all()
        nested = db.session.begin_nested()
        generate_auto_quiz("General", num_questions=1)
        nested.rollback()
        columns = [column["name"] for column in inspect(db.engine).get_columns("evaluations")]
        assert "category" in columns
