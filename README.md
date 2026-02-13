# ClarifAI MCA Project

ClarifAI is a Flask + SQLAlchemy + SQLite project for MCA departmental feedback management with role-based access for Student, Faculty, and Admin.

## Project Structure

- `01_Code/backend` Flask backend, routes, templates, static assets
- `01_Code/database/clarifai.db` SQLite database
- `01_Code/backend/data/whitelist.csv` whitelist source for student/faculty registration

## Core Features

- Whitelist-based registration (`student`, `faculty` only)
- Secure login/logout and password reset with security question verification
- Sentiment analysis on student reviews
- Role dashboards:
  - Student: review CRUD, filters, knowledge posts CRUD, checklist tracking
  - Faculty: approved review insights, sentiment filters, checklist management
  - Admin: moderation queue/actions, user management, manual password reset

## Tech Stack

- Python 3.11+
- Flask
- Flask-SQLAlchemy + SQLite
- Jinja templates + custom CSS/JS
- TextBlob (sentiment)

## Setup

From `01_Code/backend`:

1. Create venv (if needed)
	- `python -m venv venv`
2. Activate venv (PowerShell)
	- `./venv/Scripts/Activate.ps1`
3. Install dependencies
	- `pip install -r requirements.txt`
4. Run app
	- `python app.py`

The app initializes tables automatically with `db.create_all()` and applies additive schema updates for the feedback metadata fields.

## Configuration (Environment Variables)

`01_Code/backend/config.py` supports env overrides:

- `CLARIFAI_SECRET_KEY`
- `CLARIFAI_ADMIN_BOOTSTRAP_ENABLED` (`true/false`)
- `CLARIFAI_ADMIN_FULL_NAME`
- `CLARIFAI_ADMIN_EMAIL`
- `CLARIFAI_ADMIN_PASSWORD`
- `CLARIFAI_ADMIN_SECURITY_QUESTION`
- `CLARIFAI_ADMIN_SECURITY_ANSWER`

If env vars are not set, defaults from `config.py` are used.

## Whitelist Format

`01_Code/backend/data/whitelist.csv` expects columns (tab or comma delimited):

- `role`, `full_name`, `email`, `prn`, `faculty_id`, `section`, `course`, `allowed`

Notes:

- For `student`, use `prn` and `section`.
- For `faculty`, use `faculty_id`.
- `allowed` supports truthy values like `true`, `yes`, `1`.

## Final Validation

Use the automated smoke test from `01_Code/backend`:

- `python scripts/final_smoke_test.py`

This script:

- Verifies public/auth route health
- Creates temporary test accounts for all roles
- Executes student/faculty/admin critical flows
- Cleans temporary records at the end

## Notes for Demo / Viva

- Use seeded admin or env-configured admin credentials for moderation and user management.
- Student-to-faculty review routing is sentiment-driven:
  - Positive -> auto approved
  - Neutral/Negative -> under review (admin moderation required)
