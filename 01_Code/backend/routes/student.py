from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from sqlalchemy import or_

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
		Feedback.query.filter_by(student_id=student.id).order_by(Feedback.created_at.desc()).limit(5).all()
	)
	total_feedback = Feedback.query.filter_by(student_id=student.id).count()
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
		total_feedback=total_feedback,
		checklists=checklists,
		posts=posts,
		progress_percent=progress_percent,
	)


def _validate_feedback_form(form_data):
	faculty_id = form_data.get("faculty_id", "").strip()
	subject = form_data.get("subject", "").strip()
	semester = form_data.get("semester", "").strip()
	reason = form_data.get("reason", "").strip()
	feedback_text = form_data.get("feedback_text", "").strip()

	if not faculty_id or not subject or not semester or not reason or not feedback_text:
		return None, "All review fields are required."

	faculty = User.query.filter_by(id=faculty_id, role="faculty", is_active=True).first()
	if not faculty:
		return None, "Selected faculty does not exist."

	validated = {
		"faculty": faculty,
		"subject": subject,
		"semester": semester,
		"reason": reason,
		"feedback_text": feedback_text,
	}
	return validated, None


def _apply_feedback_payload(feedback_item: Feedback, payload: dict) -> None:
	feedback_item.faculty_id = payload["faculty"].id
	feedback_item.subject = payload["subject"]
	feedback_item.semester = payload["semester"]
	feedback_item.reason = payload["reason"]
	feedback_item.feedback_text = payload["feedback_text"]
	feedback_item.sentiment = analyze_sentiment(payload["feedback_text"])
	feedback_item.status = "approved" if feedback_item.sentiment == "positive" else "under_review"
	feedback_item.admin_note = None


@student_bp.route("/submit-feedback", methods=["POST"])
@login_required
@role_required("student")
def submit_feedback():
	payload, error = _validate_feedback_form(request.form)
	if error:
		flash(error, "danger")
		return redirect(url_for("student.dashboard"))

	feedback = Feedback(student_id=session["user_id"], faculty_id=payload["faculty"].id)
	_apply_feedback_payload(feedback, payload)
	db.session.add(feedback)
	db.session.commit()

	flash("Review submitted successfully.", "success")
	return redirect(url_for("student.dashboard"))


@student_bp.route("/reviews")
@login_required
@role_required("student")
def reviews():
	search = request.args.get("q", "").strip()
	sentiment = request.args.get("sentiment", "all").strip().lower()
	status = request.args.get("status", "all").strip().lower()

	query = (
		Feedback.query.join(User, Feedback.faculty_id == User.id)
		.filter(Feedback.student_id == session["user_id"])
		.order_by(Feedback.created_at.desc())
	)

	if search:
		like = f"%{search}%"
		query = query.filter(
			or_(
				Feedback.subject.ilike(like),
				Feedback.reason.ilike(like),
				Feedback.feedback_text.ilike(like),
				User.full_name.ilike(like),
			)
		)

	if sentiment in {"positive", "neutral", "negative"}:
		query = query.filter(Feedback.sentiment == sentiment)

	if status in {"under_review", "approved", "rejected", "request_edit"}:
		query = query.filter(Feedback.status == status)

	feedback_items = query.all()
	faculty_list = User.query.filter_by(role="faculty", is_active=True).order_by(User.full_name.asc()).all()

	return render_template(
		"student_reviews.html",
		feedback_items=feedback_items,
		faculty_list=faculty_list,
		search=search,
		sentiment=sentiment,
		status=status,
	)


@student_bp.route("/reviews/create", methods=["POST"])
@login_required
@role_required("student")
def create_review():
	payload, error = _validate_feedback_form(request.form)
	if error:
		flash(error, "danger")
		return redirect(url_for("student.reviews"))

	feedback = Feedback(student_id=session["user_id"], faculty_id=payload["faculty"].id)
	_apply_feedback_payload(feedback, payload)
	db.session.add(feedback)
	db.session.commit()

	flash("Review created successfully.", "success")
	return redirect(url_for("student.reviews"))


