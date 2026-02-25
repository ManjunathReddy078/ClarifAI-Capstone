from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from sqlalchemy import or_

from models import Feedback, ModerationLog, User, db
from routes.auth import login_required, role_required


admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def _last_n_month_labels(count: int = 6):
	labels = []
	now = datetime.utcnow()
	year = now.year
	month = now.month

	for _ in range(count):
		labels.append((year, month))
		month -= 1
		if month == 0:
			month = 12
			year -= 1

	labels.reverse()
	return labels


@admin_bp.route("/dashboard")
@login_required
@role_required("admin")
def dashboard():
	pending_feedback = (
		Feedback.query.filter_by(status="under_review").order_by(Feedback.created_at.desc()).all()
	)
	all_feedback = Feedback.query.order_by(Feedback.created_at.desc()).all()
	moderation_logs = ModerationLog.query.order_by(ModerationLog.created_at.desc()).limit(30).all()
	user_counts = {
		"students": User.query.filter_by(role="student").count(),
		"faculty": User.query.filter_by(role="faculty").count(),
		"admins": User.query.filter_by(role="admin").count(),
		"active": User.query.filter_by(is_active=True).count(),
	}
	recent_users = User.query.order_by(User.created_at.desc()).limit(8).all()

	month_keys = _last_n_month_labels(6)
	month_labels = [datetime(year=year, month=month, day=1).strftime("%b %Y") for year, month in month_keys]
	month_positive = {f"{year:04d}-{month:02d}": 0 for year, month in month_keys}
	month_neutral = {f"{year:04d}-{month:02d}": 0 for year, month in month_keys}
	month_negative = {f"{year:04d}-{month:02d}": 0 for year, month in month_keys}

	for feedback in all_feedback:
		key = feedback.created_at.strftime("%Y-%m")
		if key in month_positive and feedback.sentiment == "positive":
			month_positive[key] += 1
		if key in month_neutral and feedback.sentiment == "neutral":
			month_neutral[key] += 1
		if key in month_negative and feedback.sentiment == "negative":
			month_negative[key] += 1

	chart_payload = {
		"role_labels": ["Students", "Faculty", "Admins"],
		"role_counts": [user_counts["students"], user_counts["faculty"], user_counts["admins"]],
		"trend_labels": month_labels,
		"trend_positive": [month_positive[f"{year:04d}-{month:02d}"] for year, month in month_keys],
		"trend_neutral": [month_neutral[f"{year:04d}-{month:02d}"] for year, month in month_keys],
		"trend_negative": [month_negative[f"{year:04d}-{month:02d}"] for year, month in month_keys],
	}

	return render_template(
		"dashboard_admin.html",
		pending_feedback=pending_feedback,
		moderation_logs=moderation_logs,
		user_counts=user_counts,
		recent_users=recent_users,
		chart_payload=chart_payload,
	)


@admin_bp.route("/users")
@login_required
@role_required("admin")
def users():
	search = request.args.get("q", "").strip()
	role = request.args.get("role", "all").strip().lower()
	status = request.args.get("status", "all").strip().lower()

	query = User.query.order_by(User.created_at.desc())

	if search:
		like = f"%{search}%"
		query = query.filter(
			or_(
				User.full_name.ilike(like),
				User.email.ilike(like),
				User.unique_user_code.ilike(like),
			)
		)

	if role in {"student", "faculty", "admin"}:
		query = query.filter(User.role == role)

	if status == "active":
		query = query.filter(User.is_active.is_(True))
	elif status == "inactive":
		query = query.filter(User.is_active.is_(False))

	users_list = query.all()
	return render_template(
		"admin_users.html",
		users_list=users_list,
		search=search,
		role=role,
		status=status,
	)


@admin_bp.route("/moderate/<int:feedback_id>", methods=["POST"])
@login_required
@role_required("admin")
def moderate(feedback_id: int):
	action = request.form.get("action", "").strip().lower()
	note = request.form.get("note", "").strip()
	next_url = request.form.get("next_url", "").strip()
	feedback = Feedback.query.get(feedback_id)

	if not feedback:
		flash("Feedback item not found.", "danger")
		return redirect(url_for("admin.dashboard"))

	if action == "approve":
		feedback.status = "approved"
	elif action == "reject":
		feedback.status = "rejected"
	elif action == "request_edit":
		feedback.status = "request_edit"
	else:
		flash("Invalid moderation action.", "danger")
		return redirect(url_for("admin.dashboard"))

	feedback.admin_note = note or None

	log = ModerationLog(
		feedback_id=feedback.id,
		admin_id=session["user_id"],
		action=action,
		note=note or None,
	)
	db.session.add(log)
	db.session.commit()

	flash("Moderation action saved.", "success")
	if next_url and next_url.startswith("/admin/"):
		return redirect(next_url)
	return redirect(url_for("admin.dashboard"))


@admin_bp.route("/manual-reset", methods=["POST"])
@login_required
@role_required("admin")
def manual_reset_password():
	email = request.form.get("email", "").strip().lower()
	new_password = request.form.get("new_password", "")

	if not email or not new_password:
		flash("Email and new password are required.", "danger")
		return redirect(url_for("admin.dashboard"))

	user = User.query.filter_by(email=email).first()
	if not user:
		flash("User not found.", "danger")
		return redirect(url_for("admin.dashboard"))

	user.set_password(new_password)
	db.session.commit()
	flash("Password reset successfully for the user.", "success")
	return redirect(url_for("admin.dashboard"))


@admin_bp.route("/users/<int:user_id>/toggle-active", methods=["POST"])
@login_required
@role_required("admin")
def toggle_user_active(user_id: int):
	user = User.query.get(user_id)
	if not user:
		flash("User not found.", "danger")
		return redirect(url_for("admin.users"))

	if user.id == session.get("user_id"):
		flash("You cannot deactivate your own account.", "danger")
		return redirect(url_for("admin.users"))

	user.is_active = not user.is_active
	db.session.commit()
	flash("User status updated successfully.", "success")
	return redirect(url_for("admin.users"))


@admin_bp.route("/moderation")
@login_required
@role_required("admin")
def moderation_page():
	search = request.args.get("q", "").strip()
	sentiment = request.args.get("sentiment", "all").strip().lower()
	faculty = request.args.get("faculty", "all").strip().lower()

	query = Feedback.query.filter_by(status="under_review").order_by(Feedback.created_at.desc())

	if search:
		like = f"%{search}%"
		query = query.filter(
			or_(
				Feedback.subject.ilike(like),
				Feedback.reason.ilike(like),
				Feedback.feedback_text.ilike(like),
			)
		)

	if sentiment in {"positive", "neutral", "negative"}:
		query = query.filter(Feedback.sentiment == sentiment)

	faculty_list = User.query.filter_by(role="faculty", is_active=True).order_by(User.full_name.asc()).all()
	if faculty != "all":
		selected_faculty = User.query.filter_by(role="faculty", is_active=True, email=faculty).first()
		if selected_faculty:
			query = query.filter(Feedback.faculty_id == selected_faculty.id)

	pending_feedback = query.all()
	kpi = {
		"total": len(pending_feedback),
		"positive": len([item for item in pending_feedback if item.sentiment == "positive"]),
		"neutral": len([item for item in pending_feedback if item.sentiment == "neutral"]),
		"negative": len([item for item in pending_feedback if item.sentiment == "negative"]),
	}
	return render_template(
		"moderation.html",
		pending_feedback=pending_feedback,
		search=search,
		sentiment=sentiment,
		faculty=faculty,
		faculty_list=faculty_list,
		kpi=kpi,
	)
