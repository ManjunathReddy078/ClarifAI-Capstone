import csv
import random
from datetime import date, datetime
from functools import wraps
from pathlib import Path

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for

from models import CourseConfig, SemesterMismatchRequest, StudentAcademicProfile, User, db
from pending_feedback_service import release_held_feedback_for_faculty


auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

SECURITY_QUESTIONS = [
	"What is your birth city?",
	"What was your first school name?",
	"What is your favorite teacher's name?",
	"What is your mother's maiden name?",
]


def login_required(view_func):
	@wraps(view_func)
	def wrapper(*args, **kwargs):
		if not session.get("user_id"):
			flash("Please log in first.", "warning")
			return redirect(url_for("auth.login"))
		return view_func(*args, **kwargs)

	return wrapper


def role_required(*allowed_roles):
	def decorator(view_func):
		@wraps(view_func)
		def wrapper(*args, **kwargs):
			if not session.get("user_id"):
				flash("Please log in first.", "warning")
				return redirect(url_for("auth.login"))
			if session.get("role") not in allowed_roles:
				flash("You are not authorized to access this page.", "danger")
				return redirect(url_for("auth.login"))
			return view_func(*args, **kwargs)

		return wrapper

	return decorator


def _read_whitelist():
	whitelist_path = Path(__file__).resolve().parents[1] / "data" / "whitelist.csv"
	if not whitelist_path.exists():
		return []

	with whitelist_path.open("r", encoding="utf-8-sig") as file:
		lines = file.readlines()
		if not lines:
			return []

	delimiter = "\t" if "\t" in lines[0] else ","
	rows = []
	with whitelist_path.open("r", encoding="utf-8-sig") as file:
		reader = csv.DictReader(file, delimiter=delimiter)
		for row in reader:
			rows.append({(k or "").strip(): (v or "").strip() for k, v in row.items()})
	return rows


def _is_truthy(value: str) -> bool:
	return value.strip().lower() in {"1", "true", "yes", "y", "allowed"}


def _course_meta_map():
	rows = CourseConfig.query.filter_by(is_active=True).all()
	meta = {
		row.course_code.upper(): {
			"duration_years": row.duration_years,
			"total_semesters": row.total_semesters,
			"semesters_per_year": row.semesters_per_year,
		}
		for row in rows
	}

	if "MCA" not in meta:
		meta["MCA"] = {"duration_years": 2, "total_semesters": 4, "semesters_per_year": 2}
	if "BCA" not in meta:
		meta["BCA"] = {"duration_years": 3, "total_semesters": 6, "semesters_per_year": 2}
	return meta


def _register_context():
	return {
		"security_questions": SECURITY_QUESTIONS,
		"course_meta": _course_meta_map(),
	}


def _find_whitelist_row(
	*,
	role: str,
	email: str,
	full_name: str,
	prn: str,
	faculty_id: str,
	section: str,
	course: str,
	batch_start_year: int | None = None,
	batch_end_year: int | None = None,
	current_semester: int | None = None,
	enforce_batch_years: bool = True,
	enforce_current_semester: bool = True,
):
	rows = _read_whitelist()
	if not rows:
		return None

	for row in rows:
		if row.get("role", "").lower() != role.lower():
			continue
		if row.get("email", "").lower() != email.lower():
			continue
		if row.get("allowed") and not _is_truthy(row.get("allowed", "")):
			continue
		if row.get("course") and row["course"].upper() != course.upper():
			continue
		if row.get("full_name") and row["full_name"].lower() != full_name.lower():
			continue

		if role == "student":
			if row.get("prn") and row["prn"].lower() != prn.lower():
				continue
			if row.get("section") and row["section"].lower() != section.lower():
				continue
			if (
				enforce_batch_years
				and row.get("batch_start_year")
				and str(batch_start_year or "") != row["batch_start_year"]
			):
				continue
			if (
				enforce_batch_years
				and row.get("batch_end_year")
				and str(batch_end_year or "") != row["batch_end_year"]
			):
				continue
			if (
				enforce_current_semester
				and row.get("current_semester")
				and str(current_semester or "") != row["current_semester"]
			):
				continue
		if role == "faculty":
			if row.get("faculty_id") and row["faculty_id"].lower() != faculty_id.lower():
				continue
		return row
	return None


