from models import Feedback, PendingFacultyFeedback, db


def release_held_feedback_for_faculty(faculty_user) -> int:
    if not faculty_user or faculty_user.role != "faculty":
        return 0

    faculty_id = (faculty_user.faculty_id or "").strip().upper()
    faculty_email = (faculty_user.email or "").strip().lower()
    if not faculty_id and not faculty_email:
        return 0

    rows = PendingFacultyFeedback.query.filter_by(status="holding").all()
    moved = 0
    for row in rows:
        row_faculty_id = (row.assigned_faculty_id or "").strip().upper()
        row_faculty_email = (row.assigned_faculty_email or "").strip().lower()
        matches = False
        if faculty_id and row_faculty_id and row_faculty_id == faculty_id:
            matches = True
        elif faculty_email and row_faculty_email and row_faculty_email == faculty_email:
            matches = True

        if not matches:
            continue

        feedback = Feedback(
            student_id=row.student_id,
            faculty_id=faculty_user.id,
            course_code=row.subject_code,
            subject=row.subject,
            semester=row.semester,
            reason=row.reason,
            feedback_tags=row.feedback_tags,
            class_session_at=row.class_session_at,
            feedback_text=row.feedback_text,
            sentiment=row.sentiment,
            status="approved",
            admin_note=row.admin_note,
        )
        db.session.add(feedback)
        db.session.delete(row)
        moved += 1

    if moved:
        db.session.commit()
    return moved
