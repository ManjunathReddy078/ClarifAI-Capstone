from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from sqlalchemy import or_

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
		completed_count=len([item for item in checklists if item.is_completed]),
		pending_count=len([item for item in checklists if not item.is_completed]),
	)


@faculty_bp.route("/reviews")
@login_required
@role_required("faculty")
def reviews():
	search = request.args.get("q", "").strip()
	sentiment = request.args.get("sentiment", "all").strip().lower()

	base_query = Feedback.query.filter_by(faculty_id=session["user_id"], status="approved")
	query = base_query.order_by(Feedback.created_at.desc())

	if search:
		like = f"%{search}%"
		query = query.filter(
			or_(
				Feedback.feedback_text.ilike(like),
				Feedback.subject.ilike(like),
				Feedback.semester.ilike(like),
				Feedback.reason.ilike(like),
			)
		)

	if sentiment in {"positive", "neutral", "negative"}:
		query = query.filter(Feedback.sentiment == sentiment)

	reviews = query.all()
	kpi = {
		"total": base_query.count(),
		"positive": base_query.filter_by(sentiment="positive").count(),
		"neutral": base_query.filter_by(sentiment="neutral").count(),
		"negative": base_query.filter_by(sentiment="negative").count(),
	}
	return render_template(
		"faculty_reviews.html",
		reviews=reviews,
		search=search,
		sentiment=sentiment,
		kpi=kpi,
	)


@faculty_bp.route("/checklists")
@login_required
@role_required("faculty")
def checklists_page():
	status = request.args.get("status", "all").strip().lower()
	query = Checklist.query.filter_by(faculty_id=session["user_id"]).order_by(Checklist.created_at.desc())
	checklists = query.all()

	if status == "completed":
		checklists = [item for item in checklists if item.is_completed]
	elif status == "pending":
		checklists = [item for item in checklists if not item.is_completed]

	return render_template("faculty_checklists.html", checklists=checklists, status=status)


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


@faculty_bp.route("/checklist/<int:checklist_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("faculty")
def edit_checklist(checklist_id: int):
	checklist = Checklist.query.filter_by(id=checklist_id, faculty_id=session["user_id"]).first()
	if not checklist:
		flash("Checklist not found.", "danger")
		return redirect(url_for("faculty.checklists_page"))

	if request.method == "POST":
		title = request.form.get("title", "").strip()
		description = request.form.get("description", "").strip()
		student_email = request.form.get("student_email", "").strip().lower()

		if not title or not student_email:
			flash("Title and student email are required.", "danger")
			return render_template("faculty_checklist_edit.html", checklist=checklist)

		student = User.query.filter_by(email=student_email, role="student", is_active=True).first()
		if not student:
			flash("Student not found for the provided email.", "danger")
			return render_template("faculty_checklist_edit.html", checklist=checklist)

		checklist.title = title
		checklist.description = description
		checklist.student_id = student.id
		db.session.commit()
		flash("Checklist updated successfully.", "success")
		return redirect(url_for("faculty.checklists_page"))

	return render_template("faculty_checklist_edit.html", checklist=checklist)


@faculty_bp.route("/checklist/<int:checklist_id>/delete", methods=["POST"])
@login_required
@role_required("faculty")
def delete_checklist(checklist_id: int):
	checklist = Checklist.query.filter_by(id=checklist_id, faculty_id=session["user_id"]).first()
	if not checklist:
		flash("Checklist not found.", "danger")
		return redirect(url_for("faculty.checklists_page"))

	db.session.delete(checklist)
	db.session.commit()
	flash("Checklist deleted successfully.", "success")
	return redirect(url_for("faculty.checklists_page"))
