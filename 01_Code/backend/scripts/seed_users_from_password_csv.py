import csv
import secrets
import string
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app import create_app
from assignment_sync_service import sync_preset_assignments_to_db
from models import (
    Checklist,
    ExperienceReport,
    ExperienceUpvote,
    FacultyAssignment,
    Feedback,
    KnowledgePost,
    LifecycleEvent,
    ModerationLog,
    PendingFacultyFeedback,
    SemesterMismatchRequest,
    StudentAcademicProfile,
    StudentExperience,
    User,
    db,
)


CSV_PATH = BASE_DIR.parents[1] / "whitelist passwords.csv"
DEFAULT_SECURITY_QUESTION = "What is your birth city?"
DEFAULT_SECURITY_ANSWER = "changeme"


def _is_truthy(value: str) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "y", "allowed", "active"}


def _safe_int(value, default=None):
    try:
        text = str(value or "").strip()
        if not text:
            return default
        return int(text)
    except (TypeError, ValueError):
        return default


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
        suffix = secrets.randbelow(10000)
        code = f"{prefix}{suffix:04d}"
        if not User.query.filter_by(unique_user_code=code).first():
            return code
    raise RuntimeError("Unable to generate unique user code")


def _course_max_semester(course_code: str) -> int:
    course = (course_code or "MCA").strip().upper()
    if course == "BCA":
        return 6
    return 4


def _generate_password(length: int = 12) -> str:
    # Format ensures upper/lower/digit/special and is easy enough for demo handout.
    specials = "!@#$%"
    alphabet = string.ascii_letters + string.digits + specials
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(ch.islower() for ch in password)
            and any(ch.isupper() for ch in password)
            and any(ch.isdigit() for ch in password)
            and any(ch in specials for ch in password)
        ):
            return password


def _clear_non_admin_data():
    # Clear dependent records first to avoid FK failures.
    ModerationLog.query.delete(synchronize_session=False)
    Feedback.query.delete(synchronize_session=False)
    PendingFacultyFeedback.query.delete(synchronize_session=False)
    Checklist.query.delete(synchronize_session=False)
    ExperienceReport.query.delete(synchronize_session=False)
    ExperienceUpvote.query.delete(synchronize_session=False)
    StudentExperience.query.delete(synchronize_session=False)
    KnowledgePost.query.delete(synchronize_session=False)
    SemesterMismatchRequest.query.delete(synchronize_session=False)
    FacultyAssignment.query.delete(synchronize_session=False)
    LifecycleEvent.query.delete(synchronize_session=False)
    StudentAcademicProfile.query.delete(synchronize_session=False)

    User.query.filter(User.role != "admin").delete(synchronize_session=False)
    db.session.commit()


def _read_rows(path: Path):
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = [{(k or "").strip(): (v or "").strip() for k, v in row.items()} for row in reader]
    return fieldnames, rows


def _write_rows(path: Path, fieldnames, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _ensure_password_column(fieldnames):
    if "password" not in fieldnames:
        fieldnames.append("password")
    return fieldnames


def _create_user_from_row(row: dict):
    role = (row.get("role") or "").strip().lower()
    if role not in {"student", "faculty"}:
        return None

    if not _is_truthy(row.get("allowed", "YES")):
        return None

    email = (row.get("email") or "").strip().lower()
    full_name = (row.get("full_name") or "").strip()
    course = (row.get("course") or "MCA").strip().upper() or "MCA"

    if not email or not full_name:
        return None

    password = (row.get("password") or "").strip()
    if not password:
        password = _generate_password(12)
        row["password"] = password

    user = User(
        unique_user_code=_generate_unique_user_code(role, course),
        full_name=full_name,
        email=email,
        role=role,
        prn=(row.get("prn") or "").strip() or None,
        faculty_id=(row.get("faculty_id") or "").strip().upper() or None,
        section=(row.get("section") or "").strip().upper() or None,
        course=course,
        security_question=DEFAULT_SECURITY_QUESTION,
        security_answer_hash="",
        is_active=True,
    )
    user.set_password(password)
    user.set_security_answer(DEFAULT_SECURITY_ANSWER)
    db.session.add(user)
    db.session.flush()

    if role == "student":
        start_year = _safe_int(row.get("batch_start_year"), default=2024)
        end_year = _safe_int(row.get("batch_end_year"), default=start_year + (3 if course == "BCA" else 2))
        admission_month = _safe_int(row.get("admission_month"), default=7)
        admission_year = _safe_int(row.get("admission_year"), default=start_year)
        current_semester = _safe_int(row.get("current_semester"), default=1)
        max_semester = _course_max_semester(course)
        if current_semester < 1:
            current_semester = 1
        if current_semester > max_semester:
            current_semester = max_semester

        profile = StudentAcademicProfile(
            user_id=user.id,
            course_code=course,
            batch_start_year=start_year,
            batch_end_year=end_year,
            admission_month=max(1, min(12, admission_month)),
            admission_year=admission_year,
            current_semester=current_semester,
            max_semester=max_semester,
            progression_mode="auto",
            lifecycle_status="active",
        )
        db.session.add(profile)

    return user


def main():
    if not CSV_PATH.exists():
        raise FileNotFoundError(f"CSV not found: {CSV_PATH}")

    app = create_app()
    with app.app_context():
        fieldnames, rows = _read_rows(CSV_PATH)
        fieldnames = _ensure_password_column(fieldnames)

        _clear_non_admin_data()

        created_students = 0
        created_faculty = 0

        for row in rows:
            user = _create_user_from_row(row)
            if not user:
                continue
            if user.role == "student":
                created_students += 1
            elif user.role == "faculty":
                created_faculty += 1

        db.session.commit()
        sync_stats = sync_preset_assignments_to_db()
        _write_rows(CSV_PATH, fieldnames, rows)

        print("Seed complete.")
        print(f"CSV updated: {CSV_PATH}")
        print(f"Students created: {created_students}")
        print(f"Faculty created: {created_faculty}")
        print(
            "Preset assignment sync -> "
            f"created: {sync_stats['created']}, "
            f"already_mapped: {sync_stats['already_mapped']}, "
            f"missing_faculty: {sync_stats['missing_faculty']}, "
            f"missing_offering: {sync_stats['missing_offering']}"
        )
        print("Non-admin users and dependent data were cleared before seed.")


if __name__ == "__main__":
    main()
