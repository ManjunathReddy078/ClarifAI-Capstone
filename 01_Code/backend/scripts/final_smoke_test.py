from __future__ import annotations

import time
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app import app
from models import Checklist, Feedback, KnowledgePost, ModerationLog, User, db


def _unique_code(seed: int) -> str:
    for offset in range(500):
        code = f"CAIMCA{(seed + offset) % 900 + 100}"
        if not User.query.filter_by(unique_user_code=code).first():
            return code
    raise RuntimeError("Unable to generate temporary unique_user_code")


def _create_temp_user(email: str, role: str, code_seed: int, **kwargs) -> User:
    user = User.query.filter_by(email=email).first()
    if user:
        user.role = role
        user.is_active = True
    else:
        user = User(
            unique_user_code=_unique_code(code_seed),
            full_name=kwargs.get("full_name", role.title()),
            email=email,
            role=role,
            course="MCA",
            security_question="What is your birth city?",
            security_answer_hash="",
            prn=kwargs.get("prn"),
            faculty_id=kwargs.get("faculty_id"),
            section=kwargs.get("section"),
        )
        db.session.add(user)

    user.set_password("Pass@1234")
    user.set_security_answer("test")
    return user


def _cleanup_temp_data(emails: list[str]) -> None:
    users = User.query.filter(User.email.in_(emails)).all()
    ids = [user.id for user in users]
    if not ids:
        return

    Feedback.query.filter(Feedback.student_id.in_(ids)).delete(synchronize_session=False)
    Feedback.query.filter(Feedback.faculty_id.in_(ids)).delete(synchronize_session=False)
    KnowledgePost.query.filter(KnowledgePost.author_id.in_(ids)).delete(synchronize_session=False)
    Checklist.query.filter(Checklist.student_id.in_(ids)).delete(synchronize_session=False)
    Checklist.query.filter(Checklist.faculty_id.in_(ids)).delete(synchronize_session=False)
    ModerationLog.query.filter(ModerationLog.admin_id.in_(ids)).delete(synchronize_session=False)
    User.query.filter(User.id.in_(ids)).delete(synchronize_session=False)
    db.session.commit()


def _assert_status(label: str, actual: int, expected: int = 200) -> None:
    if actual != expected:
        raise AssertionError(f"{label} failed: expected {expected}, got {actual}")
    print(f"PASS: {label} -> {actual}")


def run() -> None:
    suffix = str(int(time.time()))[-6:]
    student_email = f"final_student_{suffix}@test.local"
    faculty_email = f"final_faculty_{suffix}@test.local"
    admin_email = f"final_admin_{suffix}@test.local"
    emails = [student_email, faculty_email, admin_email]

    with app.app_context():
        _cleanup_temp_data(emails)
        student = _create_temp_user(
            student_email,
            "student",
            200 + int(suffix[-1]),
            full_name="Final Student",
            prn=f"PRN{suffix}",
            section="A",
        )
        faculty = _create_temp_user(
            faculty_email,
            "faculty",
            300 + int(suffix[-1]),
            full_name="Final Faculty",
            faculty_id=f"FAC{suffix}",
        )
        _create_temp_user(
            admin_email,
            "admin",
            400 + int(suffix[-1]),
            full_name="Final Admin",
        )
        db.session.commit()
        faculty_id = faculty.id

    client = app.test_client()

    _assert_status("HOME", client.get("/").status_code)
    _assert_status("LOGIN_PAGE", client.get("/auth/login").status_code)

    # Student flow
    student_login = client.post(
        "/auth/login",
        data={"email": student_email, "password": "Pass@1234"},
        follow_redirects=True,
    )
    _assert_status("STUDENT_LOGIN", student_login.status_code)
    _assert_status("STUDENT_REVIEWS", client.get("/student/reviews").status_code)

    review_create = client.post(
        "/student/reviews/create",
        data={
            "faculty_id": str(faculty_id),
            "subject": "AI",
            "semester": "Sem 2",
            "reason": "Teaching Quality",
            "feedback_text": "Very clear explanation and useful examples.",
        },
        follow_redirects=True,
    )
    _assert_status("STUDENT_CREATE_REVIEW", review_create.status_code)

    post_create = client.post(
        "/student/knowledge-post",
        data={"title": "Final Test Post", "content": "Follow module-wise revision."},
        follow_redirects=True,
    )
    _assert_status("STUDENT_CREATE_POST", post_create.status_code)
    _assert_status("STUDENT_CHECKLISTS", client.get("/student/checklists").status_code)
    client.get("/auth/logout")

    # Faculty flow
    faculty_login = client.post(
        "/auth/login",
        data={"email": faculty_email, "password": "Pass@1234"},
        follow_redirects=True,
    )
    _assert_status("FACULTY_LOGIN", faculty_login.status_code)
    _assert_status("FACULTY_REVIEWS", client.get("/faculty/reviews").status_code)
    _assert_status("FACULTY_CHECKLISTS", client.get("/faculty/checklists").status_code)

    checklist_create = client.post(
        "/faculty/checklist/create",
        data={"title": "Final Checklist", "description": "Complete mock viva", "student_email": student_email},
        follow_redirects=True,
    )
    _assert_status("FACULTY_CREATE_CHECKLIST", checklist_create.status_code)
    client.get("/auth/logout")

    # Admin flow
    admin_login = client.post(
        "/auth/login",
        data={"email": admin_email, "password": "Pass@1234"},
        follow_redirects=True,
    )
    _assert_status("ADMIN_LOGIN", admin_login.status_code)
    _assert_status("ADMIN_USERS", client.get("/admin/users").status_code)
    _assert_status("ADMIN_MODERATION", client.get("/admin/moderation").status_code)

    with app.app_context():
        _cleanup_temp_data(emails)

    print("\nALL FINAL SMOKE TESTS PASSED")


if __name__ == "__main__":
    run()
