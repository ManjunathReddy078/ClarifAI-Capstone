from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from sqlalchemy import or_

from models import Checklist, Feedback, KnowledgePost, User, db
from routes.auth import login_required, role_required


faculty_bp = Blueprint("faculty", __name__, url_prefix="/faculty")


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
	recent_resources = (
		KnowledgePost.query.filter_by(author_id=faculty_user.id)
		.order_by(KnowledgePost.created_at.desc())
		.limit(5)
		.all()
	)
	dashboard_chart = {
		"labels": ["Completed Checklists", "Pending Checklists", "Resources Shared"],
		"values": [
			len([item for item in checklists if item.is_completed]),
			len([item for item in checklists if not item.is_completed]),
			KnowledgePost.query.filter_by(author_id=faculty_user.id).count(),
		],
	}

	return render_template(
		"dashboard_faculty.html",
		faculty=faculty_user,
		approved_feedback=approved_feedback,
		checklists=checklists,
		recent_resources=recent_resources,
		total_resources=KnowledgePost.query.filter_by(author_id=faculty_user.id).count(),
		dashboard_chart=dashboard_chart,
		completed_count=len([item for item in checklists if item.is_completed]),
		pending_count=len([item for item in checklists if not item.is_completed]),
	)


@faculty_bp.route("/resources/board")
@login_required
@role_required("faculty")
def resource_board():
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
		my_posts_url=url_for("faculty.my_resources"),
		my_posts_label="My Resources",
		create_post_url=url_for("faculty.resource_post"),
		create_post_label="Share Resource",
		empty_message="No resources or experiences available yet.",
	)


@faculty_bp.route("/resources/my")
@login_required
@role_required("faculty")
def my_resources():
	posts = (
		KnowledgePost.query.filter_by(author_id=session["user_id"])
		.order_by(KnowledgePost.created_at.desc())
		.all()
	)
	return render_template(
		"student_my_posts.html",
		posts=posts,
		page_title="My Resources",
		heading="My Shared Resources",
		board_url=url_for("faculty.resource_board"),
		board_label="Experience & Resource Board",
		create_url=url_for("faculty.resource_post"),
		create_label="Share Resource",
		empty_message="You have not shared any resources yet.",
		item_label="resource",
		edit_endpoint="faculty.edit_resource_post",
		delete_endpoint="faculty.delete_resource_post",
	)


@faculty_bp.route("/resource-post", methods=["GET", "POST"])
@login_required
@role_required("faculty")
def resource_post():
	if request.method == "POST":
		title = request.form.get("title", "").strip()
		content = request.form.get("content", "").strip()

		if not title or not content:
			flash("Title and content are required.", "danger")
			return render_template(
				"knowledge_post.html",
				page_title="Share Resource",
				submit_label="Publish Resource",
			)

		if len(content) < 20:
			flash("Please provide at least 20 characters to share a meaningful resource.", "danger")
			return render_template(
				"knowledge_post.html",
				page_title="Share Resource",
				submit_label="Publish Resource",
			)

		post = KnowledgePost(title=title, content=content, author_id=session["user_id"])
		db.session.add(post)
		db.session.commit()

		flash("Resource shared successfully.", "success")
		return redirect(url_for("faculty.my_resources"))

	return render_template(
		"knowledge_post.html",
		page_title="Share Resource",
		submit_label="Publish Resource",
	)


@faculty_bp.route("/resource-post/<int:post_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("faculty")
def edit_resource_post(post_id: int):
	post = KnowledgePost.query.filter_by(id=post_id, author_id=session["user_id"]).first()
	if not post:
		flash("Resource post not found.", "danger")
		return redirect(url_for("faculty.my_resources"))

	if request.method == "POST":
		title = request.form.get("title", "").strip()
		content = request.form.get("content", "").strip()

		if not title or not content:
			flash("Title and content are required.", "danger")
			return render_template(
				"student_post_edit.html",
				post=post,
				page_title="Edit Resource",
				back_url=url_for("faculty.my_resources"),
				item_label="resource",
			)

		post.title = title
		post.content = content
		db.session.commit()
		flash("Resource updated successfully.", "success")
		return redirect(url_for("faculty.my_resources"))

	return render_template(
		"student_post_edit.html",
		post=post,
		page_title="Edit Resource",
		back_url=url_for("faculty.my_resources"),
		item_label="resource",
	)


@faculty_bp.route("/resource-post/<int:post_id>/delete", methods=["POST"])
@login_required
@role_required("faculty")
def delete_resource_post(post_id: int):
	post = KnowledgePost.query.filter_by(id=post_id, author_id=session["user_id"]).first()
	if not post:
		flash("Resource post not found.", "danger")
		return redirect(url_for("faculty.my_resources"))

	db.session.delete(post)
	db.session.commit()
	flash("Resource deleted successfully.", "success")
	return redirect(url_for("faculty.my_resources"))


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

	kpi = {
		"total": base_query.count(),
		"positive": base_query.filter_by(sentiment="positive").count(),
		"neutral": base_query.filter_by(sentiment="neutral").count(),
		"negative": base_query.filter_by(sentiment="negative").count(),
	}

	reviews = query.all()
	reason_counts = {}
	for review in reviews:
		reason_key = (review.reason or "Other").strip() or "Other"
		reason_counts[reason_key] = reason_counts.get(reason_key, 0) + 1

	sorted_reasons = sorted(reason_counts.items(), key=lambda item: item[1], reverse=True)[:6]

	month_keys = _last_n_month_labels(6)
	month_labels = [datetime(year=year, month=month, day=1).strftime("%b %Y") for year, month in month_keys]
	month_positive = {f"{year:04d}-{month:02d}": 0 for year, month in month_keys}
	month_negative = {f"{year:04d}-{month:02d}": 0 for year, month in month_keys}
	for review in reviews:
		key = review.created_at.strftime("%Y-%m")
		if key in month_positive and review.sentiment == "positive":
			month_positive[key] += 1
		if key in month_negative and review.sentiment == "negative":
			month_negative[key] += 1

	chart_payload = {
		"sentiment_labels": ["Positive", "Neutral", "Negative"],
		"sentiment_values": [kpi["positive"], kpi["neutral"], kpi["negative"]],
		"reason_labels": [item[0] for item in sorted_reasons],
		"reason_values": [item[1] for item in sorted_reasons],
		"trend_labels": month_labels,
		"trend_positive": [month_positive[f"{year:04d}-{month:02d}"] for year, month in month_keys],
		"trend_negative": [month_negative[f"{year:04d}-{month:02d}"] for year, month in month_keys],
	}
	return render_template(
		"faculty_reviews.html",
		reviews=reviews,
		search=search,
		sentiment=sentiment,
		kpi=kpi,
		chart_payload=chart_payload,
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