def _safe_int(value: str | None) -> int | None:
	try:
		return int((value or "").strip())
	except (TypeError, ValueError, AttributeError):
		return None


def _queue_semester_mismatch_request(
	*,
	email: str,
	full_name: str,
	prn: str,
	course: str,
	section: str,
	batch_start_year: int,
	batch_end_year: int,
	admission_month: int,
	admission_year: int,
	requested_semester: int,
	suggested_semester: int,
	whitelist_semester: int | None,
):
	pending = SemesterMismatchRequest.query.filter_by(
		email=email,
		prn=prn or None,
		course_code=course,
		status="pending",
	).first()

	if pending:
		pending.full_name = full_name
		pending.section = section
		pending.batch_start_year = batch_start_year
		pending.batch_end_year = batch_end_year
		pending.admission_month = admission_month
		pending.admission_year = admission_year
		pending.requested_semester = requested_semester
		pending.suggested_semester = suggested_semester
		pending.whitelist_semester = whitelist_semester
		pending.admin_note = None
		pending.admin_id = None
		pending.reviewed_at = None
		return pending, False

	queued = SemesterMismatchRequest(
		email=email,
		full_name=full_name,
		prn=prn or None,
		course_code=course,
		section=section,
		batch_start_year=batch_start_year,
		batch_end_year=batch_end_year,
		admission_month=admission_month,
		admission_year=admission_year,
		requested_semester=requested_semester,
		suggested_semester=suggested_semester,
		whitelist_semester=whitelist_semester,
		status="pending",
	)
	db.session.add(queued)
	return queued, True


def _validate_whitelist(
	*,
	role: str,
	email: str,
	full_name: str,
	prn: str,
	faculty_id: str,
	section: str,
	course: str,
	batch_start_year: int | None = None,
	batch_end_year: int | None = None,
	current_semester: int | None = None,
):
	return _find_whitelist_row(
		role=role,
		email=email,
		full_name=full_name,
		prn=prn,
		faculty_id=faculty_id,
		section=section,
		course=course,
		batch_start_year=batch_start_year,
		batch_end_year=batch_end_year,
		current_semester=current_semester,
	) is not None


