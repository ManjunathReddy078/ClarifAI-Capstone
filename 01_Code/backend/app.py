import json
import random
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse

from flask import Flask, abort, flash, redirect, render_template, request, session, url_for
from sqlalchemy import event, text
from sqlalchemy.engine import Engine

from config import Config
from models import Checklist, CourseConfig, Feedback, KnowledgeNotification, SemesterMismatchRequest, WebsiteFeedback, db
from models import ExperienceReport, PendingFacultyFeedback, StudentExperience
from routes.admin import admin_bp
from routes.auth import auth_bp
from routes.faculty import faculty_bp
from routes.student import student_bp


IST_ZONE = timezone(timedelta(hours=5, minutes=30))
CHECKLIST_META_PREFIX = "[[CLARIFAI_META]]"


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
    if dbapi_connection.__class__.__module__.startswith("sqlite3"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def _generate_unique_user_code(user_model, prefix: str, digits: int = 4) -> str:
    max_value = (10 ** digits) - 1
    for _ in range(20000):
        candidate = f"{prefix}{random.randint(0, max_value):0{digits}d}"
        if not user_model.query.filter_by(unique_user_code=candidate).first():
            return candidate
    raise RuntimeError(f"Unable to generate unique user code with prefix {prefix}.")


def _resolve_admin_user_code(user_model, preferred_code: str) -> str:
    normalized = (preferred_code or "CAIA007").strip().upper()
    existing = user_model.query.filter_by(unique_user_code=normalized).first()
    if existing and existing.role != "admin":
        raise RuntimeError("Configured admin unique code is already assigned to a non-admin user.")
    return normalized


def _is_same_origin(candidate_url: str, host_url: str) -> bool:
    candidate = urlparse(candidate_url)
    host = urlparse(host_url)
    return candidate.scheme == host.scheme and candidate.netloc == host.netloc


def _validate_security_config(app: Flask) -> None:
    if app.config.get("APP_ENV") != "production":
        return

    blocking_errors = []
    if not app.config.get("SESSION_COOKIE_SECURE"):
        blocking_errors.append("CLARIFAI_SESSION_COOKIE_SECURE must be true in production.")
    admin_password = (app.config.get("ADMIN_PASSWORD") or "").strip()
    admin_email = (app.config.get("ADMIN_EMAIL") or "").strip()
    admin_answer = (app.config.get("ADMIN_SECURITY_ANSWER") or "").strip()
    if len(admin_password) < 8:
        blocking_errors.append("CLARIFAI_ADMIN_PASSWORD must be at least 8 characters.")
    if _is_weak_admin_password(admin_password):
        blocking_errors.append("CLARIFAI_ADMIN_PASSWORD uses an insecure default or common pattern.")
    if "@" not in admin_email:
        blocking_errors.append("CLARIFAI_ADMIN_EMAIL must be a valid email address.")
    if not admin_answer:
        blocking_errors.append("CLARIFAI_ADMIN_SECURITY_ANSWER must not be empty.")

    if blocking_errors:
        raise RuntimeError("Production security configuration is incomplete: " + " ".join(blocking_errors))


def _is_weak_admin_password(password: str) -> bool:
    normalized = (password or "").strip().lower()
    weak_defaults = {
        "admin",
        "password",
        "admin123",
        "12345678",
        "admin_cai_ca@7",
    }
    return normalized in weak_defaults


def _utc_to_ist(value):
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, datetime):
        return value
    source = value
    if source.tzinfo is None:
        source = source.replace(tzinfo=timezone.utc)
    return source.astimezone(IST_ZONE)


def _extract_checklist_group_id(raw_description: str, fallback_id: int) -> str:
    text = (raw_description or "").strip()
    if not text.startswith(CHECKLIST_META_PREFIX):
        return f"legacy-{fallback_id}"

    first_line, _, _ = text.partition("\n")
    meta_raw = first_line[len(CHECKLIST_META_PREFIX) :].strip()
    try:
        meta = json.loads(meta_raw)
    except (TypeError, json.JSONDecodeError):
        return f"legacy-{fallback_id}"

    group_id = str(meta.get("group_id") or "").strip()
    return group_id or f"legacy-{fallback_id}"


