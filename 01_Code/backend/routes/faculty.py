import csv
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import func, or_
from werkzeug.utils import secure_filename

from models import (
	Checklist,
	CourseConfig,
	FacultyAssignment,
	Feedback,
	KnowledgeAttachment,
	KnowledgeNotification,
	KnowledgePost,
	KnowledgeReaction,
	KnowledgeView,
	SubjectOffering,
	User,
	db,
)
from models import ExperienceUpvote, StudentExperience
from routes.auth import SECURITY_QUESTIONS, login_required, role_required


faculty_bp = Blueprint("faculty", __name__, url_prefix="/faculty")


CHECKLIST_META_PREFIX = "[[CLARIFAI_META]]"
SUBJECT_CATALOG_PATH = Path(__file__).resolve().parents[1] / "data" / "subjects.csv"
IST_ZONE = timezone(timedelta(hours=5, minutes=30))
CHECKLIST_ATTACHMENT_MAX_BYTES = 20 * 1024 * 1024
CHECKLIST_ALLOWED_EXTENSIONS = {
	"pdf",
	"doc",
	"docx",
	"txt",
	"ppt",
	"pptx",
	"xls",
	"xlsx",
	"csv",
	"zip",
	"rar",
	"png",
	"jpg",
	"jpeg",
}
INTERVENTION_ATTACHMENT_MAX_BYTES = 30 * 1024 * 1024
INTERVENTION_ATTACHMENT_MAX_FILES = 5
INTERVENTION_ALLOWED_EXTENSIONS = {
	"pdf",
	"doc",
	"docx",
	"txt",
	"png",
	"jpg",
	"jpeg",
	"gif",
	"webp",
}
INTERVENTION_ALLOWED_STATUSES = {"draft", "published"}
CHECKLIST_DEFAULT_CATEGORIES = [
	"Academic",
	"Projects",
	"Internship",
	"Tips & Tricks",
	"Problem Solved",
	"Career",
	"Subject",
	"General",
	"Examinations",
]


def _normalize_priority(raw_priority: str):
	priority = (raw_priority or "medium").strip().lower()
	if priority not in {"high", "medium", "low"}:
		return "medium"
	return priority


def _utc_to_ist(value):
	if value is None:
		return None
	source = value
	if source.tzinfo is None:
		source = source.replace(tzinfo=timezone.utc)
	return source.astimezone(IST_ZONE)


def _parse_iso_date(raw_value: str):
	if not raw_value:
		return None
	try:
		return datetime.strptime(raw_value, "%Y-%m-%d").date()
	except ValueError:
		return None


def _split_csv_tokens(raw_value: str):
	if not raw_value:
		return []
	return [token.strip().lower() for token in raw_value.split(",") if token.strip()]


def _normalize_course_code(raw_value: str):
	return (raw_value or "").strip().upper()


def _normalize_target_course(raw_value: str):
	value = (raw_value or "BOTH").strip().upper()
	if value not in {"MCA", "BCA", "BOTH"}:
		return "BOTH"
	return value


def _normalize_target_semester(raw_value):
	text = str(raw_value or "").strip().lower()
	if not text or text == "all":
		return "all"
	digits = "".join(ch for ch in text if ch.isdigit())
	if not digits:
		return "all"
	try:
		value = int(digits)
	except ValueError:
		return "all"
	if value < 1 or value > 12:
		return "all"
	return str(value)


def _normalize_task_lines(raw_value: str):
	if not raw_value:
		return []
	lines = []
	for line in raw_value.splitlines():
		text = line.strip()
		if not text:
			continue
		text = text.lstrip("-*")
		text = text.strip()
		while text and text[0].isdigit():
			text = text[1:]
		text = text.lstrip(".):- ")
		text = text.strip()
		if text:
			lines.append(text)
	return lines[:10]


def _extract_task_lines_from_form(form_data):
	task_pairs = []
	prefix = "checklist_item_"
	for key in form_data.keys():
		if not key.startswith(prefix):
			continue
		suffix = key[len(prefix) :].strip()
		try:
			index = int(suffix)
		except ValueError:
			continue
		value = (form_data.get(key) or "").strip()
		if not value:
			continue
		normalized = _normalize_task_lines(value)
		if not normalized:
			continue
		task_pairs.append((index, normalized[0]))

	if task_pairs:
		task_pairs.sort(key=lambda item: item[0])
		return [task for _, task in task_pairs][:10]

	return _normalize_task_lines(form_data.get("task_items", ""))


def _load_subject_catalog_by_course():
	bucket = {"MCA": [], "BCA": []}
	seen = {"MCA": set(), "BCA": set()}
	if not SUBJECT_CATALOG_PATH.exists():
		return bucket

	with SUBJECT_CATALOG_PATH.open("r", encoding="utf-8") as file:
		reader = csv.DictReader(file)
		for row in reader:
			if not row:
				continue
			is_active = (row.get("is_active") or "").strip().lower()
			if is_active not in {"yes", "true", "1"}:
				continue
			course = (row.get("degree") or "").strip().upper()
			if course not in bucket:
				continue
			subject_code = (row.get("course_code") or "").strip().upper()
			subject_name = (row.get("subject_name") or "").strip()
			label = ""
			if subject_code and subject_name:
				label = f"{subject_code} - {subject_name}"
			else:
				label = subject_name or subject_code
			if not label or label in seen[course]:
				continue
			seen[course].add(label)
			bucket[course].append(label)

	for course in bucket:
		bucket[course].sort()
	return bucket


def _subject_options_for_course(subject_catalog_by_course: dict, target_course: str):
	if target_course in {"MCA", "BCA"}:
		return list(subject_catalog_by_course.get(target_course, []))
	return []


def _normalize_subject_for_course(
	raw_subject: str,
	target_course: str,
	subject_catalog_by_course: dict,
	*,
	legacy_subject: str | None = None,
):
	subject = (raw_subject or "No specific subject").strip() or "No specific subject"
	if subject == "No specific subject":
		return subject, None
	if target_course == "BOTH":
		return None, "Select applicable course as MCA or BCA to choose a specific subject."
	valid_subjects = set(_subject_options_for_course(subject_catalog_by_course, target_course))
	if subject in valid_subjects:
		return subject, None
	if legacy_subject and subject == legacy_subject:
		return subject, None
	return None, f"Choose a valid {target_course} subject or keep No specific subject."


def _student_course_code(student: User | None):
	if not student:
		return ""
	if student.student_profile and student.student_profile.course_code:
		return _normalize_course_code(student.student_profile.course_code)
	return _normalize_course_code(student.course)


def _normalize_task_indexes(raw_indexes, total_tasks: int):
	if total_tasks <= 0:
		return []
	if not isinstance(raw_indexes, list):
		return []
	normalized = set()
	for raw in raw_indexes:
		try:
			idx = int(raw)
		except (TypeError, ValueError):
			continue
		if 0 <= idx < total_tasks:
			normalized.add(idx)
	return sorted(normalized)


def _checklist_tasks_state(title: str, details: dict, is_completed: bool):
	tasks = [item.strip() for item in details.get("tasks", []) if str(item).strip()]
	if not tasks:
		fallback = details.get("description") or title
		fallback = str(fallback or "Complete this checklist item").strip()
		tasks = [fallback]
	completed_indexes = _normalize_task_indexes(details.get("completed_tasks", []), len(tasks))
	if is_completed and not completed_indexes and tasks:
		completed_indexes = list(range(len(tasks)))
	if len(completed_indexes) == len(tasks) and tasks:
		state = "complete"
	elif completed_indexes:
		state = "partial"
	else:
		state = "null"
	return {
		"tasks": tasks,
		"completed_indexes": completed_indexes,
		"total": len(tasks),
		"completed": len(completed_indexes),
		"state": state,
	}


def _student_current_semester(student: User | None):
	if not student or not student.student_profile:
		return None
	try:
		value = int(student.student_profile.current_semester)
	except (TypeError, ValueError):
		return None
	if value < 1:
		return None
	return value


def _checklist_categories():
	try:
		from routes.student import EXPERIENCE_CATEGORIES

		if EXPERIENCE_CATEGORIES:
			return list(EXPERIENCE_CATEGORIES)
	except Exception:
		pass
	return list(CHECKLIST_DEFAULT_CATEGORIES)


def _make_checklist_group_id():
	return f"CL-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:10]}"


def _save_checklist_attachment(uploaded_file, group_id: str):
	if not uploaded_file or not getattr(uploaded_file, "filename", ""):
		return None, None

	filename = secure_filename(uploaded_file.filename or "")
	if not filename:
		return None, "Attachment filename is invalid."

	extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
	if extension not in CHECKLIST_ALLOWED_EXTENSIONS:
		return None, "Only document/image archive files are allowed. Use Drive link for audio/video."

	mime = (uploaded_file.mimetype or "").lower()
	if mime.startswith("audio/") or mime.startswith("video/"):
		return None, "Audio/video files are not accepted here. Please provide a Drive link instead."

	content_length = request.content_length or 0
	if content_length and content_length > CHECKLIST_ATTACHMENT_MAX_BYTES:
		return None, "Attachment exceeds 20MB. Please use a Drive link for larger files."

	upload_root = Path(current_app.static_folder) / "uploads" / "checklists"
	upload_root.mkdir(parents=True, exist_ok=True)
	stored_name = f"{group_id}_{filename}"
	target = upload_root / stored_name
	uploaded_file.save(target)

	file_size = target.stat().st_size
	if file_size > CHECKLIST_ATTACHMENT_MAX_BYTES:
		target.unlink(missing_ok=True)
		return None, "Attachment exceeds 20MB. Please use a Drive link for larger files."

	return {
		"kind": "file",
		"name": filename,
		"path": f"uploads/checklists/{stored_name}",
		"size_mb": round(file_size / (1024 * 1024), 2),
	}, None


def _normalize_section(raw_value: str):
	return (raw_value or "").strip().upper()


def _normalize_target_section(raw_value: str):
	text = (raw_value or "").strip()
	if not text or text.lower() == "all":
		return "all"
	return text.upper()


def _parse_csv_values(raw_value: str, *, uppercase: bool = False):
	if not raw_value:
		return []
	values = []
	for token in str(raw_value).split(","):
		clean = token.strip()
		if not clean:
			continue
		values.append(clean.upper() if uppercase else clean)
	return values


def _serialize_csv_values(values):
	items = []
	seen = set()
	for value in values or []:
		clean = str(value or "").strip()
		if not clean:
			continue
		if clean in seen:
			continue
		seen.add(clean)
		items.append(clean)
	return ",".join(items)


def _intervention_semester_options():
	semester_limits = {"MCA": 4, "BCA": 6}
	configs = CourseConfig.query.filter_by(is_active=True).all()
	for config in configs:
		code = _normalize_course_code(config.course_code)
		if code not in semester_limits:
			continue
		try:
			semester_limits[code] = max(1, min(int(config.total_semesters or semester_limits[code]), 12))
		except (TypeError, ValueError):
			continue

	options = {}
	for course, total in semester_limits.items():
		options[course] = ["all", *[str(idx) for idx in range(1, total + 1)]]
	return options


