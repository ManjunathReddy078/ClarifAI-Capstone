from datetime import datetime

from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash


db = SQLAlchemy()


class User(db.Model):
	__tablename__ = "users"

	id = db.Column(db.Integer, primary_key=True)
	unique_user_code = db.Column(db.String(16), unique=True, nullable=False)
	full_name = db.Column(db.String(120), nullable=False)
	email = db.Column(db.String(120), unique=True, nullable=False)
	role = db.Column(db.String(20), nullable=False)
	prn = db.Column(db.String(30), nullable=True)
	faculty_id = db.Column(db.String(30), nullable=True)
	section = db.Column(db.String(20), nullable=True)
	course = db.Column(db.String(20), nullable=False, default="MCA")
	password_hash = db.Column(db.String(255), nullable=False)
	security_question = db.Column(db.String(255), nullable=False)
	security_answer_hash = db.Column(db.String(255), nullable=False)
	is_active = db.Column(db.Boolean, default=True, nullable=False)
	created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

	submitted_feedback = db.relationship(
		"Feedback",
		back_populates="student",
		foreign_keys="Feedback.student_id",
		lazy="dynamic",
	)
	assigned_feedback = db.relationship(
		"Feedback",
		back_populates="faculty",
		foreign_keys="Feedback.faculty_id",
		lazy="dynamic",
	)
	authored_posts = db.relationship("KnowledgePost", back_populates="author", lazy="dynamic")
	created_checklists = db.relationship(
		"Checklist",
		back_populates="faculty",
		foreign_keys="Checklist.faculty_id",
		lazy="dynamic",
	)
	student_checklists = db.relationship(
		"Checklist",
		back_populates="student",
		foreign_keys="Checklist.student_id",
		lazy="dynamic",
	)
	moderation_logs = db.relationship("ModerationLog", back_populates="admin", lazy="dynamic")

	def set_password(self, raw_password: str) -> None:
		self.password_hash = generate_password_hash(raw_password)

	def check_password(self, raw_password: str) -> bool:
		return check_password_hash(self.password_hash, raw_password)

	def set_security_answer(self, raw_answer: str) -> None:
		self.security_answer_hash = generate_password_hash(raw_answer.strip().lower())

	def check_security_answer(self, raw_answer: str) -> bool:
		return check_password_hash(self.security_answer_hash, raw_answer.strip().lower())


class Feedback(db.Model):
	__tablename__ = "feedback"

	id = db.Column(db.Integer, primary_key=True)
	subject = db.Column(db.String(120), nullable=False, default="")
	semester = db.Column(db.String(20), nullable=False, default="")
	reason = db.Column(db.String(180), nullable=False, default="")
	feedback_text = db.Column(db.Text, nullable=False)
	sentiment = db.Column(db.String(20), nullable=False)
	status = db.Column(db.String(30), nullable=False)
	admin_note = db.Column(db.Text, nullable=True)
	student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
	faculty_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
	created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

	student = db.relationship("User", back_populates="submitted_feedback", foreign_keys=[student_id])
	faculty = db.relationship("User", back_populates="assigned_feedback", foreign_keys=[faculty_id])
	moderation_logs = db.relationship("ModerationLog", back_populates="feedback", lazy="dynamic")


class KnowledgePost(db.Model):
	__tablename__ = "knowledge_posts"

	id = db.Column(db.Integer, primary_key=True)
	title = db.Column(db.String(180), nullable=False)
	content = db.Column(db.Text, nullable=False)
	author_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
	created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

	author = db.relationship("User", back_populates="authored_posts")


class Checklist(db.Model):
	__tablename__ = "checklists"

	id = db.Column(db.Integer, primary_key=True)
	title = db.Column(db.String(180), nullable=False)
	description = db.Column(db.Text, nullable=True)
	is_completed = db.Column(db.Boolean, default=False, nullable=False)
	faculty_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
	student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
	created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

	faculty = db.relationship("User", back_populates="created_checklists", foreign_keys=[faculty_id])
	student = db.relationship("User", back_populates="student_checklists", foreign_keys=[student_id])


class ModerationLog(db.Model):
	__tablename__ = "moderation_logs"

	id = db.Column(db.Integer, primary_key=True)
	feedback_id = db.Column(db.Integer, db.ForeignKey("feedback.id"), nullable=False)
	admin_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
	action = db.Column(db.String(30), nullable=False)
	note = db.Column(db.Text, nullable=True)
	created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

	feedback = db.relationship("Feedback", back_populates="moderation_logs")
	admin = db.relationship("User", back_populates="moderation_logs")


class WebsiteFeedback(db.Model):
	__tablename__ = "website_feedback"

	id = db.Column(db.Integer, primary_key=True)
	visitor_name = db.Column(db.String(120), nullable=False)
	visitor_email = db.Column(db.String(120), nullable=False)
	message = db.Column(db.Text, nullable=False)
	created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
