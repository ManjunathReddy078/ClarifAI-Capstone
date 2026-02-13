from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from models import Checklist, Feedback, KnowledgePost, User, db
from routes.auth import login_required, role_required
from sentiment import analyze_sentiment


student_bp = Blueprint("student", __name__, url_prefix="/student")


@student_bp.route("/dashboard")
@login_required
@role_required("student")
def dashboard():
	student = User.query.get(session["user_id"])
	all_faculty = User.query.filter_by(role="faculty", is_active=True).order_by(User.full_name.asc()).all()
	feedback_items = (
		Feedback.query.filter_by(student_id=student.id).order_by(Feedback.created_at.desc()).all()
	)
	checklists = Checklist.query.filter_by(student_id=student.id).order_by(Checklist.created_at.desc()).all()
	posts = KnowledgePost.query.order_by(KnowledgePost.created_at.desc()).limit(8).all()

	total_checklists = len(checklists)
	completed_checklists = len([item for item in checklists if item.is_completed])
	progress_percent = int((completed_checklists / total_checklists) * 100) if total_checklists else 0

	return render_template(
		"dashboard_student.html",
		student=student,
		faculty_list=all_faculty,
		feedback_items=feedback_items,
		checklists=checklists,
		posts=posts,
		progress_percent=progress_percent,
	)


@student_bp.route("/submit-feedback", methods=["POST"])
@login_required
@role_required("student")
def submit_feedback():
	faculty_id = request.form.get("faculty_id", "").strip()
	feedback_text = request.form.get("feedback_text", "").strip()

	if not faculty_id or not feedback_text:
		flash("Faculty and feedback text are required.", "danger")
		return redirect(url_for("student.dashboard"))

	faculty = User.query.filter_by(id=faculty_id, role="faculty", is_active=True).first()
	if not faculty:
		flash("Selected faculty does not exist.", "danger")
		return redirect(url_for("student.dashboard"))

	sentiment = analyze_sentiment(feedback_text)
	status = "approved" if sentiment == "positive" else "under_review"

	feedback = Feedback(
		student_id=session["user_id"],
		faculty_id=faculty.id,
		feedback_text=feedback_text,
		sentiment=sentiment,
		status=status,
	)
	db.session.add(feedback)
	db.session.commit()

	flash("Feedback submitted successfully.", "success")
	return redirect(url_for("student.dashboard"))


@student_bp.route("/knowledge-board")
@login_required
@role_required("student")
def knowledge_board():
	posts = KnowledgePost.query.order_by(KnowledgePost.created_at.desc()).all()
	return render_template("knowledge_board.html", posts=posts)


@student_bp.route("/knowledge-post", methods=["GET", "POST"])
@login_required
@role_required("student")
def knowledge_post():
	if request.method == "POST":
		title = request.form.get("title", "").strip()
		content = request.form.get("content", "").strip()

		if not title or not content:
			flash("Title and content are required.", "danger")
			return render_template("knowledge_post.html")

		post = KnowledgePost(title=title, content=content, author_id=session["user_id"])
		db.session.add(post)
		db.session.commit()

		flash("Knowledge post shared successfully.", "success")
		return redirect(url_for("student.knowledge_board"))

	return render_template("knowledge_post.html")


@student_bp.route("/checklist/<int:checklist_id>/toggle", methods=["POST"])
@login_required
@role_required("student")
def toggle_checklist(checklist_id: int):
	checklist = Checklist.query.filter_by(id=checklist_id, student_id=session["user_id"]).first()
	if not checklist:
		flash("Checklist item not found.", "danger")
		return redirect(url_for("student.dashboard"))

	checklist.is_completed = not checklist.is_completed
	db.session.commit()
	flash("Checklist status updated.", "success")
	return redirect(url_for("student.dashboard"))


@student_bp.route("/submit-feedback-page")
@login_required
@role_required("student")
def submit_feedback_page():
	faculty_list = User.query.filter_by(role="faculty", is_active=True).order_by(User.full_name.asc()).all()
	return render_template("submit_feedback.html", faculty_list=faculty_list)