def _seed_course_configs() -> None:
    seed_rows = [
        {"course_code": "MCA", "duration_years": 2, "total_semesters": 4, "semesters_per_year": 2},
        {"course_code": "BCA", "duration_years": 3, "total_semesters": 6, "semesters_per_year": 2},
    ]

    for seed in seed_rows:
        existing = CourseConfig.query.filter_by(course_code=seed["course_code"]).first()
        if existing:
            if (
                existing.duration_years != seed["duration_years"]
                or existing.total_semesters != seed["total_semesters"]
                or existing.semesters_per_year != seed["semesters_per_year"]
            ):
                existing.duration_years = seed["duration_years"]
                existing.total_semesters = seed["total_semesters"]
                existing.semesters_per_year = seed["semesters_per_year"]
                existing.is_active = True
            continue

        db.session.add(
            CourseConfig(
                course_code=seed["course_code"],
                duration_years=seed["duration_years"],
                total_semesters=seed["total_semesters"],
                semesters_per_year=seed["semesters_per_year"],
                is_active=True,
            )
        )

    db.session.commit()


def _ensure_user_delete_guard(app: Flask) -> None:
    if not app.config.get("SQLALCHEMY_DATABASE_URI", "").startswith("sqlite"):
        return

    if app.config.get("USER_DELETE_GUARD_ENABLED", True):
        db.session.execute(
            text(
                """
                CREATE TRIGGER IF NOT EXISTS protect_users_from_delete
                BEFORE DELETE ON users
                BEGIN
                    SELECT RAISE(ABORT, 'User deletion is disabled by security policy.');
                END;
                """
            )
        )
    else:
        db.session.execute(text("DROP TRIGGER IF EXISTS protect_users_from_delete"))
    db.session.commit()


def _bootstrap_admin(app: Flask) -> None:
    if not app.config.get("ADMIN_BOOTSTRAP_ENABLED"):
        return

    from models import User

    email = (app.config.get("ADMIN_EMAIL") or "").strip().lower()
    full_name = (app.config.get("ADMIN_FULL_NAME") or "").strip()
    password = app.config.get("ADMIN_PASSWORD") or ""
    security_question = (app.config.get("ADMIN_SECURITY_QUESTION") or "").strip()
    security_answer = (app.config.get("ADMIN_SECURITY_ANSWER") or "").strip()
    preferred_admin_code = app.config.get("ADMIN_UNIQUE_CODE") or ""
    force_credential_sync = bool(app.config.get("ADMIN_FORCE_CREDENTIAL_SYNC"))

    if not email or not full_name:
        app.logger.warning("Admin bootstrap is enabled but identity configuration is incomplete.")
        return

    normalized_admin_code = (preferred_admin_code or "").strip().upper()

    admin_user = User.query.filter_by(email=email).first()
    if not admin_user and normalized_admin_code:
        admin_user = User.query.filter_by(unique_user_code=normalized_admin_code).first()
    if not admin_user:
        admin_user = User.query.filter_by(role="admin").order_by(User.id.asc()).first()

    if not admin_user:
        if not password or not security_question or not security_answer:
            app.logger.warning("Admin bootstrap cannot create admin because credential configuration is incomplete.")
            return
        if _is_weak_admin_password(password):
            app.logger.warning(
                "Admin bootstrap skipped because CLARIFAI_ADMIN_PASSWORD is weak. "
                "Set a strong secret via environment variable."
            )
            return

        admin_user = User(
            unique_user_code=_resolve_admin_user_code(User, preferred_admin_code),
            full_name=full_name,
            email=email,
            role="admin",
            course="MCA",
            security_question=security_question,
            security_answer_hash="",
            is_active=True,
        )
        admin_user.set_password(password)
        admin_user.set_security_answer(security_answer)
        db.session.add(admin_user)
    else:
        admin_user.email = email
        admin_user.full_name = full_name
        admin_user.role = "admin"
        admin_user.course = "MCA"
        admin_user.is_active = True
        admin_user.unique_user_code = _resolve_admin_user_code(User, preferred_admin_code)

        if security_question:
            admin_user.security_question = security_question

        if force_credential_sync:
            if not password or not security_question or not security_answer:
                app.logger.warning(
                    "ADMIN_FORCE_CREDENTIAL_SYNC is enabled but admin credential fields are incomplete. "
                    "Skipping forced credential update for existing admin."
                )
            elif _is_weak_admin_password(password):
                app.logger.warning(
                    "ADMIN_FORCE_CREDENTIAL_SYNC is enabled but CLARIFAI_ADMIN_PASSWORD is weak. "
                    "Skipping forced credential update for existing admin."
                )
            else:
                admin_user.set_password(password)
                admin_user.set_security_answer(security_answer)
        elif not admin_user.security_answer_hash and security_answer:
            # Backfill once for legacy admin rows created before security answer hashing.
            admin_user.set_security_answer(security_answer)

    db.session.commit()


