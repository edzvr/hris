# routes/assessment.py
from flask import Blueprint, render_template, request, redirect, url_for, flash
from models import Quiz, QuizResult, Evaluation, Employee, Bulletin
from utils.helpers import compute_merit_demerit, ai_suggestion
import os, csv
from werkzeug.utils import secure_filename
from datetime import datetime

assessment_bp = Blueprint('assessment', __name__)

@app.route('/assessment', methods=['GET','POST'])
@login_required
def assessment():
    action = request.args.get("action")
    quiz_id = request.args.get("quiz_id")

    # ------------------ QUIZ UPLOAD ------------------
    if request.method == 'POST' and action == "upload_quiz":
        file = request.files['quiz_file']
        if file:
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)

            if filename.endswith('.csv'):
                with open(filepath, newline='', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        q = Quiz(
                            question=row['Question'],
                            choice_a=row['OptionA'],
                            choice_b=row['OptionB'],
                            choice_c=row.get('OptionC'),
                            choice_d=row.get('OptionD'),
                            correct_answer=row['CorrectAnswer'],
                            points=1
                        )
                        db.session.add(q)
                db.session.commit()
                flash("✅ CSV Quiz uploaded!", "success")

            elif filename.endswith('.xlsx'):
                import pandas as pd
                df = pd.read_excel(filepath)
                for _, row in df.iterrows():
                    q = Quiz(
                        question=row['Question'],
                        choice_a=row['OptionA'],
                        choice_b=row['OptionB'],
                        choice_c=row.get('OptionC'),
                        choice_d=row.get('OptionD'),
                        correct_answer=row['CorrectAnswer'],
                        points=1
                    )
                    db.session.add(q)
                db.session.commit()
                flash("✅ Excel Quiz uploaded!", "success")
            else:
                flash("❌ Invalid file format.", "danger")
        return redirect(url_for('assessment'))

    # ------------------ QUIZ RESULTS ------------------
    if action == "quiz_results":
        results = db.session.query(
            QuizResult, Employee.first_name, Employee.last_name, Quiz.question.label("quiz_title")
        ).join(Employee, QuizResult.employee_id == Employee.id) \
         .join(Quiz, QuizResult.quiz_id == Quiz.id).all()
        return render_template("assessment.html", results=results, view="quiz_results")

    # ------------------ QUIZ LEADERBOARD ------------------
    if action == "quiz_leaderboard" and quiz_id:
        results = db.session.query(
            QuizResult, Employee.first_name, Employee.last_name
        ).join(Employee, QuizResult.employee_id == Employee.id) \
         .filter(QuizResult.quiz_id == quiz_id) \
         .order_by((QuizResult.score * 1.0 / QuizResult.total_points).desc()).all()
        return render_template("assessment.html", results=results, view="quiz_leaderboard")

    # ------------------ EVALUATION DASHBOARD ------------------
    if action == "evaluation":
        evaluations = Evaluation.query.filter_by(employee_id=current_user.id).all()

        # Notifications kung may kulang na evaluation
        month_start = datetime(datetime.today().year, datetime.today().month, 1)
        pending_evals = Employee.query.filter(
            ~Employee.evaluations.any(Evaluation.date >= month_start)
        ).all()
        if pending_evals and current_user.role.lower() == "admin":
            flash(f"⚠️ {len(pending_evals)} employees still need evaluation this month!", "warning")

        return render_template("assessment.html", evaluations=evaluations, view="evaluation")

    # ------------------ PEER EVALUATION ------------------
    if request.method == 'POST' and action == "peer_eval":
        score = int(request.form.get('score'))
        remarks = request.form.get('remarks')

        # Anti-cheating: require remarks if score extreme
        if score >= 9 or score <= 2:
            if not remarks:
                flash("❌ Remarks required for extreme scores.", "danger")
                return redirect(url_for('assessment', action="evaluation"))

        new_eval = Evaluation(
            employee_id=request.form.get('employee_id'),
            evaluator_id=current_user.id,
            score=score,
            remarks=remarks
        )
        db.session.add(new_eval)
        db.session.commit()
        flash("✅ Peer evaluation submitted!", "success")
        return redirect(url_for('assessment', action="evaluation"))

    # ------------------ HISTORY ------------------
    if action == "history":
        evals = Evaluation.query.filter_by(employee_id=current_user.id).order_by(Evaluation.date.desc()).all()
        quiz_results = QuizResult.query.filter_by(employee_id=current_user.id).order_by(QuizResult.date_taken.desc()).all()
        return render_template("assessment.html", evals=evals, quiz_results=quiz_results, view="history")

    # ------------------ EXPORT ------------------
    if action in ["pdf","csv","excel"]:
        evals = Evaluation.query.filter_by(employee_id=current_user.id).all()
        quiz_results = QuizResult.query.filter_by(employee_id=current_user.id).all()
        return render_template("assessment.html", evals=evals, quiz_results=quiz_results, view=action)

    # ------------------ AI INSIGHTS ------------------
    if action == "ai_insights":
        merit, demerit, total = compute_merit_demerit(current_user.id, datetime.today().month)
        suggestion = ai_suggestion(total)

        if total < 5:
            # low performance → private suggestion only
            return render_template("assessment.html", suggestion=suggestion, view="ai_private")
        else:
            # good performance → post to bulletin
            new_post = Bulletin(title="AI Performance Insight", message=suggestion, author="System")
            db.session.add(new_post)
            db.session.commit()
            return render_template("assessment.html", suggestion=suggestion, view="ai_public")

    # ------------------ DEFAULT MENU ------------------
    return render_template("assessment.html", view="menu")
