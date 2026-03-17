import csv
import io
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload
from academic_mapping_store import load_preset_assignments
from assignment_sync_service import sync_preset_assignments_to_db

from models import (
	FacultyAssignment,
	Feedback,
	ModerationLog,
	PendingFacultyFeedback,
	SemesterMismatchRequest,
	StudentAcademicProfile,
	SubjectOffering,
	User,
	WebsiteFeedback,
	db,
)
from models import ExperienceReport, StudentExperience
from routes.auth import SECURITY_QUESTIONS, login_required, role_required


admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


WHITELIST_PATH = Path(__file__).resolve().parents[1] / "data" / "whitelist.csv"
SUBJECTS_PATH = Path(__file__).resolve().parents[1] / "data" / "subjects.csv"
IST_ZONE = timezone(timedelta(hours=5, minutes=30))


def _utc_to_ist(value):
	if not value:
		return None
	source = value
	if source.tzinfo is None:
		source = source.replace(tzinfo=timezone.utc)
	return source.astimezone(IST_ZONE)


def _load_whitelist_rows():
	if not WHITELIST_PATH.exists():
		return [], []

	with WHITELIST_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
		reader = csv.DictReader(handle)
		fieldnames = [(name or "").strip() for name in (reader.fieldnames or [])]
		rows = [
			{(key or "").strip(): (value or "") for key, value in row.items()}
			for row in reader
		]
		return fieldnames, rows


def _write_whitelist_rows(fieldnames, rows):
	if not fieldnames:
		return

	with WHITELIST_PATH.open("w", encoding="utf-8", newline="") as handle:
		writer = csv.DictWriter(handle, fieldnames=fieldnames)
		writer.writeheader()
		writer.writerows(rows)


def _apply_semester_whitelist_update(email: str, prn: str, course_code: str, requested_semester: int) -> bool:
	fieldnames, rows = _load_whitelist_rows()
	if not rows:
		return False

	normalized_email = (email or "").strip().lower()
	normalized_prn = (prn or "").strip().lower()
	normalized_course = (course_code or "").strip().upper()
	updated = False

	for row in rows:
		if (row.get("role") or "").strip().lower() != "student":
			continue
		if (row.get("email") or "").strip().lower() != normalized_email:
			continue
		if (row.get("course") or "").strip().upper() != normalized_course:
			continue

		row_prn = (row.get("prn") or "").strip().lower()
		if normalized_prn and row_prn and row_prn != normalized_prn:
			continue

		row["current_semester"] = str(requested_semester)
		row["allowed"] = "YES"
		updated = True
		break

	if updated:
		_write_whitelist_rows(fieldnames, rows)

	return updated


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


def _safe_int(value, default=None):
	try:
		if value is None:
			return default
		text = str(value).strip()
		if not text:
			return default
		return int(text)
	except (TypeError, ValueError):
		return default


def _normalized_resource_links(raw_value: str) -> list[str]:
	if not raw_value:
		return []

	lines = [line.strip() for line in raw_value.splitlines() if line.strip()]
	if len(lines) == 1 and "," in lines[0]:
		parts = [item.strip() for item in lines[0].split(",") if item.strip()]
		if len(parts) > 1:
			return parts
	return lines


def _parse_iso_date(value, default=None):
	text = (value or "").strip()
	if not text:
		return default
	try:
		return datetime.strptime(text, "%Y-%m-%d").date()
	except ValueError:
		return None


def _parse_active_flag(value, default=True):
	text = (value or "").strip().lower()
	if not text:
		return default
	return text in {"yes", "y", "true", "1", "active"}


def _preview_errors(errors, limit=4):
	if not errors:
		return ""
	preview = "; ".join(errors[:limit])
	if len(errors) > limit:
		preview += f"; +{len(errors) - limit} more"
	return preview


def _annotate_preset_rows_with_registration(preset_rows, faculty_list):
	faculty_by_id = {
		(faculty.faculty_id or "").strip().upper(): faculty
		for faculty in faculty_list
		if (faculty.faculty_id or "").strip()
	}
	faculty_by_email = {
		(faculty.email or "").strip().lower(): faculty
		for faculty in faculty_list
		if (faculty.email or "").strip()
	}

	for row in preset_rows:
		faculty_match = None
		faculty_id_key = (row.get("faculty_id") or "").strip().upper()
		faculty_email_key = (row.get("faculty_email") or "").strip().lower()
		if faculty_id_key and faculty_id_key in faculty_by_id:
			faculty_match = faculty_by_id[faculty_id_key]
		elif faculty_email_key and faculty_email_key in faculty_by_email:
			faculty_match = faculty_by_email[faculty_email_key]

		row["is_registered"] = bool(faculty_match)
		row["registered_name"] = faculty_match.full_name if faculty_match else ""
		row["registered_email"] = faculty_match.email if faculty_match else ""

		first_login_at = faculty_match.first_login_at if faculty_match else None
		last_login_at = faculty_match.last_login_at if faculty_match else None

		# Backward compatible: old records may only have last_login_at.
		row["has_logged_in"] = bool(faculty_match and (first_login_at or last_login_at))
		row["first_login_at"] = first_login_at or last_login_at
		row["last_login_at"] = last_login_at or first_login_at

	return preset_rows


def _build_academic_mapping_stats(preset_rows, sync_summary=None):
	summary = sync_summary or {}
	return {
		"offerings_total": SubjectOffering.query.count(),
		"offerings_active": SubjectOffering.query.filter_by(is_active=True).count(),
		"assignments_total": FacultyAssignment.query.count(),
		"assignments_active": FacultyAssignment.query.filter_by(is_active=True).count(),
		"preset_rows_total": len(preset_rows),
		"preset_registered_rows": len([row for row in preset_rows if row.get("is_registered")]),
		"preset_logged_in_rows": len([row for row in preset_rows if row.get("has_logged_in")]),
		"preset_sync_created": summary.get("created", 0),
		"preset_sync_missing_offering": summary.get("missing_offering", 0),
		"preset_sync_missing_faculty": summary.get("missing_faculty", 0),
		"mapped_faculty": (
			db.session.query(FacultyAssignment.faculty_user_id)
			.filter(FacultyAssignment.is_active.is_(True))
			.distinct()
			.count()
		),
	}


def _build_admin_queue_stats():
	return {
		"moderation_queue": Feedback.query.filter_by(status="under_review").count(),
		"experience_pending": StudentExperience.query.filter_by(status="pending").count(),
		"reports_open": ExperienceReport.query.filter_by(status="open").count(),
		"suggestions_unread": WebsiteFeedback.query.filter_by(is_read=False).count(),
		"semester_pending": SemesterMismatchRequest.query.filter_by(status="pending").count(),
		"faculty_delivery_pending": PendingFacultyFeedback.query.filter(
			PendingFacultyFeedback.status.in_(["holding", "under_review"])
		).count(),
	}