def _suggest_current_semester(
	*,
	admission_month: int,
	admission_year: int,
	total_semesters: int,
	semesters_per_year: int,
) -> int:
	today = date.today()
	if (admission_year, admission_month) > (today.year, today.month):
		return 1

	months_elapsed = (today.year - admission_year) * 12 + (today.month - admission_month)
	months_elapsed = max(0, months_elapsed)
	months_per_semester = max(1, int(round(12 / max(1, semesters_per_year))))
	predicted_semester = 1 + (months_elapsed // months_per_semester)
	return max(1, min(total_semesters, predicted_semester))


def _resolve_user_code_prefix(role: str, course: str) -> str:
	role_value = (role or "").strip().lower()
	course_value = (course or "").strip().upper()

	if role_value == "admin":
		return "CAIA"
	if role_value == "faculty":
		return "CAICAF"
	if role_value == "student" and course_value == "BCA":
		return "CAIBCAS"
	return "CAIMCAS"


def _generate_unique_user_code(role: str, course: str) -> str:
	prefix = _resolve_user_code_prefix(role, course)
	for _ in range(20000):
		code = f"{prefix}{random.randint(0, 9999):04d}"
		if not User.query.filter_by(unique_user_code=code).first():
			return code
	raise RuntimeError("Unable to generate unique user code.")


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
	if not current_app.config.get("ALLOW_SELF_REGISTER", False):
		flash("Self-registration is disabled. Please use credentials issued by the admin team.", "warning")
		return redirect(url_for("auth.login"))

	if request.method == "POST":
		full_name = request.form.get("full_name", "").strip()
		email = request.form.get("email", "").strip().lower()
		role = request.form.get("role", "").strip().lower()
		prn = request.form.get("prn", "").strip()
		faculty_id = request.form.get("faculty_id", "").strip()
		section = request.form.get("section", "").strip()
		course = request.form.get("course", "MCA").strip().upper() or "MCA"
		batch_start_year_raw = request.form.get("batch_start_year", "").strip()
		batch_end_year_raw = request.form.get("batch_end_year", "").strip()
		admission_month_raw = request.form.get("admission_month", "").strip()
		admission_year_raw = request.form.get("admission_year", "").strip()
		current_semester_raw = request.form.get("current_semester", "1").strip() or "1"
		password = request.form.get("password", "")
		confirm_password = request.form.get("confirm_password", "")
		security_question = request.form.get("security_question", "").strip()
		security_answer = request.form.get("security_answer", "").strip()
		course_meta = _course_meta_map()

		batch_start_year = None
		batch_end_year = None
		admission_month = None
		admission_year = None
		current_semester = 1

		if not full_name or not email or role not in {"student", "faculty", "admin"}:
			flash("Full name, email, and role are required.", "danger")
			return render_template("register.html", **_register_context())

		if course not in {"MCA", "BCA"}:
			flash("Course must be MCA or BCA.", "danger")
			return render_template("register.html", **_register_context())

		selected_course_meta = course_meta.get(course, {})
		expected_duration_years = int(selected_course_meta.get("duration_years", 3 if course == "BCA" else 2))
		total_semesters = int(selected_course_meta.get("total_semesters", 6 if course == "BCA" else 4))
		semesters_per_year = int(selected_course_meta.get("semesters_per_year", 2))

		if role == "admin":
			flash("Admin accounts are manual only. Contact system owner.", "danger")
			return render_template("register.html", **_register_context())

		if role == "student" and (not prn or not section):
			flash("Student registration requires PRN and section.", "danger")
			return render_template("register.html", **_register_context())

		if role == "student":
			if not batch_start_year_raw or not batch_end_year_raw or not admission_month_raw or not admission_year_raw:
				flash("Academic duration and admission month/year are required for students.", "danger")
				return render_template("register.html", **_register_context())

			try:
				batch_start_year = int(batch_start_year_raw)
				batch_end_year = int(batch_end_year_raw)
				admission_month = int(admission_month_raw)
				admission_year = int(admission_year_raw)
				current_semester = int(current_semester_raw)
			except ValueError:
				flash("Academic year, admission month/year, and semester must be valid numbers.", "danger")
				return render_template("register.html", **_register_context())

			if not (1 <= admission_month <= 12):
				flash("Admission month must be between 1 and 12.", "danger")
				return render_template("register.html", **_register_context())

			today = date.today()
			if (admission_year, admission_month) > (today.year, today.month):
				flash("Admission month/year cannot be in the future.", "danger")
				return render_template("register.html", **_register_context())

			if batch_end_year - batch_start_year != expected_duration_years:
				flash(
					f"Academic duration for {course} must be {expected_duration_years} years (example: 2025-{2025 + expected_duration_years}).",
					"danger",
				)
				return render_template("register.html", **_register_context())

			if not (batch_start_year <= admission_year < batch_end_year):
				flash("Admission year must be within the selected academic duration.", "danger")
				return render_template("register.html", **_register_context())

			if current_semester < 1 or current_semester > total_semesters:
				flash(f"Semester must be between 1 and {total_semesters} for {course}.", "danger")
				return render_template("register.html", **_register_context())

		if role == "faculty" and not faculty_id:
			flash("Faculty registration requires faculty ID.", "danger")
			return render_template("register.html", **_register_context())

		if not password or not confirm_password or password != confirm_password:
			flash("Password and confirm password must match.", "danger")
			return render_template("register.html", **_register_context())

		if security_question not in SECURITY_QUESTIONS or not security_answer:
			flash("Security question and answer are required.", "danger")
			return render_template("register.html", **_register_context())

		existing_user = User.query.filter_by(email=email).first()
		if existing_user:
			if role == "faculty":
				if existing_user.role != "faculty":
					flash("Email already registered with a different role.", "danger")
					return render_template("register.html", **_register_context())
				if existing_user.faculty_id and existing_user.faculty_id.strip().upper() != faculty_id.strip().upper():
					flash("Faculty ID does not match the existing account for this email.", "danger")
					return render_template("register.html", **_register_context())
				if existing_user.check_password(password):
					flash("Faculty account already exists. Please login.", "warning")
					return redirect(url_for("auth.login", role="faculty"))

				existing_user.full_name = full_name
				existing_user.faculty_id = faculty_id
				existing_user.course = course
				existing_user.is_active = True
				existing_user.security_question = security_question
				existing_user.set_password(password)
				existing_user.set_security_answer(security_answer)
				db.session.commit()

				released = release_held_feedback_for_faculty(existing_user)
				if released:
					flash(f"Faculty account activated. {released} queued feedback item(s) released to your dashboard.", "success")
				else:
					flash("Faculty account activated. Please login.", "success")
				return redirect(url_for("auth.login", role="faculty"))

			flash("Email already registered.", "danger")
			return render_template("register.html", **_register_context())

		whitelist_row = _find_whitelist_row(
			role=role,
			email=email,
			full_name=full_name,
			prn=prn,
			faculty_id=faculty_id,
			section=section,
			course=course,
			batch_start_year=batch_start_year,
			batch_end_year=batch_end_year,
			current_semester=current_semester,
		)

		if role == "student":
			suggested_semester = _suggest_current_semester(
				admission_month=admission_month,
				admission_year=admission_year,
				total_semesters=total_semesters,
				semesters_per_year=semesters_per_year,
			)

			if not whitelist_row:
				base_whitelist_row = _find_whitelist_row(
					role=role,
					email=email,
					full_name=full_name,
					prn=prn,
					faculty_id=faculty_id,
					section=section,
					course=course,
					batch_start_year=batch_start_year,
					batch_end_year=batch_end_year,
					current_semester=current_semester,
					enforce_batch_years=True,
					enforce_current_semester=False,
				)
				if not base_whitelist_row:
					flash("Registration denied: details not found or not allowed in whitelist.", "danger")
					return render_template("register.html", **_register_context())

				whitelist_semester = _safe_int(base_whitelist_row.get("current_semester", ""))
				if whitelist_semester is None:
					flash(
						"Registration denied: whitelist semester is missing for your record. Please contact admin.",
						"danger",
					)
					return render_template("register.html", **_register_context())

				queued_request, created = _queue_semester_mismatch_request(
					email=email,
					full_name=full_name,
					prn=prn,
					course=course,
					section=section,
					batch_start_year=batch_start_year,
					batch_end_year=batch_end_year,
					admission_month=admission_month,
					admission_year=admission_year,
					requested_semester=current_semester,
					suggested_semester=suggested_semester,
					whitelist_semester=whitelist_semester,
				)
				db.session.commit()
				if created:
					flash(
						f"Semester mismatch submitted for admin verification (request #{queued_request.id}). Please wait for approval.",
						"warning",
					)
				else:
					flash(
						f"Your pending semester mismatch request #{queued_request.id} has been updated and is awaiting admin review.",
						"warning",
					)
				return render_template("register.html", **_register_context())

			whitelist_semester = (whitelist_row.get("current_semester") or "").strip()
			if not whitelist_semester and abs(current_semester - suggested_semester) > 1:
				flash(
					f"Selected semester looks inconsistent with admission month/year. Suggested semester is {suggested_semester}. "
					"Please correct it or ask admin to whitelist your exact semester.",
					"danger",
				)
				return render_template("register.html", **_register_context())

		if not whitelist_row:
			flash("Registration denied: details not found or not allowed in whitelist.", "danger")
			return render_template("register.html", **_register_context())

		user = User(
			unique_user_code=_generate_unique_user_code(role, course),
			full_name=full_name,
			email=email,
			role=role,
			prn=prn if role == "student" else None,
			faculty_id=faculty_id if role == "faculty" else None,
			section=section if role == "student" else None,
			course=course,
			security_question=security_question,
			security_answer_hash="",
		)
		user.set_password(password)
		user.set_security_answer(security_answer)

		db.session.add(user)

		if role == "student":
			db.session.flush()
			profile = StudentAcademicProfile(
				user_id=user.id,
				course_code=course,
				batch_start_year=batch_start_year,
				batch_end_year=batch_end_year,
				admission_month=admission_month,
				admission_year=admission_year,
				current_semester=current_semester,
				max_semester=total_semesters,
				progression_mode="auto",
				lifecycle_status="active",
			)
			db.session.add(profile)

		db.session.commit()

		if role == "faculty":
			released = release_held_feedback_for_faculty(user)
			if released:
				flash(f"Registration successful. {released} queued feedback item(s) were released to your dashboard.", "success")
				return redirect(url_for("auth.login"))

		flash("Registration successful. Please login.", "success")
		return redirect(url_for("auth.login"))

	return render_template("register.html", **_register_context())


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
	selected_role = (request.args.get("role") or request.form.get("role_hint") or "").strip().lower()
	if selected_role not in {"student", "faculty", "admin"}:
		selected_role = None

	if request.method == "POST":
		email = request.form.get("email", "").strip().lower()
		password = request.form.get("password", "")
		remember_me = request.form.get("remember_me") in {"on", "true", "1", "yes"}

		user = User.query.filter_by(email=email, is_active=True).first()
		if not user or not user.check_password(password):
			flash("Invalid email or password.", "danger")
			return render_template(
				"login.html",
				selected_role=selected_role,
				remember_me_days=current_app.config.get("REMEMBER_ME_DAYS", 3),
				allow_self_register=current_app.config.get("ALLOW_SELF_REGISTER", False),
			)

		now_utc = datetime.utcnow()
		if not user.first_login_at:
			user.first_login_at = now_utc
		user.last_login_at = now_utc
		db.session.commit()

		session["user_id"] = user.id
		session["role"] = user.role
		session["full_name"] = user.full_name
		session["email"] = user.email
		session["user_code"] = user.unique_user_code
		session.permanent = remember_me
		session.modified = True

		if user.role == "student":
			return redirect(url_for("student.dashboard"))
		if user.role == "faculty":
			released = release_held_feedback_for_faculty(user)
			if released:
				flash(f"{released} queued feedback item(s) moved to your faculty reviews.", "success")
			return redirect(url_for("faculty.dashboard"))
		if user.role == "admin":
			return redirect(url_for("admin.dashboard"))
		return redirect(url_for("auth.logout"))

	return render_template(
		"login.html",
		selected_role=selected_role,
		remember_me_days=current_app.config.get("REMEMBER_ME_DAYS", 3),
		allow_self_register=current_app.config.get("ALLOW_SELF_REGISTER", False),
	)


@auth_bp.route("/logout")
def logout():
	session.clear()
	flash("Logged out successfully.", "success")
	return redirect(url_for("auth.login"))


@auth_bp.route("/session-expired")
def session_expired():
	return render_template("session_expired.html")


@auth_bp.route("/reset/request", methods=["GET", "POST"])
def reset_request():
	if request.method == "POST":
		email = request.form.get("email", "").strip().lower()
		user = User.query.filter_by(email=email).first()
		if not user:
			flash("Email not found. Contact admin.", "danger")
			return render_template("reset_request.html")

		session["reset_user_id"] = user.id
		session["reset_verified"] = False
		return redirect(url_for("auth.reset_verify"))

	return render_template("reset_request.html")


@auth_bp.route("/reset/verify", methods=["GET", "POST"])
def reset_verify():
	reset_user_id = session.get("reset_user_id")
	if not reset_user_id:
		flash("Start password reset from email step.", "warning")
		return redirect(url_for("auth.reset_request"))

	user = User.query.get(reset_user_id)
	if not user:
		session.pop("reset_user_id", None)
		flash("Invalid reset session. Try again.", "danger")
		return redirect(url_for("auth.reset_request"))

	if request.method == "POST":
		security_answer = request.form.get("security_answer", "")
		if user.check_security_answer(security_answer):
			session["reset_verified"] = True
			return redirect(url_for("auth.reset_password"))
		flash("Security answer mismatch. Please contact admin.", "danger")

	return render_template("reset_verify.html", user=user)


@auth_bp.route("/reset/password", methods=["GET", "POST"])
def reset_password():
	reset_user_id = session.get("reset_user_id")
	reset_verified = session.get("reset_verified")
	if not reset_user_id or not reset_verified:
		flash("Unauthorized password reset flow.", "danger")
		return redirect(url_for("auth.reset_request"))

	user = User.query.get(reset_user_id)
	if not user:
		flash("Invalid reset user.", "danger")
		return redirect(url_for("auth.reset_request"))

	if request.method == "POST":
		password = request.form.get("password", "")
		confirm_password = request.form.get("confirm_password", "")
		if not password or password != confirm_password:
			flash("Password confirmation mismatch.", "danger")
			return render_template("reset_password.html")

		user.set_password(password)
		db.session.commit()

		session.pop("reset_user_id", None)
		session.pop("reset_verified", None)
		flash("Password reset successful. Please login.", "success")
		return redirect(url_for("auth.login"))

	return render_template("reset_password.html")