def _intervention_section_options():
	sections = sorted(
		{
			_normalize_section(student.section)
			for student in User.query.filter_by(role="student", is_active=True).all()
			if _normalize_section(student.section)
		}
	)
	if not sections:
		sections = ["A", "B", "C"]
	return ["ALL", *sections]


def _normalize_intervention_status(raw_status: str):
	status = (raw_status or "draft").strip().lower()
	if status not in INTERVENTION_ALLOWED_STATUSES:
		return "draft"
	return status


def _normalize_intervention_semester_for_course(raw_value: str, course_code: str, semester_options_by_course: dict):
	value = _normalize_target_semester(raw_value)
	if value == "all":
		return "all"
	valid = set(semester_options_by_course.get(course_code, ["all"]))
	if value in valid:
		return value
	return None


def _normalize_intervention_section(raw_value: str, section_options: list[str]):
	value = _normalize_target_section(raw_value)
	if value == "all":
		value = "ALL"
	if value in set(section_options):
		return value
	return None


def _parse_intervention_links(raw_value: str):
	if not raw_value:
		return ""
	cleaned = []
	for line in raw_value.splitlines():
		link = line.strip()
		if not link:
			continue
		if not (link.startswith("http://") or link.startswith("https://")):
			continue
		cleaned.append(link)
	return "\n".join(cleaned[:20])


def _intervention_form_payload(form_data):
	title = (form_data.get("title") or "").strip()
	content = (form_data.get("content") or "").strip()
	status = _normalize_intervention_status(form_data.get("post_status"))
	semester_options_by_course = _intervention_semester_options()
	section_options = _intervention_section_options()

	target_course = _normalize_course_code(form_data.get("target_course") or "")
	if target_course not in {"MCA", "BCA"}:
		target_course = ""

	target_semester = _normalize_intervention_semester_for_course(
		form_data.get("target_semester"),
		target_course,
		semester_options_by_course,
	)
	target_section = _normalize_intervention_section(form_data.get("target_section"), section_options)

	if not title:
		return None, "Title is required."
	if not content:
		return None, "Content is required."
	if len(content) < 20:
		return None, "Please provide at least 20 characters for meaningful guidance."
	if not target_course:
		return None, "Select one target course (MCA or BCA)."
	if not target_semester:
		return None, "Select a valid semester for the selected course."
	if not target_section:
		return None, "Select a valid section."

	payload = {
		"title": title,
		"content": content,
		"problem_context": (form_data.get("problem_context") or "").strip() or None,
		"solution_steps": (form_data.get("solution_steps") or "").strip() or None,
		"resource_references": (form_data.get("resource_references") or "").strip() or None,
		"outcome_result": (form_data.get("outcome_result") or "").strip() or None,
		"resource_links": _parse_intervention_links(form_data.get("resource_links") or "") or None,
		"target_course": target_course,
		"target_semester": target_semester,
		"target_section": target_section,
		"status": status,
	}
	return payload, None


def _intervention_form_values(form_data):
	course_value = _normalize_course_code(form_data.get("target_course") or "")
	if course_value not in {"MCA", "BCA"}:
		course_value = "MCA"
	semester_options_by_course = _intervention_semester_options()
	semester_value = _normalize_intervention_semester_for_course(
		form_data.get("target_semester"),
		course_value,
		semester_options_by_course,
	) or "all"
	section_value = _normalize_intervention_section(
		form_data.get("target_section"),
		_intervention_section_options(),
	) or "ALL"

	return {
		"title": (form_data.get("title") or "").strip(),
		"content": (form_data.get("content") or "").strip(),
		"problem_context": (form_data.get("problem_context") or "").strip(),
		"solution_steps": (form_data.get("solution_steps") or "").strip(),
		"resource_references": (form_data.get("resource_references") or "").strip(),
		"outcome_result": (form_data.get("outcome_result") or "").strip(),
		"resource_links": (form_data.get("resource_links") or "").strip(),
		"target_course": course_value,
		"target_semester": semester_value,
		"target_section": section_value,
		"post_status": _normalize_intervention_status(form_data.get("post_status")),
	}


def _post_is_within_edit_window(post: KnowledgePost):
	anchor = post.published_at or post.created_at
	if not anchor:
		return False
	return datetime.utcnow() - anchor <= timedelta(hours=24)


def _student_matches_intervention_targets(student: User, post: KnowledgePost):
	courses = set(_parse_csv_values(post.target_courses, uppercase=True))
	semesters = {value.lower() for value in _parse_csv_values(post.target_semesters, uppercase=False)}
	sections = set(_parse_csv_values(post.target_sections, uppercase=True))

	student_course = _student_course_code(student)
	if courses and student_course not in courses:
		return False

	student_semester = _student_current_semester(student)
	if semesters and "all" not in semesters:
		if student_semester is None or str(student_semester) not in semesters:
			return False

	if sections and "ALL" not in sections:
		student_section = _normalize_section(student.section)
		if not student_section or student_section not in sections:
			return False

	return True


def _targeted_students_for_post(post: KnowledgePost):
	students = User.query.filter_by(role="student", is_active=True).all()
	return [student for student in students if _student_matches_intervention_targets(student, post)]


def _save_intervention_attachments(uploaded_files, post_id: int):
	valid_files = [f for f in (uploaded_files or []) if f and getattr(f, "filename", "")]
	if len(valid_files) > INTERVENTION_ATTACHMENT_MAX_FILES:
		return None, f"You can upload at most {INTERVENTION_ATTACHMENT_MAX_FILES} files per intervention."

	upload_root = Path(current_app.static_folder) / "uploads" / "interventions"
	upload_root.mkdir(parents=True, exist_ok=True)

	attachments = []
	saved_paths = []
	for item in valid_files:
		filename = secure_filename(item.filename or "")
		if not filename:
			continue
		extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
		if extension not in INTERVENTION_ALLOWED_EXTENSIONS:
			for path in saved_paths:
				Path(path).unlink(missing_ok=True)
			return None, "Only PDF, DOC, DOCX, and image files are allowed."

		stored_name = f"post_{post_id}_{uuid4().hex[:10]}_{filename}"
		target = upload_root / stored_name
		item.save(target)
		saved_paths.append(target)

		file_size = target.stat().st_size
		if file_size > INTERVENTION_ATTACHMENT_MAX_BYTES:
			for path in saved_paths:
				Path(path).unlink(missing_ok=True)
			return None, "Each attachment must be 30MB or smaller."

		attachments.append(
			KnowledgeAttachment(
				post_id=post_id,
				file_name=filename,
				file_path=f"uploads/interventions/{stored_name}",
				file_ext=extension,
				file_size=file_size,
			)
		)

	return attachments, None


def _delete_attachment_file(attachment: KnowledgeAttachment):
	file_path = (attachment.file_path or "").strip()
	if not file_path:
		return
	target = Path(current_app.static_folder) / file_path
	target.unlink(missing_ok=True)


def _intervention_reaction_counts(post_ids):
	if not post_ids:
		return {}, {}, {}
	like_rows = (
		db.session.query(KnowledgeReaction.post_id, func.count(KnowledgeReaction.id))
		.filter(KnowledgeReaction.post_id.in_(post_ids), KnowledgeReaction.reaction_type == "like")
		.group_by(KnowledgeReaction.post_id)
		.all()
	)
	bookmark_rows = (
		db.session.query(KnowledgeReaction.post_id, func.count(KnowledgeReaction.id))
		.filter(KnowledgeReaction.post_id.in_(post_ids), KnowledgeReaction.reaction_type == "bookmark")
		.group_by(KnowledgeReaction.post_id)
		.all()
	)
	view_rows = (
		db.session.query(KnowledgeView.post_id, func.count(KnowledgeView.id))
		.filter(KnowledgeView.post_id.in_(post_ids))
		.group_by(KnowledgeView.post_id)
		.all()
	)
	return (
		{row[0]: row[1] for row in like_rows},
		{row[0]: row[1] for row in bookmark_rows},
		{row[0]: row[1] for row in view_rows},
	)


def _grouped_checklist_activity(checklist_rows):
	grouped = {}
	for row in checklist_rows:
		details = _parse_checklist_description(row.description or "")
		state = _checklist_tasks_state(row.title, details, row.is_completed)["state"]
		group_id = details["group_id"] or f"legacy-{row.id}"

		if group_id not in grouped:
			grouped[group_id] = {
				"group_id": group_id,
				"title": row.title,
				"created_at": row.created_at,
				"target_course": details.get("target_course", "BOTH"),
				"target_semester": details.get("target_semester", "all"),
				"target_section": details.get("target_section", "all"),
				"students": 0,
				"complete_count": 0,
				"partial_count": 0,
				"null_count": 0,
			}

		bucket = grouped[group_id]
		bucket["students"] += 1
		if row.created_at and (bucket["created_at"] is None or row.created_at > bucket["created_at"]):
			bucket["created_at"] = row.created_at

		if state == "complete":
			bucket["complete_count"] += 1
		elif state == "partial":
			bucket["partial_count"] += 1
		else:
			bucket["null_count"] += 1

	items = []
	for bucket in grouped.values():
		if bucket["students"] and bucket["complete_count"] == bucket["students"]:
			aggregate_state = "complete"
		elif bucket["partial_count"] > 0 or bucket["complete_count"] > 0:
			aggregate_state = "partial"
		else:
			aggregate_state = "null"

		bucket["aggregate_state"] = aggregate_state
		items.append(bucket)

	items.sort(key=lambda item: item["created_at"] or datetime.min, reverse=True)
	return items


def _faculty_visible_post(post_id: int):
	post = (
		KnowledgePost.query.join(User, KnowledgePost.author_id == User.id)
		.filter(KnowledgePost.id == post_id, User.role == "faculty")
		.first()
	)
	if not post:
		return None
	if post.status != "published" and post.author_id != session.get("user_id"):
		return None
	return post


def _target_summary(post: KnowledgePost):
	courses = _parse_csv_values(post.target_courses, uppercase=True)
	semesters = _parse_csv_values(post.target_semesters)
	sections = _parse_csv_values(post.target_sections, uppercase=True)

	course_label = courses[0] if courses else "-"
	if semesters and semesters[0].lower() == "all":
		semester_label = "All Semesters"
	elif semesters:
		semester_label = f"Sem {semesters[0]}"
	else:
		semester_label = "-"

	section_label = "All Sections" if sections and sections[0].upper() == "ALL" else (f"Section {sections[0]}" if sections else "-")
	return {
		"courses": course_label,
		"semesters": semester_label,
		"sections": section_label,
	}