def _build_admin_updates_snapshot(selected_type: str):
	moderation_queue_count = Feedback.query.filter_by(status="under_review").count()
	pending_experience_count = StudentExperience.query.filter_by(status="pending").count()
	open_experience_reports_count = ExperienceReport.query.filter_by(status="open").count()
	unread_suggestions_count = WebsiteFeedback.query.filter_by(is_read=False).count()
	pending_semester_exceptions_count = SemesterMismatchRequest.query.filter_by(status="pending").count()
	pending_faculty_feedback_count = PendingFacultyFeedback.query.filter(
		PendingFacultyFeedback.status.in_(["holding", "under_review"])
	).count()

	events = []

	if selected_type in {"all", "moderation"}:
		logs = ModerationLog.query.order_by(ModerationLog.created_at.desc()).limit(60).all()
		for log in logs:
			action_label = (log.action or "update").replace("_", " ").title()
			subject_name = log.feedback.subject if log.feedback else "Feedback"
			student_name = log.feedback.student.full_name if log.feedback and log.feedback.student else "Student"
			events.append(
				{
					"created_at": log.created_at,
					"kind": "moderation",
					"title": f"{action_label} · {subject_name}",
					"detail": f"Student: {student_name}. Admin note: {log.note or '-'}",
				}
			)

	if selected_type in {"all", "experience"}:
		experience_items = (
			StudentExperience.query.filter(StudentExperience.status.in_(["pending", "request_edit", "rejected"]))
			.order_by(StudentExperience.created_at.desc())
			.limit(60)
			.all()
		)
		for exp in experience_items:
			events.append(
				{
					"created_at": exp.created_at,
					"kind": "experience",
					"title": f"{exp.status.replace('_', ' ').title()} · {exp.anon_id}",
					"detail": f"{exp.title} ({exp.sentiment.title()})",
				}
			)

	if selected_type in {"all", "reports"}:
		report_items = ExperienceReport.query.order_by(ExperienceReport.created_at.desc()).limit(60).all()
		for report in report_items:
			exp_title = report.experience.title if report.experience else "Deleted experience"
			reporter = report.reporter.full_name if report.reporter else "Student"
			events.append(
				{
					"created_at": report.created_at,
					"kind": "reports",
					"title": f"{report.status.title()} report · {report.report_category}",
					"detail": f"Reporter: {reporter}. Post: {exp_title}",
				}
			)

	if selected_type in {"all", "system"}:
		suggestion_items = WebsiteFeedback.query.order_by(WebsiteFeedback.created_at.desc()).limit(40).all()
		for suggestion in suggestion_items:
			events.append(
				{
					"created_at": suggestion.created_at,
					"kind": "system",
					"title": f"Website suggestion · {'Read' if suggestion.is_read else 'Unread'}",
					"detail": f"{suggestion.visitor_name} ({suggestion.visitor_email})",
				}
			)

		exception_items = SemesterMismatchRequest.query.order_by(SemesterMismatchRequest.created_at.desc()).limit(40).all()
		for exception in exception_items:
			events.append(
				{
					"created_at": exception.created_at,
					"kind": "system",
					"title": f"Semester exception · {exception.status.title()}",
					"detail": f"{exception.full_name} requested Sem {exception.requested_semester}",
				}
			)

	events.sort(key=lambda item: item["created_at"] or datetime.min, reverse=True)

	return {
		"selected_type": selected_type,
		"events": events,
		"moderation_queue_count": moderation_queue_count,
		"pending_experience_count": pending_experience_count,
		"open_experience_reports_count": open_experience_reports_count,
		"unread_suggestions_count": unread_suggestions_count,
		"pending_semester_exceptions_count": pending_semester_exceptions_count,
		"pending_faculty_feedback_count": pending_faculty_feedback_count,
	}


def _resolve_faculty_user_from_row(row):
	faculty_user_id = _safe_int(row.get("faculty_user_id"))
	if faculty_user_id:
		candidate = User.query.get(faculty_user_id)
		if candidate and candidate.role == "faculty":
			return candidate

	faculty_email = (row.get("faculty_email") or row.get("email") or "").strip().lower()
	if faculty_email:
		candidate = User.query.filter_by(email=faculty_email, role="faculty").first()
		if candidate:
			return candidate

	faculty_code = (row.get("faculty_id") or "").strip()
	if faculty_code:
		candidate = User.query.filter(
			User.role == "faculty",
			or_(User.faculty_id == faculty_code, User.unique_user_code == faculty_code),
		).first()
		if candidate:
			return candidate

	return None


def _resolve_offering_from_row(row):
	offering_id = _safe_int(row.get("subject_offering_id"))
	if offering_id:
		return SubjectOffering.query.get(offering_id)

	course_code = (row.get("course_code") or "").strip().upper()
	semester_no = _safe_int(row.get("semester_no") or row.get("semester"))
	section = (row.get("section") or "").strip().upper()
	subject_code = (row.get("subject_code") or "").strip().upper()

	if not all([course_code, semester_no, section, subject_code]):
		return None

	return SubjectOffering.query.filter_by(
		course_code=course_code,
		semester_no=semester_no,
		section=section,
		subject_code=subject_code,
	).first()


