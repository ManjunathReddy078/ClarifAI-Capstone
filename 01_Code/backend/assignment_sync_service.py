from datetime import date

from models import FacultyAssignment, SubjectOffering, User, db
from academic_mapping_store import load_preset_assignments


def _resolve_faculty_user(faculty_id: str, faculty_email: str):
    faculty_id_key = (faculty_id or "").strip().upper()
    faculty_email_key = (faculty_email or "").strip().lower()

    if faculty_id_key:
        user = User.query.filter_by(role="faculty", faculty_id=faculty_id_key).first()
        if user:
            return user

    if faculty_email_key:
        user = User.query.filter_by(role="faculty", email=faculty_email_key).first()
        if user:
            return user

    return None


def sync_preset_assignments_to_db(*, reference_date=None) -> dict:
    target_date = reference_date or date.today()
    rows = load_preset_assignments(active_only=True)

    stats = {
        "preset_rows": len(rows),
        "created": 0,
        "already_mapped": 0,
        "missing_offering": 0,
        "missing_faculty": 0,
    }

    for row in rows:
        course_code = (row.get("course_code") or "").strip().upper()
        semester_text = (row.get("semester_no") or "").strip()
        section = (row.get("section") or "").strip().upper()
        subject_code = (row.get("subject_code") or "").strip().upper()

        try:
            semester_no = int(semester_text)
        except (TypeError, ValueError):
            stats["missing_offering"] += 1
            continue

        offering = SubjectOffering.query.filter_by(
            course_code=course_code,
            semester_no=semester_no,
            section=section,
            subject_code=subject_code,
            is_active=True,
        ).first()
        if not offering:
            stats["missing_offering"] += 1
            continue

        faculty_user = _resolve_faculty_user(row.get("faculty_id", ""), row.get("faculty_email", ""))
        if not faculty_user:
            stats["missing_faculty"] += 1
            continue

        existing = (
            FacultyAssignment.query.filter_by(
                subject_offering_id=offering.id,
                faculty_user_id=faculty_user.id,
            )
            .order_by(FacultyAssignment.effective_from.desc(), FacultyAssignment.created_at.desc())
            .first()
        )
        if existing:
            stats["already_mapped"] += 1
            continue

        db.session.add(
            FacultyAssignment(
                subject_offering_id=offering.id,
                faculty_user_id=faculty_user.id,
                effective_from=target_date,
                effective_to=None,
                is_active=True,
            )
        )
        stats["created"] += 1

    if stats["created"]:
        db.session.commit()
    else:
        db.session.rollback()

    return stats