def _queue_intervention_update_notifications(post: KnowledgePost, students):
	engaged_ids = set(
		row[0]
		for row in db.session.query(KnowledgeView.user_id)
		.filter(KnowledgeView.post_id == post.id)
		.all()
	)
	engaged_ids.update(
		row[0]
		for row in db.session.query(KnowledgeReaction.user_id)
		.filter(
			KnowledgeReaction.post_id == post.id,
			KnowledgeReaction.reaction_type == "bookmark",
		)
		.all()
	)

	now = datetime.utcnow()
	notified_count = 0
	for student in students:
		if student.id not in engaged_ids:
			continue
		db.session.add(
			KnowledgeNotification(
				post_id=post.id,
				user_id=student.id,
				message=f"Intervention updated: {post.title}. Please review the latest changes.",
				is_read=False,
				created_at=now,
			)
		)
		notified_count += 1
	return notified_count


def _extract_semester_token(raw_value):
	if raw_value is None:
		return ""
	text = str(raw_value).strip()
	if not text:
		return ""
	digits = "".join(ch for ch in text if ch.isdigit())
	return digits or text


def _serialize_checklist_description(
	description: str,
	subject: str,
	priority: str,
	due_date,
	*,
	category: str = "General",
	target_course: str = "BOTH",
	target_semester: str = "all",
	target_section: str = "all",
	tasks=None,
	completed_tasks=None,
	completion_locked: bool = False,
	group_id: str = "",
	attachment: dict | None = None,
):
	task_values = [item.strip() for item in (tasks or []) if str(item).strip()]
	completed_values = _normalize_task_indexes(completed_tasks or [], len(task_values))
	meta = {
		"subject": (subject or "No specific subject").strip() or "No specific subject",
		"priority": _normalize_priority(priority),
		"due_date": due_date.strftime("%Y-%m-%d") if due_date else "",
		"category": (category or "General").strip() or "General",
		"target_course": _normalize_target_course(target_course),
		"target_semester": _normalize_target_semester(target_semester),
		"target_section": _normalize_target_section(target_section),
		"tasks": task_values,
		"completed_tasks": completed_values,
		"completion_locked": bool(completion_locked),
		"group_id": (group_id or "").strip(),
		"attachment": attachment or None,
	}
	meta_blob = f"{CHECKLIST_META_PREFIX}{json.dumps(meta, separators=(',', ':'))}"
	body = (description or "").strip()
	if body:
		return f"{meta_blob}\n{body}"
	return meta_blob


def _parse_checklist_description(raw_description: str):
	details = {
		"subject": "No specific subject",
		"priority": "medium",
		"due_date": None,
		"category": "General",
		"target_course": "BOTH",
		"target_semester": "all",
		"target_section": "all",
		"tasks": [],
		"completed_tasks": [],
		"completion_locked": False,
		"group_id": "",
		"attachment": None,
		"description": (raw_description or "").strip(),
	}

	if not details["description"].startswith(CHECKLIST_META_PREFIX):
		return details

	first_line, _, remainder = details["description"].partition("\n")
	meta_raw = first_line[len(CHECKLIST_META_PREFIX) :].strip()
	try:
		meta = json.loads(meta_raw)
	except (TypeError, json.JSONDecodeError):
		meta = {}

	details["subject"] = (meta.get("subject") or "No specific subject").strip() or "No specific subject"
	details["priority"] = _normalize_priority(meta.get("priority", "medium"))
	details["due_date"] = _parse_iso_date(meta.get("due_date", ""))
	details["category"] = (meta.get("category") or "General").strip() or "General"
	details["target_course"] = _normalize_target_course(meta.get("target_course", "BOTH"))
	details["target_semester"] = _normalize_target_semester(meta.get("target_semester", "all"))
	details["target_section"] = _normalize_target_section(meta.get("target_section", "all"))
	details["tasks"] = [item.strip() for item in meta.get("tasks", []) if str(item).strip()]
	details["completed_tasks"] = _normalize_task_indexes(meta.get("completed_tasks", []), len(details["tasks"]))
	details["completion_locked"] = bool(meta.get("completion_locked", False))
	details["group_id"] = (meta.get("group_id") or "").strip()
	attachment = meta.get("attachment")
	details["attachment"] = attachment if isinstance(attachment, dict) else None
	details["description"] = remainder.strip()
	return details


def _due_state(due_date, is_completed: bool):
	if is_completed:
		return "completed"
	if due_date is None:
		return "on-track"

	today = datetime.utcnow().date()
	if due_date < today:
		return "overdue"
	if due_date <= today + timedelta(days=3):
		return "due-soon"
	return "on-track"


def _due_label(due_date, due_state: str):
	if due_date is None:
		return "-"
	label = due_date.strftime("%d %b")
	if due_state == "overdue":
		return f"{label} (Overdue)"
	if due_state == "due-soon":
		return f"{label} (Due Soon)"
	return label


def _aspect_scores(feedback_rows):
	aspect_tokens = {
		"Clarity": {"teaching_clarity", "communication", "clarity"},
		"Pace": {"pace_of_teaching", "pace", "speed"},
		"Assessment": {"assessment_fairness", "assessment", "assignment_quality"},
		"Engagement": {"engagement", "mentoring", "doubt_support", "lab_support"},
	}
	sentiment_score = {"positive": 0.55, "neutral": 0.0, "negative": -0.55}

	if not feedback_rows:
		return {}

	result = {}
	for aspect, tokens in aspect_tokens.items():
		deltas = []
		for row in feedback_rows:
			merged = set(_split_csv_tokens(row.feedback_tags))
			merged.add((row.reason or "").strip().lower())
			if merged.intersection(tokens):
				deltas.append(sentiment_score.get(row.sentiment, 0.0))
		if not deltas:
			continue
		raw_score = 3.6 + (sum(deltas) / len(deltas))
		result[aspect] = round(max(1.0, min(5.0, raw_score)), 1)

	return result


def _subject_sentiment(feedback_rows, limit: int = 5):
	bucket = {}
	for row in feedback_rows:
		subject = (row.subject or "Unknown").strip() or "Unknown"
		if subject not in bucket:
			bucket[subject] = {"positive": 0, "neutral": 0, "negative": 0}
		if row.sentiment in bucket[subject]:
			bucket[subject][row.sentiment] += 1

	ordered = sorted(bucket.items(), key=lambda item: sum(item[1].values()), reverse=True)[:limit]

	return {
		"labels": [item[0] for item in ordered],
		"positive": [item[1]["positive"] for item in ordered],
		"neutral": [item[1]["neutral"] for item in ordered],
		"negative": [item[1]["negative"] for item in ordered],
	}


def _month_sentiment(feedback_rows, month_keys):
	positive = {f"{year:04d}-{month:02d}": 0 for year, month in month_keys}
	neutral = {f"{year:04d}-{month:02d}": 0 for year, month in month_keys}
	negative = {f"{year:04d}-{month:02d}": 0 for year, month in month_keys}

	for row in feedback_rows:
		key = row.created_at.strftime("%Y-%m")
		if key in positive and row.sentiment == "positive":
			positive[key] += 1
		if key in neutral and row.sentiment == "neutral":
			neutral[key] += 1
		if key in negative and row.sentiment == "negative":
			negative[key] += 1

	return {
		"positive": [positive[f"{year:04d}-{month:02d}"] for year, month in month_keys],
		"neutral": [neutral[f"{year:04d}-{month:02d}"] for year, month in month_keys],
		"negative": [negative[f"{year:04d}-{month:02d}"] for year, month in month_keys],
	}


def _build_insights(approved_feedback, pending_count: int, aspect_scores):
	negative_count = len([row for row in approved_feedback if row.sentiment == "negative"])
	low_aspect = min(aspect_scores.items(), key=lambda item: item[1]) if aspect_scores else None

	insights = []
	if low_aspect and low_aspect[1] < 3.6:
		insights.append(
			{
				"icon": "triangle-alert",
				"title": f"{low_aspect[0]} score is currently {low_aspect[1]:.1f}/5.",
				"detail": "Consider targeted interventions and more examples in the next cycle.",
			}
		)
	if negative_count > 0:
		insights.append(
			{
				"icon": "siren",
				"title": f"{negative_count} approved entries still carry negative sentiment.",
				"detail": "Track these topics and communicate remediation progress to students.",
			}
		)
	if pending_count > 0:
		insights.append(
			{
				"icon": "list-todo",
				"title": f"{pending_count} checklist interventions are pending completion.",
				"detail": "Review deadlines this week to keep improvement plans on track.",
			}
		)

	if not insights and not approved_feedback:
		insights.append(
			{
				"icon": "info",
				"title": "No approved feedback available yet.",
				"detail": "Insights will appear automatically after approved reviews are available.",
			}
		)

	if not insights:
		insights.append(
			{
				"icon": "sparkles",
				"title": "Current cycle looks healthy.",
				"detail": "Keep monitoring monthly trends and continue sharing resources.",
			}
		)

	return insights


def _reason_label(raw_reason: str):
	value = (raw_reason or "Other").strip()
	if not value:
		value = "Other"
	return value.replace("_", " ").title()


def _month_index(year: int, month: int):
	return (year * 12) + (month - 1)


