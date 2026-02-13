from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from models import Checklist, Feedback, User, db
from routes.auth import login_required, role_required


faculty_bp = Blueprint("faculty", __name__, url_prefix="/faculty")


@faculty_bp.route("/dashboard")
@login_required
@role_required("faculty")
def dashboard():
	faculty_user = User.query.get(session["user_id"])
	approved_feedback = (
		Feedback.query.filter_by(faculty_id=faculty_user.id, status="approved")
		.order_by(Feedback.created_at.desc())
		.all()
	)
	checklists = (
		Checklist.query.filter_by(faculty_id=faculty_user.id)
		.order_by(Checklist.created_at.desc())
		.all()
	)

	return render_template(
		"dashboard_faculty.html",
		faculty=faculty_user,
		approved_feedback=approved_feedback,
		checklists=checklists,
	)


@faculty_bp.route("/checklist/create", methods=["POST"])
@login_required
@role_required("faculty")
def create_checklist():
	title = request.form.get("title", "").strip()
	description = request.form.get("description", "").strip()
	student_email = request.form.get("student_email", "").strip().lower()

	if not title or not student_email:
		flash("Title and student email are required.", "danger")
		return redirect(url_for("faculty.dashboard"))

	student = User.query.filter_by(email=student_email, role="student", is_active=True).first()
	if not student:
		flash("Student not found for the provided email.", "danger")
		return redirect(url_for("faculty.dashboard"))

	checklist = Checklist(
		title=title,
		description=description,
		faculty_id=session["user_id"],
		student_id=student.id,
	)
	db.session.add(checklist)
	db.session.commit()

	flash("Checklist created successfully.", "success")
	return redirect(url_for("faculty.dashboard"))
