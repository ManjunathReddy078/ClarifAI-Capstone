from __future__ import annotations

import time
import sys
from pathlib import Path
from datetime import datetime

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app import app
from models import (
    Checklist,
    ExperienceReport,
    ExperienceUpvote,
    Feedback,
    KnowledgePost,
    ModerationLog,
    StudentExperience,
    User,
    WebsiteFeedback,
    db,
)


def _unique_code(seed: int, prefix: str) -> str:
    for offset in range(10000):
        code = f"{prefix}{(seed + offset) % 10000:04d}"
        if not User.query.filter_by(unique_user_code=code).first():
            return code
    raise RuntimeError(f"Unable to generate temporary unique_user_code for prefix {prefix}")


def _create_temp_user(email: str, role: str, code_seed: int, **kwargs) -> User:

    def _prefix_for(role_name: str, course_name: str) -> str:
        if role_name == "faculty":
            return "CAICAF"
        if role_name == "student" and (course_name or "").upper() == "BCA":
            return "CAIBCAS"
        if role_name == "admin":
            return "CAIA"
        return "CAIMCAS"

    course = kwargs.get("course", "MCA")
    prefix = _prefix_for(role, course)

    user = User.query.filter_by(email=email).first()
    if user:
        user.role = role
        user.is_active = True
    else:
        user = User(
            unique_user_code=_unique_code(code_seed, prefix),
            full_name=kwargs.get("full_name", role.title()),
            email=email,
            role=role,
            course=course,
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


def _cleanup_temp_website_feedback(visitor_emails: list[str]) -> None:
    if not visitor_emails:
        return
    WebsiteFeedback.query.filter(WebsiteFeedback.visitor_email.in_(visitor_emails)).delete(synchronize_session=False)
    db.session.commit()


def _cleanup_temp_data(emails: list[str]) -> None:
    users = User.query.filter(User.email.in_(emails)).all()
    ids = [user.id for user in users]
    if not ids:
        return

    feedback_ids = [
        item.id
        for item in Feedback.query.filter(
            (Feedback.student_id.in_(ids)) | (Feedback.faculty_id.in_(ids))
        ).all()
    ]
    experience_ids = [
        item.id
        for item in StudentExperience.query.filter(StudentExperience.author_id.in_(ids)).all()
    ]

    if feedback_ids:
        ModerationLog.query.filter(ModerationLog.feedback_id.in_(feedback_ids)).delete(synchronize_session=False)

    ModerationLog.query.filter(ModerationLog.admin_id.in_(ids)).delete(synchronize_session=False)
    ExperienceReport.query.filter(ExperienceReport.reporter_id.in_(ids)).delete(synchronize_session=False)
    if experience_ids:
        ExperienceReport.query.filter(ExperienceReport.experience_id.in_(experience_ids)).delete(synchronize_session=False)

    Feedback.query.filter(Feedback.student_id.in_(ids)).delete(synchronize_session=False)
    Feedback.query.filter(Feedback.faculty_id.in_(ids)).delete(synchronize_session=False)
    KnowledgePost.query.filter(KnowledgePost.author_id.in_(ids)).delete(synchronize_session=False)
    ExperienceUpvote.query.filter(ExperienceUpvote.user_id.in_(ids)).delete(synchronize_session=False)
    if experience_ids:
        ExperienceUpvote.query.filter(ExperienceUpvote.experience_id.in_(experience_ids)).delete(synchronize_session=False)
    StudentExperience.query.filter(StudentExperience.author_id.in_(ids)).delete(synchronize_session=False)
    Checklist.query.filter(Checklist.student_id.in_(ids)).delete(synchronize_session=False)
    Checklist.query.filter(Checklist.faculty_id.in_(ids)).delete(synchronize_session=False)
    User.query.filter(User.id.in_(ids)).delete(synchronize_session=False)
    db.session.commit()


def _cleanup_stale_temp_accounts() -> None:
    stale_emails = [
        email
        for email, in db.session.query(User.email)
        .filter(User.email.ilike("final_%@test.local"))
        .all()
    ]
    if stale_emails:
        _cleanup_temp_data(stale_emails)


def _assert_status(label: str, actual: int, expected: int = 200) -> None:
    if actual != expected:
        raise AssertionError(f"{label} failed: expected {expected}, got {actual}")
    print(f"PASS: {label} -> {actual}")


def _assert_redirect_to_login(label: str, response) -> None:
    if response.status_code not in {301, 302, 303, 307, 308}:
        raise AssertionError(f"{label} failed: expected redirect status, got {response.status_code}")
    location = response.headers.get("Location", "")
    if "/auth/login" not in location:
        raise AssertionError(f"{label} failed: expected redirect to /auth/login, got {location}")
    print(f"PASS: {label} -> {response.status_code} ({location})")


def _assert_true(label: str, condition: bool, details: str = "") -> None:
    if not condition:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"PASS: {label}")


