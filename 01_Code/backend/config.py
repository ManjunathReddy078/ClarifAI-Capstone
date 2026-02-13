from pathlib import Path
import os


BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent


class Config:
	SECRET_KEY = os.getenv("CLARIFAI_SECRET_KEY", "clarifai-mca-secret-key")
	SQLALCHEMY_DATABASE_URI = f"sqlite:///{PROJECT_ROOT / 'database' / 'clarifai.db'}"
	SQLALCHEMY_TRACK_MODIFICATIONS = False
	ADMIN_BOOTSTRAP_ENABLED = os.getenv("CLARIFAI_ADMIN_BOOTSTRAP_ENABLED", "true").lower() in {
		"1",
		"true",
		"yes",
		"y",
	}
	ADMIN_FULL_NAME = os.getenv("CLARIFAI_ADMIN_FULL_NAME", "Seelam Manjunath Reddy")
	ADMIN_EMAIL = os.getenv("CLARIFAI_ADMIN_EMAIL", "seelammanjunathreddy6@gmail.com")
	ADMIN_PASSWORD = os.getenv("CLARIFAI_ADMIN_PASSWORD", "Admin@007")
	ADMIN_SECURITY_QUESTION = os.getenv(
		"CLARIFAI_ADMIN_SECURITY_QUESTION",
		"What is your birth city?",
	)
	ADMIN_SECURITY_ANSWER = os.getenv("CLARIFAI_ADMIN_SECURITY_ANSWER", "Kadapa")