@student_bp.route("/reviews/<int:feedback_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("student")
def edit_review(feedback_id: int):
	feedback_item = Feedback.query.filter_by(id=feedback_id, student_id=session["user_id"]).first()
	if not feedback_item:
		flash("Review not found.", "danger")
		return redirect(url_for("student.reviews"))

	faculty_list = User.query.filter_by(role="faculty", is_active=True).order_by(User.full_name.asc()).all()

	if request.method == "POST":
		payload, error = _validate_feedback_form(request.form)
		if error:
			flash(error, "danger")
			return render_template(
				"student_review_edit.html",
				feedback_item=feedback_item,
				faculty_list=faculty_list,
			)

		_apply_feedback_payload(feedback_item, payload)
		db.session.commit()
		flash("Review updated successfully.", "success")
		return redirect(url_for("student.reviews"))

	return render_template(
		"student_review_edit.html",
		feedback_item=feedback_item,
		faculty_list=faculty_list,
	)


@student_bp.route("/reviews/<int:feedback_id>/delete", methods=["POST"])
@login_required
@role_required("student")
def delete_review(feedback_id: int):
	feedback_item = Feedback.query.filter_by(id=feedback_id, student_id=session["user_id"]).first()
	if not feedback_item:
		flash("Review not found.", "danger")
		return redirect(url_for("student.reviews"))

	db.session.delete(feedback_item)
	db.session.commit()
	flash("Review deleted successfully.", "success")
	return redirect(url_for("student.reviews"))


@student_bp.route("/knowledge-board")
@login_required
@role_required("student")
def knowledge_board():
	posts = (
		KnowledgePost.query.join(User, KnowledgePost.author_id == User.id)
		.filter(User.role.in_(["student", "faculty"]))
		.order_by(KnowledgePost.created_at.desc())
		.all()
	)
	return render_template(
		"knowledge_board.html",
		posts=posts,
		board_page_title="Experience & Resource Board",
		board_heading="Faculty Resources and Student Experiences",
		my_posts_url=url_for("student.my_knowledge_posts"),
		my_posts_label="My Experiences",
		create_post_url=url_for("student.knowledge_post"),
		create_post_label="Share Experience",
		empty_message="No resources or experiences available yet.",
	)


@student_bp.route("/knowledge/my-posts")
@login_required
@role_required("student")
def my_knowledge_posts():
	posts = (
		KnowledgePost.query.filter_by(author_id=session["user_id"])
		.order_by(KnowledgePost.created_at.desc())
		.all()
	)
	return render_template(
		"student_my_posts.html",
		posts=posts,
		page_title="My Experiences",
		heading="My Experiences",
		board_url=url_for("student.knowledge_board"),
		board_label="Experience & Resource Board",
		create_url=url_for("student.knowledge_post"),
		create_label="Share Experience",
		empty_message="You have not shared any experiences yet.",
		item_label="experience",
		edit_endpoint="student.edit_knowledge_post",
		delete_endpoint="student.delete_knowledge_post",
	)


@student_bp.route("/knowledge-post", methods=["GET", "POST"])
@login_required
@role_required("student")
def knowledge_post():
	if request.method == "POST":
		title = request.form.get("title", "").strip()
		content = request.form.get("content", "").strip()

		if not title or not content:
			flash("Title and content are required.", "danger")
			return render_template(
				"knowledge_post.html",
				page_title="Share Experience",
				submit_label="Publish Experience",
			)

		if len(content) < 20:
			flash("Please provide at least 20 characters to share a meaningful experience.", "danger")
			return render_template(
				"knowledge_post.html",
				page_title="Share Experience",
				submit_label="Publish Experience",
			)

		post = KnowledgePost(title=title, content=content, author_id=session["user_id"])
		db.session.add(post)
		db.session.commit()

		flash("Experience shared successfully.", "success")
		return redirect(url_for("student.my_knowledge_posts"))

	return render_template(
		"knowledge_post.html",
		page_title="Share Experience",
		submit_label="Publish Experience",
	)


@student_bp.route("/knowledge-post/<int:post_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("student")
def edit_knowledge_post(post_id: int):
	post = KnowledgePost.query.filter_by(id=post_id, author_id=session["user_id"]).first()
	if not post:
		flash("Experience post not found.", "danger")
		return redirect(url_for("student.my_knowledge_posts"))

	if request.method == "POST":
		title = request.form.get("title", "").strip()
		content = request.form.get("content", "").strip()

		if not title or not content:
			flash("Title and content are required.", "danger")
			return render_template(
				"student_post_edit.html",
				post=post,
				page_title="Edit Experience",
				back_url=url_for("student.my_knowledge_posts"),
				item_label="experience",
			)

		post.title = title
		post.content = content
		db.session.commit()
		flash("Experience updated successfully.", "success")
		return redirect(url_for("student.my_knowledge_posts"))

	return render_template(
		"student_post_edit.html",
		post=post,
		page_title="Edit Experience",
		back_url=url_for("student.my_knowledge_posts"),
		item_label="experience",
	)


@student_bp.route("/knowledge-post/<int:post_id>/delete", methods=["POST"])
@login_required
@role_required("student")
def delete_knowledge_post(post_id: int):
	post = KnowledgePost.query.filter_by(id=post_id, author_id=session["user_id"]).first()
	if not post:
		flash("Experience post not found.", "danger")
		return redirect(url_for("student.my_knowledge_posts"))

	db.session.delete(post)
	db.session.commit()
	flash("Experience deleted successfully.", "success")
	return redirect(url_for("student.my_knowledge_posts"))


@student_bp.route("/checklist/<int:checklist_id>/toggle", methods=["POST"])
@login_required
@role_required("student")
def toggle_checklist(checklist_id: int):
	checklist = Checklist.query.filter_by(id=checklist_id, student_id=session["user_id"]).first()
	if not checklist:
		flash("Checklist item not found.", "danger")
		return redirect(url_for("student.my_checklists"))

	checklist.is_completed = not checklist.is_completed
	db.session.commit()
	flash("Checklist status updated.", "success")
	return redirect(url_for("student.my_checklists"))


@student_bp.route("/checklists")
@login_required
@role_required("student")
def my_checklists():
	status = request.args.get("status", "all").strip().lower()
	query = Checklist.query.filter_by(student_id=session["user_id"]).order_by(Checklist.created_at.desc())
	checklists = query.all()

	if status == "completed":
		checklists = [item for item in checklists if item.is_completed]
	elif status == "pending":
		checklists = [item for item in checklists if not item.is_completed]

	return render_template("student_checklists.html", checklists=checklists, status=status)


@student_bp.route("/submit-feedback-page")
@login_required
@role_required("student")
def submit_feedback_page():
	faculty_list = User.query.filter_by(role="faculty", is_active=True).order_by(User.full_name.asc()).all()
	return render_template("submit_feedback.html", faculty_list=faculty_list)