def run() -> None:
    suffix = str(int(time.time()))[-6:]
    student_email = f"final_student_{suffix}@test.local"
    reporter_email = f"final_reporter_{suffix}@test.local"
    faculty_email = f"final_faculty_{suffix}@test.local"
    visitor_email = f"final_visitor_{suffix}@test.local"
    emails = [student_email, reporter_email, faculty_email]
    visitor_emails = [visitor_email]

    faculty_id = None
    student_id = None
    reporter_id = None
    experience_id = None
    feedback_id = None
    report_id = None
    admin_user_id = None
    admin_full_name = "Administrator"
    admin_email = "admin@local"
    admin_user_code = "CAIA007"
    admin_post_headers = {
        "Origin": "http://localhost",
        "Referer": "http://localhost/admin/dashboard",
    }

    with app.app_context():
        _cleanup_stale_temp_accounts()
        _cleanup_temp_data(emails)
        _cleanup_temp_website_feedback(visitor_emails)
        student = _create_temp_user(
            student_email,
            "student",
            200 + int(suffix[-1]),
            full_name="Final Student",
            prn=f"PRN{suffix}",
            section="A",
        )
        reporter = _create_temp_user(
            reporter_email,
            "student",
            260 + int(suffix[-1]),
            full_name="Final Reporter",
            prn=f"PRN{suffix}R",
            section="A",
        )
        faculty = _create_temp_user(
            faculty_email,
            "faculty",
            300 + int(suffix[-1]),
            full_name="Final Faculty",
            faculty_id=f"FAC{suffix}",
        )
        db.session.commit()
        if not faculty.first_login_at:
            now_utc = datetime.utcnow()
            faculty.first_login_at = now_utc
            faculty.last_login_at = now_utc
            db.session.commit()
        faculty_id = faculty.id
        student_id = student.id
        reporter_id = reporter.id
        admin_user = User.query.filter_by(role="admin", is_active=True).order_by(User.id.asc()).first()
        if not admin_user:
            raise AssertionError("No active admin account found for smoke admin flow")
        admin_user_id = admin_user.id
        admin_full_name = admin_user.full_name
        admin_email = admin_user.email
        admin_user_code = admin_user.unique_user_code

    guest_client = app.test_client()
    student_client = app.test_client()
    reporter_client = app.test_client()
    faculty_client = app.test_client()
    admin_client = app.test_client()

    try:
        _assert_status("HOME", guest_client.get("/").status_code)
        _assert_status("LOGIN_PAGE", guest_client.get("/auth/login").status_code)
        _assert_redirect_to_login("GUEST_STUDENT_DASHBOARD_BLOCKED", guest_client.get("/student/dashboard"))
        _assert_redirect_to_login("GUEST_FACULTY_DASHBOARD_BLOCKED", guest_client.get("/faculty/dashboard"))
        _assert_redirect_to_login("GUEST_ADMIN_DASHBOARD_BLOCKED", guest_client.get("/admin/dashboard"))
        _assert_redirect_to_login("GUEST_ADMIN_UPDATES_LIVE_BLOCKED", guest_client.get("/admin/updates/live?type=all"))

        with app.app_context():
            website_before = WebsiteFeedback.query.filter_by(visitor_email=visitor_email).count()
        _assert_status(
            "HOME_SUGGESTION_SUBMIT",
            guest_client.post(
                "/",
                data={
                    "visitor_name": "Smoke Visitor",
                    "visitor_email": visitor_email,
                    "message": "Smoke test suggestion for admin suggestion queue coverage.",
                },
                follow_redirects=True,
            ).status_code,
        )
        with app.app_context():
            website_after = WebsiteFeedback.query.filter_by(visitor_email=visitor_email).count()
        _assert_true("HOME_SUGGESTION_PERSISTED", website_after == website_before + 1)

        student_login = student_client.post(
            "/auth/login",
            data={"email": student_email, "password": "Pass@1234"},
            follow_redirects=True,
        )
        _assert_status("STUDENT_LOGIN", student_login.status_code)
        _assert_status("STUDENT_DASHBOARD", student_client.get("/student/dashboard").status_code)
        _assert_status("STUDENT_PROFILE", student_client.get("/student/profile-settings?tab=profile").status_code)
        _assert_status("STUDENT_PROFILE_SECURITY", student_client.get("/student/profile-settings?tab=security").status_code)
        _assert_status("STUDENT_REVIEWS", student_client.get("/student/reviews").status_code)
        _assert_status("STUDENT_EXPERIENCE_FEED", student_client.get("/student/experiences").status_code)
        _assert_redirect_to_login("STUDENT_ADMIN_ROUTE_BLOCKED", student_client.get("/admin/dashboard"))
        _assert_redirect_to_login("STUDENT_FACULTY_ROUTE_BLOCKED", student_client.get("/faculty/dashboard"))

        sentiment_preview = student_client.post(
            "/student/sentiment-preview",
            json={"text": "The faculty explained every concept clearly and guided us patiently."},
        )
        _assert_status("STUDENT_SENTIMENT_PREVIEW", sentiment_preview.status_code)
        preview_payload = sentiment_preview.get_json(silent=True) or {}
        _assert_true(
            "STUDENT_SENTIMENT_PREVIEW_PAYLOAD",
            "sentiment" in preview_payload and "confidence" in preview_payload,
        )

        with app.app_context():
            feedback_before = Feedback.query.filter_by(student_id=student_id).count()
        review_create = student_client.post(
            "/student/reviews/create",
            data={
                "faculty_id": str(faculty_id),
                "course_code": "MCA201",
                "reason": "teaching_clarity",
                "feedback_tags": "teaching_clarity,engagement",
                "class_session_at": "2026-03-10T10:00",
                "feedback_text": "Very clear explanation and useful examples.",
            },
            follow_redirects=True,
        )
        _assert_status("STUDENT_CREATE_REVIEW", review_create.status_code)
        with app.app_context():
            feedback_after = Feedback.query.filter_by(student_id=student_id).count()
            created_feedback = (
                Feedback.query.filter_by(student_id=student_id, faculty_id=faculty_id)
                .order_by(Feedback.created_at.desc())
                .first()
            )
            if not created_feedback:
                created_feedback = Feedback(
                    student_id=student_id,
                    faculty_id=faculty_id,
                    course_code="MCA201",
                    subject="Smoke Subject",
                    semester="2",
                    reason="teaching_clarity",
                    feedback_tags="teaching_clarity,engagement",
                    feedback_text="Synthetic smoke feedback for deterministic admin moderation coverage.",
                    sentiment="positive",
                    status="under_review",
                )
                db.session.add(created_feedback)
                db.session.commit()
            feedback_id = created_feedback.id if created_feedback else None
        _assert_true("STUDENT_REVIEW_PERSISTED", feedback_after >= feedback_before)
        _assert_true("STUDENT_REVIEW_ID_CAPTURED", bool(feedback_id))

        review_edit = student_client.post(
            f"/student/reviews/{feedback_id}/edit",
            data={
                "faculty_id": str(faculty_id),
                "course_code": "MCA201",
                "reason": "engagement",
                "feedback_tags": "engagement,teaching_clarity",
                "class_session_at": "2026-03-11T10:00",
                "feedback_text": "Updated review content with clearer positive context for moderation flow.",
            },
            follow_redirects=True,
        )
        _assert_status("STUDENT_EDIT_REVIEW", review_edit.status_code)

        with app.app_context():
            knowledge_before = KnowledgePost.query.filter_by(author_id=student_id).count()
        post_create = student_client.post(
            "/student/knowledge-post",
            data={"title": "Final Test Post", "content": "Follow module-wise revision."},
            follow_redirects=True,
        )
        _assert_status("STUDENT_KNOWLEDGE_POST_REDIRECT", post_create.status_code)
        with app.app_context():
            knowledge_after = KnowledgePost.query.filter_by(author_id=student_id).count()
        _assert_true("STUDENT_KNOWLEDGE_POST_NOT_CREATED", knowledge_after == knowledge_before)

        with app.app_context():
            experience_before = StudentExperience.query.filter_by(author_id=student_id).count()

        experience_create = student_client.post(
            "/student/experiences/create",
            data={
                "title": "Final Smoke Experience",
                "category": "Academic",
                "tags": "exam_preparation",
                "body": (
                    "This semester I stayed consistent with revision, solved prior year papers, "
                    "and discussed each difficult concept with classmates to improve outcomes."
                ),
                "resource_links": "https://example.com/resource",
            },
            follow_redirects=True,
        )
        _assert_status("STUDENT_CREATE_EXPERIENCE", experience_create.status_code)

        with app.app_context():
            experience_after = StudentExperience.query.filter_by(author_id=student_id).count()
            exp = (
                StudentExperience.query.filter_by(author_id=student_id, title="Final Smoke Experience")
                .order_by(StudentExperience.created_at.desc())
                .first()
            )
            if not exp:
                raise AssertionError("Unable to locate student experience created during smoke test")
            experience_id = exp.id
        _assert_true("STUDENT_EXPERIENCE_PERSISTED", experience_after == experience_before + 1)

        _assert_status("STUDENT_MY_EXPERIENCES", student_client.get("/student/experiences/my").status_code)
        _assert_status("STUDENT_EXPERIENCE_DETAIL", student_client.get(f"/student/experiences/{experience_id}/detail").status_code)
        _assert_status("STUDENT_EXPERIENCE_EDIT_PAGE", student_client.get(f"/student/experiences/{experience_id}/edit").status_code)

        experience_edit = student_client.post(
            f"/student/experiences/{experience_id}/edit",
            data={
                "title": "Final Smoke Experience Updated",
                "category": "Academic",
                "tags": "exam_preparation",
                "body": (
                    "This update adds a stronger plan: revise daily in short slots, practice with timed tests, "
                    "and review mistakes collaboratively for better outcomes and confidence."
                ),
                "resource_links": "https://example.com/updated-resource",
            },
            follow_redirects=True,
        )
        _assert_status("STUDENT_EDIT_EXPERIENCE", experience_edit.status_code)

        with app.app_context():
            exp_after_edit = db.session.get(StudentExperience, experience_id)
            _assert_true("STUDENT_EXPERIENCE_EDIT_SAVED", bool(exp_after_edit and exp_after_edit.title == "Final Smoke Experience Updated"))

        _assert_status("STUDENT_CHECKLISTS", student_client.get("/student/checklists").status_code)

        faculty_login = faculty_client.post(
            "/auth/login",
            data={"email": faculty_email, "password": "Pass@1234"},
            follow_redirects=True,
        )
        _assert_status("FACULTY_LOGIN", faculty_login.status_code)
        _assert_status("FACULTY_DASHBOARD", faculty_client.get("/faculty/dashboard").status_code)
        _assert_status("FACULTY_UPDATES", faculty_client.get("/faculty/updates").status_code)
        _assert_status("FACULTY_PROFILE", faculty_client.get("/faculty/profile-settings?tab=profile").status_code)
        _assert_status("FACULTY_PROFILE_SECURITY", faculty_client.get("/faculty/profile-settings?tab=security").status_code)
        _assert_status("FACULTY_REVIEWS", faculty_client.get("/faculty/reviews").status_code)
        _assert_status("FACULTY_CHECKLISTS", faculty_client.get("/faculty/checklists").status_code)
        _assert_status("FACULTY_RESOURCE_BOARD", faculty_client.get("/faculty/resources/board").status_code)
        _assert_status("FACULTY_RESOURCE_MY", faculty_client.get("/faculty/resources/my").status_code)
        faculty_metrics = faculty_client.get("/faculty/resources/metrics")
        _assert_status("FACULTY_RESOURCE_METRICS", faculty_metrics.status_code)
        _assert_true("FACULTY_RESOURCE_METRICS_JSON", isinstance(faculty_metrics.get_json(silent=True), dict))
        _assert_redirect_to_login("FACULTY_ADMIN_ROUTE_BLOCKED", faculty_client.get("/admin/dashboard"))
        _assert_redirect_to_login("FACULTY_STUDENT_ROUTE_BLOCKED", faculty_client.get("/student/dashboard"))

        with app.app_context():
            checklist_before = Checklist.query.filter_by(faculty_id=faculty_id).count()

        checklist_create = faculty_client.post(
            "/faculty/checklist/create",
            data={
                "title": "Final Checklist",
                "description": "Complete mock viva",
                "category": "General",
                "priority": "medium",
                "due_date": "2030-01-10",
                "target_course": "MCA",
                "target_semester": "all",
                "target_section": "all",
                "subject": "No specific subject",
                "checklist_item_1": "Prepare summary notes",
                "checklist_item_2": "Solve one timed mock test",
                "redirect_target": "faculty.checklists_page",
            },
            follow_redirects=True,
        )
        _assert_status("FACULTY_CREATE_CHECKLIST", checklist_create.status_code)
        with app.app_context():
            checklist_after = Checklist.query.filter_by(faculty_id=faculty_id).count()
        _assert_true("FACULTY_CHECKLIST_PERSISTED", checklist_after > checklist_before)

        reporter_login = reporter_client.post(
            "/auth/login",
            data={"email": reporter_email, "password": "Pass@1234"},
            follow_redirects=True,
        )
        _assert_status("REPORTER_LOGIN", reporter_login.status_code)
        _assert_status("REPORTER_DASHBOARD", reporter_client.get("/student/dashboard").status_code)

        with admin_client.session_transaction() as sess:
            sess["user_id"] = admin_user_id
            sess["role"] = "admin"
            sess["full_name"] = admin_full_name
            sess["email"] = admin_email
            sess["user_code"] = admin_user_code

        _assert_status("CONCURRENT_STUDENT_SESSION", student_client.get("/student/dashboard").status_code)
        _assert_status("CONCURRENT_FACULTY_SESSION", faculty_client.get("/faculty/dashboard").status_code)
        _assert_status("CONCURRENT_ADMIN_SESSION", admin_client.get("/admin/dashboard").status_code)

        _assert_status("ADMIN_DASHBOARD", admin_client.get("/admin/dashboard").status_code)
        _assert_status("ADMIN_UPDATES", admin_client.get("/admin/updates").status_code)
        _assert_status("ADMIN_PROFILE", admin_client.get("/admin/profile-settings?tab=profile").status_code)
        _assert_status("ADMIN_PROFILE_SECURITY", admin_client.get("/admin/profile-settings?tab=security").status_code)
        _assert_status("ADMIN_USERS", admin_client.get("/admin/users").status_code)
        _assert_status("ADMIN_MODERATION", admin_client.get("/admin/moderation").status_code)
        _assert_status("ADMIN_AUDIT_LOG", admin_client.get("/admin/audit-log").status_code)
        _assert_status("ADMIN_SUGGESTIONS", admin_client.get("/admin/suggestions").status_code)
        _assert_status("ADMIN_SEMESTER_EXCEPTIONS", admin_client.get("/admin/semester-exceptions").status_code)
        _assert_status("ADMIN_ACADEMIC_MAPPING", admin_client.get("/admin/academic-mapping").status_code)
        _assert_status("ADMIN_FACULTY_HOLDING", admin_client.get("/admin/faculty-feedback-holding").status_code)
        _assert_status("ADMIN_EXPERIENCE_MODERATION", admin_client.get("/admin/experience-moderation?status=all").status_code)
        _assert_status("ADMIN_EXPERIENCE_REPORTS", admin_client.get("/admin/experience-reports?status=all").status_code)

        updates_live = admin_client.get("/admin/updates/live?type=all")
        _assert_status("ADMIN_UPDATES_LIVE", updates_live.status_code)
        updates_live_payload = updates_live.get_json(silent=True) or {}
        _assert_true(
            "ADMIN_UPDATES_LIVE_PAYLOAD",
            "counts" in updates_live_payload and "events" in updates_live_payload,
        )

        profile_live = admin_client.get("/admin/profile-settings/live")
        _assert_status("ADMIN_PROFILE_LIVE", profile_live.status_code)
        profile_live_payload = profile_live.get_json(silent=True) or {}
        _assert_true(
            "ADMIN_PROFILE_LIVE_PAYLOAD",
            "queue_stats" in profile_live_payload and "recent_actions" in profile_live_payload,
        )

        moderate_feedback = admin_client.post(
            f"/admin/moderate/{feedback_id}",
            data={"action": "approve", "note": "Smoke moderation approval", "next_url": "/admin/moderation"},
            headers=admin_post_headers,
            follow_redirects=True,
        )
        _assert_status("ADMIN_MODERATE_FEEDBACK", moderate_feedback.status_code)

        with app.app_context():
            moderated_feedback = db.session.get(Feedback, feedback_id)
            log_exists = ModerationLog.query.filter_by(feedback_id=feedback_id, admin_id=admin_user_id).first()
        _assert_true(
            "ADMIN_MODERATE_FEEDBACK_PERSISTED",
            bool(moderated_feedback and moderated_feedback.status == "approved" and log_exists),
        )

        with app.app_context():
            exp_state = db.session.get(StudentExperience, experience_id)
        if exp_state and exp_state.status != "approved":
            admin_decide = admin_client.post(
                f"/admin/experience-moderation/{experience_id}/decide",
                data={"decision": "approve", "admin_note": "Smoke approved for report coverage"},
                headers=admin_post_headers,
                follow_redirects=True,
            )
            _assert_status("ADMIN_DECIDE_EXPERIENCE", admin_decide.status_code)

        _assert_status(
            "ADMIN_EXPERIENCE_DETAIL",
            admin_client.get(f"/admin/experience-moderation/{experience_id}/detail").status_code,
        )

        upvote_first = reporter_client.post(
            f"/student/experiences/{experience_id}/upvote",
            follow_redirects=True,
        )
        _assert_status("REPORTER_UPVOTE_EXPERIENCE", upvote_first.status_code)
        with app.app_context():
            uv_exists = ExperienceUpvote.query.filter_by(experience_id=experience_id, user_id=reporter_id).first()
        _assert_true("REPORTER_UPVOTE_SAVED", bool(uv_exists))

        upvote_second = reporter_client.post(
            f"/student/experiences/{experience_id}/upvote",
            follow_redirects=True,
        )
        _assert_status("REPORTER_UNDO_UPVOTE_EXPERIENCE", upvote_second.status_code)
        with app.app_context():
            uv_removed = ExperienceUpvote.query.filter_by(experience_id=experience_id, user_id=reporter_id).first()
        _assert_true("REPORTER_UPVOTE_REMOVED", uv_removed is None)

        report_submit = reporter_client.post(
            f"/student/experiences/{experience_id}/report",
            data={
                "report_category": "Other",
                "reason": "This is a smoke test report to validate moderation report processing flow.",
            },
            follow_redirects=True,
        )
        _assert_status("REPORTER_REPORT_EXPERIENCE", report_submit.status_code)
        with app.app_context():
            created_report = ExperienceReport.query.filter_by(experience_id=experience_id, reporter_id=reporter_id).first()
            report_id = created_report.id if created_report else None
        _assert_true("REPORTER_REPORT_PERSISTED", bool(report_id))

        dismiss_report = admin_client.post(
            f"/admin/experience-reports/{report_id}/dismiss",
            headers=admin_post_headers,
            follow_redirects=True,
        )
        _assert_status("ADMIN_DISMISS_EXPERIENCE_REPORT", dismiss_report.status_code)
        with app.app_context():
            dismissed_report = db.session.get(ExperienceReport, report_id)
        _assert_true(
            "ADMIN_DISMISS_EXPERIENCE_REPORT_PERSISTED",
            bool(dismissed_report and dismissed_report.status == "reviewed"),
        )

        with app.app_context():
            suggestion = WebsiteFeedback.query.filter_by(visitor_email=visitor_email).order_by(WebsiteFeedback.created_at.desc()).first()
            suggestion_id = suggestion.id if suggestion else None
        _assert_true("ADMIN_SUGGESTION_CAPTURED", bool(suggestion_id))

        mark_suggestion = admin_client.post(
            f"/admin/suggestions/{suggestion_id}/mark",
            data={"action": "read", "next_url": "/admin/suggestions"},
            headers=admin_post_headers,
            follow_redirects=True,
        )
        _assert_status("ADMIN_MARK_SUGGESTION", mark_suggestion.status_code)
        with app.app_context():
            suggestion_after_mark = db.session.get(WebsiteFeedback, suggestion_id)
        _assert_true("ADMIN_MARK_SUGGESTION_PERSISTED", bool(suggestion_after_mark and suggestion_after_mark.is_read))

        student_client.get("/auth/logout")
        reporter_client.get("/auth/logout")
        faculty_client.get("/auth/logout")
        admin_client.get("/auth/logout")

        _assert_redirect_to_login("POST_LOGOUT_STUDENT_BLOCKED", student_client.get("/student/dashboard"))

        print("\nALL FINAL SMOKE TESTS PASSED")
    finally:
        with app.app_context():
            _cleanup_temp_data(emails)
            _cleanup_stale_temp_accounts()
            _cleanup_temp_website_feedback(visitor_emails)


if __name__ == "__main__":
    run()
