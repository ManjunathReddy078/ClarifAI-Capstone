from datetime import date, datetime

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
	phone = db.Column(db.String(30), nullable=True)
	notification_prefs = db.Column(db.Text, nullable=True)
	password_hash = db.Column(db.String(255), nullable=False)
	security_question = db.Column(db.String(255), nullable=False)
	security_answer_hash = db.Column(db.String(255), nullable=False)
	is_active = db.Column(db.Boolean, default=True, nullable=False)
	first_login_at = db.Column(db.DateTime, nullable=True)
	last_login_at = db.Column(db.DateTime, nullable=True)
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
	knowledge_reactions = db.relationship("KnowledgeReaction", back_populates="user", lazy="dynamic")
	knowledge_notifications = db.relationship("KnowledgeNotification", back_populates="user", lazy="dynamic")
	knowledge_views = db.relationship("KnowledgeView", back_populates="user", lazy="dynamic")
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
	student_profile = db.relationship(
		"StudentAcademicProfile",
		back_populates="user",
		uselist=False,
		cascade="all, delete-orphan",
	)
	lifecycle_events = db.relationship(
		"LifecycleEvent",
		back_populates="user",
		lazy="dynamic",
		cascade="all, delete-orphan",
	)

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
	course_code = db.Column(db.String(20), nullable=False, default="")
	subject = db.Column(db.String(120), nullable=False, default="")
	semester = db.Column(db.String(20), nullable=False, default="")
	reason = db.Column(db.String(180), nullable=False, default="")
	feedback_tags = db.Column(db.String(255), nullable=False, default="")
	class_session_at = db.Column(db.DateTime, nullable=True, default=datetime.utcnow)
	feedback_text = db.Column(db.Text, nullable=False)
	sentiment = db.Column(db.String(20), nullable=False)
	status = db.Column(db.String(30), nullable=False)
	admin_note = db.Column(db.Text, nullable=True)
	student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
	faculty_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
	created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

	student = db.relationship("User", back_populates="submitted_feedback", foreign_keys=[student_id])
	faculty = db.relationship("User", back_populates="assigned_feedback", foreign_keys=[faculty_id])
	moderation_logs = db.relationship(
		"ModerationLog",
		back_populates="feedback",
		lazy="dynamic",
		cascade="all, delete-orphan",
	)


class KnowledgePost(db.Model):
	__tablename__ = "knowledge_posts"

	id = db.Column(db.Integer, primary_key=True)
	title = db.Column(db.String(180), nullable=False)
	content = db.Column(db.Text, nullable=False)
	problem_context = db.Column(db.Text, nullable=True)
	solution_steps = db.Column(db.Text, nullable=True)
	resource_references = db.Column(db.Text, nullable=True)
	outcome_result = db.Column(db.Text, nullable=True)
	resource_links = db.Column(db.Text, nullable=True)
	status = db.Column(db.String(20), nullable=False, default="published")
	target_courses = db.Column(db.String(120), nullable=False, default="")
	target_semesters = db.Column(db.String(120), nullable=False, default="")
	target_sections = db.Column(db.String(120), nullable=False, default="")
	published_at = db.Column(db.DateTime, nullable=True)
	updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
	revision_count = db.Column(db.Integer, nullable=False, default=0)
	author_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
	created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

	author = db.relationship("User", back_populates="authored_posts")
	attachments = db.relationship(
		"KnowledgeAttachment",
		back_populates="post",
		lazy="dynamic",
		cascade="all, delete-orphan",
	)
	reactions = db.relationship(
		"KnowledgeReaction",
		back_populates="post",
		lazy="dynamic",
		cascade="all, delete-orphan",
	)
	notifications = db.relationship(
		"KnowledgeNotification",
		back_populates="post",
		lazy="dynamic",
		cascade="all, delete-orphan",
	)
	views = db.relationship(
		"KnowledgeView",
		back_populates="post",
		lazy="dynamic",
		cascade="all, delete-orphan",
	)


