from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from models import Feedback, ModerationLog, User, db
from routes.auth import login_required, role_required


admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.route("/dashboard")
@login_required
@role_required("admin")
def dashboard():
	pending_feedback = (
		Feedback.query.filter_by(status="under_review").order_by(Feedback.created_at.desc()).all()
	)
	moderation_logs = ModerationLog.query.order_by(ModerationLog.created_at.desc()).limit(30).all()
	return render_template(
		"dashboard_admin.html",
		pending_feedback=pending_feedback,
		moderation_logs=moderation_logs,
	)


@admin_bp.route("/moderate/<int:feedback_id>", methods=["POST"])
@login_required
@role_required("admin")
def moderate(feedback_id: int):
	action = request.form.get("action", "").strip().lower()
	note = request.form.get("note", "").strip()
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


@admin_bp.route("/moderation")
@login_required
@role_required("admin")
def moderation_page():
	pending_feedback = (
		Feedback.query.filter_by(status="under_review").order_by(Feedback.created_at.desc()).all()
	)
	return render_template("moderation.html", pending_feedback=pending_feedback)