def _month_tuple(index: int):
	return (index // 12, (index % 12) + 1)


def _month_keys_between(start_year: int, start_month: int, end_year: int, end_month: int):
	start_idx = _month_index(start_year, start_month)
	end_idx = _month_index(end_year, end_month)
	if end_idx < start_idx:
		return []
	return [_month_tuple(idx) for idx in range(start_idx, end_idx + 1)]


def _parse_month_input(raw_value: str):
	text = (raw_value or "").strip()
	if not text:
		return None
	try:
		parsed = datetime.strptime(text, "%Y-%m")
		return parsed.year, parsed.month
	except ValueError:
		return None


def _format_month_input(year: int, month: int):
	return f"{year:04d}-{month:02d}"


def _resolve_trend_month_window(start_raw: str, end_raw: str, minimum_months: int = 6, default_months: int = 6):
	now = datetime.utcnow()
	default_end = (now.year, now.month)
	default_end_idx = _month_index(default_end[0], default_end[1])
	default_start_idx = default_end_idx - max(default_months - 1, 0)
	default_start = _month_tuple(default_start_idx)

	parsed_start = _parse_month_input(start_raw)
	parsed_end = _parse_month_input(end_raw)

	error = None
	if parsed_start and parsed_end:
		start_idx = _month_index(parsed_start[0], parsed_start[1])
		end_idx = _month_index(parsed_end[0], parsed_end[1])
		span = (end_idx - start_idx) + 1
		if end_idx < start_idx:
			error = "End month must be greater than or equal to start month. Showing default trend range."
		elif span < minimum_months:
			error = f"Minimum trend range is {minimum_months} months. Showing default trend range."
		else:
			keys = _month_keys_between(parsed_start[0], parsed_start[1], parsed_end[0], parsed_end[1])
			return {
				"month_keys": keys,
				"start_value": _format_month_input(parsed_start[0], parsed_start[1]),
				"end_value": _format_month_input(parsed_end[0], parsed_end[1]),
				"error": None,
			}
	elif start_raw or end_raw:
		error = "Please provide both start and end month in YYYY-MM format. Showing default trend range."

	keys = _month_keys_between(default_start[0], default_start[1], default_end[0], default_end[1])
	return {
		"month_keys": keys,
		"start_value": _format_month_input(default_start[0], default_start[1]),
		"end_value": _format_month_input(default_end[0], default_end[1]),
		"error": error,
	}


@faculty_bp.route("/dashboard")
@login_required
@role_required("faculty")
def dashboard():
	faculty_user = User.query.get(session["user_id"])
	selected_subject_semester = (request.args.get("subject_semester", "all") or "all").strip()
	selected_subject_section = _normalize_section(request.args.get("subject_section", "all")) or "all"
	trend_start = (request.args.get("trend_start") or "").strip()
	trend_end = (request.args.get("trend_end") or "").strip()

	approved_feedback = (
		Feedback.query.filter_by(faculty_id=faculty_user.id, status="approved")
		.order_by(Feedback.created_at.desc())
		.all()
	)

	today = date.today()
	faculty_subject_rows = (
		FacultyAssignment.query.join(SubjectOffering, FacultyAssignment.subject_offering_id == SubjectOffering.id)
		.filter(
			FacultyAssignment.faculty_user_id == faculty_user.id,
			FacultyAssignment.is_active.is_(True),
			or_(FacultyAssignment.effective_from.is_(None), FacultyAssignment.effective_from <= today),
			or_(FacultyAssignment.effective_to.is_(None), FacultyAssignment.effective_to >= today),
			SubjectOffering.is_active.is_(True),
		)
		.order_by(
			SubjectOffering.course_code.asc(),
			SubjectOffering.semester_no.asc(),
			SubjectOffering.section.asc(),
			SubjectOffering.subject_code.asc(),
		)
		.all()
	)
	assigned_offerings = []
	seen_offerings = set()
	for assignment in faculty_subject_rows:
		offering = assignment.subject_offering
		if not offering or offering.id in seen_offerings:
			continue
		seen_offerings.add(offering.id)
		assigned_offerings.append(offering)

	assigned_courses = sorted(
		{
			(offering.course_code or "").strip().upper()
			for offering in assigned_offerings
			if (offering.course_code or "").strip()
		}
	)
	if len(assigned_courses) == 1:
		assigned_course_display = assigned_courses[0]
	elif assigned_courses:
		assigned_course_display = ", ".join(assigned_courses)
	else:
		assigned_course_display = "Not Assigned"

	subject_semester_options = sorted(
		{
			str(offering.semester_no)
			for offering in assigned_offerings
			if offering.semester_no is not None
		},
		key=lambda item: int(item) if item.isdigit() else item,
	)
	subject_section_options = sorted(
		{
			(offering.section or "").strip().upper()
			for offering in assigned_offerings
			if (offering.section or "").strip()
		}
	)

	if selected_subject_semester != "all" and selected_subject_semester not in subject_semester_options:
		selected_subject_semester = "all"
	if selected_subject_section != "all" and selected_subject_section not in subject_section_options:
		selected_subject_section = "all"

	filtered_teaching_offerings = assigned_offerings
	if selected_subject_semester != "all":
		filtered_teaching_offerings = [
			offering for offering in filtered_teaching_offerings if str(offering.semester_no) == selected_subject_semester
		]
	if selected_subject_section != "all":
		filtered_teaching_offerings = [
			offering
			for offering in filtered_teaching_offerings
			if (offering.section or "").strip().upper() == selected_subject_section
		]

	def _feedback_course(row):
		if row.student and row.student.student_profile and row.student.student_profile.course_code:
			return _normalize_course_code(row.student.student_profile.course_code)
		return _normalize_course_code(row.student.course if row.student else "")

	def _feedback_semester(row):
		return _extract_semester_token(row.semester)

	def _feedback_section(row):
		return _normalize_section(row.student.section if row.student else "")

	subject_filtered_feedback = approved_feedback
	if assigned_courses:
		subject_filtered_feedback = [row for row in subject_filtered_feedback if _feedback_course(row) in assigned_courses]
	if selected_subject_semester != "all":
		subject_filtered_feedback = [
			row for row in subject_filtered_feedback if _feedback_semester(row) == selected_subject_semester
		]
	if selected_subject_section != "all":
		subject_filtered_feedback = [
			row for row in subject_filtered_feedback if _feedback_section(row) == selected_subject_section
		]

	faculty_subjects = []
	seen_subjects = set()
	for offering in assigned_offerings:
		subject_name = (offering.subject_name or "").strip()
		if not subject_name or subject_name in seen_subjects:
			continue
		faculty_subjects.append(subject_name)
		seen_subjects.add(subject_name)

	if not faculty_subjects:
		for row in approved_feedback:
			subject_name = (row.subject or "").strip()
			if not subject_name or subject_name in seen_subjects:
				continue
			faculty_subjects.append(subject_name)
			seen_subjects.add(subject_name)

	if not faculty_subjects:
		faculty_subject_summary = "No assigned subjects yet"
	elif len(faculty_subjects) <= 2:
		faculty_subject_summary = " & ".join(faculty_subjects)
	else:
		faculty_subject_summary = f"{faculty_subjects[0]}, {faculty_subjects[1]} +{len(faculty_subjects) - 2} more"

	checklists = (
		Checklist.query.filter_by(faculty_id=faculty_user.id)
		.order_by(Checklist.created_at.desc())
		.all()
	)
	intervention_posts = (
		KnowledgePost.query.filter_by(author_id=faculty_user.id)
		.order_by(KnowledgePost.created_at.desc())
		.all()
	)
	published_interventions = [post for post in intervention_posts if post.status == "published"]
	recent_resources = published_interventions[:5]
	trend_window = _resolve_trend_month_window(trend_start, trend_end, minimum_months=6, default_months=6)
	month_keys = trend_window["month_keys"]
	month_labels = [datetime(year=year, month=month, day=1).strftime("%b") for year, month in month_keys]
	month_data = _month_sentiment(approved_feedback, month_keys)
	aspect_scores = _aspect_scores(approved_feedback)
	subject_data = _subject_sentiment(subject_filtered_feedback, limit=5)

	approved_count = len(approved_feedback)
	group_states = {}
	for item in checklists:
		details = _parse_checklist_description(item.description or "")
		group_id = details["group_id"] or f"legacy-{item.id}"
		state = _checklist_tasks_state(item.title, details, item.is_completed)["state"]
		if state == "complete":
			group_states.setdefault(group_id, "complete")
		elif state == "partial":
			group_states[group_id] = "partial"
		else:
			if group_states.get(group_id) != "partial":
				group_states[group_id] = "null"

	pending_count = len([state for state in group_states.values() if state != "complete"])
	active_checklists = len(group_states)
	interventions_logged = sum(len(_targeted_students_for_post(post)) for post in published_interventions)
	avg_aspect_score = round(sum(aspect_scores.values()) / len(aspect_scores), 1) if aspect_scores else None

	dashboard_payload = {
		"trend_labels": month_labels,
		"trend_positive": month_data["positive"],
		"trend_neutral": month_data["neutral"],
		"trend_negative": month_data["negative"],
		"subject_labels": subject_data["labels"],
		"subject_positive": subject_data["positive"],
		"subject_neutral": subject_data["neutral"],
		"subject_negative": subject_data["negative"],
		"aspect_labels": list(aspect_scores.keys()),
		"aspect_values": list(aspect_scores.values()),
	}
	dashboard_has_trend_data = any(month_data["positive"]) or any(month_data["neutral"]) or any(month_data["negative"])
	dashboard_has_aspect_data = bool(aspect_scores)
	dashboard_has_subject_data = bool(subject_data["labels"])

	insights = _build_insights(approved_feedback, pending_count, aspect_scores)

	return render_template(
		"dashboard_faculty.html",
		faculty=faculty_user,
		faculty_subject_summary=faculty_subject_summary,
		trend_start=trend_window["start_value"],
		trend_end=trend_window["end_value"],
		trend_filter_error=trend_window["error"],
		dashboard_has_trend_data=dashboard_has_trend_data,
		dashboard_has_aspect_data=dashboard_has_aspect_data,
		dashboard_has_subject_data=dashboard_has_subject_data,
		assigned_course_display=assigned_course_display,
		subject_semester_options=subject_semester_options,
		subject_section_options=subject_section_options,
		selected_subject_semester=selected_subject_semester,
		selected_subject_section=selected_subject_section,
		subject_filtered_count=len(subject_filtered_feedback),
		filtered_teaching_offerings=filtered_teaching_offerings,
		total_teaching_offerings=len(assigned_offerings),
		approved_feedback=approved_feedback,
		checklists=checklists,
		recent_resources=recent_resources,
		total_resources=len(published_interventions),
		completed_count=len([item for item in checklists if item.is_completed]),
		pending_count=pending_count,
		approved_count=approved_count,
		active_checklists=active_checklists,
		interventions_logged=interventions_logged,
		avg_aspect_score=avg_aspect_score,
		aspect_scores=aspect_scores,
		insights=insights,
		dashboard_payload=dashboard_payload,
	)


@faculty_bp.route("/resources/board")
@login_required
@role_required("faculty")
def resource_board():
	search = request.args.get("q", "").strip()
	sort_by = request.args.get("sort", "most_upvoted").strip().lower()
	date_from_value = request.args.get("date_from", "").strip()
	date_to_value = request.args.get("date_to", "").strip()
	date_from = _parse_iso_date(date_from_value)
	date_to = _parse_iso_date(date_to_value)
	if sort_by not in {"most_upvoted", "oldest", "recent"}:
		sort_by = "most_upvoted"

	posts = (
		KnowledgePost.query.join(User, KnowledgePost.author_id == User.id)
		.filter(User.role == "faculty", KnowledgePost.status == "published")
		.order_by(KnowledgePost.created_at.desc())
		.all()
	)
	post_ids = [post.id for post in posts]
	like_counts, bookmark_counts, view_counts = _intervention_reaction_counts(post_ids)

	board_cards = []
	for post in posts:
		tags = []
		tag_source = " ".join(
			[
				post.title or "",
				post.content or "",
				post.problem_context or "",
				post.solution_steps or "",
				post.resource_references or "",
				post.outcome_result or "",
			]
		)
		for token in tag_source.lower().split():
			clean = "".join(ch for ch in token if ch.isalnum() or ch == "-")
			if len(clean) >= 4:
				tags.append(clean)
		tags = sorted(set(tags))[:8]

		likes = like_counts.get(post.id, 0)
		bookmarks = bookmark_counts.get(post.id, 0)
		opened = view_counts.get(post.id, 0)
		reach_count = len(_targeted_students_for_post(post))
		board_cards.append(
			{
				"post": post,
				"tags": tags,
				"likes": likes,
				"bookmarks": bookmarks,
				"opened_count": opened,
				"reach_count": reach_count,
				"attachments": post.attachments.order_by(KnowledgeAttachment.created_at.desc()).all(),
				"target_summary": _target_summary(post),
				"rank": (likes * 2) + bookmarks + reach_count,
			}
		)

	if search:
		needle = search.lower()
		board_cards = [
			item
			for item in board_cards
			if needle in item["post"].title.lower()
			or needle in (item["post"].author.full_name or "").lower()
		]

	if date_from:
		board_cards = [
			item
			for item in board_cards
			if item["post"].created_at and item["post"].created_at.date() >= date_from
		]

	if date_to:
		board_cards = [
			item
			for item in board_cards
			if item["post"].created_at and item["post"].created_at.date() <= date_to
		]

	if sort_by == "recent":
		board_cards.sort(key=lambda item: item["post"].created_at, reverse=True)
	elif sort_by == "oldest":
		board_cards.sort(key=lambda item: item["post"].created_at)
	else:
		board_cards.sort(key=lambda item: item["rank"], reverse=True)

	return render_template(
		"knowledge_board.html",
		board_cards=board_cards,
		search=search,
		sort_by=sort_by,
		date_from_value=date_from_value if date_from else "",
		date_to_value=date_to_value if date_to else "",
		board_filter_endpoint="faculty.resource_board",
		board_page_title="Interventions - Resource Board",
		board_heading="Faculty Resource Interventions",
		board_subtitle="Publish targeted intervention resources for students and track engagement.",
		board_breadcrumb_primary="Faculty",
		board_breadcrumb_secondary="Resource Board",
		my_posts_url=url_for("faculty.my_resources"),
		my_posts_label="My Resources",
		create_post_url=url_for("faculty.resource_post"),
		create_post_label="Share Resource",
		empty_message="No faculty resources available yet.",
		show_reaction_actions=False,
		detail_endpoint="faculty.resource_post_detail",
		metrics_endpoint="faculty.resource_metrics",
		notification_items=[],
		enable_compose_modal=False,
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
	post_ids = [post.id for post in posts]
	like_counts, bookmark_counts, view_counts = _intervention_reaction_counts(post_ids)
	for post in posts:
		post.like_count = like_counts.get(post.id, 0)
		post.bookmark_count = bookmark_counts.get(post.id, 0)
		post.opened_count = view_counts.get(post.id, 0)
		post.reach_count = len(_targeted_students_for_post(post)) if post.status == "published" else 0
		post.edit_window_open = post.status != "published" or _post_is_within_edit_window(post)

	return render_template(
		"student_my_posts.html",
		posts=posts,
		page_title="My Intervention Resources",
		heading="My Intervention Resources",
		board_url=url_for("faculty.resource_board"),
		board_label="Intervention Resource Hub",
		create_url=url_for("faculty.resource_post"),
		create_label="Share Resource",
		empty_message="You have not shared any resources yet.",
		item_label="resource",
		edit_endpoint="faculty.edit_resource_post",
		delete_endpoint="faculty.delete_resource_post",
	)


@faculty_bp.route("/resource-post/<int:post_id>/detail")
@login_required
@role_required("faculty")
def resource_post_detail(post_id: int):
	post = _faculty_visible_post(post_id)
	if not post:
		return jsonify({"error": "not_found"}), 404

	likes, bookmarks, opened = _intervention_reaction_counts([post.id])
	attachments = post.attachments.order_by(KnowledgeAttachment.created_at.desc()).all()

	payload = {
		"id": post.id,
		"title": post.title,
		"content": post.content,
		"problem_context": post.problem_context or "",
		"solution_steps": post.solution_steps or "",
		"resource_references": post.resource_references or "",
		"outcome_result": post.outcome_result or "",
		"resource_links": [line.strip() for line in (post.resource_links or "").splitlines() if line.strip()],
		"status": post.status,
		"author": post.author.full_name if post.author else "Faculty",
		"created_at_ist": _utc_to_ist(post.created_at).strftime("%d %b %Y %I:%M %p") if post.created_at else "-",
		"target": _target_summary(post),
		"metrics": {
			"likes": int(likes.get(post.id, 0)),
			"saved": int(bookmarks.get(post.id, 0)),
			"opened": int(opened.get(post.id, 0)),
			"reach": len(_targeted_students_for_post(post)) if post.status == "published" else 0,
		},
		"attachments": [
			{
				"name": attachment.file_name,
				"url": url_for("static", filename=attachment.file_path),
			}
			for attachment in attachments
		],
	}
	return jsonify(payload)


@faculty_bp.route("/resources/metrics")
@login_required
@role_required("faculty")
def resource_metrics():
	raw_ids = request.args.getlist("post_id")
	post_ids = []
	for raw in raw_ids:
		try:
			post_ids.append(int(raw))
		except (TypeError, ValueError):
			continue
	post_ids = sorted(set(post_ids))[:80]
	if not post_ids:
		return jsonify({"items": {}})

	visible_posts = (
		KnowledgePost.query.join(User, KnowledgePost.author_id == User.id)
		.filter(KnowledgePost.id.in_(post_ids), User.role == "faculty")
		.all()
	)
	allowed_posts = []
	for post in visible_posts:
		if post.status == "published" or post.author_id == session.get("user_id"):
			allowed_posts.append(post)

	allowed_ids = [post.id for post in allowed_posts]
	likes, bookmarks, opened = _intervention_reaction_counts(allowed_ids)

	items = {}
	for post in allowed_posts:
		items[str(post.id)] = {
			"likes": int(likes.get(post.id, 0)),
			"saved": int(bookmarks.get(post.id, 0)),
			"opened": int(opened.get(post.id, 0)),
			"reach": len(_targeted_students_for_post(post)) if post.status == "published" else 0,
		}

	return jsonify({"items": items})


@faculty_bp.route("/resource-post", methods=["GET", "POST"])
@login_required
@role_required("faculty")
def resource_post():
	course_options = ["MCA", "BCA"]
	semester_options_by_course = _intervention_semester_options()
	section_options = _intervention_section_options()

	def _render_create(default_values=None):
		defaults = default_values or {}
		return render_template(
			"knowledge_post.html",
			page_title="Share Intervention Resource",
			submit_label="Publish Resource",
			draft_label="Save Draft",
			course_options=course_options,
			semester_options_by_course=semester_options_by_course,
			section_options=section_options,
			form_values=defaults,
			is_edit=False,
		)

	if request.method == "POST":
		payload, error = _intervention_form_payload(request.form)
		if error:
			flash(error, "danger")
			return _render_create(_intervention_form_values(request.form))

		post = KnowledgePost(
			title=payload["title"],
			content=payload["content"],
			problem_context=payload["problem_context"],
			solution_steps=payload["solution_steps"],
			resource_references=payload["resource_references"],
			outcome_result=payload["outcome_result"],
			resource_links=payload["resource_links"],
			status=payload["status"],
			target_courses=payload["target_course"],
			target_semesters=payload["target_semester"],
			target_sections=payload["target_section"],
			author_id=session["user_id"],
			published_at=datetime.utcnow() if payload["status"] == "published" else None,
		)

		db.session.add(post)
		db.session.flush()
		attachments, attach_error = _save_intervention_attachments(request.files.getlist("attachments"), post.id)
		if attach_error:
			db.session.rollback()
			flash(attach_error, "danger")
			return _render_create(_intervention_form_values(request.form))
		for attachment in attachments or []:
			db.session.add(attachment)
		db.session.commit()

		if post.status == "draft":
			flash("Intervention draft saved. Only you can see drafts.", "success")
		else:
			flash("Intervention published for targeted students.", "success")
		return redirect(url_for("faculty.my_resources"))

	return _render_create(
		{
			"target_course": "MCA",
			"target_semester": "all",
			"target_section": "ALL",
			"post_status": "published",
		}
	)


@faculty_bp.route("/resource-post/<int:post_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("faculty")
def edit_resource_post(post_id: int):
	post = KnowledgePost.query.filter_by(id=post_id, author_id=session["user_id"]).first()
	if not post:
		flash("Resource post not found.", "danger")
		return redirect(url_for("faculty.my_resources"))

	course_options = ["MCA", "BCA"]
	semester_options_by_course = _intervention_semester_options()
	section_options = _intervention_section_options()
	edit_window_open = post.status != "published" or _post_is_within_edit_window(post)

	def _render_edit(default_values=None):
		values = default_values or {}
		if not default_values:
			course_values = _parse_csv_values(post.target_courses, uppercase=True)
			semester_values = _parse_csv_values(post.target_semesters)
			section_values = _parse_csv_values(post.target_sections, uppercase=True)
			values = {
				"title": post.title,
				"content": post.content,
				"problem_context": post.problem_context or "",
				"solution_steps": post.solution_steps or "",
				"resource_references": post.resource_references or "",
				"outcome_result": post.outcome_result or "",
				"resource_links": post.resource_links or "",
				"target_course": course_values[0] if course_values else "MCA",
				"target_semester": semester_values[0] if semester_values else "all",
				"target_section": section_values[0] if section_values else "ALL",
				"post_status": post.status,
			}
		return render_template(
			"knowledge_post.html",
			page_title="Edit Intervention Resource",
			submit_label="Update Resource",
			draft_label="Save Draft",
			course_options=course_options,
			semester_options_by_course=semester_options_by_course,
			section_options=section_options,
			form_values=values,
			is_edit=True,
			post=post,
			existing_attachments=post.attachments.order_by(KnowledgeAttachment.created_at.desc()).all(),
			edit_window_open=edit_window_open,
		)

	if post.status == "published" and not edit_window_open:
		flash("Published interventions can only be edited within 24 hours.", "warning")
		return redirect(url_for("faculty.my_resources"))

	if request.method == "POST":
		if post.status == "published" and not _post_is_within_edit_window(post):
			flash("Published interventions can only be edited within 24 hours.", "danger")
			return redirect(url_for("faculty.my_resources"))

		payload, error = _intervention_form_payload(request.form)
		if error:
			flash(error, "danger")
			return _render_edit(_intervention_form_values(request.form))

		was_published = post.status == "published"
		new_status = payload["status"] if post.status != "published" else "published"

		post.title = payload["title"]
		post.content = payload["content"]
		post.problem_context = payload["problem_context"]
		post.solution_steps = payload["solution_steps"]
		post.resource_references = payload["resource_references"]
		post.outcome_result = payload["outcome_result"]
		post.resource_links = payload["resource_links"]
		post.status = new_status
		post.target_courses = payload["target_course"]
		post.target_semesters = payload["target_semester"]
		post.target_sections = payload["target_section"]
		post.revision_count = int(post.revision_count or 0) + 1
		if post.status == "published" and not post.published_at:
			post.published_at = datetime.utcnow()

		remove_ids = set()
		for raw_id in request.form.getlist("remove_attachment_ids"):
			try:
				remove_ids.add(int(raw_id))
			except (TypeError, ValueError):
				continue
		if remove_ids:
			remove_rows = KnowledgeAttachment.query.filter(
				KnowledgeAttachment.post_id == post.id,
				KnowledgeAttachment.id.in_(remove_ids),
			).all()
			for row in remove_rows:
				_delete_attachment_file(row)
				db.session.delete(row)

		new_attachments, attach_error = _save_intervention_attachments(request.files.getlist("attachments"), post.id)
		if attach_error:
			db.session.rollback()
			flash(attach_error, "danger")
			return _render_edit(_intervention_form_values(request.form))
		for attachment in new_attachments or []:
			db.session.add(attachment)

		if was_published and post.status == "published":
			target_students = _targeted_students_for_post(post)
			notified_count = _queue_intervention_update_notifications(post, target_students)
		else:
			notified_count = 0

		db.session.commit()
		if was_published and post.status == "published":
			flash(f"Intervention updated. Notified {notified_count} engaged student(s).", "success")
		elif post.status == "draft":
			flash("Draft updated successfully.", "success")
		else:
			flash("Draft published for targeted students.", "success")
		return redirect(url_for("faculty.my_resources"))

	return _render_edit()


@faculty_bp.route("/resource-post/<int:post_id>/delete", methods=["POST"])
@login_required
@role_required("faculty")
def delete_resource_post(post_id: int):
	post = KnowledgePost.query.filter_by(id=post_id, author_id=session["user_id"]).first()
	if not post:
		flash("Resource post not found.", "danger")
		return redirect(url_for("faculty.my_resources"))
	attachments = post.attachments.all()
	for attachment in attachments:
		_delete_attachment_file(attachment)

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
	subject = request.args.get("subject", "all").strip()
	semester = request.args.get("semester", "all").strip()
	section = _normalize_section(request.args.get("section", "all")) or "all"
	reason = request.args.get("reason", "all").strip()
	trend_start = (request.args.get("trend_start") or "").strip()
	trend_end = (request.args.get("trend_end") or "").strip()
	active_view = request.args.get("view", "charts").strip().lower()
	if active_view not in {"charts", "entries"}:
		active_view = "charts"

	base_query = Feedback.query.filter_by(faculty_id=session["user_id"], status="approved")
	base_rows = base_query.order_by(Feedback.created_at.desc()).all()
	section_options = sorted(
		{
			_normalize_section(row.student.section if row.student else "")
			for row in base_rows
			if _normalize_section(row.student.section if row.student else "")
		}
	)
	if section != "all" and section not in section_options:
		section = "all"
	query = base_query.order_by(Feedback.created_at.desc())

	if search:
		like = f"%{search}%"
		query = query.filter(
			or_(
				Feedback.feedback_text.ilike(like),
				Feedback.course_code.ilike(like),
				Feedback.subject.ilike(like),
				Feedback.semester.ilike(like),
				Feedback.reason.ilike(like),
				Feedback.feedback_tags.ilike(like),
			)
		)

	if sentiment in {"positive", "neutral", "negative"}:
		query = query.filter(Feedback.sentiment == sentiment)
	if subject != "all":
		query = query.filter(Feedback.subject == subject)
	if semester != "all":
		query = query.filter(Feedback.semester == semester)
	if section != "all":
		query = query.join(User, Feedback.student_id == User.id).filter(User.section == section)
	if reason != "all":
		query = query.filter(Feedback.reason == reason)

	trend_window = _resolve_trend_month_window(trend_start, trend_end, minimum_months=6, default_months=6)
	trend_start_tuple = _parse_month_input(trend_window["start_value"])
	trend_end_tuple = _parse_month_input(trend_window["end_value"])
	if trend_start_tuple and trend_end_tuple:
		start_dt = datetime(trend_start_tuple[0], trend_start_tuple[1], 1)
		next_idx = _month_index(trend_end_tuple[0], trend_end_tuple[1]) + 1
		next_year, next_month = _month_tuple(next_idx)
		end_dt = datetime(next_year, next_month, 1)
		query = query.filter(Feedback.created_at >= start_dt, Feedback.created_at < end_dt)

	reviews = query.all()
	kpi = {
		"total": len(reviews),
		"positive": len([row for row in reviews if row.sentiment == "positive"]),
		"neutral": len([row for row in reviews if row.sentiment == "neutral"]),
		"negative": len([row for row in reviews if row.sentiment == "negative"]),
	}
	total_for_split = max(kpi["total"], 1)
	reason_counts = {}
	for review in reviews:
		reason_key = (review.reason or "Other").strip() or "Other"
		reason_counts[reason_key] = reason_counts.get(reason_key, 0) + 1

	sorted_reasons = sorted(reason_counts.items(), key=lambda item: item[1], reverse=True)[:6]

	month_keys = trend_window["month_keys"]
	month_labels = [datetime(year=year, month=month, day=1).strftime("%b") for year, month in month_keys]
	month_data = _month_sentiment(reviews, month_keys)
	aspect_scores = _aspect_scores(reviews)
	subject_data = _subject_sentiment(reviews, limit=5)

	aspect_order = ["Clarity", "Pace", "Assessment", "Engagement"]
	score_summary = []
	for label in aspect_order:
		value = aspect_scores.get(label)
		if value is None:
			score_summary.append({"label": label, "value": None, "trend": "na"})
			continue
		trend = "up" if value >= 3.8 else "down" if value < 3.4 else "steady"
		score_summary.append({"label": label, "value": value, "trend": trend})

	chart_payload = {
		"sentiment_labels": ["Positive", "Neutral", "Negative"],
		"sentiment_values": [kpi["positive"], kpi["neutral"], kpi["negative"]],
		"reason_labels": [_reason_label(item[0]) for item in sorted_reasons],
		"reason_values": [item[1] for item in sorted_reasons],
		"trend_labels": month_labels,
		"trend_positive": month_data["positive"],
		"trend_neutral": month_data["neutral"],
		"trend_negative": month_data["negative"],
		"subject_labels": subject_data["labels"],
		"subject_positive": subject_data["positive"],
		"subject_neutral": subject_data["neutral"],
		"subject_negative": subject_data["negative"],
		"aspect_labels": list(aspect_scores.keys()),
		"aspect_values": list(aspect_scores.values()),
		"split_labels": [
			f"Positive {round((kpi['positive'] / total_for_split) * 100)}%",
			f"Neutral {round((kpi['neutral'] / total_for_split) * 100)}%",
			f"Negative {round((kpi['negative'] / total_for_split) * 100)}%",
		],
		"has_trend_data": any(month_data["positive"]) or any(month_data["neutral"]) or any(month_data["negative"]),
		"has_subject_data": bool(subject_data["labels"]),
		"has_aspect_data": bool(aspect_scores),
		"has_split_data": kpi["total"] > 0,
	}

	subject_options = sorted({(row.subject or "").strip() for row in base_rows if (row.subject or "").strip()})
	semester_options = sorted({(row.semester or "").strip() for row in base_rows if (row.semester or "").strip()})
	reason_options = sorted({(row.reason or "").strip() for row in base_rows if (row.reason or "").strip()})

	return render_template(
		"faculty_reviews.html",
		reviews=reviews,
		search=search,
		sentiment=sentiment,
		selected_subject=subject,
		selected_semester=semester,
		selected_section=section,
		selected_reason=reason,
		trend_start=trend_window["start_value"],
		trend_end=trend_window["end_value"],
		trend_filter_error=trend_window["error"],
		subject_options=subject_options,
		semester_options=semester_options,
		section_options=section_options,
		reason_options=reason_options,
		active_view=active_view,
		kpi=kpi,
		score_summary=score_summary,
		chart_payload=chart_payload,
	)


@faculty_bp.route("/checklists")
@login_required
@role_required("faculty")
def checklists_page():
	status = request.args.get("status", "all").strip().lower()
	if status not in {"all", "complete", "partial", "null"}:
		status = "all"

	selected_course = (request.args.get("course") or "all").strip().upper()
	if selected_course not in {"ALL", "MCA", "BCA", "BOTH"}:
		selected_course = "ALL"
	selected_category = (request.args.get("category") or "all").strip()
	category_options = _checklist_categories()
	valid_categories = set(category_options)
	if selected_category != "all" and selected_category not in valid_categories:
		selected_category = "all"
	selected_semester = _normalize_target_semester(request.args.get("semester", "all"))
	selected_section_raw = (request.args.get("section") or "all").strip()
	selected_section = _normalize_target_section(selected_section_raw)

	all_rows = Checklist.query.filter_by(faculty_id=session["user_id"]).order_by(Checklist.created_at.desc()).all()
	grouped = {}
	for row in all_rows:
		details = _parse_checklist_description(row.description or "")
		state = _checklist_tasks_state(row.title, details, row.is_completed)
		group_id = details["group_id"] or f"legacy-{row.id}"

		if group_id not in grouped:
			grouped[group_id] = {
				"id": row.id,
				"group_id": group_id,
				"title": row.title,
				"description": details["description"],
				"subject": details["subject"],
				"category": details["category"],
				"priority": details["priority"],
				"target_course": details["target_course"],
				"target_semester": details["target_semester"],
				"target_section": details["target_section"],
				"due_date": details["due_date"],
				"completion_locked": bool(details.get("completion_locked", False)),
				"attachment": details["attachment"],
				"created_at": row.created_at,
				"students": 0,
				"complete_count": 0,
				"partial_count": 0,
				"null_count": 0,
				"total_tasks": max(state["total"], 1),
				"completed_tasks": 0,
			}

		bucket = grouped[group_id]
		bucket["completion_locked"] = bool(bucket["completion_locked"] and details.get("completion_locked", False))
		bucket["students"] += 1
		bucket["total_tasks"] = max(bucket["total_tasks"], state["total"])
		bucket["completed_tasks"] += state["completed"]
		if state["state"] == "complete":
			bucket["complete_count"] += 1
		elif state["state"] == "partial":
			bucket["partial_count"] += 1
		else:
			bucket["null_count"] += 1

	checklists = []
	for item in grouped.values():
		if item["students"] and item["complete_count"] == item["students"]:
			aggregate_state = "complete"
		elif item["partial_count"] > 0 or item["complete_count"] > 0:
			aggregate_state = "partial"
		else:
			aggregate_state = "null"

		due_state = _due_state(item["due_date"], aggregate_state == "complete")
		item["aggregate_state"] = aggregate_state
		item["due_state"] = due_state
		item["due_label"] = _due_label(item["due_date"], due_state)
		denom = max(item["students"], 1)
		item["complete_ratio"] = int(round((item["complete_count"] / denom) * 100))
		item["partial_ratio"] = int(round((item["partial_count"] / denom) * 100))
		item["null_ratio"] = int(round((item["null_count"] / denom) * 100))
		task_denom = max(item["students"] * max(item["total_tasks"], 1), 1)
		item["task_progress_percent"] = int(round((item["completed_tasks"] / task_denom) * 100))
		checklists.append(item)

	if selected_course != "ALL":
		if selected_course == "BOTH":
			checklists = [item for item in checklists if item["target_course"] == "BOTH"]
		else:
			checklists = [
				item
				for item in checklists
				if item["target_course"] in {selected_course, "BOTH"}
			]

	if selected_semester != "all":
		checklists = [
			item
			for item in checklists
			if item["target_semester"] in {"all", selected_semester}
		]

	if selected_section != "all":
		checklists = [
			item
			for item in checklists
			if item["target_section"] in {"all", selected_section}
		]

	if selected_category != "all":
		checklists = [item for item in checklists if item["category"] == selected_category]

	if status != "all":
		checklists = [item for item in checklists if item["aggregate_state"] == status]

	checklists.sort(key=lambda item: item["created_at"], reverse=True)
	checklist_totals = {
		"total": len(checklists),
		"complete": len([item for item in checklists if item["aggregate_state"] == "complete"]),
		"partial": len([item for item in checklists if item["aggregate_state"] == "partial"]),
		"null": len([item for item in checklists if item["aggregate_state"] == "null"]),
		"students": sum(item["students"] for item in checklists),
	}

	subject_catalog_by_course = _load_subject_catalog_by_course()
	active_students = User.query.filter_by(role="student", is_active=True).all()
	section_options = sorted(
		{
			_normalize_section(student.section)
			for student in active_students
			if _normalize_section(student.section)
		}
	)
	section_options_by_course = {"MCA": set(), "BCA": set(), "BOTH": set()}
	for student in active_students:
		section = _normalize_section(student.section)
		if not section:
			continue
		course = _student_course_code(student)
		if course in {"MCA", "BCA"}:
			section_options_by_course[course].add(section)
		section_options_by_course["BOTH"].add(section)
	for course in section_options_by_course:
		section_options_by_course[course] = sorted(section_options_by_course[course])
	semester_options_by_course = {
		"MCA": [str(value) for value in range(1, 5)],
		"BCA": [str(value) for value in range(1, 7)],
		"BOTH": [str(value) for value in range(1, 9)],
	}
	semester_options = [str(value) for value in range(1, 9)]

	return render_template(
		"faculty_checklists.html",
		checklists=checklists,
		status=status,
		selected_course=selected_course,
		selected_category=selected_category,
		selected_semester=selected_semester,
		selected_section=selected_section,
		checklist_totals=checklist_totals,
		subject_options=[],
		subject_catalog_by_course=subject_catalog_by_course,
		category_options=category_options,
		semester_options=semester_options,
		semester_options_by_course=semester_options_by_course,
		section_options=section_options,
		section_options_by_course=section_options_by_course,
	)


@faculty_bp.route("/checklist/create", methods=["POST"])
@login_required
@role_required("faculty")
def create_checklist():
	title = request.form.get("title", "").strip()
	description = request.form.get("description", "").strip()
	category = request.form.get("category", "General").strip()
	priority = _normalize_priority(request.form.get("priority", "medium"))
	due_date_raw = request.form.get("due_date", "").strip()
	target_course = _normalize_target_course(request.form.get("target_course", "BOTH"))
	target_semester = _normalize_target_semester(request.form.get("target_semester", "all"))
	target_section = _normalize_target_section(request.form.get("target_section", "all"))
	task_lines = _extract_task_lines_from_form(request.form)
	attachment_link = request.form.get("attachment_link", "").strip()
	redirect_target = request.form.get("redirect_target", "faculty.dashboard").strip()
	if redirect_target not in {"faculty.dashboard", "faculty.checklists_page"}:
		redirect_target = "faculty.dashboard"
	subject_catalog_by_course = _load_subject_catalog_by_course()
	subject, subject_error = _normalize_subject_for_course(
		request.form.get("subject", "No specific subject"),
		target_course,
		subject_catalog_by_course,
	)
	if subject_error:
		flash(subject_error, "danger")
		return redirect(url_for(redirect_target))

	due_date = _parse_iso_date(due_date_raw)
	if due_date is None:
		flash("Due date is required and must be in YYYY-MM-DD format.", "danger")
		return redirect(url_for(redirect_target))

	if not title:
		flash("Title is required.", "danger")
		return redirect(url_for(redirect_target))
	if len(task_lines) < 2:
		flash("Add at least 2 checklist items.", "danger")
		return redirect(url_for(redirect_target))
	if category not in _checklist_categories():
		category = "General"

	group_id = _make_checklist_group_id()
	attachment_meta = None
	file_payload = request.files.get("attachment_file")
	if file_payload and (file_payload.filename or "").strip():
		attachment_meta, file_error = _save_checklist_attachment(file_payload, group_id)
		if file_error:
			flash(file_error, "danger")
			return redirect(url_for(redirect_target))

	if attachment_link:
		if not attachment_link.lower().startswith(("http://", "https://")):
			flash("Attachment link must start with http:// or https://", "danger")
			return redirect(url_for(redirect_target))
		if not attachment_meta:
			attachment_meta = {"kind": "link", "url": attachment_link}
		else:
			attachment_meta["link"] = attachment_link

	students = User.query.filter_by(role="student", is_active=True).order_by(User.id.asc()).all()
	matched_students = []
	for student in students:
		course = _student_course_code(student)
		section = _normalize_section(student.section) or "A"
		if target_course != "BOTH" and course != target_course:
			continue
		if target_section != "all" and section != target_section:
			continue
		if target_semester != "all":
			current_sem = _student_current_semester(student)
			if str(current_sem or "") != target_semester:
				continue
		matched_students.append(student)

	if not matched_students:
		flash("No active students match the selected course/semester/section filters.", "danger")
		return redirect(url_for(redirect_target))

	for student in matched_students:
		checklist = Checklist(
			title=title,
			description=_serialize_checklist_description(
				description,
				subject,
				priority,
				due_date,
				category=category,
				target_course=target_course,
				target_semester=target_semester,
				target_section=target_section,
				tasks=task_lines,
				completed_tasks=[],
				completion_locked=False,
				group_id=group_id,
				attachment=attachment_meta,
			),
			faculty_id=session["user_id"],
			student_id=student.id,
		)
		db.session.add(checklist)
	db.session.commit()

	flash(f"Checklist published to {len(matched_students)} students.", "success")
	return redirect(url_for(redirect_target))


@faculty_bp.route("/checklist/<int:checklist_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("faculty")
def edit_checklist(checklist_id: int):
	checklist = Checklist.query.filter_by(id=checklist_id, faculty_id=session["user_id"]).first()
	if not checklist:
		flash("Checklist not found.", "danger")
		return redirect(url_for("faculty.checklists_page"))

	current_details = _parse_checklist_description(checklist.description or "")
	group_id = current_details["group_id"]
	all_faculty_rows = Checklist.query.filter_by(faculty_id=session["user_id"]).all()
	if group_id:
		group_rows = [
			row
			for row in all_faculty_rows
			if _parse_checklist_description(row.description or "")["group_id"] == group_id
		]
	else:
		group_rows = [checklist]
	if not group_rows:
		group_rows = [checklist]

	def _render_edit_form(override: dict | None = None):
		payload = {
			"subject": current_details["subject"],
			"category": current_details["category"],
			"priority": current_details["priority"],
			"due_date": current_details["due_date"].strftime("%Y-%m-%d") if current_details["due_date"] else "",
			"checklist_items": list(current_details.get("tasks", [])),
			"attachment": current_details.get("attachment"),
			"target_course": current_details.get("target_course", "BOTH"),
			"target_semester": current_details.get("target_semester", "all"),
			"target_section": current_details.get("target_section", "all"),
			"description": current_details["description"],
		}
		if override:
			payload.update(override)

		items = payload.get("checklist_items")
		if not isinstance(items, list):
			items = _normalize_task_lines(payload.get("task_items", ""))
		items = [item.strip() for item in items if str(item).strip()][:10]
		while len(items) < 2:
			items.append("")
		payload["checklist_items"] = items
		payload["task_items"] = "\n".join([item for item in items if item])
		payload["subject"] = (payload.get("subject") or "No specific subject").strip() or "No specific subject"
		payload["target_course"] = _normalize_target_course(payload.get("target_course", "BOTH"))

		subject_catalog_by_course = _load_subject_catalog_by_course()
		subject_options = _subject_options_for_course(subject_catalog_by_course, payload["target_course"])
		if payload["subject"] != "No specific subject" and payload["subject"] not in subject_options:
			subject_options = [payload["subject"], *subject_options]

		return render_template(
			"faculty_checklist_edit.html",
			checklist=checklist,
			checklist_details=payload,
			category_options=_checklist_categories(),
			subject_options=subject_options,
			subject_catalog_by_course=subject_catalog_by_course,
		)

	if request.method == "POST":
		title = request.form.get("title", "").strip()
		description = request.form.get("description", "").strip()
		category = request.form.get("category", current_details.get("category", "General")).strip()
		priority = _normalize_priority(request.form.get("priority", "medium"))
		due_date_raw = request.form.get("due_date", "").strip()
		task_lines = _extract_task_lines_from_form(request.form)
		attachment_link = request.form.get("attachment_link", "").strip()
		remove_attachment = request.form.get("remove_attachment") == "1"
		due_date = _parse_iso_date(due_date_raw)
		target_course = current_details.get("target_course", "BOTH")
		subject_catalog_by_course = _load_subject_catalog_by_course()
		subject, subject_error = _normalize_subject_for_course(
			request.form.get("subject", "No specific subject"),
			target_course,
			subject_catalog_by_course,
			legacy_subject=current_details.get("subject", ""),
		)

		render_payload = {
			"subject": request.form.get("subject", "No specific subject").strip() or "No specific subject",
			"category": category,
			"priority": priority,
			"due_date": due_date_raw,
			"checklist_items": task_lines,
			"attachment": current_details.get("attachment"),
			"target_course": target_course,
			"target_semester": current_details.get("target_semester", "all"),
			"target_section": current_details.get("target_section", "all"),
			"description": description,
		}

		if not title:
			flash("Title is required.", "danger")
			return _render_edit_form(render_payload)

		if subject_error:
			flash(subject_error, "danger")
			return _render_edit_form(render_payload)

		if len(task_lines) < 2:
			flash("Add at least 2 checklist items.", "danger")
			return _render_edit_form(render_payload)

		if due_date is None:
			flash("Due date is required and must be in valid YYYY-MM-DD format.", "danger")
			return _render_edit_form(render_payload)

		updated_attachment = None if remove_attachment else current_details.get("attachment")
		file_payload = request.files.get("attachment_file")
		if file_payload and (file_payload.filename or "").strip():
			attachment_group = group_id or _make_checklist_group_id()
			updated_attachment, file_error = _save_checklist_attachment(file_payload, attachment_group)
			if file_error:
				flash(file_error, "danger")
				return _render_edit_form(render_payload)

		if attachment_link:
			if not attachment_link.lower().startswith(("http://", "https://")):
				flash("Attachment link must start with http:// or https://", "danger")
				return _render_edit_form(render_payload)
			if not updated_attachment:
				updated_attachment = {"kind": "link", "url": attachment_link}
			else:
				updated_attachment["link"] = attachment_link

		stable_group_id = group_id or _make_checklist_group_id()
		if category not in _checklist_categories():
			category = "General"

		for row in group_rows:
			existing = _parse_checklist_description(row.description or "")
			existing_state = _checklist_tasks_state(row.title, existing, row.is_completed)
			new_completed = [idx for idx in existing_state["completed_indexes"] if idx < len(task_lines)]
			if row.is_completed and len(new_completed) < len(task_lines):
				new_completed = list(range(len(task_lines)))
			row.title = title
			row.is_completed = bool(task_lines) and len(new_completed) == len(task_lines)
			row.description = _serialize_checklist_description(
				description,
				subject,
				priority,
				due_date,
				category=category,
				target_course=existing.get("target_course", "BOTH"),
				target_semester=existing.get("target_semester", "all"),
				target_section=existing.get("target_section", "all"),
				tasks=task_lines,
				completed_tasks=new_completed,
				completion_locked=existing.get("completion_locked", False),
				group_id=stable_group_id,
				attachment=updated_attachment,
			)

		db.session.commit()
		flash(f"Checklist updated for {len(group_rows)} student assignment(s).", "success")
		return redirect(url_for("faculty.checklists_page"))

	return _render_edit_form()


@faculty_bp.route("/checklist/<int:checklist_id>/delete", methods=["POST"])
@login_required
@role_required("faculty")
def delete_checklist(checklist_id: int):
	checklist = Checklist.query.filter_by(id=checklist_id, faculty_id=session["user_id"]).first()
	if not checklist:
		flash("Checklist not found.", "danger")
		return redirect(url_for("faculty.checklists_page"))
	details = _parse_checklist_description(checklist.description or "")
	group_id = details["group_id"]
	if group_id:
		all_rows = Checklist.query.filter_by(faculty_id=session["user_id"]).all()
		delete_rows = [
			row
			for row in all_rows
			if _parse_checklist_description(row.description or "")["group_id"] == group_id
		]
	else:
		delete_rows = [checklist]

	for row in delete_rows:
		db.session.delete(row)
	db.session.commit()
	flash(f"Checklist deleted for {len(delete_rows)} student assignment(s).", "success")
	return redirect(url_for("faculty.checklists_page"))


@faculty_bp.route("/experiences")
@login_required
@role_required("faculty")
def experiences():
	from datetime import timezone as _tz, timedelta as _td

	IST_ZONE = _tz((_td(hours=5, minutes=30)))

	def _to_ist(dt):
		if dt is None:
			return dt
		if dt.tzinfo is None:
			dt = dt.replace(tzinfo=_tz.utc)
		return dt.astimezone(IST_ZONE)

	from sqlalchemy import or_ as _or
	from routes.student import EXPERIENCE_CATEGORIES, EXPERIENCE_TAGS, _labelize_exp_tag

	selected_category = request.args.get("category", "all").strip()
	selected_tag = request.args.get("tag", "all").strip().lower()
	sort_by = request.args.get("sort", "recent").strip().lower()

	query = StudentExperience.query.filter(StudentExperience.status == "approved")

	if selected_category != "all" and selected_category in EXPERIENCE_CATEGORIES:
		query = query.filter(StudentExperience.category == selected_category)

	if selected_tag != "all" and selected_tag.replace("-", "_") in EXPERIENCE_TAGS:
		tag_val = selected_tag.replace("-", "_")
		query = query.filter(
			_or(
				StudentExperience.tags == tag_val,
				StudentExperience.tags.like(f"{tag_val},%"),
				StudentExperience.tags.like(f"%,{tag_val},%"),
				StudentExperience.tags.like(f"%,{tag_val}"),
			)
		)

	if sort_by == "upvotes":
		query = query.order_by(StudentExperience.upvote_count.desc(), StudentExperience.created_at.desc())
	else:
		query = query.order_by(StudentExperience.created_at.desc())

	experiences = query.all()
	for exp in experiences:
		exp.created_at_ist = _to_ist(exp.created_at)

	user_id = session["user_id"]
	upvoted_ids = {uv.experience_id for uv in ExperienceUpvote.query.filter_by(user_id=user_id).all()}

	return render_template(
		"student_experiences.html",
		experiences=experiences,
		own_pending=[],
		upvoted_ids=upvoted_ids,
		selected_category=selected_category,
		selected_tag=selected_tag,
		sort_by=sort_by,
		experience_categories=EXPERIENCE_CATEGORIES,
		experience_tags=EXPERIENCE_TAGS,
		experience_report_categories=["Inappropriate Content", "Misinformation", "Spam / Advertisement", "Offensive Language", "Other"],
		labelize_exp_tag=_labelize_exp_tag,
	)


@faculty_bp.route("/updates")
@login_required
@role_required("faculty")
def updates_page():
	faculty_user = User.query.get(session["user_id"])
	selected_type = (request.args.get("type") or "all").strip().lower()
	if selected_type not in {"all", "feedback", "checklist", "intervention"}:
		selected_type = "all"

	approved_feedback = (
		Feedback.query.filter_by(faculty_id=faculty_user.id, status="approved")
		.order_by(Feedback.created_at.desc())
		.all()
	)
	checklist_rows = (
		Checklist.query.filter_by(faculty_id=faculty_user.id)
		.order_by(Checklist.created_at.desc())
		.all()
	)
	checklist_groups = _grouped_checklist_activity(checklist_rows)
	intervention_posts = (
		KnowledgePost.query.filter_by(author_id=faculty_user.id)
		.order_by(KnowledgePost.updated_at.desc(), KnowledgePost.created_at.desc())
		.all()
	)
	published_interventions = [post for post in intervention_posts if post.status == "published"]
	draft_interventions = [post for post in intervention_posts if post.status == "draft"]

	post_ids = [post.id for post in intervention_posts]
	_, bookmark_counts, view_counts = _intervention_reaction_counts(post_ids)

	events = []
	for row in approved_feedback[:25]:
		events.append(
			{
				"kind": "feedback",
				"created_at": row.created_at,
				"title": f"Approved feedback: {row.subject or 'Subject'}",
				"detail": f"{(row.reason or 'General').replace('_', ' ').title()} - {row.sentiment.title()}",
			}
		)

	for item in checklist_groups[:25]:
		semester_label = "All Semesters" if item["target_semester"] == "all" else f"Sem {item['target_semester']}"
		section_label = "All Sections" if item["target_section"] == "all" else f"Section {item['target_section']}"
		events.append(
			{
				"kind": "checklist",
				"created_at": item["created_at"],
				"title": f"Checklist group: {item['title']}",
				"detail": (
					f"{item['complete_count']}/{item['students']} complete - "
					f"{item['aggregate_state'].title()} - "
					f"Course {item['target_course']} - {semester_label} - {section_label}"
				),
			}
		)

	for post in intervention_posts[:25]:
		target = _target_summary(post)
		events.append(
			{
				"kind": "intervention",
				"created_at": post.updated_at or post.created_at,
				"title": f"{post.status.title()} intervention: {post.title}",
				"detail": f"{target['courses']} | {target['semesters']} | {target['sections']} - Opened {view_counts.get(post.id, 0)} - Saved {bookmark_counts.get(post.id, 0)}",
			}
		)

	if selected_type != "all":
		events = [item for item in events if item["kind"] == selected_type]

	events.sort(key=lambda item: item["created_at"] or datetime.min, reverse=True)

	return render_template(
		"faculty_updates.html",
		faculty=faculty_user,
		selected_type=selected_type,
		events=events,
		approved_feedback_count=len(approved_feedback),
		active_checklist_count=len([item for item in checklist_groups if item["aggregate_state"] != "complete"]),
		published_intervention_count=len(published_interventions),
		draft_intervention_count=len(draft_interventions),
		engaged_open_count=sum(view_counts.get(post.id, 0) for post in intervention_posts),
		engaged_save_count=sum(bookmark_counts.get(post.id, 0) for post in intervention_posts),
	)


@faculty_bp.route("/profile-settings", methods=["GET", "POST"])
@login_required
@role_required("faculty")
def profile_settings():
	faculty_user = User.query.get(session["user_id"])
	if not faculty_user:
		flash("Faculty account not found.", "danger")
		return redirect(url_for("auth.logout"))

	active_tab = (request.args.get("tab") or "profile").strip().lower()
	if active_tab not in {"profile", "security"}:
		active_tab = "profile"

	if request.method == "POST":
		form_type = (request.form.get("form_type") or "").strip().lower()

		if form_type == "profile":
			flash("Personal details are managed by admin and cannot be edited from faculty profile settings.", "warning")
			return redirect(url_for("faculty.profile_settings", tab="profile"))

		if form_type == "security_password":
			current_password = request.form.get("current_password", "")
			new_password = request.form.get("new_password", "")
			confirm_new_password = request.form.get("confirm_new_password", "")

			if not faculty_user.check_password(current_password):
				flash("Current password is incorrect.", "danger")
				return redirect(url_for("faculty.profile_settings", tab="security"))

			if len(new_password) < 8:
				flash("New password must be at least 8 characters.", "danger")
				return redirect(url_for("faculty.profile_settings", tab="security"))

			if new_password != confirm_new_password:
				flash("New password and confirmation do not match.", "danger")
				return redirect(url_for("faculty.profile_settings", tab="security"))

			faculty_user.set_password(new_password)
			db.session.commit()
			flash("Password updated successfully.", "success")
			return redirect(url_for("faculty.profile_settings", tab="security"))

		if form_type == "security_question":
			security_question = (request.form.get("security_question") or "").strip()
			security_answer = (request.form.get("security_answer") or "").strip()

			if security_question not in SECURITY_QUESTIONS:
				flash("Please select a valid security question.", "danger")
				return redirect(url_for("faculty.profile_settings", tab="security"))

			if not security_answer:
				flash("Security answer is required.", "danger")
				return redirect(url_for("faculty.profile_settings", tab="security"))

			faculty_user.security_question = security_question
			faculty_user.set_security_answer(security_answer)
			db.session.commit()
			flash("Security question updated successfully.", "success")
			return redirect(url_for("faculty.profile_settings", tab="security"))

	today = date.today()
	assignments = (
		FacultyAssignment.query.join(SubjectOffering, FacultyAssignment.subject_offering_id == SubjectOffering.id)
		.filter(
			FacultyAssignment.faculty_user_id == faculty_user.id,
			FacultyAssignment.is_active.is_(True),
			SubjectOffering.is_active.is_(True),
			or_(FacultyAssignment.effective_from.is_(None), FacultyAssignment.effective_from <= today),
			or_(FacultyAssignment.effective_to.is_(None), FacultyAssignment.effective_to >= today),
		)
		.order_by(
			SubjectOffering.course_code.asc(),
			SubjectOffering.semester_no.asc(),
			SubjectOffering.section.asc(),
			SubjectOffering.subject_code.asc(),
		)
		.all()
	)

	teaching_rows = []
	seen = set()
	for assignment in assignments:
		offering = assignment.subject_offering
		if not offering or offering.id in seen:
			continue
		seen.add(offering.id)
		teaching_rows.append(offering)

	assigned_courses = sorted({(row.course_code or "").strip().upper() for row in teaching_rows if (row.course_code or "").strip()})

	return render_template(
		"faculty_profile_settings.html",
		faculty=faculty_user,
		active_tab=active_tab,
		security_questions=SECURITY_QUESTIONS,
		teaching_rows=teaching_rows,
		assigned_courses=assigned_courses,
	)