class KnowledgeAttachment(db.Model):
	__tablename__ = "knowledge_attachments"

	id = db.Column(db.Integer, primary_key=True)
	post_id = db.Column(db.Integer, db.ForeignKey("knowledge_posts.id"), nullable=False)
	file_name = db.Column(db.String(255), nullable=False)
	file_path = db.Column(db.String(400), nullable=False)
	file_ext = db.Column(db.String(20), nullable=False)
	file_size = db.Column(db.Integer, nullable=False, default=0)
	created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

	post = db.relationship("KnowledgePost", back_populates="attachments")


class KnowledgeReaction(db.Model):
	__tablename__ = "knowledge_reactions"

	id = db.Column(db.Integer, primary_key=True)
	post_id = db.Column(db.Integer, db.ForeignKey("knowledge_posts.id"), nullable=False)
	user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
	reaction_type = db.Column(db.String(20), nullable=False)
	created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

	post = db.relationship("KnowledgePost", back_populates="reactions")
	user = db.relationship("User", back_populates="knowledge_reactions")

	__table_args__ = (
		db.UniqueConstraint("post_id", "user_id", "reaction_type", name="uq_knowledge_reaction"),
	)


class KnowledgeNotification(db.Model):
	__tablename__ = "knowledge_notifications"

	id = db.Column(db.Integer, primary_key=True)
	post_id = db.Column(db.Integer, db.ForeignKey("knowledge_posts.id"), nullable=False)
	user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
	message = db.Column(db.String(300), nullable=False)
	is_read = db.Column(db.Boolean, nullable=False, default=False)
	created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

	post = db.relationship("KnowledgePost", back_populates="notifications")
	user = db.relationship("User", back_populates="knowledge_notifications")


class KnowledgeView(db.Model):
	__tablename__ = "knowledge_views"

	id = db.Column(db.Integer, primary_key=True)
	post_id = db.Column(db.Integer, db.ForeignKey("knowledge_posts.id"), nullable=False)
	user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
	first_opened_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
	last_opened_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

	post = db.relationship("KnowledgePost", back_populates="views")
	user = db.relationship("User", back_populates="knowledge_views")

	__table_args__ = (
		db.UniqueConstraint("post_id", "user_id", name="uq_knowledge_view"),
	)


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
	is_read = db.Column(db.Boolean, default=False, nullable=False)
	read_at = db.Column(db.DateTime, nullable=True)
	created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class CourseConfig(db.Model):
	__tablename__ = "course_configs"

	id = db.Column(db.Integer, primary_key=True)
	course_code = db.Column(db.String(20), unique=True, nullable=False)
	duration_years = db.Column(db.Integer, nullable=False)
	total_semesters = db.Column(db.Integer, nullable=False)
	semesters_per_year = db.Column(db.Integer, nullable=False, default=2)
	is_active = db.Column(db.Boolean, nullable=False, default=True)
	created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class StudentAcademicProfile(db.Model):
	__tablename__ = "student_academic_profiles"

	id = db.Column(db.Integer, primary_key=True)
	user_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False)
	course_code = db.Column(db.String(20), nullable=False)
	batch_start_year = db.Column(db.Integer, nullable=False)
	batch_end_year = db.Column(db.Integer, nullable=False)
	admission_month = db.Column(db.Integer, nullable=False)
	admission_year = db.Column(db.Integer, nullable=False)
	current_semester = db.Column(db.Integer, nullable=False, default=1)
	max_semester = db.Column(db.Integer, nullable=False)
	progression_mode = db.Column(db.String(20), nullable=False, default="auto")
	lifecycle_status = db.Column(db.String(20), nullable=False, default="active")
	graduation_date = db.Column(db.Date, nullable=True)
	grace_until = db.Column(db.Date, nullable=True)
	last_semester_updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
	created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
	updated_at = db.Column(
		db.DateTime,
		nullable=False,
		default=datetime.utcnow,
		onupdate=datetime.utcnow,
	)

	user = db.relationship("User", back_populates="student_profile")


