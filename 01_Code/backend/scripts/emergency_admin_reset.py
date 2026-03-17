"""
ClarifAI Emergency Admin Password Reset
=========================================
Use this script if the admin account is locked out and you cannot log in.

HOW IT WORKS:
    1. Reads ADMIN_EMAIL from config.py to identify the admin account.
    2. Asks you to type CONFIRM, then enter a new password twice.
    3. Updates the password directly in the SQLite database.
    4. Also re-activates the account if it was deactivated.

USAGE:
    cd 01_Code\\backend
    python scripts/emergency_admin_reset.py

ALTERNATIVE (optional force-sync mode):
    You can temporarily force bootstrap credential sync via environment variables:
        1. Set CLARIFAI_ADMIN_FORCE_CREDENTIAL_SYNC=true
        2. Set CLARIFAI_ADMIN_PASSWORD and CLARIFAI_ADMIN_SECURITY_ANSWER
        3. Restart app once (python app.py)
        4. Set CLARIFAI_ADMIN_FORCE_CREDENTIAL_SYNC=false afterward
    This script remains the safest direct recovery path.

REQUIREMENTS:
    Run from inside 01_Code/backend/ with the virtual environment activated.
    The database (clarifai.db) must already exist (i.e. the app was run at
    least once so the admin account was bootstrapped).
"""

import getpass
import os
import sys
from pathlib import Path

# ── Ensure we can import the Flask app ───────────────────────────────────────
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
os.chdir(BACKEND_DIR)          # SQLite path resolution relies on cwd


def _validate_password(password: str) -> str | None:
    """Return an error message string, or None if the password is acceptable."""
    if len(password) < 8:
        return "Password must be at least 8 characters."
    if not any(c.isupper() for c in password):
        return "Password must contain at least one uppercase letter."
    if not any(c.isdigit() for c in password):
        return "Password must contain at least one digit."
    return None


def _import_app_dependencies():
    """Import app and model dependencies with a friendly interpreter hint on failure."""
    try:
        from app import create_app
        from models import User, db
    except ModuleNotFoundError as exc:
        if exc.name == "flask":
            print("[ERROR] Flask is not installed in the interpreter used to run this script.")
            print("        This usually happens when using 'py -3.11' instead of the project venv.")
            print()
            print("Use one of these commands from 01_Code\\backend:")
            print(r"  .\\venv\\Scripts\\python.exe scripts\\emergency_admin_reset.py")
            print(r"  python scripts\\emergency_admin_reset.py")
            print()
            print("If dependencies are missing, install them with:")
            print(r"  python -m pip install -r requirements.txt")
            sys.exit(1)
        raise
    return create_app, User, db


def main() -> None:
    print("=" * 52)
    print("  ClarifAI — Emergency Admin Password Reset")
    print("=" * 52)
    print()

    # ── Import app inside main() so path is set up first ─────────────────────
    create_app, User, db = _import_app_dependencies()

    app = create_app()

    with app.app_context():
        admin_email = (app.config.get("ADMIN_EMAIL") or "").strip().lower()

        if not admin_email:
            print("[ERROR] ADMIN_EMAIL is not set in config.py.")
            print("        Open config.py and set ADMIN_EMAIL, then retry.")
            sys.exit(1)

        admin = User.query.filter_by(email=admin_email, role="admin").first()

        if not admin:
            print(f"[ERROR] No admin account found with email: {admin_email}")
            print()
            print("  Possible causes:")
            print("  1. The app has never been started — run it once to bootstrap admin.")
            print("  2. ADMIN_EMAIL in config.py does not match the stored email.")
            sys.exit(1)

        # ── Show current status ───────────────────────────────────────────────
        print(f"  Admin email  : {admin_email}")
        print(f"  Admin name   : {admin.full_name}")
        print(f"  Account ID   : {admin.unique_user_code}")
        print(f"  Active       : {admin.is_active}")
        print()

        # ── Confirmation prompt ───────────────────────────────────────────────
        confirm = input("  Type CONFIRM to proceed with password reset: ").strip()
        if confirm.upper() != "CONFIRM":
            print("\nAborted — no changes made.")
            sys.exit(0)

        print()

        # ── New password (with validation loop) ───────────────────────────────
        for attempt in range(3):
            new_pass     = getpass.getpass("  New password       : ")
            confirm_pass = getpass.getpass("  Confirm password   : ")

            if new_pass != confirm_pass:
                print("  [!] Passwords do not match. Try again.\n")
                if attempt == 2:
                    print("  Too many failed attempts. Aborted.")
                    sys.exit(1)
                continue

            err = _validate_password(new_pass)
            if err:
                print(f"  [!] {err} Try again.\n")
                if attempt == 2:
                    print("  Too many failed attempts. Aborted.")
                    sys.exit(1)
                continue

            break  # password is valid
        else:
            sys.exit(1)

        # ── Apply changes ─────────────────────────────────────────────────────
        admin.set_password(new_pass)
        admin.is_active = True
        db.session.commit()

        print()
        print("  [SUCCESS] Admin password has been reset and account re-activated.")
        print()
        print(f"  Log in at  : /auth/login")
        print(f"  Email      : {admin_email}")
        print(f"  Password   : (the one you just entered)")
        print()


if __name__ == "__main__":
    main()
