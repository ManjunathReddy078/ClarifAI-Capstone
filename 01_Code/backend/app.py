import random

from flask import Flask, flash, redirect, render_template, request, session, url_for
from sqlalchemy import text

from config import Config
from models import WebsiteFeedback, db
from routes.admin import admin_bp
from routes.auth import auth_bp
from routes.faculty import faculty_bp
from routes.student import student_bp


def _generate_unique_user_code(user_model) -> str:
    for _ in range(1200):
        candidate = f"CAIMCA{random.randint(100, 999)}"
        if not user_model.query.filter_by(unique_user_code=candidate).first():
            return candidate
    raise RuntimeError("Unable to generate unique admin user code.")


def _bootstrap_admin(app: Flask) -> None:
    if not app.config.get("ADMIN_BOOTSTRAP_ENABLED"):
        return

    from models import User

    email = (app.config.get("ADMIN_EMAIL") or "").strip().lower()
    full_name = (app.config.get("ADMIN_FULL_NAME") or "").strip()
    password = app.config.get("ADMIN_PASSWORD") or ""
    security_question = (app.config.get("ADMIN_SECURITY_QUESTION") or "").strip()
    security_answer = (app.config.get("ADMIN_SECURITY_ANSWER") or "").strip()

    if not email or not full_name or not password or not security_question or not security_answer:
        app.logger.warning("Admin bootstrap is enabled but configuration is incomplete.")
        return

    admin_user = User.query.filter_by(email=email).first()
    if not admin_user:
        admin_user = User(
            unique_user_code=_generate_unique_user_code(User),
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
        admin_user.full_name = full_name
        admin_user.role = "admin"
        admin_user.course = "MCA"
        admin_user.security_question = security_question
        admin_user.is_active = True
        if not admin_user.unique_user_code:
            admin_user.unique_user_code = _generate_unique_user_code(User)
        admin_user.set_password(password)
        admin_user.set_security_answer(security_answer)

    db.session.commit()


def _ensure_schema_updates() -> None:
    feedback_columns = {
        row[1]
        for row in db.session.execute(text("PRAGMA table_info('feedback')")).fetchall()
    }

    alter_statements = []
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

    for statement in alter_statements:
        db.session.execute(text(statement))

    if alter_statements:
        db.session.commit()


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    app.register_blueprint(auth_bp)
    app.register_blueprint(student_bp)
    app.register_blueprint(faculty_bp)
    app.register_blueprint(admin_bp)

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
        _bootstrap_admin(app)

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