class SemesterMismatchRequest(db.Model):
	__tablename__ = "semester_mismatch_requests"

	id = db.Column(db.Integer, primary_key=True)
	email = db.Column(db.String(120), nullable=False, index=True)
	full_name = db.Column(db.String(120), nullable=False)
	prn = db.Column(db.String(30), nullable=True)
	course_code = db.Column(db.String(20), nullable=False)
	section = db.Column(db.String(20), nullable=True)
	batch_start_year = db.Column(db.Integer, nullable=True)
	batch_end_year = db.Column(db.Integer, nullable=True)
	admission_month = db.Column(db.Integer, nullable=True)
	admission_year = db.Column(db.Integer, nullable=True)
	requested_semester = db.Column(db.Integer, nullable=False)
	suggested_semester = db.Column(db.Integer, nullable=True)
	whitelist_semester = db.Column(db.Integer, nullable=True)
	status = db.Column(db.String(20), nullable=False, default="pending", index=True)
	admin_note = db.Column(db.Text, nullable=True)
	admin_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
	reviewed_at = db.Column(db.DateTime, nullable=True)
	created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

	admin = db.relationship("User", foreign_keys=[admin_id])

	__table_args__ = (
		db.Index("ix_semester_mismatch_email_prn_status", "email", "prn", "status"),
	)


class SemesterCalendar(db.Model):
	__tablename__ = "semester_calendars"

	id = db.Column(db.Integer, primary_key=True)
	course_code = db.Column(db.String(20), nullable=False)
	semester_no = db.Column(db.Integer, nullable=False)
	start_date = db.Column(db.Date, nullable=False)
	end_date = db.Column(db.Date, nullable=False)
	is_active = db.Column(db.Boolean, nullable=False, default=True)
	created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

	__table_args__ = (
		db.UniqueConstraint("course_code", "semester_no", "start_date", name="uq_semester_calendar_slot"),
	)


class SubjectOffering(db.Model):
	__tablename__ = "subject_offerings"

	id = db.Column(db.Integer, primary_key=True)
	course_code = db.Column(db.String(20), nullable=False)
	semester_no = db.Column(db.Integer, nullable=False)
	section = db.Column(db.String(20), nullable=False)
	subject_code = db.Column(db.String(20), nullable=False)
	subject_name = db.Column(db.String(180), nullable=False)
	is_active = db.Column(db.Boolean, nullable=False, default=True)
	created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

	faculty_assignments = db.relationship(
		"FacultyAssignment",
		back_populates="subject_offering",
		lazy="dynamic",
		cascade="all, delete-orphan",
	)

	__table_args__ = (
		db.UniqueConstraint(
			"course_code",
			"semester_no",
			"section",
			"subject_code",
			name="uq_subject_offering",
		),
	)


class FacultyAssignment(db.Model):
	__tablename__ = "faculty_assignments"

	id = db.Column(db.Integer, primary_key=True)
	subject_offering_id = db.Column(db.Integer, db.ForeignKey("subject_offerings.id"), nullable=False)
	faculty_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
	effective_from = db.Column(db.Date, nullable=False, default=date.today)
	effective_to = db.Column(db.Date, nullable=True)
	is_active = db.Column(db.Boolean, nullable=False, default=True)
	created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

	subject_offering = db.relationship("SubjectOffering", back_populates="faculty_assignments")
	faculty_user = db.relationship("User", foreign_keys=[faculty_user_id])

	__table_args__ = (
		db.UniqueConstraint(
			"subject_offering_id",
			"faculty_user_id",
			"effective_from",
			name="uq_faculty_assignment",
		),
	)


class LifecycleEvent(db.Model):
	__tablename__ = "lifecycle_events"

	id = db.Column(db.Integer, primary_key=True)
	user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
	event_type = db.Column(db.String(40), nullable=False)
	old_status = db.Column(db.String(20), nullable=True)
	new_status = db.Column(db.String(20), nullable=True)
	note = db.Column(db.Text, nullable=True)
	created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

	user = db.relationship("User", back_populates="lifecycle_events")


