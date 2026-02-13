import csv
import random
from functools import wraps
from pathlib import Path

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from models import User, db


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

	with whitelist_path.open("r", encoding="utf-8") as file:
		lines = file.readlines()
		if not lines:
			return []

	delimiter = "\t" if "\t" in lines[0] else ","
	rows = []
	with whitelist_path.open("r", encoding="utf-8") as file:
		reader = csv.DictReader(file, delimiter=delimiter)
		for row in reader:
			rows.append({(k or "").strip(): (v or "").strip() for k, v in row.items()})
	return rows


def _is_truthy(value: str) -> bool:
	return value.strip().lower() in {"1", "true", "yes", "y", "allowed"}


def _validate_whitelist(
	*,
	role: str,
	email: str,
	full_name: str,
	prn: str,
	faculty_id: str,
	section: str,
	course: str,
):
	rows = _read_whitelist()
	if not rows:
		return False

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
		if role == "faculty":
			if row.get("faculty_id") and row["faculty_id"].lower() != faculty_id.lower():
				continue
		return True
	return False


def _generate_unique_user_code() -> str:
	for _ in range(1200):
		code = f"CAIMCA{random.randint(100, 999)}"
		if not User.query.filter_by(unique_user_code=code).first():
			return code
	raise RuntimeError("Unable to generate unique user code.")


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
	if request.method == "POST":
		full_name = request.form.get("full_name", "").strip()
		email = request.form.get("email", "").strip().lower()
		role = request.form.get("role", "").strip().lower()
		prn = request.form.get("prn", "").strip()
		faculty_id = request.form.get("faculty_id", "").strip()
		section = request.form.get("section", "").strip()
		course = "MCA"
		password = request.form.get("password", "")
		confirm_password = request.form.get("confirm_password", "")
		security_question = request.form.get("security_question", "").strip()
		security_answer = request.form.get("security_answer", "").strip()

		if not full_name or not email or role not in {"student", "faculty", "admin"}:
			flash("Full name, email, and role are required.", "danger")
			return render_template("register.html", security_questions=SECURITY_QUESTIONS)

		if role == "admin":
			flash("Admin accounts are manual only. Contact system owner.", "danger")
			return render_template("register.html", security_questions=SECURITY_QUESTIONS)

		if role == "student" and (not prn or not section):
			flash("Student registration requires PRN and section.", "danger")
			return render_template("register.html", security_questions=SECURITY_QUESTIONS)

		if role == "faculty" and not faculty_id:
			flash("Faculty registration requires faculty ID.", "danger")
			return render_template("register.html", security_questions=SECURITY_QUESTIONS)

		if not password or not confirm_password or password != confirm_password:
			flash("Password and confirm password must match.", "danger")
			return render_template("register.html", security_questions=SECURITY_QUESTIONS)

		if security_question not in SECURITY_QUESTIONS or not security_answer:
			flash("Security question and answer are required.", "danger")
			return render_template("register.html", security_questions=SECURITY_QUESTIONS)

		if User.query.filter_by(email=email).first():
			flash("Email already registered.", "danger")
			return render_template("register.html", security_questions=SECURITY_QUESTIONS)

		if not _validate_whitelist(
			role=role,
			email=email,
			full_name=full_name,
			prn=prn,
			faculty_id=faculty_id,
			section=section,
			course=course,
		):
			flash("Registration denied: details not found or not allowed in whitelist.", "danger")
			return render_template("register.html", security_questions=SECURITY_QUESTIONS)

		user = User(
			unique_user_code=_generate_unique_user_code(),
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
		db.session.commit()

		flash("Registration successful. Please login.", "success")
		return redirect(url_for("auth.login"))

	return render_template("register.html", security_questions=SECURITY_QUESTIONS)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
	if request.method == "POST":
		email = request.form.get("email", "").strip().lower()
		password = request.form.get("password", "")

		user = User.query.filter_by(email=email, is_active=True).first()
		if not user or not user.check_password(password):
			flash("Invalid email or password.", "danger")
			return render_template("login.html")

		session["user_id"] = user.id
		session["role"] = user.role
		session["full_name"] = user.full_name

		if user.role == "student":
			return redirect(url_for("student.dashboard"))
		if user.role == "faculty":
			return redirect(url_for("faculty.dashboard"))
		if user.role == "admin":
			return redirect(url_for("admin.dashboard"))
		return redirect(url_for("auth.logout"))

	return render_template("login.html")


@auth_bp.route("/logout")
def logout():
	session.clear()
	flash("Logged out successfully.", "success")
	return redirect(url_for("auth.login"))


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
