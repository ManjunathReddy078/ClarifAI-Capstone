import csv
import json
import random
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import func, or_
from academic_mapping_store import find_assignment, list_assignments_for_slot

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
	PendingFacultyFeedback,
	SemesterMismatchRequest,
	SubjectOffering,
	User,
	db,
)
from models import ExperienceReport, ExperienceUpvote, StudentExperience
from routes.auth import SECURITY_QUESTIONS, login_required, role_required
from sentiment import analyze_sentiment_with_confidence


student_bp = Blueprint("student", __name__, url_prefix="/student")

FIXED_FEEDBACK_TAGS = [
	"teaching_clarity",
	"pace_of_teaching",
	"subject_depth",
	"doubt_support",
	"lab_support",
	"assessment_fairness",
	"punctuality",
	"communication",
	"engagement",
	"course_material",
	"practical_relevance",
	"mentoring",
	"classroom_discipline",
	"assignment_quality",
	"improvement_suggestion",
]

SUBJECT_CATALOG_PATH = Path(__file__).resolve().parents[1] / "data" / "subjects.csv"

EXPERIENCE_CATEGORIES = [
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

EXPERIENCE_TAGS = [
	"academic_insight",
	"project_learning",
	"exam_preparation",
	"internship_story",
	"career_advice",
	"bug_solved",
	"concept_clarity",
	"lab_experience",
	"group_study",
	"presentation_tips",
	"time_management",
	"stress_management",
	"resource_recommendation",
	"practical_knowledge",
	"soft_skills",
	"networking",
	"open_source",
	"hackathon",
	"research_experience",
	"industry_insight",
	"coding_challenge",
	"mathematics",
	"theory_vs_practice",
	"revision_strategy",
	"viva_preparation",
	"placement_experience",
	"seminar_workshop",
	"peer_mentoring",
	"self_learning",
	"campus_life",
]

EXPERIENCE_REPORT_CATEGORIES = [
	"Inappropriate Content",
	"Misinformation",
	"Spam / Advertisement",
	"Offensive Language",
	"Other",
]
IST_ZONE = timezone(timedelta(hours=5, minutes=30))
CHECKLIST_META_PREFIX = "[[CLARIFAI_META]]"


def _ist_now() -> datetime:
	return datetime.now(IST_ZONE)


def _parse_iso_date(raw_value: str):
	if not raw_value:
		return None
	try:
		return datetime.strptime(raw_value, "%Y-%m-%d").date()
	except ValueError:
		return None


def _normalize_checklist_priority(raw_priority: str):
	priority = (raw_priority or "medium").strip().lower()
	if priority not in {"high", "medium", "low"}:
		return "medium"
	return priority


def _normalize_checklist_target_course(raw_value: str):
	value = (raw_value or "BOTH").strip().upper()
	if value not in {"MCA", "BCA", "BOTH"}:
		return "BOTH"
	return value


def _normalize_checklist_target_semester(raw_value):
	text = str(raw_value or "").strip().lower()
	if not text or text == "all":
		return "all"
	digits = "".join(ch for ch in text if ch.isdigit())
	if not digits:
		return "all"
	try:
		value = int(digits)
	except (TypeError, ValueError):
		return "all"
	if value < 1 or value > 12:
		return "all"
	return str(value)


def _normalize_checklist_section(raw_value: str):
	text = (raw_value or "").strip()
	if not text or text.lower() == "all":
		return "all"
	return text.upper()


def _normalize_checklist_indexes(raw_indexes, total_tasks: int):
	if total_tasks <= 0 or not isinstance(raw_indexes, list):
		return []
	values = set()
	for raw in raw_indexes:
		try:
			idx = int(raw)
		except (TypeError, ValueError):
			continue
		if 0 <= idx < total_tasks:
			values.add(idx)
	return sorted(values)


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
	details["priority"] = _normalize_checklist_priority(meta.get("priority", "medium"))
	details["due_date"] = _parse_iso_date(meta.get("due_date", ""))
	details["category"] = (meta.get("category") or "General").strip() or "General"
	details["target_course"] = _normalize_checklist_target_course(meta.get("target_course", "BOTH"))
	details["target_semester"] = _normalize_checklist_target_semester(meta.get("target_semester", "all"))
	details["target_section"] = _normalize_checklist_section(meta.get("target_section", "all"))
	details["tasks"] = [item.strip() for item in meta.get("tasks", []) if str(item).strip()]
	details["completed_tasks"] = _normalize_checklist_indexes(meta.get("completed_tasks", []), len(details["tasks"]))
	details["completion_locked"] = bool(meta.get("completion_locked", False))
	details["group_id"] = (meta.get("group_id") or "").strip()
	attachment = meta.get("attachment")
	details["attachment"] = attachment if isinstance(attachment, dict) else None
	details["description"] = remainder.strip()
	return details


def _serialize_checklist_description(details: dict):
	due_date = details.get("due_date")
	meta = {
		"subject": (details.get("subject") or "No specific subject").strip() or "No specific subject",
		"priority": _normalize_checklist_priority(details.get("priority", "medium")),
		"due_date": due_date.strftime("%Y-%m-%d") if due_date else "",
		"category": (details.get("category") or "General").strip() or "General",
		"target_course": _normalize_checklist_target_course(details.get("target_course", "BOTH")),
		"target_semester": _normalize_checklist_target_semester(details.get("target_semester", "all")),
		"target_section": _normalize_checklist_section(details.get("target_section", "all")),
		"tasks": [item.strip() for item in details.get("tasks", []) if str(item).strip()],
		"completed_tasks": _normalize_checklist_indexes(
			details.get("completed_tasks", []),
			len([item.strip() for item in details.get("tasks", []) if str(item).strip()]),
		),
		"completion_locked": bool(details.get("completion_locked", False)),
		"group_id": (details.get("group_id") or "").strip(),
		"attachment": details.get("attachment") if isinstance(details.get("attachment"), dict) else None,
	}
	meta_blob = f"{CHECKLIST_META_PREFIX}{json.dumps(meta, separators=(',', ':'))}"
	body = (details.get("description") or "").strip()
	if body:
		return f"{meta_blob}\n{body}"
	return meta_blob


def _checklist_task_state(title: str, details: dict, is_completed: bool):
	tasks = [item.strip() for item in details.get("tasks", []) if str(item).strip()]
	if not tasks:
		fallback = details.get("description") or title or "Complete this checklist item"
		tasks = [str(fallback).strip()]
	completed_indexes = _normalize_checklist_indexes(details.get("completed_tasks", []), len(tasks))
	if is_completed and not completed_indexes:
		completed_indexes = list(range(len(tasks)))
	if len(completed_indexes) == len(tasks):
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


def _checklist_due_state(due_date, progress_state: str):
	if progress_state == "complete":
		return "completed"
	if due_date is None:
		return "on-track"
	today = datetime.utcnow().date()
	if due_date < today:
		return "overdue"
	if due_date <= today + timedelta(days=3):
		return "due-soon"
	return "on-track"


def _checklist_due_label(due_date, due_state: str):
	if due_date is None:
		return "-"
	base = due_date.strftime("%d %b")
	if due_state == "overdue":
		return f"{base} (Overdue)"
	if due_state == "due-soon":
		return f"{base} (Due Soon)"
	if due_state == "completed":
		return f"{base} (Completed)"
	return base


def _normalized_step_lines(raw_value: str):
	if not raw_value:
		return []
	cleaned = []
	for line in raw_value.splitlines():
		item = re.sub(r"^(?:\d+[\)\.\-:]?\s+|[-*]\s+)", "", line.strip())
		if item:
			cleaned.append(item)
	return cleaned


def _build_knowledge_content(form_data):
	entry_title = form_data.get("entry_title", "").strip()
	category = form_data.get("category", "").strip()
	tags_raw = form_data.get("tags", "").strip()
	problem_context = form_data.get("problem_context", "").strip()
	solution_steps_raw = form_data.get("solution_steps", "")
	resources_raw = form_data.get("resources", "")
	outcome_result = form_data.get("outcome_result", "").strip()

	if not entry_title or not category or not problem_context or not outcome_result:
		return None, None, "Title, category, problem context, and outcome are required."

	solution_steps = _normalized_step_lines(solution_steps_raw)
	if len(solution_steps) < 1:
		return None, None, "Please add at least one clear solution step."

	tags = [token.strip() for token in tags_raw.split(",") if token.strip()]
	tags = list(dict.fromkeys(tags))[:8]

	resources = [line.strip() for line in resources_raw.splitlines() if line.strip()]

	sections = [
		f"Category: {category}",
	]
	if tags:
		sections.append(f"Tags: {', '.join(tags)}")

	sections.extend(
		[
			"Problem Context:",
			problem_context,
			"Solution Steps:",
			"\n".join([f"{idx + 1}. {step}" for idx, step in enumerate(solution_steps)]),
		]
	)

	if resources:
		sections.extend(
			[
				"Resources:",
				"\n".join([f"- {item}" for item in resources]),
			]
		)

	sections.extend(["Outcome / Result:", outcome_result])

	content = "\n\n".join(sections).strip()
	if len(content) < 80:
		return None, None, "Please provide richer details so peers can learn from your entry."

	return entry_title, content, None


def _labelize_tag(tag: str) -> str:
	return tag.replace("_", " ").title()


def _load_subject_catalog(student_course: str):
	if not SUBJECT_CATALOG_PATH.exists():
		return []

	rows = []
	with SUBJECT_CATALOG_PATH.open("r", encoding="utf-8") as file:
		reader = csv.DictReader(file)
		for row in reader:
			if not row:
				continue
			is_active = (row.get("is_active") or "").strip().lower()
			if is_active not in {"yes", "true", "1"}:
				continue
			degree = (row.get("degree") or "").strip().upper()
			if degree != (student_course or "MCA").strip().upper():
				continue
			rows.append(
				{
					"course_code": (row.get("course_code") or "").strip().upper(),
					"subject_name": (row.get("subject_name") or "").strip(),
					"semester": (row.get("semester") or "").strip(),
					"subject_type": (row.get("subject_type") or "").strip().upper(),
				}
			)

	rows.sort(key=lambda item: (item["semester"], item["course_code"]))
	return rows


def _subject_map_by_code(student_course: str):
	return {item["course_code"]: item for item in _load_subject_catalog(student_course)}


def _is_faculty_allowed_for_subject(
	*,
	student: User | None,
	student_course: str,
	subject_code: str,
	subject_semester: str,
	faculty_user_id: int,
):
	if not student:
		return True, None

	try:
		semester_no = int((subject_semester or "").strip())
	except (TypeError, ValueError):
		return True, None

	section = (student.section or "").strip().upper()
	if not section:
		return True, None

	offering = SubjectOffering.query.filter_by(
		course_code=(student_course or "MCA").strip().upper(),
		semester_no=semester_no,
		section=section,
		subject_code=(subject_code or "").strip().upper(),
		is_active=True,
	).first()

	if not offering:
		return True, None

	today = date.today()
	assignments = FacultyAssignment.query.filter_by(subject_offering_id=offering.id, is_active=True).all()
	active_faculty_ids = set()
	for assignment in assignments:
		if assignment.effective_from and assignment.effective_from > today:
			continue
		if assignment.effective_to and assignment.effective_to < today:
			continue
		active_faculty_ids.add(assignment.faculty_user_id)

	if not active_faculty_ids:
		return True, None

	if faculty_user_id not in active_faculty_ids:
		return False, "Selected faculty is not assigned to this subject for your semester and section."

	return True, None


def _split_feedback_tags(raw_value: str):
	if not raw_value:
		return []
	return [token.strip() for token in raw_value.split(",") if token.strip()]


def _extract_post_tags(title: str, content: str):
	corpus = f"{title} {content}".lower()
	keyword_map = {
		"algorithms": ["algorithm", "dsa", "graph", "dynamic programming", "sorting", "search"],
		"backtracking": ["backtracking", "n-queen", "subset", "recursive"],
		"recursion": ["recursion", "recursive", "stack"],
		"exam-prep": ["exam", "viva", "revision", "question"],
		"dbms": ["dbms", "database", "sql", "normalization", "transaction"],
		"normalization": ["1nf", "2nf", "3nf", "bcnf", "normalization"],
		"software-engineering": ["software engineering", "sdlc", "agile", "testing"],
		"ci-cd": ["ci/cd", "pipeline", "deployment", "github actions", "gitlab"],
		"devops": ["devops", "docker", "kubernetes", "container"],
		"machine-learning": ["machine learning", "ml", "model", "training"],
		"pytorch": ["pytorch", "tensor", "cuda"],
		"cuda": ["cuda", "gpu", "nvidia"],
		"3nf": ["3nf"],
		"bcnf": ["bcnf"],
	}

	tags = []
	for label, tokens in keyword_map.items():
		if any(token in corpus for token in tokens):
			tags.append(label)

	if not tags:
		tags = ["study-notes", "student-experience"]

	return tags[:5]


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


def _student_course_for_intervention(student: User | None):
	if not student:
		return ""
	profile = student.student_profile
	if profile and profile.course_code:
		return (profile.course_code or "").strip().upper()
	return (student.course or "").strip().upper()


def _student_matches_intervention(post: KnowledgePost, student: User, semester_value: int | None):
	if post.status != "published":
		return False

	target_courses = set(_parse_csv_values(post.target_courses, uppercase=True))
	target_semesters = {value.lower() for value in _parse_csv_values(post.target_semesters, uppercase=False)}
	target_sections = set(_parse_csv_values(post.target_sections, uppercase=True))

	student_course = _student_course_for_intervention(student)
	if target_courses and student_course not in target_courses:
		return False

	if target_semesters and "all" not in target_semesters:
		if semester_value is None or str(semester_value) not in target_semesters:
			return False

	if target_sections and "ALL" not in target_sections:
		student_section = (student.section or "").strip().upper()
		if not student_section or student_section not in target_sections:
			return False

	return True


def _knowledge_reaction_counts(post_ids):
	if not post_ids:
		return {}, {}
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
	return ({row[0]: row[1] for row in like_rows}, {row[0]: row[1] for row in bookmark_rows})


def _intervention_target_summary(post: KnowledgePost):
	courses = _parse_csv_values(post.target_courses, uppercase=True)
	semesters = _parse_csv_values(post.target_semesters)
	sections = _parse_csv_values(post.target_sections, uppercase=True)
	semester_label = "-"
	if semesters:
		if semesters[0].lower() == "all":
			semester_label = "All Semesters"
		else:
			semester_label = f"Sem {semesters[0]}"
	section_label = "-"
	if sections:
		section_label = "All Sections" if sections[0].upper() == "ALL" else f"Section {sections[0]}"
	return {
		"courses": courses[0] if courses else "-",
		"semesters": semester_label,
		"sections": section_label,
	}


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


def _predict_realtime_semester(student: User | None) -> int | None:
	profile = student.student_profile if student else None
	if not profile:
		return None

	course_code = (profile.course_code or (student.course if student else "") or "MCA").strip().upper()
	config = CourseConfig.query.filter_by(course_code=course_code, is_active=True).first()
	semesters_per_year = int(config.semesters_per_year) if config and config.semesters_per_year else 2
	max_semester = int(profile.max_semester or (config.total_semesters if config else 4) or 4)

	today = _ist_now().date()
	if (profile.admission_year, profile.admission_month) > (today.year, today.month):
		return 1

	months_elapsed = (today.year - profile.admission_year) * 12 + (today.month - profile.admission_month)
	months_elapsed = max(0, months_elapsed)
	months_per_semester = max(1, int(round(12 / max(1, semesters_per_year))))
	predicted_semester = 1 + (months_elapsed // months_per_semester)
	return max(1, min(max_semester, predicted_semester))


def _utc_to_ist(value: datetime | None) -> datetime | None:
	if not value:
		return None
	source = value
	if source.tzinfo is None:
		source = source.replace(tzinfo=timezone.utc)
	return source.astimezone(IST_ZONE)


def _merge_student_feedback_rows(student_id: int):
	feedback_rows = Feedback.query.filter_by(student_id=student_id).all()
	pending_rows = PendingFacultyFeedback.query.filter_by(student_id=student_id).all()

	merged = []
	for item in feedback_rows:
		merged.append(
			SimpleNamespace(
				id=item.id,
				source="feedback",
				faculty_name=item.faculty.full_name if item.faculty else "-",
				faculty_selector=str(item.faculty_id),
				course_code=item.course_code,
				subject=item.subject,
				semester=item.semester,
				reason=item.reason,
				feedback_tags=item.feedback_tags,
				class_session_at=item.class_session_at,
				sentiment=item.sentiment,
				status=item.status,
				created_at=item.created_at,
				submitted_at_ist=_utc_to_ist(item.created_at),
				can_edit_delete=True,
			)
		)

	for item in pending_rows:
		merged.append(
			SimpleNamespace(
				id=item.id,
				source="pending",
				faculty_name=item.assigned_faculty_name or item.assigned_faculty_id,
				faculty_selector=(item.assigned_faculty_id or "").strip().upper(),
				course_code=item.subject_code,
				subject=item.subject,
				semester=item.semester,
				reason=item.reason,
				feedback_tags=item.feedback_tags,
				class_session_at=item.class_session_at,
				sentiment=item.sentiment,
				status=item.status,
				created_at=item.created_at,
				submitted_at_ist=_utc_to_ist(item.created_at),
				can_edit_delete=False,
			)
		)

	merged.sort(key=lambda row: row.created_at or datetime.min, reverse=True)
	return merged


def _generate_experience_anon_id() -> str:
	import string
	chars = string.ascii_uppercase + string.digits
	for _ in range(10000):
		candidate = "EXP-" + "".join(random.choices(chars, k=6))
		if not StudentExperience.query.filter_by(anon_id=candidate).first():
			return candidate
	raise RuntimeError("Unable to generate a unique experience ID.")


def _labelize_exp_tag(tag: str) -> str:
	return tag.replace("_", " ").title()


def _normalize_experience_links(raw_value: str) -> list[str]:
	if not raw_value:
		return []

	lines = [line.strip() for line in raw_value.splitlines() if line.strip()]
	if len(lines) == 1 and "," in lines[0]:
		parts = [item.strip() for item in lines[0].split(",") if item.strip()]
		if len(parts) > 1:
			return parts
	return lines


def _experience_auto_status(sentiment: str) -> str:
	return "approved" if sentiment == "positive" else "pending"


def _reviews_filter_options(student: User | None):
	current_semester_display = _predict_realtime_semester(student) or 1
	if not student:
		return current_semester_display, [], []

	student_course = (student.course or "MCA").strip().upper()
	student_section = (student.section or "").strip().upper()
	today_ist = _ist_now().date()

	subject_map = {}
	faculty_map = {}

	if student_section:
		offerings = (
			SubjectOffering.query.filter_by(
				course_code=student_course,
				semester_no=current_semester_display,
				section=student_section,
				is_active=True,
			)
			.order_by(SubjectOffering.subject_name.asc())
			.all()
		)

		for offering in offerings:
			subject_map[offering.subject_code] = {
				"code": offering.subject_code,
				"name": offering.subject_name,
			}
			assignments = FacultyAssignment.query.filter_by(subject_offering_id=offering.id, is_active=True).all()
			for assignment in assignments:
				if assignment.effective_from and assignment.effective_from > today_ist:
					continue
				if assignment.effective_to and assignment.effective_to < today_ist:
					continue
				faculty_user = assignment.faculty_user
				if not faculty_user or not faculty_user.is_active:
					continue
				faculty_map[faculty_user.id] = {
					"id": faculty_user.id,
					"name": faculty_user.full_name,
				}

	if not subject_map or not faculty_map:
		current_semester_rows = (
			Feedback.query.join(User, Feedback.faculty_id == User.id)
			.filter(
				Feedback.student_id == student.id,
				Feedback.semester == str(current_semester_display),
			)
			.order_by(Feedback.created_at.desc())
			.all()
		)
		for row in current_semester_rows:
			if row.course_code and row.subject:
				subject_map.setdefault(
					row.course_code,
					{
						"code": row.course_code,
						"name": row.subject,
					},
				)
			if row.faculty and row.faculty.is_active:
				faculty_map.setdefault(
					row.faculty.id,
					{
						"id": row.faculty.id,
						"name": row.faculty.full_name,
					},
				)

		pending_rows = (
			PendingFacultyFeedback.query.filter_by(student_id=student.id, semester=str(current_semester_display))
			.order_by(PendingFacultyFeedback.created_at.desc())
			.all()
		)
		for row in pending_rows:
			if row.subject_code and row.subject:
				subject_map.setdefault(
					row.subject_code,
					{
						"code": row.subject_code,
						"name": row.subject,
					},
				)
			if row.assigned_faculty_id:
				faculty_map.setdefault(
					row.assigned_faculty_id,
					{
						"id": row.assigned_faculty_id,
						"name": row.assigned_faculty_name or row.assigned_faculty_id,
					},
				)

	subject_options = sorted(subject_map.values(), key=lambda item: item["name"].lower())
	faculty_options = sorted(faculty_map.values(), key=lambda item: item["name"].lower())
	return current_semester_display, subject_options, faculty_options


@student_bp.route("/dashboard")
@login_required
@role_required("student")
def dashboard():
	student = User.query.get(session["user_id"])
	subject_catalog = _load_subject_catalog(student.course if student else "MCA")
	current_semester_display = 1
	profile = student.student_profile if student else None
	realtime_semester = _predict_realtime_semester(student)
	if realtime_semester:
		current_semester_display = realtime_semester
	elif profile and profile.current_semester:
		current_semester_display = profile.current_semester
	elif subject_catalog:
		try:
			current_semester_display = int((subject_catalog[0].get("semester") or "1").strip())
		except (TypeError, ValueError, AttributeError):
			current_semester_display = 1

	all_faculty = User.query.filter_by(role="faculty", is_active=True).order_by(User.full_name.asc()).all()
	merged_feedback = _merge_student_feedback_rows(student.id)
	feedback_items = merged_feedback[:5]
	total_feedback = len(merged_feedback)
	checklists = Checklist.query.filter_by(student_id=student.id).order_by(Checklist.created_at.desc()).all()
	student_course = (student.course or "").strip().upper() if student else ""
	if student_course not in {"MCA", "BCA"}:
		student_course = "MCA"
	checklist_course_options = [student_course, "BOTH"]
	checklist_filter_course = (request.args.get("chk_course") or student_course).strip().upper()
	if checklist_filter_course not in checklist_course_options:
		checklist_filter_course = student_course
	checklist_filter_category = (request.args.get("chk_category") or "all").strip()
	checklist_filter_semester = _normalize_checklist_target_semester(request.args.get("chk_semester", "all"))
	checklist_filter_section = _normalize_checklist_section(request.args.get("chk_section", "all"))
	checklist_filter_status = (request.args.get("chk_status") or "all").strip().lower()
	if checklist_filter_status not in {"all", "complete", "partial", "null"}:
		checklist_filter_status = "all"

	checklist_category_pool = set(EXPERIENCE_CATEGORIES)
	for item in checklists:
		details = _parse_checklist_description(item.description or "")
		if details.get("category"):
			checklist_category_pool.add(details["category"])
	checklist_category_options = sorted(checklist_category_pool)
	if checklist_filter_category != "all" and checklist_filter_category not in checklist_category_options:
		checklist_filter_category = "all"

	dashboard_checklists = []
	total_task_units = 0
	completed_task_units = 0
	completed_checklists = 0
	partial_checklists = 0
	null_checklists = 0
	for item in checklists:
		details = _parse_checklist_description(item.description or "")
		target_course = details.get("target_course", "BOTH")
		target_semester = details.get("target_semester", "all")
		target_section = details.get("target_section", "all")
		if checklist_filter_course == "BOTH" and target_course != "BOTH":
			continue
		if checklist_filter_course in {"MCA", "BCA"} and target_course not in {checklist_filter_course, "BOTH"}:
			continue
		if checklist_filter_category != "all" and details.get("category", "General") != checklist_filter_category:
			continue
		if checklist_filter_semester != "all" and target_semester not in {"all", checklist_filter_semester}:
			continue
		if checklist_filter_section != "all" and target_section not in {"all", checklist_filter_section}:
			continue

		state = _checklist_task_state(item.title, details, item.is_completed)
		if checklist_filter_status != "all" and state["state"] != checklist_filter_status:
			continue
		completed_indexes = set(state["completed_indexes"])
		total_task_units += state["total"]
		completed_task_units += state["completed"]
		if state["state"] == "complete":
			completed_checklists += 1
		elif state["state"] == "partial":
			partial_checklists += 1
		else:
			null_checklists += 1

		dashboard_checklists.append(
			{
				"item": item,
				"details": details,
				"state": state["state"],
				"completed_tasks": state["completed"],
				"total_tasks": state["total"],
				"task_rows": [
					{
						"text": task_text,
						"is_done": idx in completed_indexes,
					}
					for idx, task_text in enumerate(state["tasks"])
				],
				"task_percent": int(round((state["completed"] / max(state["total"], 1)) * 100)),
			}
		)
	posts = KnowledgePost.query.order_by(KnowledgePost.created_at.desc()).limit(8).all()
	all_feedback = Feedback.query.filter_by(student_id=student.id).order_by(Feedback.created_at.asc()).all()

	total_checklists = len(dashboard_checklists)
	progress_percent = int((completed_task_units / max(total_task_units, 1)) * 100) if total_task_units else 0
	checklist_semester_options = sorted(
		{
			entry["details"].get("target_semester", "all")
			for entry in dashboard_checklists
			if entry["details"].get("target_semester", "all") != "all"
		},
		key=lambda value: int(value) if str(value).isdigit() else value,
	)
	checklist_section_options = sorted(
		{
			entry["details"].get("target_section", "all")
			for entry in dashboard_checklists
			if entry["details"].get("target_section", "all") != "all"
		}
	)

	sentiment_counts = {"positive": 0, "neutral": 0, "negative": 0}
	status_counts = {"approved": 0, "under_review": 0, "request_edit": 0, "rejected": 0}

	for item in all_feedback:
		if item.sentiment in sentiment_counts:
			sentiment_counts[item.sentiment] += 1
		if item.status in status_counts:
			status_counts[item.status] += 1

	month_keys = _last_n_month_labels(6)
	month_label_text = [datetime(year=year, month=month, day=1).strftime("%b %Y") for year, month in month_keys]
	month_map = {f"{year:04d}-{month:02d}": 0 for year, month in month_keys}
	for item in all_feedback:
		key = item.created_at.strftime("%Y-%m")
		if key in month_map:
			month_map[key] += 1

	chart_payload = {
		"monthly_labels": month_label_text,
		"monthly_counts": [month_map[f"{year:04d}-{month:02d}"] for year, month in month_keys],
		"sentiment_labels": ["Positive", "Neutral", "Negative"],
		"sentiment_counts": [
			sentiment_counts["positive"],
			sentiment_counts["neutral"],
			sentiment_counts["negative"],
		],
		"status_labels": ["Approved", "Under Review", "Request Edit", "Rejected"],
		"status_counts": [
			status_counts["approved"],
			status_counts["under_review"],
			status_counts["request_edit"],
			status_counts["rejected"],
		],
	}

	recent_notifications = []
	if feedback_items:
		item = feedback_items[0]
		recent_notifications.append(
			{
				"title": f"Feedback {item.status.replace('_', ' ').title()}",
				"message": f"{item.subject} feedback is currently {item.status.replace('_', ' ')}.",
				"kind": item.sentiment,
				"time": _utc_to_ist(item.created_at),
			}
		)

	pending_checklist = next((entry for entry in dashboard_checklists if entry["state"] != "complete"), None)
	if pending_checklist:
		recent_notifications.append(
			{
				"title": "Checklist Action Pending",
				"message": f"{pending_checklist['item'].title} is waiting for completion.",
				"kind": "neutral",
				"time": _utc_to_ist(pending_checklist["item"].created_at),
			}
		)

	if posts:
		recent_notifications.append(
			{
				"title": "Knowledge Board Updated",
				"message": f"{posts[0].title} was recently posted to the board.",
				"kind": "positive",
				"time": _utc_to_ist(posts[0].created_at),
			}
		)

	experience_reports = (
		ExperienceReport.query.filter_by(reporter_id=student.id)
		.order_by(ExperienceReport.created_at.desc())
		.limit(6)
		.all()
	)
	for report in experience_reports:
		report.created_at_ist = _utc_to_ist(report.created_at)

	return render_template(
		"dashboard_student.html",
		student=student,
		faculty_list=all_faculty,
		subject_catalog=subject_catalog,
		current_semester_display=current_semester_display,
		checklist_filter_course=checklist_filter_course,
		checklist_course_options=checklist_course_options,
		checklist_filter_category=checklist_filter_category,
		checklist_filter_semester=checklist_filter_semester,
		checklist_filter_section=checklist_filter_section,
		checklist_filter_status=checklist_filter_status,
		checklist_category_options=checklist_category_options,
		checklist_semester_options=checklist_semester_options,
		checklist_section_options=checklist_section_options,
		fixed_feedback_tags=FIXED_FEEDBACK_TAGS,
		feedback_items=feedback_items,
		total_feedback=total_feedback,
		checklists=checklists,
		dashboard_checklists=dashboard_checklists,
		total_checklists=total_checklists,
		completed_checklists=completed_checklists,
		partial_checklists=partial_checklists,
		null_checklists=null_checklists,
		posts=posts,
		progress_percent=progress_percent,
		chart_payload=chart_payload,
		recent_notifications=recent_notifications,
		experience_reports=experience_reports,
	)


def _validate_feedback_form(form_data, student: User | None):
	student_course = student.course if student else "MCA"
	faculty_id = form_data.get("faculty_id", "").strip().upper()
	course_code = form_data.get("course_code", "").strip().upper()
	reason = form_data.get("reason", "").strip()
	class_session_at = form_data.get("class_session_at", "").strip()
	feedback_tags = [tag.strip() for tag in form_data.getlist("feedback_tags") if tag.strip()]
	if not feedback_tags:
		feedback_tags = _split_feedback_tags(form_data.get("feedback_tags", ""))
	feedback_text = form_data.get("feedback_text", "").strip()

	if not faculty_id or not course_code or not reason or not class_session_at or not feedback_text:
		return None, "All review fields are required."
	if not feedback_tags:
		return None, "Please select at least one feedback tag."

	invalid_tags = [tag for tag in feedback_tags if tag not in FIXED_FEEDBACK_TAGS]
	if invalid_tags:
		return None, "Invalid feedback tag selection."

	if reason not in FIXED_FEEDBACK_TAGS:
		return None, "Please select a valid reason from the fixed tag list."

	current_semester = _predict_realtime_semester(student) or 1
	student_section = (student.section or "").strip().upper() if student else ""
	today_ist = _ist_now().date()

	subject_entry = None
	assignment_entry = None
	if student and student_section:
		offering = SubjectOffering.query.filter_by(
			course_code=(student_course or "MCA").strip().upper(),
			semester_no=current_semester,
			section=student_section,
			subject_code=course_code,
			is_active=True,
		).first()

		if offering:
			subject_entry = {
				"course_code": offering.subject_code,
				"subject_name": offering.subject_name,
				"semester": str(offering.semester_no),
			}

			assignment_entry = find_assignment(
				course_code=(student_course or "MCA").strip().upper(),
				semester_no=current_semester,
				section=student_section,
				subject_code=offering.subject_code,
			)

	if not subject_entry:
		subject_rows = _load_subject_catalog(student_course)
		subject_map = {
			item["course_code"]: item
			for item in subject_rows
			if str(item.get("semester") or "").strip() == str(current_semester)
		}
		subject_entry = subject_map.get(course_code)
		if not subject_entry:
			return None, "Please select a valid subject from your current semester."

	if not assignment_entry and student_section:
		assignment_entry = find_assignment(
			course_code=(student_course or "MCA").strip().upper(),
			semester_no=current_semester,
			section=student_section,
			subject_code=subject_entry["course_code"],
		)

	if not assignment_entry:
		return None, "No faculty assignment found for this subject in your section."

	assigned_faculty_id = (assignment_entry.get("faculty_id") or "").strip().upper()
	if not assigned_faculty_id:
		return None, "Assigned faculty ID is missing in preset mapping."

	if faculty_id != assigned_faculty_id:
		return None, "Selected faculty does not match the assigned faculty for this subject and section."

	assigned_faculty_email = (assignment_entry.get("faculty_email") or "").strip().lower()
	assigned_faculty_name = (assignment_entry.get("faculty_name") or "").strip()
	faculty_candidate = User.query.filter_by(role="faculty", is_active=True, faculty_id=assigned_faculty_id).first()
	if not faculty_candidate and assigned_faculty_email:
		faculty_candidate = User.query.filter_by(role="faculty", is_active=True, email=assigned_faculty_email).first()

	# Keep feedback in delivery queue until faculty has logged in at least once.
	# Backward compatibility: honor last_login_at for legacy rows.
	faculty_user = faculty_candidate if faculty_candidate and (faculty_candidate.first_login_at or faculty_candidate.last_login_at) else None

	try:
		parsed_class_session_at = datetime.strptime(class_session_at, "%Y-%m-%dT%H:%M")
	except ValueError:
		return None, "Class session date/time format is invalid."

	if parsed_class_session_at.date() > today_ist:
		return None, "Class session date cannot be in the future."

	if parsed_class_session_at.weekday() == 6:
		return None, "Class session date cannot be on Sunday."

	session_time = parsed_class_session_at.time()
	if session_time < time(8, 0) or session_time > time(17, 0):
		return None, "Class session time must be between 08:00 AM and 05:00 PM (IST)."

	validated = {
		"faculty_user": faculty_user,
		"assigned_faculty_id": assigned_faculty_id,
		"assigned_faculty_email": assigned_faculty_email,
		"assigned_faculty_name": assigned_faculty_name,
		"course_code": subject_entry["course_code"],
		"subject": subject_entry["subject_name"],
		"semester": subject_entry["semester"],
		"section": student_section or "A",
		"reason": reason,
		"feedback_tags": ",".join(feedback_tags),
		"class_session_at": parsed_class_session_at,
		"feedback_text": feedback_text,
	}
	return validated, None


def _apply_feedback_payload(feedback_item: Feedback, payload: dict) -> int:
	feedback_item.faculty_id = payload["faculty_user"].id
	feedback_item.course_code = payload["course_code"]
	feedback_item.subject = payload["subject"]
	feedback_item.semester = payload["semester"]
	feedback_item.reason = payload["reason"]
	feedback_item.feedback_tags = payload["feedback_tags"]
	feedback_item.class_session_at = payload["class_session_at"]
	feedback_item.feedback_text = payload["feedback_text"]
	sentiment, confidence = analyze_sentiment_with_confidence(payload["feedback_text"])
	feedback_item.sentiment = sentiment
	feedback_item.status = "approved" if feedback_item.sentiment == "positive" else "under_review"
	feedback_item.admin_note = None
	return confidence


@student_bp.route("/submit-feedback", methods=["POST"])
@login_required
@role_required("student")
def submit_feedback():
	student = User.query.get(session["user_id"])
	payload, error = _validate_feedback_form(request.form, student)
	if error:
		flash(error, "danger")
		redirect_target = "student.submit_feedback_page" if request.form.get("context") == "fullpage" else "student.dashboard"
		return redirect(url_for(redirect_target))

	confidence = 0
	if payload["faculty_user"]:
		feedback = Feedback(student_id=session["user_id"], faculty_id=payload["faculty_user"].id)
		confidence = _apply_feedback_payload(feedback, payload)
		db.session.add(feedback)
		db.session.commit()
		result_sentiment = feedback.sentiment
		result_status = feedback.status
		delivery_state = "faculty"
	else:
		sentiment, confidence = analyze_sentiment_with_confidence(payload["feedback_text"])
		queue_status = "holding" if sentiment == "positive" else "under_review"
		pending = PendingFacultyFeedback(
			student_id=session["user_id"],
			course_code=(student.course or "MCA").strip().upper() if student else "MCA",
			section=payload["section"],
			semester=payload["semester"],
			subject_code=payload["course_code"],
			subject=payload["subject"],
			assigned_faculty_id=payload["assigned_faculty_id"],
			assigned_faculty_email=payload["assigned_faculty_email"] or None,
			assigned_faculty_name=payload["assigned_faculty_name"] or None,
			reason=payload["reason"],
			feedback_tags=payload["feedback_tags"],
			class_session_at=payload["class_session_at"],
			feedback_text=payload["feedback_text"],
			sentiment=sentiment,
			sentiment_confidence=confidence,
			status=queue_status,
		)
		db.session.add(pending)
		db.session.commit()
		result_sentiment = sentiment
		result_status = queue_status
		delivery_state = "holding"

	if request.form.get("context", "").strip().lower() == "fullpage":
		return redirect(
			url_for(
				"student.submit_feedback_page",
				success="1",
				sentiment=result_sentiment,
				status=result_status,
				delivery=delivery_state,
				confidence=confidence,
			)
		)

	flash("Review submitted successfully.", "success")
	return redirect(url_for("student.dashboard"))


@student_bp.route("/sentiment-preview", methods=["POST"])
@login_required
@role_required("student")
def sentiment_preview():
	data = request.get_json(silent=True) or {}
	text = (data.get("text") or "").strip()
	if not text:
		return jsonify({"error": "Feedback text is required."}), 400

	sentiment, confidence = analyze_sentiment_with_confidence(text)
	return jsonify({"sentiment": sentiment, "confidence": confidence})


@student_bp.route("/reviews")
@login_required
@role_required("student")
def reviews():
	sentiment = request.args.get("sentiment", "all").strip().lower()
	status = request.args.get("status", "all").strip().lower()
	selected_faculty = request.args.get("faculty", "all").strip()
	raw_subject = request.args.get("subject", "all").strip()
	selected_subject = raw_subject.upper() if raw_subject.lower() != "all" else "all"
	selected_reason = request.args.get("reason", "all").strip().lower()
	selected_tag = request.args.get("tag", "all").strip().lower()
	valid_tag_set = set(FIXED_FEEDBACK_TAGS)

	feedback_items = _merge_student_feedback_rows(session["user_id"])

	if sentiment in {"positive", "neutral", "negative"}:
		feedback_items = [item for item in feedback_items if item.sentiment == sentiment]

	if status in {"holding", "under_review", "approved", "rejected", "request_edit"}:
		feedback_items = [item for item in feedback_items if item.status == status]

	if selected_faculty != "all":
		selected_faculty_norm = selected_faculty.strip().upper()
		feedback_items = [
			item
			for item in feedback_items
			if (item.faculty_selector or "").strip().upper() == selected_faculty_norm
		]

	if selected_subject != "all":
		feedback_items = [item for item in feedback_items if (item.course_code or "").strip().upper() == selected_subject]

	if selected_reason in valid_tag_set:
		feedback_items = [item for item in feedback_items if item.reason == selected_reason]
	elif selected_reason != "all":
		selected_reason = "all"

	if selected_tag in valid_tag_set:
		feedback_items = [
			item
			for item in feedback_items
			if selected_tag in {token.strip() for token in (item.feedback_tags or "").split(",") if token.strip()}
		]
	elif selected_tag != "all":
		selected_tag = "all"

	student = User.query.get(session["user_id"])
	current_semester_display, subject_filter_options, faculty_filter_options = _reviews_filter_options(student)
	faculty_list = User.query.filter_by(role="faculty", is_active=True).order_by(User.full_name.asc()).all()

	if selected_faculty != "all" and not any(str(item["id"]).upper() == selected_faculty.upper() for item in faculty_filter_options):
		selected_faculty = "all"
	if selected_subject != "all" and not any(item["code"] == selected_subject for item in subject_filter_options):
		selected_subject = "all"

	reason_filter_options = [{"value": tag, "label": _labelize_tag(tag)} for tag in FIXED_FEEDBACK_TAGS]
	tag_filter_options = [{"value": tag, "label": _labelize_tag(tag)} for tag in FIXED_FEEDBACK_TAGS]

	return render_template(
		"student_reviews.html",
		feedback_items=feedback_items,
		faculty_list=faculty_list,
		subject_catalog=_load_subject_catalog(student.course if student else "MCA"),
		fixed_feedback_tags=FIXED_FEEDBACK_TAGS,
		sentiment=sentiment,
		status=status,
		current_semester_display=current_semester_display,
		faculty_filter_options=faculty_filter_options,
		subject_filter_options=subject_filter_options,
		reason_filter_options=reason_filter_options,
		tag_filter_options=tag_filter_options,
		selected_faculty=selected_faculty,
		selected_subject=selected_subject,
		selected_reason=selected_reason,
		selected_tag=selected_tag,
	)


@student_bp.route("/reviews/create", methods=["POST"])
@login_required
@role_required("student")
def create_review():
	student = User.query.get(session["user_id"])
	payload, error = _validate_feedback_form(request.form, student)
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

	student = User.query.get(session["user_id"])
	faculty_list = User.query.filter_by(role="faculty", is_active=True).order_by(User.full_name.asc()).all()
	subject_catalog = _load_subject_catalog(student.course if student else "MCA")
	selected_tags = _split_feedback_tags(feedback_item.feedback_tags)

	if request.method == "POST":
		payload, error = _validate_feedback_form(request.form, student)
		if error:
			flash(error, "danger")
			return render_template(
				"student_review_edit.html",
				feedback_item=feedback_item,
				faculty_list=faculty_list,
				subject_catalog=subject_catalog,
				fixed_feedback_tags=FIXED_FEEDBACK_TAGS,
				selected_tags=selected_tags,
			)

		_apply_feedback_payload(feedback_item, payload)
		db.session.commit()
		flash("Review updated successfully.", "success")
		return redirect(url_for("student.reviews"))

	return render_template(
		"student_review_edit.html",
		feedback_item=feedback_item,
		faculty_list=faculty_list,
		subject_catalog=subject_catalog,
		fixed_feedback_tags=FIXED_FEEDBACK_TAGS,
		selected_tags=selected_tags,
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
	search = request.args.get("q", "").strip()
	sort_by = request.args.get("sort", "most_upvoted").strip().lower()
	date_from_value = request.args.get("date_from", "").strip()
	date_to_value = request.args.get("date_to", "").strip()
	date_from = _parse_iso_date(date_from_value)
	date_to = _parse_iso_date(date_to_value)
	if sort_by not in {"most_upvoted", "oldest", "recent"}:
		sort_by = "most_upvoted"
	student = User.query.get(session["user_id"])
	current_semester = _predict_realtime_semester(student)
	if current_semester is None and student and student.student_profile and student.student_profile.current_semester:
		try:
			current_semester = int(student.student_profile.current_semester)
		except (TypeError, ValueError):
			current_semester = None

	posts = (
		KnowledgePost.query.join(User, KnowledgePost.author_id == User.id)
		.filter(User.role == "faculty", KnowledgePost.status == "published")
		.order_by(KnowledgePost.created_at.desc())
		.all()
	)
	posts = [post for post in posts if student and _student_matches_intervention(post, student, current_semester)]
	post_ids = [post.id for post in posts]
	now = datetime.utcnow()
	if post_ids:
		existing_views = {
			row.post_id: row
			for row in KnowledgeView.query.filter(
				KnowledgeView.user_id == session["user_id"],
				KnowledgeView.post_id.in_(post_ids),
			).all()
		}
		for post_id in post_ids:
			view = existing_views.get(post_id)
			if view:
				view.last_opened_at = now
				continue
			db.session.add(
				KnowledgeView(
					post_id=post_id,
					user_id=session["user_id"],
					first_opened_at=now,
					last_opened_at=now,
				)
			)
	like_counts, bookmark_counts = _knowledge_reaction_counts(post_ids)
	reaction_rows = []
	if post_ids:
		reaction_rows = KnowledgeReaction.query.filter(
			KnowledgeReaction.user_id == session["user_id"],
			KnowledgeReaction.post_id.in_(post_ids),
		).all()
	active_reactions = {(row.post_id, row.reaction_type) for row in reaction_rows}

	board_cards = []
	for post in posts:
		tags = _extract_post_tags(
			post.title,
			" ".join(
				[
					post.content or "",
					post.problem_context or "",
					post.solution_steps or "",
					post.resource_references or "",
					post.outcome_result or "",
				]
			),
		)
		likes = like_counts.get(post.id, 0)
		bookmarks = bookmark_counts.get(post.id, 0)
		board_cards.append(
			{
				"post": post,
				"tags": tags,
				"likes": likes,
				"bookmarks": bookmarks,
				"comments": 0,
				"rank": (likes * 2) + bookmarks,
				"attachments": post.attachments.order_by(KnowledgeAttachment.created_at.desc()).all(),
				"target_summary": _intervention_target_summary(post),
				"liked": (post.id, "like") in active_reactions,
				"bookmarked": (post.id, "bookmark") in active_reactions,
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

	unread_notifications = (
		KnowledgeNotification.query.filter_by(user_id=session["user_id"], is_read=False)
		.order_by(KnowledgeNotification.created_at.desc())
		.limit(12)
		.all()
	)
	notification_items = [
		{
			"message": note.message,
			"created_at": note.created_at,
			"post_id": note.post_id,
		}
		for note in unread_notifications
	]
	if unread_notifications:
		for note in unread_notifications:
			note.is_read = True
	db.session.commit()

	return render_template(
		"knowledge_board.html",
		board_cards=board_cards,
		search=search,
		sort_by=sort_by,
		date_from_value=date_from_value if date_from else "",
		date_to_value=date_to_value if date_to else "",
		board_filter_endpoint="student.knowledge_board",
		board_page_title="Knowledge Board",
		board_heading="Knowledge Board",
		board_subtitle="Faculty interventions tailored to your course, semester, and section.",
		board_breadcrumb_primary="Student",
		board_breadcrumb_secondary="Knowledge Board",
		my_posts_url=None,
		my_posts_label="",
		create_post_url=url_for("student.knowledge_board", compose="1"),
		create_post_label="Create Entry",
		empty_message="No knowledge entries available yet.",
		open_compose=False,
		show_publish_success=False,
		published_entry_name="",
		show_reaction_actions=True,
		detail_endpoint="student.resource_post_detail",
		metrics_endpoint=None,
		notification_items=notification_items,
		enable_compose_modal=False,
	)


@student_bp.route("/resource-post/<int:post_id>/detail")
@login_required
@role_required("student")
def resource_post_detail(post_id: int):
	student = User.query.get(session["user_id"])
	if not student:
		return jsonify({"error": "not_found"}), 404

	current_semester = _predict_realtime_semester(student)
	if current_semester is None and student.student_profile and student.student_profile.current_semester:
		try:
			current_semester = int(student.student_profile.current_semester)
		except (TypeError, ValueError):
			current_semester = None

	post = (
		KnowledgePost.query.join(User, KnowledgePost.author_id == User.id)
		.filter(KnowledgePost.id == post_id, KnowledgePost.status == "published", User.role == "faculty")
		.first()
	)
	if not post or not _student_matches_intervention(post, student, current_semester):
		return jsonify({"error": "not_found"}), 404

	likes, bookmarks = _knowledge_reaction_counts([post.id])
	opened_count = (
		db.session.query(func.count(KnowledgeView.id)).filter(KnowledgeView.post_id == post.id).scalar() or 0
	)
	reach_count = (
		db.session.query(func.count(func.distinct(KnowledgeView.user_id))).filter(KnowledgeView.post_id == post.id).scalar()
		or 0
	)
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
		"target": _intervention_target_summary(post),
		"metrics": {
			"likes": int(likes.get(post.id, 0)),
			"saved": int(bookmarks.get(post.id, 0)),
			"opened": int(opened_count),
			"reach": int(reach_count),
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
		page_title="My Knowledge Entries",
		heading="My Entries",
		board_url=url_for("student.knowledge_board"),
		board_label="Knowledge Board",
		create_url=url_for("student.knowledge_board", compose="1"),
		create_label="Create Entry",
		empty_message="You have not shared any knowledge entries yet.",
		item_label="entry",
		edit_endpoint="student.edit_knowledge_post",
		delete_endpoint="student.delete_knowledge_post",
	)


@student_bp.route("/knowledge-post", methods=["GET", "POST"])
@login_required
@role_required("student")
def knowledge_post():
	if request.method == "POST":
		flash("Knowledge posts are now faculty-only. Use Experiences to share your learning.", "warning")
		return redirect(url_for("student.experience_feed"))

	return redirect(url_for("student.knowledge_board"))


@student_bp.route("/knowledge-post/<int:post_id>/react", methods=["POST"])
@login_required
@role_required("student")
def react_knowledge_post(post_id: int):
	reaction_type = (request.form.get("reaction_type") or "").strip().lower()
	if reaction_type not in {"like", "bookmark"}:
		flash("Invalid reaction type.", "danger")
		return redirect(request.referrer or url_for("student.knowledge_board"))

	student = User.query.get(session["user_id"])
	if not student:
		flash("Student account not found.", "danger")
		return redirect(url_for("auth.logout"))

	current_semester = _predict_realtime_semester(student)
	if current_semester is None and student.student_profile and student.student_profile.current_semester:
		try:
			current_semester = int(student.student_profile.current_semester)
		except (TypeError, ValueError):
			current_semester = None

	post = (
		KnowledgePost.query.join(User, KnowledgePost.author_id == User.id)
		.filter(KnowledgePost.id == post_id, KnowledgePost.status == "published", User.role == "faculty")
		.first()
	)
	if not post or not _student_matches_intervention(post, student, current_semester):
		flash("This intervention is not available for your target group.", "warning")
		return redirect(request.referrer or url_for("student.knowledge_board"))

	existing = KnowledgeReaction.query.filter_by(
		post_id=post_id,
		user_id=session["user_id"],
		reaction_type=reaction_type,
	).first()
	if existing:
		db.session.delete(existing)
	else:
		db.session.add(
			KnowledgeReaction(
				post_id=post_id,
				user_id=session["user_id"],
				reaction_type=reaction_type,
			)
		)
	db.session.commit()
	return redirect(request.referrer or url_for("student.knowledge_board"))


@student_bp.route("/experiences")
@login_required
def experience_feed():
	user_id = session["user_id"]
	role = session.get("role", "student")

	selected_category = request.args.get("category", "all").strip()
	selected_tag = request.args.get("tag", "all").strip().lower()
	sort_by = request.args.get("sort", "recent").strip().lower()

	query = StudentExperience.query.filter(StudentExperience.status == "approved")

	if selected_category != "all" and selected_category in EXPERIENCE_CATEGORIES:
		query = query.filter(StudentExperience.category == selected_category)

	if selected_tag != "all" and selected_tag.replace("-", "_") in EXPERIENCE_TAGS:
		tag_val = selected_tag.replace("-", "_")
		query = query.filter(
			or_(
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

	own_pending = []
	if role == "student":
		own_pending = (
			StudentExperience.query.filter(
				StudentExperience.author_id == user_id,
				StudentExperience.status.in_(["pending", "rejected", "request_edit"]),
			)
			.order_by(StudentExperience.created_at.desc())
			.all()
		)

	upvoted_ids: set[int] = set()
	user_upvotes = ExperienceUpvote.query.filter_by(user_id=user_id).all()
	upvoted_ids = {uv.experience_id for uv in user_upvotes}

	for exp in experiences:
		exp.created_at_ist = _utc_to_ist(exp.created_at)
	for exp in own_pending:
		exp.created_at_ist = _utc_to_ist(exp.created_at)

	return render_template(
		"student_experiences.html",
		experiences=experiences,
		own_pending=own_pending,
		upvoted_ids=upvoted_ids,
		selected_category=selected_category,
		selected_tag=selected_tag,
		sort_by=sort_by,
		experience_categories=EXPERIENCE_CATEGORIES,
		experience_tags=EXPERIENCE_TAGS,
		experience_report_categories=EXPERIENCE_REPORT_CATEGORIES,
		labelize_exp_tag=_labelize_exp_tag,
	)


@student_bp.route("/experiences/create", methods=["GET", "POST"])
@login_required
@role_required("student")
def create_experience():
	if request.method == "GET":
		return render_template(
			"student_experience_create.html",
			experience_categories=EXPERIENCE_CATEGORIES,
			experience_tags=EXPERIENCE_TAGS,
			labelize_exp_tag=_labelize_exp_tag,
		)

	title = request.form.get("title", "").strip()
	body = request.form.get("body", "").strip()
	category = request.form.get("category", "").strip()
	tags_raw = request.form.getlist("tags")
	resource_links = request.form.get("resource_links", "").strip()

	if not title or not body or not category:
		flash("Title, body, and category are required.", "danger")
		return redirect(url_for("student.create_experience"))

	if category not in EXPERIENCE_CATEGORIES:
		flash("Please select a valid category.", "danger")
		return redirect(url_for("student.create_experience"))

	valid_tags = [tag for tag in tags_raw if tag in EXPERIENCE_TAGS]
	if not valid_tags:
		flash("Please select at least one tag.", "danger")
		return redirect(url_for("student.create_experience"))

	if len(body) < 100:
		flash("Experience body must be at least 100 characters.", "danger")
		return redirect(url_for("student.create_experience"))

	if len(body) > 10000:
		flash("Experience body must not exceed 10,000 characters.", "danger")
		return redirect(url_for("student.create_experience"))

	sentiment, confidence = analyze_sentiment_with_confidence(body)
	auto_status = _experience_auto_status(sentiment)

	anon_id = _generate_experience_anon_id()
	exp = StudentExperience(
		anon_id=anon_id,
		author_id=session["user_id"],
		title=title,
		body=body,
		category=category,
		tags=",".join(valid_tags),
		resource_links=resource_links if resource_links else None,
		sentiment=sentiment,
		sentiment_confidence=confidence,
		status=auto_status,
	)
	db.session.add(exp)
	db.session.commit()

	if auto_status == "approved":
		flash("Experience shared successfully and is now visible.", "success")
	else:
		flash("Experience submitted and is awaiting admin review.", "warning")
	return redirect(url_for("student.experience_feed"))


@student_bp.route("/experiences/<int:exp_id>/detail")
@login_required
@role_required("student")
def experience_detail(exp_id: int):
	exp = StudentExperience.query.filter_by(id=exp_id).first()
	if not exp:
		return jsonify({"error": "not_found"}), 404

	if exp.status != "approved" and exp.author_id != session["user_id"]:
		return jsonify({"error": "not_found"}), 404

	tags = [_labelize_exp_tag(tag) for tag in (exp.tags or "").split(",") if tag]
	payload = {
		"id": exp.id,
		"anon_id": exp.anon_id,
		"title": exp.title,
		"body": exp.body,
		"category": exp.category,
		"tags": tags,
		"resource_links": _normalize_experience_links(exp.resource_links or ""),
		"sentiment": (exp.sentiment or "neutral").lower(),
		"sentiment_confidence": int(exp.sentiment_confidence or 0),
		"status": exp.status,
		"admin_note": exp.admin_note or "",
		"upvote_count": int(exp.upvote_count or 0),
		"created_at_ist": _utc_to_ist(exp.created_at).strftime("%d %b %Y %I:%M %p") if exp.created_at else "-",
	}
	return jsonify(payload)


@student_bp.route("/experiences/my")
@login_required
@role_required("student")
def my_experiences():
	exps = (
		StudentExperience.query.filter_by(author_id=session["user_id"])
		.order_by(StudentExperience.created_at.desc())
		.all()
	)
	for exp in exps:
		exp.created_at_ist = _utc_to_ist(exp.created_at)
	return render_template(
		"student_my_experiences.html",
		experiences=exps,
	)


@student_bp.route("/experiences/<int:exp_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("student")
def edit_experience(exp_id: int):
	exp = StudentExperience.query.filter_by(id=exp_id, author_id=session["user_id"]).first()
	if not exp:
		flash("Experience not found.", "danger")
		return redirect(url_for("student.my_experiences"))

	if request.method == "GET":
		selected_tags = [tag for tag in (exp.tags or "").split(",") if tag]
		return render_template(
			"student_experience_edit.html",
			exp=exp,
			selected_tags=selected_tags,
			experience_categories=EXPERIENCE_CATEGORIES,
			experience_tags=EXPERIENCE_TAGS,
			labelize_exp_tag=_labelize_exp_tag,
		)

	title = request.form.get("title", "").strip()
	body = request.form.get("body", "").strip()
	category = request.form.get("category", "").strip()
	tags_raw = request.form.getlist("tags")
	resource_links = request.form.get("resource_links", "").strip()

	if not title or not body or not category:
		flash("Title, body, and category are required.", "danger")
		return redirect(url_for("student.edit_experience", exp_id=exp_id))

	if category not in EXPERIENCE_CATEGORIES:
		flash("Please select a valid category.", "danger")
		return redirect(url_for("student.edit_experience", exp_id=exp_id))

	valid_tags = [tag for tag in tags_raw if tag in EXPERIENCE_TAGS]
	if not valid_tags:
		flash("Please select at least one tag.", "danger")
		return redirect(url_for("student.edit_experience", exp_id=exp_id))

	if len(body) < 100:
		flash("Experience body must be at least 100 characters.", "danger")
		return redirect(url_for("student.edit_experience", exp_id=exp_id))

	if len(body) > 10000:
		flash("Experience body must not exceed 10,000 characters.", "danger")
		return redirect(url_for("student.edit_experience", exp_id=exp_id))

	sentiment, confidence = analyze_sentiment_with_confidence(body)
	next_status = _experience_auto_status(sentiment)

	exp.title = title
	exp.body = body
	exp.category = category
	exp.tags = ",".join(valid_tags)
	exp.resource_links = resource_links if resource_links else None
	exp.sentiment = sentiment
	exp.sentiment_confidence = confidence
	exp.status = next_status
	exp.admin_note = None
	db.session.commit()

	if next_status == "approved":
		flash("Experience updated and published successfully.", "success")
	else:
		flash("Experience updated and sent to admin moderation.", "warning")
	return redirect(url_for("student.my_experiences"))


@student_bp.route("/experiences/<int:exp_id>/delete", methods=["POST"])
@login_required
@role_required("student")
def delete_experience(exp_id: int):
	exp = StudentExperience.query.filter_by(id=exp_id, author_id=session["user_id"]).first()
	if not exp:
		flash("Experience not found.", "danger")
		return redirect(url_for("student.my_experiences"))
	db.session.delete(exp)
	db.session.commit()
	flash("Experience deleted.", "success")
	return redirect(url_for("student.my_experiences"))


@student_bp.route("/experiences/<int:exp_id>/upvote", methods=["POST"])
@login_required
def upvote_experience(exp_id: int):
	user_id = session["user_id"]
	exp = StudentExperience.query.filter_by(id=exp_id, status="approved").first()
	if not exp:
		flash("Experience not found.", "danger")
		return redirect(url_for("student.experience_feed"))

	existing = ExperienceUpvote.query.filter_by(experience_id=exp_id, user_id=user_id).first()
	if existing:
		db.session.delete(existing)
		exp.upvote_count = max(0, exp.upvote_count - 1)
	else:
		db.session.add(ExperienceUpvote(experience_id=exp_id, user_id=user_id))
		exp.upvote_count += 1
	db.session.commit()
	return redirect(request.referrer or url_for("student.experience_feed"))


@student_bp.route("/experiences/<int:exp_id>/report", methods=["POST"])
@login_required
def report_experience(exp_id: int):
	user_id = session["user_id"]
	exp = StudentExperience.query.filter_by(id=exp_id, status="approved").first()
	if not exp:
		flash("Experience not found.", "danger")
		return redirect(url_for("student.experience_feed"))

	if ExperienceReport.query.filter_by(experience_id=exp_id, reporter_id=user_id).first():
		flash("You have already reported this experience.", "warning")
		return redirect(request.referrer or url_for("student.experience_feed"))

	report_category = request.form.get("report_category", "").strip()
	reason = request.form.get("reason", "").strip()

	if not report_category or report_category not in EXPERIENCE_REPORT_CATEGORIES:
		flash("Please select a valid report category.", "danger")
		return redirect(request.referrer or url_for("student.experience_feed"))

	if not reason or len(reason) < 20:
		flash("Please provide a reason of at least 20 characters.", "danger")
		return redirect(request.referrer or url_for("student.experience_feed"))

	db.session.add(
		ExperienceReport(
			experience_id=exp_id,
			reporter_id=user_id,
			report_category=report_category,
			reason=reason,
		)
	)
	db.session.commit()
	flash("Report submitted. Thank you for helping keep the community safe.", "success")
	return redirect(request.referrer or url_for("student.experience_feed"))


@student_bp.route("/knowledge-post/<int:post_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("student")
def edit_knowledge_post(post_id: int):
	post = KnowledgePost.query.filter_by(id=post_id, author_id=session["user_id"]).first()
	if not post:
		flash("Knowledge entry not found.", "danger")
		return redirect(url_for("student.my_knowledge_posts"))

	if request.method == "POST":
		title = request.form.get("title", "").strip()
		content = request.form.get("content", "").strip()

		if not title or not content:
			flash("Title and content are required.", "danger")
			return render_template(
				"student_post_edit.html",
				post=post,
				page_title="Edit Knowledge Entry",
				back_url=url_for("student.my_knowledge_posts"),
				item_label="entry",
			)

		post.title = title
		post.content = content
		db.session.commit()
		flash("Knowledge entry updated successfully.", "success")
		return redirect(url_for("student.my_knowledge_posts"))

	return render_template(
		"student_post_edit.html",
		post=post,
		page_title="Edit Knowledge Entry",
		back_url=url_for("student.my_knowledge_posts"),
		item_label="entry",
	)


@student_bp.route("/knowledge-post/<int:post_id>/delete", methods=["POST"])
@login_required
@role_required("student")
def delete_knowledge_post(post_id: int):
	post = KnowledgePost.query.filter_by(id=post_id, author_id=session["user_id"]).first()
	if not post:
		flash("Knowledge entry not found.", "danger")
		return redirect(url_for("student.my_knowledge_posts"))

	db.session.delete(post)
	db.session.commit()
	flash("Knowledge entry deleted successfully.", "success")
	return redirect(url_for("student.my_knowledge_posts"))


@student_bp.route("/profile-settings", methods=["GET", "POST"])
@login_required
@role_required("student")
def profile_settings():
	student = User.query.get(session["user_id"])
	if not student:
		flash("Student account not found.", "danger")
		return redirect(url_for("auth.logout"))

	active_tab = request.args.get("tab", "profile").strip().lower() or "profile"
	if active_tab not in {"profile", "security"}:
		active_tab = "profile"

	current_semester_display = 1
	profile = student.student_profile if student else None
	realtime_semester = _predict_realtime_semester(student)
	if realtime_semester:
		current_semester_display = realtime_semester
	elif profile and profile.current_semester:
		current_semester_display = profile.current_semester

	max_semester = 8
	if profile and profile.max_semester:
		try:
			max_semester = max(1, int(profile.max_semester))
		except (TypeError, ValueError):
			max_semester = 8
	semester_exception_options = [str(value) for value in range(1, max_semester + 1)]

	semester_requests_query = SemesterMismatchRequest.query.filter(
		SemesterMismatchRequest.email == student.email,
		SemesterMismatchRequest.course_code == student.course,
	)
	if student.prn:
		semester_requests_query = semester_requests_query.filter(SemesterMismatchRequest.prn == student.prn)
	semester_requests = semester_requests_query.order_by(SemesterMismatchRequest.created_at.desc()).limit(10).all()
	pending_semester_request = next((item for item in semester_requests if item.status == "pending"), None)

	if request.method == "POST":
		form_type = request.form.get("form_type", "").strip().lower()

		if form_type == "profile":
			full_name = request.form.get("full_name", "").strip()
			section = request.form.get("section", "").strip()
			phone = request.form.get("phone", "").strip()

			if not full_name:
				flash("Full name is required.", "danger")
				return redirect(url_for("student.profile_settings", tab="profile"))

			student.full_name = full_name
			student.section = section or student.section
			student.phone = phone or None
			db.session.commit()
			session["full_name"] = student.full_name
			flash("Profile details updated successfully.", "success")
			return redirect(url_for("student.profile_settings", tab="profile"))

		if form_type == "security_password":
			current_password = request.form.get("current_password", "")
			new_password = request.form.get("new_password", "")
			confirm_new_password = request.form.get("confirm_new_password", "")

			if not student.check_password(current_password):
				flash("Current password is incorrect.", "danger")
				return redirect(url_for("student.profile_settings", tab="security"))

			if not new_password or len(new_password) < 6:
				flash("New password must be at least 6 characters.", "danger")
				return redirect(url_for("student.profile_settings", tab="security"))

			if new_password != confirm_new_password:
				flash("New password and confirm password must match.", "danger")
				return redirect(url_for("student.profile_settings", tab="security"))

			student.set_password(new_password)
			db.session.commit()
			flash("Password updated successfully.", "success")
			return redirect(url_for("student.profile_settings", tab="security"))

		if form_type == "security_question":
			security_question = request.form.get("security_question", "").strip()
			security_answer = request.form.get("security_answer", "").strip()

			if security_question not in SECURITY_QUESTIONS or not security_answer:
				flash("Select a valid security question and provide an answer.", "danger")
				return redirect(url_for("student.profile_settings", tab="security"))

			student.security_question = security_question
			student.set_security_answer(security_answer)
			db.session.commit()
			flash("Security question saved successfully.", "success")
			return redirect(url_for("student.profile_settings", tab="security"))

		if form_type == "semester_exception":
			if not profile:
				flash("Academic profile is required before raising a semester exception request.", "danger")
				return redirect(url_for("student.profile_settings", tab="profile"))

			requested_semester_raw = (request.form.get("requested_semester") or "").strip()
			try:
				requested_semester = int(requested_semester_raw)
			except ValueError:
				flash("Select a valid semester value.", "danger")
				return redirect(url_for("student.profile_settings", tab="profile"))

			if requested_semester < 1 or requested_semester > max_semester:
				flash(f"Semester must be between 1 and {max_semester}.", "danger")
				return redirect(url_for("student.profile_settings", tab="profile"))

			suggested_semester = _predict_realtime_semester(student) or profile.current_semester or requested_semester

			existing_pending = SemesterMismatchRequest.query.filter_by(
				email=student.email,
				prn=student.prn or None,
				course_code=student.course,
				status="pending",
			).first()

			if existing_pending:
				existing_pending.full_name = student.full_name
				existing_pending.section = (student.section or "").strip().upper() or "A"
				existing_pending.batch_start_year = profile.batch_start_year
				existing_pending.batch_end_year = profile.batch_end_year
				existing_pending.admission_month = profile.admission_month
				existing_pending.admission_year = profile.admission_year
				existing_pending.requested_semester = requested_semester
				existing_pending.suggested_semester = suggested_semester
				existing_pending.whitelist_semester = profile.current_semester
				existing_pending.admin_id = None
				existing_pending.admin_note = None
				existing_pending.reviewed_at = None
				db.session.commit()
				flash(
					f"Pending semester exception request #{existing_pending.id} updated.",
					"warning",
				)
			else:
				queued_request = SemesterMismatchRequest(
					email=student.email,
					full_name=student.full_name,
					prn=student.prn or None,
					course_code=student.course,
					section=(student.section or "").strip().upper() or "A",
					batch_start_year=profile.batch_start_year,
					batch_end_year=profile.batch_end_year,
					admission_month=profile.admission_month,
					admission_year=profile.admission_year,
					requested_semester=requested_semester,
					suggested_semester=suggested_semester,
					whitelist_semester=profile.current_semester,
					status="pending",
				)
				db.session.add(queued_request)
				db.session.commit()
				flash(
					f"Semester exception request #{queued_request.id} submitted for admin review.",
					"success",
				)

			return redirect(url_for("student.profile_settings", tab="profile"))

		flash("Unsupported settings action.", "danger")
		return redirect(url_for("student.profile_settings", tab=active_tab))

	return render_template(
		"student_profile_settings.html",
		student=student,
		current_semester_display=current_semester_display,
		max_semester=max_semester,
		semester_exception_options=semester_exception_options,
		pending_semester_request=pending_semester_request,
		semester_requests=semester_requests,
		active_tab=active_tab,
		security_questions=SECURITY_QUESTIONS,
	)


@student_bp.route("/checklist/<int:checklist_id>/toggle", methods=["POST"])
@login_required
@role_required("student")
def toggle_checklist(checklist_id: int):
	checklist = Checklist.query.filter_by(id=checklist_id, student_id=session["user_id"]).first()
	if not checklist:
		flash("Checklist item not found.", "danger")
		return redirect(url_for("student.my_checklists"))

	redirect_target = request.form.get("redirect_target", "student.my_checklists").strip()
	if redirect_target not in {"student.my_checklists", "student.dashboard"}:
		redirect_target = "student.my_checklists"
	status_filter = (request.form.get("status") or "all").strip().lower()
	if status_filter not in {"all", "complete", "partial", "null"}:
		status_filter = "all"
	course_filter = (request.form.get("course") or "all").strip().upper()
	if course_filter not in {"ALL", "MCA", "BCA", "BOTH"}:
		course_filter = "ALL"
	category_filter = (request.form.get("category") or "all").strip()
	semester_filter = _normalize_checklist_target_semester(request.form.get("semester", "all"))
	section_filter = _normalize_checklist_section(request.form.get("section", "all"))
	action = (request.form.get("action") or "toggle_task").strip().lower()

	details = _parse_checklist_description(checklist.description or "")
	state = _checklist_task_state(checklist.title, details, checklist.is_completed)
	if details.get("completion_locked"):
		flash("Checklist is finalized and cannot be changed.", "warning")
		if redirect_target == "student.dashboard":
			return redirect(url_for("student.dashboard"))
		return redirect(
			url_for(
				"student.my_checklists",
				status=status_filter,
				course=course_filter,
				category=category_filter,
				semester=semester_filter,
				section=section_filter,
			)
		)

	if action == "mark_complete":
		details["tasks"] = list(state["tasks"])
		details["completed_tasks"] = list(range(len(state["tasks"])))
		details["completion_locked"] = True
		checklist.description = _serialize_checklist_description(details)
		checklist.is_completed = True
		db.session.commit()
		flash("Checklist marked as completed permanently.", "success")
		if redirect_target == "student.dashboard":
			return redirect(url_for("student.dashboard"))
		return redirect(
			url_for(
				"student.my_checklists",
				status=status_filter,
				course=course_filter,
				category=category_filter,
				semester=semester_filter,
				section=section_filter,
			)
		)

	current = set(state["completed_indexes"])
	task_index_raw = (request.form.get("task_index") or "").strip()

	if task_index_raw:
		try:
			task_index = int(task_index_raw)
		except ValueError:
			flash("Invalid checklist task selection.", "danger")
			if redirect_target == "student.dashboard":
				return redirect(url_for("student.dashboard"))
			return redirect(
				url_for(
					"student.my_checklists",
					status=status_filter,
					course=course_filter,
					category=category_filter,
					semester=semester_filter,
					section=section_filter,
				)
			)

		if task_index < 0 or task_index >= len(state["tasks"]):
			flash("Checklist task index is out of range.", "danger")
			if redirect_target == "student.dashboard":
				return redirect(url_for("student.dashboard"))
			return redirect(
				url_for(
					"student.my_checklists",
					status=status_filter,
					course=course_filter,
					category=category_filter,
					semester=semester_filter,
					section=section_filter,
				)
			)

		if task_index in current:
			current.remove(task_index)
		else:
			current.add(task_index)
	else:
		if state["state"] == "complete":
			current = set()
		else:
			current = set(range(len(state["tasks"])))

	updated_completed = sorted(current)
	details["tasks"] = list(state["tasks"])
	details["completed_tasks"] = updated_completed
	checklist.description = _serialize_checklist_description(details)
	checklist.is_completed = bool(state["tasks"]) and len(updated_completed) == len(state["tasks"])
	db.session.commit()

	if redirect_target == "student.dashboard":
		return redirect(url_for("student.dashboard"))
	return redirect(
		url_for(
			"student.my_checklists",
			status=status_filter,
			course=course_filter,
			category=category_filter,
			semester=semester_filter,
			section=section_filter,
		)
	)


@student_bp.route("/checklists")
@login_required
@role_required("student")
def my_checklists():
	status = request.args.get("status", "all").strip().lower()
	if status not in {"all", "complete", "partial", "null"}:
		status = "all"
	student = User.query.get(session["user_id"])
	student_course = (student.course or "").strip().upper() if student else ""
	if student_course not in {"MCA", "BCA"}:
		student_course = "MCA"
	course_options = [student_course, "BOTH"]
	selected_course = (request.args.get("course") or student_course).strip().upper()
	if selected_course not in course_options:
		selected_course = student_course
	selected_category = (request.args.get("category") or "all").strip()
	selected_semester = _normalize_checklist_target_semester(request.args.get("semester", "all"))
	selected_section = _normalize_checklist_section(request.args.get("section", "all"))
	all_items = Checklist.query.filter_by(student_id=session["user_id"]).order_by(Checklist.created_at.desc()).all()

	category_pool = set(EXPERIENCE_CATEGORIES)
	for item in all_items:
		details = _parse_checklist_description(item.description or "")
		if details.get("category"):
			category_pool.add(details["category"])
	category_options = sorted(category_pool)
	if selected_category != "all" and selected_category not in category_options:
		selected_category = "all"

	total_task_units = 0
	completed_task_units = 0
	completed_count = 0
	partial_count = 0
	null_count = 0
	overdue_count = 0

	checklist_cards = []
	for item in all_items:
		days_open = max((datetime.utcnow() - item.created_at).days, 0)
		details = _parse_checklist_description(item.description or "")
		target_course = details.get("target_course", "BOTH")
		target_semester = details.get("target_semester", "all")
		target_section = details.get("target_section", "all")
		if selected_course == "BOTH" and target_course != "BOTH":
			continue
		if selected_course in {"MCA", "BCA"} and target_course not in {selected_course, "BOTH"}:
			continue
		if selected_category != "all" and details.get("category", "General") != selected_category:
			continue
		if selected_semester != "all" and target_semester not in {"all", selected_semester}:
			continue
		if selected_section != "all" and target_section not in {"all", selected_section}:
			continue
		state = _checklist_task_state(item.title, details, item.is_completed)
		due_state = _checklist_due_state(details["due_date"], state["state"])
		due_label = _checklist_due_label(details["due_date"], due_state)

		total_task_units += state["total"]
		completed_task_units += state["completed"]
		if state["state"] == "complete":
			completed_count += 1
		elif state["state"] == "partial":
			partial_count += 1
		else:
			null_count += 1
		if due_state == "overdue":
			overdue_count += 1

		task_rows = []
		completed_indexes = set(state["completed_indexes"])
		for idx, task_text in enumerate(state["tasks"]):
			task_rows.append(
				{
					"index": idx,
					"text": task_text,
					"is_done": idx in completed_indexes,
				}
			)

		checklist_cards.append(
			{
				"item": item,
				"description": details.get("description", ""),
				"priority": details["priority"],
				"category": details["category"],
				"subject": details["subject"],
				"state": state["state"],
				"completed_tasks": state["completed"],
				"total_tasks": state["total"],
				"task_rows": task_rows,
				"attachment": details.get("attachment"),
				"target_course": target_course,
				"target_semester": target_semester,
				"target_section": target_section,
				"completion_locked": bool(details.get("completion_locked", False)),
				"can_mark_complete": not bool(details.get("completion_locked", False)),
				"due_label": due_label,
				"due_state": due_state,
				"days_open": days_open,
			}
		)

	semester_options = sorted(
		{
			entry["target_semester"]
			for entry in checklist_cards
			if entry["target_semester"] != "all"
		},
		key=lambda value: int(value) if str(value).isdigit() else value,
	)
	section_options = sorted(
		{
			entry["target_section"]
			for entry in checklist_cards
			if entry["target_section"] != "all"
		}
	)
	total_count = len(checklist_cards)

	if status != "all":
		checklist_cards = [entry for entry in checklist_cards if entry["state"] == status]

	pending_count = partial_count + null_count
	progress_percent = int((completed_task_units / max(total_task_units, 1)) * 100) if total_task_units else 0

	return render_template(
		"student_checklists.html",
		checklist_cards=checklist_cards,
		status=status,
		selected_course=selected_course,
		selected_category=selected_category,
		selected_semester=selected_semester,
		selected_section=selected_section,
		course_options=course_options,
		category_options=category_options,
		semester_options=semester_options,
		section_options=section_options,
		total_count=total_count,
		pending_count=pending_count,
		completed_count=completed_count,
		partial_count=partial_count,
		null_count=null_count,
		overdue_count=overdue_count,
		progress_percent=progress_percent,
	)


@student_bp.route("/submit-feedback-page")
@login_required
@role_required("student")
def submit_feedback_page():
	student = User.query.get(session["user_id"])
	student_course = (student.course if student else "MCA").strip().upper()
	student_section = (student.section if student else "").strip().upper()
	current_semester_display = _predict_realtime_semester(student) or 1
	ist_now_iso = _ist_now().strftime("%Y-%m-%dT%H:%M")

	subject_catalog = []
	subject_faculty_map = {}
	faculty_map = {}

	offerings = []
	if student and student_section:
		offerings = (
			SubjectOffering.query.filter_by(
				course_code=student_course,
				semester_no=current_semester_display,
				section=student_section,
				is_active=True,
			)
			.order_by(SubjectOffering.subject_name.asc())
			.all()
		)

	if offerings:
		for offering in offerings:
			subject_catalog.append(
				{
					"course_code": offering.subject_code,
					"subject_name": offering.subject_name,
					"semester": str(offering.semester_no),
					"subject_type": "",
				}
			)

			assignment = find_assignment(
				course_code=student_course,
				semester_no=current_semester_display,
				section=student_section,
				subject_code=offering.subject_code,
			)
			faculty_entries = []
			if assignment and assignment.get("faculty_id"):
				entry = {
					"id": assignment.get("faculty_id", "").strip().upper(),
					"full_name": assignment.get("faculty_name", "") or assignment.get("faculty_id", ""),
					"faculty_id": assignment.get("faculty_id", "").strip().upper(),
				}
				faculty_entries.append(entry)
				faculty_map[entry["id"]] = entry
			subject_faculty_map[offering.subject_code] = faculty_entries

		subject_catalog.sort(key=lambda item: item["subject_name"])
		faculty_list = list(faculty_map.values())
	else:
		subject_catalog = [
			item
			for item in _load_subject_catalog(student_course)
			if str(item.get("semester") or "").strip() == str(current_semester_display)
		]
		slot_assignments = list_assignments_for_slot(student_course, current_semester_display, student_section or "A")
		for assignment in slot_assignments:
			entry = {
				"id": assignment.get("faculty_id", "").strip().upper(),
				"full_name": assignment.get("faculty_name", "") or assignment.get("faculty_id", ""),
				"faculty_id": assignment.get("faculty_id", "").strip().upper(),
			}
			faculty_map[entry["id"]] = entry
			subject_faculty_map.setdefault(assignment.get("subject_code", "").strip().upper(), []).append(entry)
		faculty_list = list(faculty_map.values())

	faculty_options = [
		{
			"id": faculty.get("id"),
			"full_name": faculty.get("full_name"),
			"faculty_id": faculty.get("faculty_id") or "",
		}
		for faculty in faculty_list
	]

	success_flag = request.args.get("success") == "1"
	success_payload = None
	if success_flag:
		success_payload = {
			"sentiment": request.args.get("sentiment", "neutral").strip().lower() or "neutral",
			"status": request.args.get("status", "under_review").strip().lower() or "under_review",
			"delivery": request.args.get("delivery", "faculty").strip().lower() or "faculty",
			"confidence": request.args.get("confidence", "72").strip() or "72",
		}
	return render_template(
		"submit_feedback.html",
		faculty_list=faculty_list,
		faculty_options=faculty_options,
		subject_catalog=subject_catalog,
		subject_faculty_map=subject_faculty_map,
		current_semester_display=current_semester_display,
		ist_now_iso=ist_now_iso,
		fixed_feedback_tags=FIXED_FEEDBACK_TAGS,
		tag_labelize=_labelize_tag,
		student=student,
		success_payload=success_payload,
	)