@admin_bp.route("/dashboard")
@login_required
@role_required("admin")
def dashboard():
	trend_months_raw = (request.args.get("trend_months") or "6").strip()
	trend_months = int(trend_months_raw) if trend_months_raw.isdigit() else 6
	if trend_months not in {3, 6, 12}:
		trend_months = 6

	pending_feedback = (
		Feedback.query.options(joinedload(Feedback.student), joinedload(Feedback.faculty))
		.filter_by(status="under_review")
		.order_by(Feedback.created_at.desc())
		.all()
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
	unread_suggestions_count = WebsiteFeedback.query.filter_by(is_read=False).count()
	pending_semester_exceptions_count = SemesterMismatchRequest.query.filter_by(status="pending").count()
	pending_faculty_feedback_count = PendingFacultyFeedback.query.filter(
		PendingFacultyFeedback.status.in_(["holding", "under_review"])
	).count()

	month_keys = _last_n_month_labels(trend_months)
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

	total_feedback = len(all_feedback)
	approved_feedback = len([item for item in all_feedback if item.status == "approved"])
	pending_policy = len(pending_feedback)
	pending_threshold = datetime.utcnow() - timedelta(hours=72)
	pending_over_72h = len(
		[item for item in pending_feedback if item.created_at and item.created_at <= pending_threshold]
	)

	resolved_statuses = {"approved", "rejected", "request_edit"}
	resolved_feedback = [item for item in all_feedback if item.status in resolved_statuses]
	resolved_ids = [item.id for item in resolved_feedback]
	first_log_by_feedback: dict[int, datetime] = {}
	if resolved_ids:
		resolved_logs = (
			ModerationLog.query.filter(ModerationLog.feedback_id.in_(resolved_ids))
			.order_by(ModerationLog.feedback_id.asc(), ModerationLog.created_at.asc())
			.all()
		)
		for log in resolved_logs:
			if log.feedback_id not in first_log_by_feedback:
				first_log_by_feedback[log.feedback_id] = log.created_at

	within_sla = 0
	for item in resolved_feedback:
		first_log_at = first_log_by_feedback.get(item.id)
		if not first_log_at or not item.created_at:
			continue
		if first_log_at <= item.created_at + timedelta(hours=4):
			within_sla += 1

	total_users = user_counts["students"] + user_counts["faculty"] + user_counts["admins"]
	policy_stats = {
		"ack_rate": int((approved_feedback / total_feedback) * 100) if total_feedback else 0,
		"sla_rate": int((within_sla / len(resolved_feedback)) * 100) if resolved_feedback else 0,
		"pending": pending_over_72h,
		"active_accounts": int((user_counts["active"] / total_users) * 100) if total_users else 0,
	}

	return render_template(
		"dashboard_admin.html",
		pending_feedback=pending_feedback,
		moderation_logs=moderation_logs,
		user_counts=user_counts,
		recent_users=recent_users,
		chart_payload=chart_payload,
		trend_months=trend_months,
		policy_stats=policy_stats,
		unread_suggestions_count=unread_suggestions_count,
		pending_semester_exceptions_count=pending_semester_exceptions_count,
		pending_faculty_feedback_count=pending_faculty_feedback_count,
	)


@admin_bp.route("/semester-exceptions")
@login_required
@role_required("admin")
def semester_exceptions_page():
	search = request.args.get("q", "").strip()
	status = request.args.get("status", "pending").strip().lower()

	query = SemesterMismatchRequest.query.order_by(SemesterMismatchRequest.created_at.desc())
	if status in {"pending", "approved", "rejected"}:
		query = query.filter(SemesterMismatchRequest.status == status)

	if search:
		like = f"%{search}%"
		query = query.filter(
			or_(
				SemesterMismatchRequest.full_name.ilike(like),
				SemesterMismatchRequest.email.ilike(like),
				SemesterMismatchRequest.prn.ilike(like),
				SemesterMismatchRequest.course_code.ilike(like),
			)
		)

	requests_list = query.all()
	stats = {
		"total": SemesterMismatchRequest.query.count(),
		"pending": SemesterMismatchRequest.query.filter_by(status="pending").count(),
		"approved": SemesterMismatchRequest.query.filter_by(status="approved").count(),
		"rejected": SemesterMismatchRequest.query.filter_by(status="rejected").count(),
	}

	return render_template(
		"admin_semester_exceptions.html",
		requests_list=requests_list,
		search=search,
		status=status,
		stats=stats,
	)


@admin_bp.route("/semester-exceptions/<int:request_id>/review", methods=["POST"])
@login_required
@role_required("admin")
def review_semester_exception(request_id: int):
	action = request.form.get("action", "").strip().lower()
	note = request.form.get("note", "").strip()
	next_url = request.form.get("next_url", "").strip()

	request_item = SemesterMismatchRequest.query.get(request_id)
	if not request_item:
		flash("Semester mismatch request not found.", "danger")
		return redirect(url_for("admin.semester_exceptions_page"))

	if request_item.status != "pending":
		flash("This request has already been reviewed.", "warning")
		return redirect(url_for("admin.semester_exceptions_page"))

	if action not in {"approve", "reject"}:
		flash("Invalid review action.", "danger")
		return redirect(url_for("admin.semester_exceptions_page"))

	if action == "approve":
		updated = _apply_semester_whitelist_update(
			email=request_item.email,
			prn=request_item.prn or "",
			course_code=request_item.course_code,
			requested_semester=request_item.requested_semester,
		)
		if not updated:
			flash("Unable to update whitelist row for this request. Please verify whitelist data.", "danger")
			return redirect(url_for("admin.semester_exceptions_page"))
		request_item.status = "approved"
	else:
		request_item.status = "rejected"

	request_item.admin_id = session.get("user_id")
	request_item.admin_note = note or None
	request_item.reviewed_at = datetime.utcnow()
	db.session.commit()

	flash(f"Semester mismatch request {request_item.id} has been {request_item.status}.", "success")
	if next_url.startswith("/admin/semester-exceptions"):
		return redirect(next_url)
	return redirect(url_for("admin.semester_exceptions_page"))


@admin_bp.route("/academic-mapping")
@login_required
@role_required("admin")
def academic_mapping_page():
	preset_course = (request.args.get("preset_course") or "all").strip().upper()
	preset_semester = (request.args.get("preset_semester") or "all").strip()
	preset_section = (request.args.get("preset_section") or "all").strip().upper()
	preset_subject = (request.args.get("preset_subject") or "all").strip().upper()
	preset_faculty = (request.args.get("preset_faculty") or "all").strip().upper()

	sync_summary = sync_preset_assignments_to_db()
	all_preset_rows = load_preset_assignments(active_only=False)

	offerings_query = SubjectOffering.query.order_by(
		SubjectOffering.course_code.asc(),
		SubjectOffering.semester_no.asc(),
		SubjectOffering.section.asc(),
		SubjectOffering.subject_code.asc(),
	)

	offerings = offerings_query.limit(200).all()
	offering_ids = [item.id for item in offerings]

	assignment_rows = []
	assignments_by_offering = {}
	if offering_ids:
		assignment_rows = (
			FacultyAssignment.query
			.join(User, FacultyAssignment.faculty_user_id == User.id)
			.filter(FacultyAssignment.subject_offering_id.in_(offering_ids))
			.order_by(FacultyAssignment.effective_from.desc(), FacultyAssignment.created_at.desc())
			.all()
		)
		for assignment in assignment_rows:
			assignments_by_offering.setdefault(assignment.subject_offering_id, []).append(assignment)

	faculty_list = User.query.filter_by(role="faculty").order_by(User.full_name.asc()).all()
	annotated_rows = _annotate_preset_rows_with_registration(all_preset_rows, faculty_list)

	course_options = sorted({(row.get("course_code") or "").strip().upper() for row in annotated_rows if (row.get("course_code") or "").strip()})
	semester_options = sorted(
		{(row.get("semester_no") or "").strip() for row in annotated_rows if (row.get("semester_no") or "").strip()},
		key=lambda value: int(value) if str(value).isdigit() else str(value),
	)
	section_options = sorted({(row.get("section") or "").strip().upper() for row in annotated_rows if (row.get("section") or "").strip()})
	subject_options = sorted({(row.get("subject_code") or "").strip().upper() for row in annotated_rows if (row.get("subject_code") or "").strip()})

	faculty_lookup = {}
	for row in annotated_rows:
		faculty_id = (row.get("faculty_id") or "").strip().upper()
		faculty_email = (row.get("faculty_email") or "").strip().lower()
		faculty_name = (row.get("faculty_name") or "").strip()
		if faculty_id:
			key = f"ID::{faculty_id}"
			label = f"{faculty_name or '-'} ({faculty_id})"
			faculty_lookup[key] = label
		elif faculty_email:
			key = f"EMAIL::{faculty_email.upper()}"
			label = f"{faculty_name or '-'} ({faculty_email})"
			faculty_lookup[key] = label

	faculty_options = [{"value": key, "label": faculty_lookup[key]} for key in sorted(faculty_lookup.keys())]

	preset_rows = []
	for row in annotated_rows:
		row_course = (row.get("course_code") or "").strip().upper()
		row_semester = (row.get("semester_no") or "").strip()
		row_section = (row.get("section") or "").strip().upper()
		row_subject = (row.get("subject_code") or "").strip().upper()
		row_faculty_id = (row.get("faculty_id") or "").strip().upper()
		row_faculty_email = (row.get("faculty_email") or "").strip().upper()

		if preset_course != "ALL" and row_course != preset_course:
			continue
		if preset_semester.lower() != "all" and row_semester != preset_semester:
			continue
		if preset_section != "ALL" and row_section != preset_section:
			continue
		if preset_subject != "ALL" and row_subject != preset_subject:
			continue
		if preset_faculty != "ALL":
			if preset_faculty.startswith("ID::"):
				if row_faculty_id != preset_faculty.split("::", 1)[1]:
					continue
			elif preset_faculty.startswith("EMAIL::"):
				if row_faculty_email != preset_faculty.split("::", 1)[1]:
					continue

		preset_rows.append(row)

	offering_options = SubjectOffering.query.filter_by(is_active=True).order_by(
		SubjectOffering.course_code.asc(),
		SubjectOffering.semester_no.asc(),
		SubjectOffering.section.asc(),
		SubjectOffering.subject_code.asc(),
	).all()
	stats = _build_academic_mapping_stats(annotated_rows, sync_summary)

	return render_template(
		"admin_academic_mapping.html",
		preset_rows=preset_rows,
		preset_filters={
			"course": preset_course,
			"semester": preset_semester,
			"section": preset_section,
			"subject": preset_subject,
			"faculty": preset_faculty,
		},
		preset_filter_options={
			"courses": course_options,
			"semesters": semester_options,
			"sections": section_options,
			"subjects": subject_options,
			"faculty": faculty_options,
		},
		offerings=offerings,
		assignment_rows=assignment_rows,
		assignments_by_offering=assignments_by_offering,
		faculty_list=faculty_list,
		offering_options=offering_options,
		stats=stats,
	)


@admin_bp.route("/academic-mapping/stats")
@login_required
@role_required("admin")
def academic_mapping_stats():
	preset_rows = load_preset_assignments(active_only=False)
	faculty_list = User.query.filter_by(role="faculty").all()
	preset_rows = _annotate_preset_rows_with_registration(preset_rows, faculty_list)
	stats = _build_academic_mapping_stats(preset_rows)
	return jsonify({"ok": True, "stats": stats, "updated_at": datetime.utcnow().isoformat() + "Z"})


@admin_bp.route("/faculty-feedback-holding")
@login_required
@role_required("admin")
def faculty_feedback_holding_page():
	status = request.args.get("status", "all").strip().lower()
	search = request.args.get("q", "").strip()
	if status not in {"all", "holding", "under_review", "request_edit", "rejected"}:
		status = "all"

	query = PendingFacultyFeedback.query.order_by(PendingFacultyFeedback.created_at.desc())
	if status != "all":
		query = query.filter(PendingFacultyFeedback.status == status)

	if search:
		like = f"%{search}%"
		query = query.filter(
			or_(
				PendingFacultyFeedback.course_code.ilike(like),
				PendingFacultyFeedback.section.ilike(like),
				PendingFacultyFeedback.semester.ilike(like),
				PendingFacultyFeedback.subject_code.ilike(like),
				PendingFacultyFeedback.subject.ilike(like),
				PendingFacultyFeedback.assigned_faculty_id.ilike(like),
				PendingFacultyFeedback.assigned_faculty_name.ilike(like),
				PendingFacultyFeedback.assigned_faculty_email.ilike(like),
				PendingFacultyFeedback.feedback_text.ilike(like),
			)
		)

	items = query.all()
	for item in items:
		item.created_at_ist = _utc_to_ist(item.created_at)
	counts = {
		"holding": PendingFacultyFeedback.query.filter_by(status="holding").count(),
		"under_review": PendingFacultyFeedback.query.filter_by(status="under_review").count(),
		"request_edit": PendingFacultyFeedback.query.filter_by(status="request_edit").count(),
		"rejected": PendingFacultyFeedback.query.filter_by(status="rejected").count(),
	}
	return render_template(
		"admin_faculty_feedback_holding.html",
		items=items,
		status=status,
		search=search,
		counts=counts,
	)


@admin_bp.route("/faculty-feedback-holding/<int:item_id>/moderate", methods=["POST"])
@login_required
@role_required("admin")
def moderate_faculty_feedback_holding(item_id: int):
	item = PendingFacultyFeedback.query.get(item_id)
	if not item:
		flash("Queue item not found.", "danger")
		return redirect(url_for("admin.faculty_feedback_holding_page"))

	action = request.form.get("action", "").strip().lower()
	note = request.form.get("note", "").strip()
	if action not in {"approve", "request_edit", "reject"}:
		flash("Invalid moderation action.", "danger")
		return redirect(url_for("admin.faculty_feedback_holding_page"))

	if action == "approve":
		item.status = "holding"
	elif action == "request_edit":
		item.status = "request_edit"
	else:
		item.status = "rejected"

	item.admin_note = note or None
	db.session.commit()
	flash("Queue item updated.", "success")
	return redirect(url_for("admin.faculty_feedback_holding_page"))


@admin_bp.route("/subject-offerings/create", methods=["POST"])
@login_required
@role_required("admin")
def create_subject_offering():
	course_code = (request.form.get("course_code") or "").strip().upper()
	semester_no = _safe_int(request.form.get("semester_no"))
	section = (request.form.get("section") or "").strip().upper()
	subject_code = (request.form.get("subject_code") or "").strip().upper()
	subject_name = (request.form.get("subject_name") or "").strip()
	is_active = _parse_active_flag(request.form.get("is_active"), default=True)

	if not all([course_code, semester_no, section, subject_code, subject_name]):
		flash("Course, semester, section, subject code, and subject name are required.", "danger")
		return redirect(url_for("admin.academic_mapping_page"))

	try:
		existing = SubjectOffering.query.filter_by(
			course_code=course_code,
			semester_no=semester_no,
			section=section,
			subject_code=subject_code,
		).first()

		if existing:
			existing.subject_name = subject_name
			existing.is_active = is_active
			message = "Subject offering updated successfully."
		else:
			db.session.add(
				SubjectOffering(
					course_code=course_code,
					semester_no=semester_no,
					section=section,
					subject_code=subject_code,
					subject_name=subject_name,
					is_active=is_active,
				)
			)
			message = "Subject offering created successfully."

		db.session.commit()
		flash(message, "success")
	except IntegrityError:
		db.session.rollback()
		flash("Unable to save subject offering due to a duplicate or invalid value.", "danger")

	return redirect(url_for("admin.academic_mapping_page"))


@admin_bp.route("/subject-offerings/<int:offering_id>/toggle-active", methods=["POST"])
@login_required
@role_required("admin")
def toggle_subject_offering_active(offering_id: int):
	offering = SubjectOffering.query.get(offering_id)
	if not offering:
		flash("Subject offering not found.", "danger")
		return redirect(url_for("admin.academic_mapping_page"))

	offering.is_active = not offering.is_active
	db.session.commit()
	flash("Subject offering status updated.", "success")
	return redirect(url_for("admin.academic_mapping_page"))


@admin_bp.route("/subject-offerings/import-catalog", methods=["POST"])
@login_required
@role_required("admin")
def import_subject_offerings_from_catalog():
	if not SUBJECTS_PATH.exists():
		flash("subjects.csv file not found in backend/data.", "danger")
		return redirect(url_for("admin.academic_mapping_page"))

	section = (request.form.get("section") or "A").strip().upper() or "A"
	course_filter = (request.form.get("course_code") or "ALL").strip().upper()
	semester_filter = _safe_int(request.form.get("semester_no"))

	created = 0
	updated = 0
	skipped = 0

	with SUBJECTS_PATH.open("r", encoding="utf-8", newline="") as handle:
		reader = csv.DictReader(handle)
		for row in reader:
			course_code = (row.get("degree") or "").strip().upper()
			semester_no = _safe_int(row.get("semester"))
			subject_code = (row.get("course_code") or "").strip().upper()
			subject_name = (row.get("subject_name") or "").strip()
			is_active = _parse_active_flag(row.get("is_active"), default=True)

			if not all([course_code, semester_no, subject_code, subject_name]):
				skipped += 1
				continue

			if course_filter not in {"", "ALL"} and course_code != course_filter:
				continue

			if semester_filter and semester_no != semester_filter:
				continue

			existing = SubjectOffering.query.filter_by(
				course_code=course_code,
				semester_no=semester_no,
				section=section,
				subject_code=subject_code,
			).first()

			if existing:
				changed = False
				if existing.subject_name != subject_name:
					existing.subject_name = subject_name
					changed = True
				if existing.is_active != is_active:
					existing.is_active = is_active
					changed = True
				if changed:
					updated += 1
				continue

			db.session.add(
				SubjectOffering(
					course_code=course_code,
					semester_no=semester_no,
					section=section,
					subject_code=subject_code,
					subject_name=subject_name,
					is_active=is_active,
				)
			)
			created += 1

	db.session.commit()
	flash(
		f"Catalog import completed. Created: {created}, Updated: {updated}, Skipped: {skipped}.",
		"success",
	)
	return redirect(url_for("admin.academic_mapping_page"))


@admin_bp.route("/subject-offerings/import-csv", methods=["POST"])
@login_required
@role_required("admin")
def import_subject_offerings_csv():
	csv_file = request.files.get("offering_csv")
	if not csv_file or not csv_file.filename:
		flash("Please choose a CSV file for subject offering import.", "danger")
		return redirect(url_for("admin.academic_mapping_page"))

	try:
		decoded = csv_file.read().decode("utf-8-sig")
	except UnicodeDecodeError:
		flash("CSV file must be UTF-8 encoded.", "danger")
		return redirect(url_for("admin.academic_mapping_page"))

	reader = csv.DictReader(io.StringIO(decoded))
	fieldnames = {name.strip().lower() for name in (reader.fieldnames or [])}
	required = {"course_code", "semester_no", "section", "subject_code", "subject_name"}
	if not required.issubset(fieldnames):
		flash("CSV must include: course_code, semester_no, section, subject_code, subject_name.", "danger")
		return redirect(url_for("admin.academic_mapping_page"))

	created = 0
	updated = 0
	skipped = 0
	errors = []

	for line_no, row in enumerate(reader, start=2):
		course_code = (row.get("course_code") or "").strip().upper()
		semester_no = _safe_int(row.get("semester_no"))
		section = (row.get("section") or "").strip().upper()
		subject_code = (row.get("subject_code") or "").strip().upper()
		subject_name = (row.get("subject_name") or "").strip()
		is_active = _parse_active_flag(row.get("is_active"), default=True)

		if not all([course_code, semester_no, section, subject_code, subject_name]):
			skipped += 1
			errors.append(f"L{line_no}: Missing required value")
			continue

		existing = SubjectOffering.query.filter_by(
			course_code=course_code,
			semester_no=semester_no,
			section=section,
			subject_code=subject_code,
		).first()

		if existing:
			existing.subject_name = subject_name
			existing.is_active = is_active
			updated += 1
			continue

		db.session.add(
			SubjectOffering(
				course_code=course_code,
				semester_no=semester_no,
				section=section,
				subject_code=subject_code,
				subject_name=subject_name,
				is_active=is_active,
			)
		)
		created += 1

	try:
		db.session.commit()
	except IntegrityError:
		db.session.rollback()
		flash("Import failed due to duplicate or invalid values.", "danger")
		return redirect(url_for("admin.academic_mapping_page"))

	if skipped:
		flash(
			f"Subject offering CSV processed. Created: {created}, Updated: {updated}, Skipped: {skipped}. {_preview_errors(errors)}",
			"warning",
		)
	else:
		flash(f"Subject offering CSV processed. Created: {created}, Updated: {updated}.", "success")

	return redirect(url_for("admin.academic_mapping_page"))


@admin_bp.route("/faculty-assignments/create", methods=["POST"])
@login_required
@role_required("admin")
def create_faculty_assignment():
	subject_offering_id = _safe_int(request.form.get("subject_offering_id"))
	faculty_user_id = _safe_int(request.form.get("faculty_user_id"))
	effective_from = _parse_iso_date(request.form.get("effective_from"), default=date.today())
	effective_to_raw = (request.form.get("effective_to") or "").strip()
	effective_to = _parse_iso_date(effective_to_raw) if effective_to_raw else None
	is_active = _parse_active_flag(request.form.get("is_active"), default=True)

	if not subject_offering_id or not faculty_user_id or not effective_from:
		flash("Offering, faculty, and a valid effective-from date are required.", "danger")
		return redirect(url_for("admin.academic_mapping_page"))

	if effective_to and effective_to < effective_from:
		flash("Effective-to date cannot be before effective-from date.", "danger")
		return redirect(url_for("admin.academic_mapping_page"))

	offering = SubjectOffering.query.get(subject_offering_id)
	faculty = User.query.get(faculty_user_id)
	if not offering:
		flash("Selected subject offering was not found.", "danger")
		return redirect(url_for("admin.academic_mapping_page"))
	if not faculty or faculty.role != "faculty":
		flash("Selected faculty record is invalid.", "danger")
		return redirect(url_for("admin.academic_mapping_page"))

	try:
		existing = FacultyAssignment.query.filter_by(
			subject_offering_id=subject_offering_id,
			faculty_user_id=faculty_user_id,
			effective_from=effective_from,
		).first()

		if existing:
			existing.effective_to = effective_to
			existing.is_active = is_active
			message = "Faculty assignment updated successfully."
		else:
			db.session.add(
				FacultyAssignment(
					subject_offering_id=subject_offering_id,
					faculty_user_id=faculty_user_id,
					effective_from=effective_from,
					effective_to=effective_to,
					is_active=is_active,
				)
			)
			message = "Faculty assignment created successfully."

		db.session.commit()
		flash(message, "success")
	except IntegrityError:
		db.session.rollback()
		flash("Unable to save faculty assignment due to duplicate values.", "danger")

	return redirect(url_for("admin.academic_mapping_page"))


@admin_bp.route("/faculty-assignments/<int:assignment_id>/toggle-active", methods=["POST"])
@login_required
@role_required("admin")
def toggle_faculty_assignment_active(assignment_id: int):
	assignment = FacultyAssignment.query.get(assignment_id)
	if not assignment:
		flash("Faculty assignment not found.", "danger")
		return redirect(url_for("admin.academic_mapping_page"))

	assignment.is_active = not assignment.is_active
	db.session.commit()
	flash("Faculty assignment status updated.", "success")
	return redirect(url_for("admin.academic_mapping_page"))


@admin_bp.route("/faculty-assignments/import-csv", methods=["POST"])
@login_required
@role_required("admin")
def import_faculty_assignments_csv():
	csv_file = request.files.get("assignment_csv")
	if not csv_file or not csv_file.filename:
		flash("Please choose a CSV file for faculty assignment import.", "danger")
		return redirect(url_for("admin.academic_mapping_page"))

	try:
		decoded = csv_file.read().decode("utf-8-sig")
	except UnicodeDecodeError:
		flash("CSV file must be UTF-8 encoded.", "danger")
		return redirect(url_for("admin.academic_mapping_page"))

	reader = csv.DictReader(io.StringIO(decoded))
	if not reader.fieldnames:
		flash("CSV appears empty or has no header row.", "danger")
		return redirect(url_for("admin.academic_mapping_page"))

	created = 0
	updated = 0
	skipped = 0
	errors = []

	for line_no, row in enumerate(reader, start=2):
		offering = _resolve_offering_from_row(row)
		if not offering:
			skipped += 1
			errors.append(f"L{line_no}: Subject offering not found")
			continue

		faculty = _resolve_faculty_user_from_row(row)
		if not faculty:
			skipped += 1
			errors.append(f"L{line_no}: Faculty record not found")
			continue

		effective_from = _parse_iso_date(row.get("effective_from"), default=date.today())
		effective_to_raw = (row.get("effective_to") or "").strip()
		effective_to = _parse_iso_date(effective_to_raw) if effective_to_raw else None
		if not effective_from:
			skipped += 1
			errors.append(f"L{line_no}: Invalid effective_from date")
			continue
		if effective_to_raw and not effective_to:
			skipped += 1
			errors.append(f"L{line_no}: Invalid effective_to date")
			continue
		if effective_to and effective_to < effective_from:
			skipped += 1
			errors.append(f"L{line_no}: effective_to before effective_from")
			continue

		is_active = _parse_active_flag(row.get("is_active"), default=True)

		existing = FacultyAssignment.query.filter_by(
			subject_offering_id=offering.id,
			faculty_user_id=faculty.id,
			effective_from=effective_from,
		).first()

		if existing:
			existing.effective_to = effective_to
			existing.is_active = is_active
			updated += 1
			continue

		db.session.add(
			FacultyAssignment(
				subject_offering_id=offering.id,
				faculty_user_id=faculty.id,
				effective_from=effective_from,
				effective_to=effective_to,
				is_active=is_active,
			)
		)
		created += 1

	try:
		db.session.commit()
	except IntegrityError:
		db.session.rollback()
		flash("Import failed due to duplicate or invalid assignment rows.", "danger")
		return redirect(url_for("admin.academic_mapping_page"))

	if skipped:
		flash(
			f"Faculty assignment CSV processed. Created: {created}, Updated: {updated}, Skipped: {skipped}. {_preview_errors(errors)}",
			"warning",
		)
	else:
		flash(f"Faculty assignment CSV processed. Created: {created}, Updated: {updated}.", "success")

	return redirect(url_for("admin.academic_mapping_page"))


@admin_bp.route("/suggestions")
@login_required
@role_required("admin")
def suggestions_page():
	search = request.args.get("q", "").strip()
	status = request.args.get("status", "all").strip().lower()

	query = WebsiteFeedback.query.order_by(WebsiteFeedback.created_at.desc())
	if search:
		like = f"%{search}%"
		query = query.filter(
			or_(
				WebsiteFeedback.visitor_name.ilike(like),
				WebsiteFeedback.visitor_email.ilike(like),
				WebsiteFeedback.message.ilike(like),
			)
		)

	if status == "new":
		query = query.filter(WebsiteFeedback.is_read.is_(False))
	elif status == "read":
		query = query.filter(WebsiteFeedback.is_read.is_(True))

	feedback_items = query.all()
	unread_count = WebsiteFeedback.query.filter(WebsiteFeedback.is_read.is_(False)).count()
	filtered_total = len(feedback_items)
	read_count = len([item for item in feedback_items if item.is_read])
	unique_senders = len({item.visitor_email for item in feedback_items})

	return render_template(
		"admin_suggestions.html",
		feedback_items=feedback_items,
		search=search,
		status=status,
		unread_count=unread_count,
		filtered_total=filtered_total,
		read_count=read_count,
		unique_senders=unique_senders,
	)


@admin_bp.route("/updates")
@login_required
@role_required("admin")
def updates_page():
	selected_type = (request.args.get("type") or "all").strip().lower()
	if selected_type not in {"all", "moderation", "experience", "reports", "system"}:
		selected_type = "all"

	snapshot = _build_admin_updates_snapshot(selected_type)

	return render_template(
		"admin_updates.html",
		selected_type=snapshot["selected_type"],
		events=snapshot["events"],
		moderation_queue_count=snapshot["moderation_queue_count"],
		pending_experience_count=snapshot["pending_experience_count"],
		open_experience_reports_count=snapshot["open_experience_reports_count"],
		unread_suggestions_count=snapshot["unread_suggestions_count"],
		pending_semester_exceptions_count=snapshot["pending_semester_exceptions_count"],
		pending_faculty_feedback_count=snapshot["pending_faculty_feedback_count"],
	)


@admin_bp.route("/updates/live")
@login_required
@role_required("admin")
def updates_live():
	selected_type = (request.args.get("type") or "all").strip().lower()
	if selected_type not in {"all", "moderation", "experience", "reports", "system"}:
		selected_type = "all"

	snapshot = _build_admin_updates_snapshot(selected_type)
	events_payload = [
		{
			"kind": item["kind"],
			"title": item["title"],
			"detail": item["detail"],
			"created_at_ist": _utc_to_ist(item["created_at"]).strftime("%d %b %Y %I:%M %p") if item["created_at"] else "-",
		}
		for item in snapshot["events"][:80]
	]

	return jsonify(
		{
			"selected_type": snapshot["selected_type"],
			"counts": {
				"moderation_queue_count": snapshot["moderation_queue_count"],
				"pending_experience_count": snapshot["pending_experience_count"],
				"open_experience_reports_count": snapshot["open_experience_reports_count"],
				"unread_suggestions_count": snapshot["unread_suggestions_count"],
				"pending_semester_exceptions_count": snapshot["pending_semester_exceptions_count"],
				"pending_faculty_feedback_count": snapshot["pending_faculty_feedback_count"],
			},
			"events": events_payload,
		}
	)


@admin_bp.route("/profile-settings", methods=["GET", "POST"])
@login_required
@role_required("admin")
def profile_settings():
	admin_user = User.query.get(session["user_id"])
	if not admin_user:
		flash("Admin account not found.", "danger")
		return redirect(url_for("auth.logout"))

	active_tab = (request.args.get("tab") or "profile").strip().lower()
	if active_tab not in {"profile", "security"}:
		active_tab = "profile"

	if request.method == "POST":
		form_type = (request.form.get("form_type") or "").strip().lower()

		if form_type == "profile":
			flash("Profile identity fields are protected and cannot be edited here.", "warning")
			return redirect(url_for("admin.profile_settings", tab="profile"))

		if form_type == "security_password":
			current_password = request.form.get("current_password", "")
			new_password = request.form.get("new_password", "")
			confirm_new_password = request.form.get("confirm_new_password", "")

			if not admin_user.check_password(current_password):
				flash("Current password is incorrect.", "danger")
				return redirect(url_for("admin.profile_settings", tab="security"))

			if len(new_password) < 8:
				flash("New password must be at least 8 characters.", "danger")
				return redirect(url_for("admin.profile_settings", tab="security"))

			if new_password != confirm_new_password:
				flash("New password and confirmation do not match.", "danger")
				return redirect(url_for("admin.profile_settings", tab="security"))

			admin_user.set_password(new_password)
			db.session.commit()
			flash("Password updated successfully.", "success")
			return redirect(url_for("admin.profile_settings", tab="security"))

		if form_type == "security_question":
			security_question = (request.form.get("security_question") or "").strip()
			security_answer = (request.form.get("security_answer") or "").strip()

			if security_question not in SECURITY_QUESTIONS:
				flash("Please select a valid security question.", "danger")
				return redirect(url_for("admin.profile_settings", tab="security"))

			if not security_answer:
				flash("Security answer is required.", "danger")
				return redirect(url_for("admin.profile_settings", tab="security"))

			admin_user.security_question = security_question
			admin_user.set_security_answer(security_answer)
			db.session.commit()
			flash("Security question updated successfully.", "success")
			return redirect(url_for("admin.profile_settings", tab="security"))

	total_users = User.query.count()
	active_users = User.query.filter_by(is_active=True).count()
	moderation_actions_count = ModerationLog.query.filter_by(admin_id=admin_user.id).count()
	queue_stats = _build_admin_queue_stats()

	recent_actions = (
		ModerationLog.query.filter_by(admin_id=admin_user.id).order_by(ModerationLog.created_at.desc()).limit(10).all()
	)

	return render_template(
		"admin_profile_settings.html",
		admin_user=admin_user,
		active_tab=active_tab,
		security_questions=SECURITY_QUESTIONS,
		total_users=total_users,
		active_users=active_users,
		moderation_actions_count=moderation_actions_count,
		queue_stats=queue_stats,
		recent_actions=recent_actions,
	)


@admin_bp.route("/profile-settings/live")
@login_required
@role_required("admin")
def profile_settings_live():
	admin_user = User.query.get(session["user_id"])
	if not admin_user:
		return jsonify({"error": "not_found"}), 404

	total_users = User.query.count()
	active_users = User.query.filter_by(is_active=True).count()
	moderation_actions_count = ModerationLog.query.filter_by(admin_id=admin_user.id).count()
	queue_stats = _build_admin_queue_stats()
	recent_actions = (
		ModerationLog.query.filter_by(admin_id=admin_user.id).order_by(ModerationLog.created_at.desc()).limit(10).all()
	)

	return jsonify(
		{
			"moderation_actions_count": moderation_actions_count,
			"total_users": total_users,
			"active_users": active_users,
			"queue_stats": queue_stats,
			"recent_actions": [
				{
					"created_at_ist": _utc_to_ist(log.created_at).strftime("%d %b %Y %I:%M %p") if log.created_at else "-",
					"action": (log.action or "").replace("_", " ").title(),
					"action_class": log.action or "",
					"feedback_subject": log.feedback.subject if log.feedback else "-",
					"note": log.note or "-",
				}
				for log in recent_actions
			],
		}
	)


@admin_bp.route("/suggestions/<int:feedback_id>/mark", methods=["POST"])
@login_required
@role_required("admin")
def mark_suggestion(feedback_id: int):
	action = request.form.get("action", "read").strip().lower()
	next_url = request.form.get("next_url", "").strip()
	feedback_item = WebsiteFeedback.query.get(feedback_id)

	if not feedback_item:
		flash("Suggestion record not found.", "danger")
		return redirect(url_for("admin.suggestions_page"))

	if action == "unread":
		feedback_item.is_read = False
		feedback_item.read_at = None
		flash("Suggestion marked as new.", "success")
	else:
		feedback_item.is_read = True
		feedback_item.read_at = datetime.utcnow()
		flash("Suggestion marked as read.", "success")

	db.session.commit()

	if next_url.startswith("/admin/suggestions"):
		return redirect(next_url)
	return redirect(url_for("admin.suggestions_page"))


@admin_bp.route("/users")
@login_required
@role_required("admin")
def users():
	search = request.args.get("q", "").strip()
	role = request.args.get("role", "all").strip().lower()
	status = request.args.get("status", "all").strip().lower()
	course_raw = request.args.get("course", "all").strip()
	course = course_raw.upper() if course_raw and course_raw.lower() != "all" else "all"
	semester = request.args.get("semester", "all").strip().lower()
	section_raw = request.args.get("section", "all").strip()
	section = section_raw.upper() if section_raw and section_raw.lower() != "all" else "all"

	query = (
		User.query.options(joinedload(User.student_profile))
		.outerjoin(StudentAcademicProfile, StudentAcademicProfile.user_id == User.id)
		.order_by(User.created_at.desc())
	)

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

	if course != "all":
		query = query.filter(func.upper(User.course) == course)

	if section != "all":
		query = query.filter(func.upper(func.coalesce(User.section, "")) == section)

	if semester != "all":
		try:
			semester_value = int(semester)
		except ValueError:
			semester = "all"
		else:
			query = query.filter(
				User.role == "student",
				StudentAcademicProfile.current_semester == semester_value,
			)
			semester = str(semester_value)

	users_list = query.all()
	course_options = [
		value
		for value, in db.session.query(func.upper(User.course))
		.distinct()
		.order_by(func.upper(User.course).asc())
		.all()
		if (value or "").strip()
	]
	section_options = [
		value
		for value, in db.session.query(func.upper(User.section))
		.filter(User.section.isnot(None), func.trim(User.section) != "")
		.distinct()
		.order_by(func.upper(User.section).asc())
		.all()
		if (value or "").strip()
	]
	semester_options = [
		str(value)
		for value, in db.session.query(StudentAcademicProfile.current_semester)
		.filter(StudentAcademicProfile.current_semester.isnot(None))
		.distinct()
		.order_by(StudentAcademicProfile.current_semester.asc())
		.all()
	]

	totals = {
		"total": User.query.count(),
		"students": User.query.filter_by(role="student").count(),
		"faculty": User.query.filter_by(role="faculty").count(),
		"inactive": User.query.filter_by(is_active=False).count(),
	}
	return render_template(
		"admin_users.html",
		users_list=users_list,
		search=search,
		role=role,
		status=status,
		course=course,
		semester=semester,
		section=section,
		course_options=course_options,
		semester_options=semester_options,
		section_options=section_options,
		totals=totals,
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

	if user.role == "admin":
		flash("Admin accounts are protected and cannot be deactivated here.", "danger")
		return redirect(url_for("admin.users"))

	if user.id == session.get("user_id"):
		flash("You cannot deactivate your own account.", "danger")
		return redirect(url_for("admin.users"))

	user.is_active = not user.is_active
	db.session.commit()
	flash("User status updated successfully.", "success")
	return redirect(url_for("admin.users"))


@admin_bp.route("/users/<int:user_id>/set-active", methods=["POST"])
@login_required
@role_required("admin")
def set_user_active_state(user_id: int):
	action = (request.form.get("action") or "").strip().lower()
	next_url = (request.form.get("next_url") or "").strip().rstrip("?")
	if action not in {"activate", "deactivate"}:
		flash("Invalid account action.", "danger")
		return redirect(url_for("admin.users"))

	user = User.query.get(user_id)
	if not user:
		flash("User not found.", "danger")
		return redirect(url_for("admin.users"))

	if user.role == "admin":
		flash("Admin accounts are protected and cannot be modified here.", "danger")
		return redirect(url_for("admin.users"))

	if user.id == session.get("user_id"):
		flash("You cannot change your own account status.", "danger")
		return redirect(url_for("admin.users"))

	next_state = action == "activate"
	if user.is_active == next_state:
		flash("User is already in the requested state.", "warning")
	else:
		user.is_active = next_state
		db.session.commit()
		flash("User account updated successfully.", "success")

	if next_url.startswith("/admin/users"):
		return redirect(next_url)
	return redirect(url_for("admin.users"))


@admin_bp.route("/users/<int:user_id>/delete", methods=["POST"])
@login_required
@role_required("admin")
def delete_user(user_id: int):
	next_url = (request.form.get("next_url") or "").strip().rstrip("?")
	user = User.query.get(user_id)
	if not user:
		flash("User not found.", "danger")
		return redirect(url_for("admin.users"))

	if user.role == "admin":
		flash("Admin accounts are protected and cannot be deleted.", "danger")
		return redirect(url_for("admin.users"))

	if user.id == session.get("user_id"):
		flash("You cannot delete your own account.", "danger")
		return redirect(url_for("admin.users"))

	try:
		db.session.delete(user)
		db.session.commit()
	except IntegrityError:
		db.session.rollback()
		flash("Unable to delete this user because related records exist. Deactivate the account instead.", "warning")
	else:
		flash("User deleted successfully.", "success")

	if next_url.startswith("/admin/users"):
		return redirect(next_url)
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


@admin_bp.route("/audit-log")
@login_required
@role_required("admin")
def audit_log():
	search = request.args.get("q", "").strip()
	action = request.args.get("action", "all").strip().lower()
	admin_id = request.args.get("admin_id", "all").strip().lower()
	view = request.args.get("view", "timeline").strip().lower()

	query = (
		ModerationLog.query
		.join(User, ModerationLog.admin_id == User.id)
		.join(Feedback, ModerationLog.feedback_id == Feedback.id)
	)

	if search:
		like = f"%{search}%"
		query = query.filter(
			or_(
				Feedback.subject.ilike(like),
				Feedback.feedback_text.ilike(like),
				User.full_name.ilike(like),
				User.email.ilike(like),
				ModerationLog.action.ilike(like),
				ModerationLog.note.ilike(like),
			)
		)

	allowed_actions = {"approve", "reject", "request_edit"}
	if action in allowed_actions:
		query = query.filter(ModerationLog.action == action)

	admin_options = User.query.filter_by(role="admin", is_active=True).order_by(User.full_name.asc()).all()
	if admin_id != "all":
		try:
			selected_admin_id = int(admin_id)
		except ValueError:
			selected_admin_id = None
		if selected_admin_id:
			query = query.filter(ModerationLog.admin_id == selected_admin_id)

	logs = query.order_by(ModerationLog.created_at.desc()).all()
	logs_today = len([item for item in logs if item.created_at.date() == datetime.utcnow().date()])
	recent_window = datetime.utcnow() - timedelta(hours=24)
	recent_count = len([item for item in logs if item.created_at >= recent_window])

	kpi = {
		"total": len(logs),
		"approved": len([item for item in logs if item.action == "approve"]),
		"rejected": len([item for item in logs if item.action == "reject"]),
		"edit_requested": len([item for item in logs if item.action == "request_edit"]),
		"admins": len({item.admin_id for item in logs}),
		"today": logs_today,
		"recent": recent_count,
	}

	if view not in {"timeline", "table"}:
		view = "timeline"

	return render_template(
		"admin_audit_log.html",
		logs=logs,
		search=search,
		action=action,
		admin_id=admin_id,
		admin_options=admin_options,
		kpi=kpi,
		view=view,
	)


@admin_bp.route("/experience-moderation")
@login_required
@role_required("admin")
def experience_moderation():
	status_filter = request.args.get("status", "pending").strip().lower()
	valid_statuses = {"pending", "approved", "rejected", "request_edit", "all"}
	if status_filter not in valid_statuses:
		status_filter = "pending"

	query = StudentExperience.query
	if status_filter != "all":
		query = query.filter(StudentExperience.status == status_filter)
	experiences = query.order_by(StudentExperience.created_at.desc()).all()

	counts = {
		"pending": StudentExperience.query.filter_by(status="pending").count(),
		"approved": StudentExperience.query.filter_by(status="approved").count(),
		"rejected": StudentExperience.query.filter_by(status="rejected").count(),
		"request_edit": StudentExperience.query.filter_by(status="request_edit").count(),
	}
	return render_template(
		"admin_experience_moderation.html",
		experiences=experiences,
		status_filter=status_filter,
		counts=counts,
	)


@admin_bp.route("/experience-moderation/<int:exp_id>/detail")
@login_required
@role_required("admin")
def experience_moderation_detail(exp_id: int):
	exp = StudentExperience.query.get_or_404(exp_id)
	payload = {
		"id": exp.id,
		"anon_id": exp.anon_id,
		"title": exp.title,
		"body": exp.body,
		"category": exp.category,
		"tags": [tag.replace("_", " ").title() for tag in (exp.tags or "").split(",") if tag],
		"resource_links": _normalized_resource_links(exp.resource_links or ""),
		"sentiment": (exp.sentiment or "neutral").lower(),
		"sentiment_confidence": int(exp.sentiment_confidence or 0),
		"status": exp.status,
		"admin_note": exp.admin_note or "",
		"upvote_count": int(exp.upvote_count or 0),
		"created_at_ist": _utc_to_ist(exp.created_at).strftime("%d %b %Y %I:%M %p") if exp.created_at else "-",
	}
	return jsonify(payload)


@admin_bp.route("/experience-moderation/<int:exp_id>/decide", methods=["POST"])
@login_required
@role_required("admin")
def decide_experience(exp_id: int):
	exp = StudentExperience.query.get_or_404(exp_id)
	decision = request.form.get("decision", "").strip().lower()
	admin_note = request.form.get("admin_note", "").strip()

	if decision not in {"approve", "reject", "request_edit"}:
		flash("Invalid decision.", "danger")
		return redirect(url_for("admin.experience_moderation"))

	status_map = {"approve": "approved", "reject": "rejected", "request_edit": "request_edit"}
	exp.status = status_map[decision]
	exp.admin_note = admin_note if admin_note else None
	db.session.commit()

	flash(f"Experience {exp.anon_id} marked as {exp.status}.", "success")
	return redirect(url_for("admin.experience_moderation"))


@admin_bp.route("/experience-reports")
@login_required
@role_required("admin")
def experience_reports_list():
	status_filter = request.args.get("status", "open").strip().lower()
	if status_filter not in {"open", "reviewed", "all"}:
		status_filter = "open"

	query = ExperienceReport.query
	if status_filter != "all":
		query = query.filter(ExperienceReport.status == status_filter)
	reports = query.order_by(ExperienceReport.created_at.desc()).all()

	open_count = ExperienceReport.query.filter_by(status="open").count()
	return render_template(
		"admin_experience_reports.html",
		reports=reports,
		status_filter=status_filter,
		open_count=open_count,
	)


@admin_bp.route("/experience-reports/<int:report_id>/dismiss", methods=["POST"])
@login_required
@role_required("admin")
def dismiss_experience_report(report_id: int):
	report = ExperienceReport.query.get_or_404(report_id)
	report.status = "reviewed"
	report.admin_id = session["user_id"]
	report.reviewed_at = datetime.utcnow()
	db.session.commit()
	flash("Report dismissed.", "success")
	return redirect(url_for("admin.experience_reports_list"))


@admin_bp.route("/experience-reports/<int:report_id>/remove-post", methods=["POST"])
@login_required
@role_required("admin")
def remove_reported_experience(report_id: int):
	report = ExperienceReport.query.get_or_404(report_id)
	exp = report.experience
	if exp:
		db.session.delete(exp)
		message = "Experience removed and all related reports closed."
	else:
		report.status = "reviewed"
		report.admin_id = session["user_id"]
		report.reviewed_at = datetime.utcnow()
		message = "Report marked as reviewed."
	db.session.commit()
	flash(message, "success")
	return redirect(url_for("admin.experience_reports_list"))
