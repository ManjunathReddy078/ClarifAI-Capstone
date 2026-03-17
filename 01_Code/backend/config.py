from pathlib import Path
import os
import secrets
from datetime import timedelta


BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent


def _resolve_secret_key() -> str:
	env_secret = (os.getenv("CLARIFAI_SECRET_KEY") or "").strip()
	if env_secret:
		return env_secret

	# Keep a stable local key so sessions survive app restarts during localhost demos.
	secret_file = BASE_DIR / ".clarifai_secret_key"
	if secret_file.exists():
		stored = secret_file.read_text(encoding="utf-8").strip()
		if stored:
			return stored

	generated = secrets.token_hex(32)
	secret_file.write_text(generated, encoding="utf-8")
	return generated


class Config:
	SECRET_KEY = _resolve_secret_key()
	SQLALCHEMY_DATABASE_URI = f"sqlite:///{PROJECT_ROOT / 'database' / 'clarifai.db'}"
	SQLALCHEMY_TRACK_MODIFICATIONS = False
	APP_ENV = os.getenv("CLARIFAI_ENV", os.getenv("FLASK_ENV", "development")).strip().lower()
	SESSION_PERMANENT = False
	SESSION_COOKIE_HTTPONLY = True
	SESSION_COOKIE_SAMESITE = "Lax"
	SESSION_COOKIE_SECURE = os.getenv("CLARIFAI_SESSION_COOKIE_SECURE", "false").lower() in {
		"1",
		"true",
		"yes",
		"y",
	}
	ADMIN_BOOTSTRAP_ENABLED = os.getenv("CLARIFAI_ADMIN_BOOTSTRAP_ENABLED", "true").lower() in {
		"1",
		"true",
		"yes",
		"y",
	}
	ADMIN_FORCE_CREDENTIAL_SYNC = os.getenv("CLARIFAI_ADMIN_FORCE_CREDENTIAL_SYNC", "false").lower() in {
		"1",
		"true",
		"yes",
		"y",
	}
	USER_DELETE_GUARD_ENABLED = os.getenv(
		"CLARIFAI_USER_DELETE_GUARD_ENABLED",
		"true" if APP_ENV == "production" else "false",
	).lower() in {
		"1",
		"true",
		"yes",
		"y",
	}
	REMEMBER_ME_DAYS = int(os.getenv("CLARIFAI_REMEMBER_ME_DAYS", "3"))
	PERMANENT_SESSION_LIFETIME = timedelta(days=REMEMBER_ME_DAYS)
	ADMIN_FULL_NAME = os.getenv("CLARIFAI_ADMIN_FULL_NAME", "Administrator")
	ADMIN_EMAIL = os.getenv("CLARIFAI_ADMIN_EMAIL", "caiadminca007@gmail.com")
	ADMIN_PASSWORD = os.getenv("CLARIFAI_ADMIN_PASSWORD", "")
	ADMIN_UNIQUE_CODE = os.getenv("CLARIFAI_ADMIN_UNIQUE_CODE", "CAIA007")
	ADMIN_SECURITY_QUESTION = os.getenv(
		"CLARIFAI_ADMIN_SECURITY_QUESTION",
		"What is your birth city ?",
	)
	ADMIN_SECURITY_ANSWER = os.getenv("CLARIFAI_ADMIN_SECURITY_ANSWER", "")
	ALLOW_SELF_REGISTER = os.getenv("CLARIFAI_ALLOW_SELF_REGISTER", "false").lower() in {
		"1",
		"true",
		"yes",
		"y",
	}