class StudentExperience(db.Model):
	__tablename__ = "student_experiences"

	id = db.Column(db.Integer, primary_key=True)
	anon_id = db.Column(db.String(16), unique=True, nullable=False)
	author_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
	title = db.Column(db.String(180), nullable=False)
	body = db.Column(db.Text, nullable=False)
	category = db.Column(db.String(50), nullable=False)
	tags = db.Column(db.String(600), nullable=False, default="")
	resource_links = db.Column(db.Text, nullable=True)
	sentiment = db.Column(db.String(20), nullable=False, default="neutral")
	sentiment_confidence = db.Column(db.Integer, nullable=False, default=0)
	status = db.Column(db.String(20), nullable=False, default="pending")
	admin_note = db.Column(db.Text, nullable=True)
	upvote_count = db.Column(db.Integer, nullable=False, default=0)
	created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

	author = db.relationship("User", foreign_keys=[author_id])
	upvotes = db.relationship(
		"ExperienceUpvote",
		back_populates="experience",
		lazy="dynamic",
		cascade="all, delete-orphan",
	)
	reports = db.relationship(
		"ExperienceReport",
		back_populates="experience",
		lazy="dynamic",
		cascade="all, delete-orphan",
	)


class ExperienceUpvote(db.Model):
	__tablename__ = "experience_upvotes"

	id = db.Column(db.Integer, primary_key=True)
	experience_id = db.Column(db.Integer, db.ForeignKey("student_experiences.id"), nullable=False)
	user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
	created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

	experience = db.relationship("StudentExperience", back_populates="upvotes")
	user = db.relationship("User")

	__table_args__ = (
		db.UniqueConstraint("experience_id", "user_id", name="uq_experience_upvote"),
	)


class ExperienceReport(db.Model):
	__tablename__ = "experience_reports"

	id = db.Column(db.Integer, primary_key=True)
	experience_id = db.Column(db.Integer, db.ForeignKey("student_experiences.id"), nullable=False)
	reporter_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
	report_category = db.Column(db.String(60), nullable=False)
	reason = db.Column(db.Text, nullable=False)
	status = db.Column(db.String(20), nullable=False, default="open")
	admin_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
	reviewed_at = db.Column(db.DateTime, nullable=True)
	created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

	experience = db.relationship("StudentExperience", back_populates="reports")
	reporter = db.relationship("User", foreign_keys=[reporter_id])
	admin = db.relationship("User", foreign_keys=[admin_id])

	__table_args__ = (
		db.UniqueConstraint("experience_id", "reporter_id", name="uq_experience_report"),
	)


class PendingFacultyFeedback(db.Model):
	__tablename__ = "pending_faculty_feedback"

	id = db.Column(db.Integer, primary_key=True)
	student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
	course_code = db.Column(db.String(20), nullable=False)
	section = db.Column(db.String(20), nullable=False)
	semester = db.Column(db.String(20), nullable=False)
	subject_code = db.Column(db.String(20), nullable=False)
	subject = db.Column(db.String(120), nullable=False)
	assigned_faculty_id = db.Column(db.String(40), nullable=False)
	assigned_faculty_email = db.Column(db.String(120), nullable=True)
	assigned_faculty_name = db.Column(db.String(120), nullable=True)
	reason = db.Column(db.String(180), nullable=False)
	feedback_tags = db.Column(db.String(255), nullable=False)
	class_session_at = db.Column(db.DateTime, nullable=True)
	feedback_text = db.Column(db.Text, nullable=False)
	sentiment = db.Column(db.String(20), nullable=False)
	sentiment_confidence = db.Column(db.Integer, nullable=False, default=0)
	status = db.Column(db.String(30), nullable=False, default="holding")
	admin_note = db.Column(db.Text, nullable=True)
	created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

	student = db.relationship("User", foreign_keys=[student_id])