def _ensure_schema_updates() -> None:
    user_columns = {
        row[1]
        for row in db.session.execute(text("PRAGMA table_info('users')")).fetchall()
    }
    feedback_columns = {
        row[1]
        for row in db.session.execute(text("PRAGMA table_info('feedback')")).fetchall()
    }
    knowledge_post_columns = {
        row[1]
        for row in db.session.execute(text("PRAGMA table_info('knowledge_posts')")).fetchall()
    }
    website_feedback_columns = {
        row[1]
        for row in db.session.execute(text("PRAGMA table_info('website_feedback')")).fetchall()
    }

    alter_statements = []
    if "phone" not in user_columns:
        alter_statements.append(
            "ALTER TABLE users ADD COLUMN phone VARCHAR(30)"
        )
    if "notification_prefs" not in user_columns:
        alter_statements.append(
            "ALTER TABLE users ADD COLUMN notification_prefs TEXT"
        )
    if "last_login_at" not in user_columns:
        alter_statements.append(
            "ALTER TABLE users ADD COLUMN last_login_at DATETIME"
        )
    if "first_login_at" not in user_columns:
        alter_statements.append(
            "ALTER TABLE users ADD COLUMN first_login_at DATETIME"
        )

    if "subject" not in feedback_columns:
        alter_statements.append(
            "ALTER TABLE feedback ADD COLUMN subject VARCHAR(120) NOT NULL DEFAULT ''"
        )
    if "semester" not in feedback_columns:
        alter_statements.append(
            "ALTER TABLE feedback ADD COLUMN semester VARCHAR(20) NOT NULL DEFAULT ''"
        )
    if "reason" not in feedback_columns:
        alter_statements.append(
            "ALTER TABLE feedback ADD COLUMN reason VARCHAR(180) NOT NULL DEFAULT ''"
        )
    if "course_code" not in feedback_columns:
        alter_statements.append(
            "ALTER TABLE feedback ADD COLUMN course_code VARCHAR(20) NOT NULL DEFAULT ''"
        )
    if "feedback_tags" not in feedback_columns:
        alter_statements.append(
            "ALTER TABLE feedback ADD COLUMN feedback_tags VARCHAR(255) NOT NULL DEFAULT ''"
        )
    if "class_session_at" not in feedback_columns:
        alter_statements.append(
            "ALTER TABLE feedback ADD COLUMN class_session_at DATETIME"
        )

    if "problem_context" not in knowledge_post_columns:
        alter_statements.append(
            "ALTER TABLE knowledge_posts ADD COLUMN problem_context TEXT"
        )
    if "solution_steps" not in knowledge_post_columns:
        alter_statements.append(
            "ALTER TABLE knowledge_posts ADD COLUMN solution_steps TEXT"
        )
    if "resource_references" not in knowledge_post_columns:
        alter_statements.append(
            "ALTER TABLE knowledge_posts ADD COLUMN resource_references TEXT"
        )
    if "outcome_result" not in knowledge_post_columns:
        alter_statements.append(
            "ALTER TABLE knowledge_posts ADD COLUMN outcome_result TEXT"
        )
    if "resource_links" not in knowledge_post_columns:
        alter_statements.append(
            "ALTER TABLE knowledge_posts ADD COLUMN resource_links TEXT"
        )
    if "status" not in knowledge_post_columns:
        alter_statements.append(
            "ALTER TABLE knowledge_posts ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'published'"
        )
    if "target_courses" not in knowledge_post_columns:
        alter_statements.append(
            "ALTER TABLE knowledge_posts ADD COLUMN target_courses VARCHAR(120) NOT NULL DEFAULT ''"
        )
    if "target_semesters" not in knowledge_post_columns:
        alter_statements.append(
            "ALTER TABLE knowledge_posts ADD COLUMN target_semesters VARCHAR(120) NOT NULL DEFAULT ''"
        )
    if "target_sections" not in knowledge_post_columns:
        alter_statements.append(
            "ALTER TABLE knowledge_posts ADD COLUMN target_sections VARCHAR(120) NOT NULL DEFAULT ''"
        )
    if "published_at" not in knowledge_post_columns:
        alter_statements.append(
            "ALTER TABLE knowledge_posts ADD COLUMN published_at DATETIME"
        )
    if "updated_at" not in knowledge_post_columns:
        alter_statements.append(
            "ALTER TABLE knowledge_posts ADD COLUMN updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
        )
    if "revision_count" not in knowledge_post_columns:
        alter_statements.append(
            "ALTER TABLE knowledge_posts ADD COLUMN revision_count INTEGER NOT NULL DEFAULT 0"
        )

    if "is_read" not in website_feedback_columns:
        alter_statements.append(
            "ALTER TABLE website_feedback ADD COLUMN is_read BOOLEAN NOT NULL DEFAULT 0"
        )
    if "read_at" not in website_feedback_columns:
        alter_statements.append(
            "ALTER TABLE website_feedback ADD COLUMN read_at DATETIME"
        )

    for statement in alter_statements:
        db.session.execute(text(statement))

    if alter_statements:
        db.session.commit()

    refreshed_user_columns = {
        row[1]
        for row in db.session.execute(text("PRAGMA table_info('users')")).fetchall()
    }
    if "first_login_at" in refreshed_user_columns and "last_login_at" in refreshed_user_columns:
        db.session.execute(
            text(
                "UPDATE users "
                "SET first_login_at = last_login_at "
                "WHERE first_login_at IS NULL AND last_login_at IS NOT NULL"
            )
        )
        db.session.commit()


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    app.register_blueprint(auth_bp)
    app.register_blueprint(student_bp)
    app.register_blueprint(faculty_bp)
    app.register_blueprint(admin_bp)

    _validate_security_config(app)

    @app.template_filter("ist_datetime")
    def _ist_datetime_filter(value, fmt: str = "%d %b %Y %I:%M %p"):
        localized = _utc_to_ist(value)
        if not localized:
            return "-"
        return localized.strftime(fmt)

    @app.context_processor
    def _inject_admin_notification_count():
        unread_suggestions_count = 0
        pending_semester_exceptions_count = 0
        role_notification_badge_count = 0
        pending_experience_moderation_count = 0
        open_experience_reports_count = 0
        pending_faculty_feedback_count = 0

        user_id = session.get("user_id")
        role = session.get("role")
        if user_id and role == "admin":
            unread_suggestions_count = WebsiteFeedback.query.filter_by(is_read=False).count()
            pending_semester_exceptions_count = SemesterMismatchRequest.query.filter_by(status="pending").count()
            moderation_queue_count = Feedback.query.filter_by(status="under_review").count()
            pending_experience_moderation_count = StudentExperience.query.filter_by(status="pending").count()
            open_experience_reports_count = ExperienceReport.query.filter_by(status="open").count()
            pending_faculty_feedback_count = PendingFacultyFeedback.query.filter(
                PendingFacultyFeedback.status.in_(["holding", "under_review"])
            ).count()
            role_notification_badge_count = (
                unread_suggestions_count
                + pending_semester_exceptions_count
                + moderation_queue_count
                + pending_experience_moderation_count
                + open_experience_reports_count
                + pending_faculty_feedback_count
            )
        elif user_id and role == "student":
            feedback_updates_count = Feedback.query.filter(
                Feedback.student_id == user_id,
                Feedback.status.in_(["approved", "request_edit", "rejected"]),
            ).count()
            pending_checklist_count = Checklist.query.filter_by(student_id=user_id, is_completed=False).count()
            intervention_updates_count = KnowledgeNotification.query.filter_by(user_id=user_id, is_read=False).count()
            role_notification_badge_count = feedback_updates_count + pending_checklist_count + intervention_updates_count
        elif user_id and role == "faculty":
            approved_feedback_count = Feedback.query.filter_by(faculty_id=user_id, status="approved").count()
            checklist_rows = Checklist.query.filter_by(faculty_id=user_id).all()
            grouped_completion = {}
            for row in checklist_rows:
                group_id = _extract_checklist_group_id(row.description, row.id)
                if group_id not in grouped_completion:
                    grouped_completion[group_id] = True
                if not row.is_completed:
                    grouped_completion[group_id] = False

            active_checklists_count = len([done for done in grouped_completion.values() if not done])
            role_notification_badge_count = approved_feedback_count + active_checklists_count

        return {
            "unread_suggestions_count": unread_suggestions_count,
            "pending_semester_exceptions_count": pending_semester_exceptions_count,
            "role_notification_badge_count": role_notification_badge_count,
            "pending_experience_moderation_count": pending_experience_moderation_count,
            "open_experience_reports_count": open_experience_reports_count,
            "pending_faculty_feedback_count": pending_faculty_feedback_count,
        }

    @app.before_request
    def _protect_authenticated_posts():
        if app.testing:
            return None

        if request.method != "POST" or not session.get("user_id"):
            return None

        if not request.path.startswith("/admin/"):
            return None

        host_url = request.host_url.rstrip("/")
        origin = request.headers.get("Origin", "").strip()
        referer = request.headers.get("Referer", "").strip()

        if origin and not _is_same_origin(origin, host_url):
            abort(403)

        if not origin:
            if not referer or not _is_same_origin(referer, host_url):
                abort(403)

        return None

    @app.after_request
    def _apply_cache_control_headers(response):
        if request.path.startswith("/static/"):
            return response

        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0, private"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    @app.route("/", methods=["GET", "POST"])
    def home():
        role = session.get("role") if session.get("user_id") else None
        dashboard_url = None
        if role == "student":
            dashboard_url = url_for("student.dashboard")
        elif role == "faculty":
            dashboard_url = url_for("faculty.dashboard")
        elif role == "admin":
            dashboard_url = url_for("admin.dashboard")

        if request.method == "POST":
            visitor_name = request.form.get("visitor_name", "").strip()
            visitor_email = request.form.get("visitor_email", "").strip().lower()
            message = request.form.get("message", "").strip()

            if not visitor_name or not visitor_email or not message:
                flash("Please fill all website feedback fields.", "danger")
                return render_template("home.html", dashboard_url=dashboard_url, user_role=role)

            website_feedback = WebsiteFeedback(
                visitor_name=visitor_name,
                visitor_email=visitor_email,
                message=message,
            )
            db.session.add(website_feedback)
            db.session.commit()
            flash("Thank you! Your suggestion has been submitted.", "success")
            return redirect(url_for("home"))

        return render_template("home.html", dashboard_url=dashboard_url, user_role=role)

    with app.app_context():
        db.create_all()
        _ensure_schema_updates()
        _seed_course_configs()
        _ensure_user_delete_guard(app)
        _bootstrap_admin(app)

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
