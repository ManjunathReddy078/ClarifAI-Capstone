# Admin Recovery Flow

This runbook documents exactly how to recover admin access:

1. Before deployment (staging/pre-prod validation)
2. After deployment (production-safe recovery)
3. Worst-case lockout situations

Always use the project virtual environment interpreter for scripts.

## Normal Recovery (User Self-Service)

1. Open login page and select `Forgot password?`
2. Enter registered email.
3. Verify security answer.
4. Set new password.

Routes involved:

- `/auth/reset/request`
- `/auth/reset/verify`
- `/auth/reset/password`

## Admin-Assisted Recovery (In-App)

Admin can reset another user's password from admin controls.

- Route: `/admin/manual-reset`
- Required fields: `email`, `new_password`

## Break-Glass Recovery (Admin Locked Out)

Use script: `scripts/emergency_admin_reset.py`

From `01_Code/backend`:

```powershell
.\venv\Scripts\python.exe scripts/emergency_admin_reset.py
```

What it does:

1. Locates configured admin by `CLARIFAI_ADMIN_EMAIL`.
2. Prompts for confirmation and a new strong password.
3. Resets password directly in DB.
4. Reactivates admin account if disabled.

## Before Deployment: Step-by-Step Recovery Validation

Use this once per release cycle so recovery is guaranteed before go-live.

1. Open terminal in `01_Code/backend`.
2. Confirm venv interpreter works:

```powershell
.\venv\Scripts\python.exe -c "import flask; print('ok')"
```

3. Confirm app boots with current config:

```powershell
.\venv\Scripts\python.exe -c "from app import create_app; create_app(); print('app-start-ok')"
```

4. Confirm admin email configured correctly (must match bootstrap target):

```powershell
.\venv\Scripts\python.exe -c "from app import create_app; app=create_app(); print(app.config.get('ADMIN_EMAIL'))"
```

5. Dry-run break-glass script (do NOT reset):

```powershell
cmd /c "echo NO| .\venv\Scripts\python.exe scripts\emergency_admin_reset.py"
```

6. Expected result: script prints admin account details and exits with "Aborted — no changes made."
7. Log validation date, operator name, and output in your deployment notes.

## After Deployment: Production Recovery Procedure

Use this only when admin is locked out and UI reset is not possible.

1. Open a secure shell on production host.
2. Navigate to deployed backend folder (`01_Code/backend` equivalent).
3. Activate project venv (or use full venv python path directly).
4. Create a pre-reset DB backup (mandatory):

```powershell
Copy-Item ..\database\clarifai.db ..\database\clarifai.pre_admin_reset.bak
```

5. Run break-glass script:

```powershell
.\venv\Scripts\python.exe scripts\emergency_admin_reset.py
```

6. Type `CONFIRM` when prompted.
7. Enter new strong password twice.
8. Verify login from `/auth/login` with configured admin email.
9. If login succeeds, rotate any shared secrets and update password vault.
10. Record incident details: timestamp, operator, reason, and confirmation that login was restored.

## Worst-Case Scenarios

### Case 1: Module error (`No module named flask`)

1. You are using wrong interpreter.
2. Use venv python explicitly:

```powershell
.\venv\Scripts\python.exe scripts\emergency_admin_reset.py
```

3. If still failing, install dependencies into active interpreter:

```powershell
python -m pip install -r requirements.txt
```

### Case 2: Script cannot find admin row

1. Check `CLARIFAI_ADMIN_EMAIL` value.
2. Confirm DB path is correct and current DB is the live one.
3. Start app once to trigger bootstrap if admin row never existed.

### Case 3: DB locked

1. Close DB Browser/other writers.
2. Retry script.
3. If still locked, restart app service and retry.

### Case 4: Password reset done but login still fails

1. Re-check admin email used in login.
2. Ensure account is active (`is_active=True`; script already sets this).
3. Run one controlled reset again with known strong password.

## Controlled Startup Credential Sync (Optional)

Default behavior is safe: existing admin password is NOT overwritten on startup.

If you intentionally need startup-based credential rotation:

1. Set `CLARIFAI_ADMIN_FORCE_CREDENTIAL_SYNC=true`.
2. Set `CLARIFAI_ADMIN_PASSWORD` and `CLARIFAI_ADMIN_SECURITY_ANSWER`.
3. Start app once.
4. Set `CLARIFAI_ADMIN_FORCE_CREDENTIAL_SYNC=false`.

Important: keep `CLARIFAI_ADMIN_FORCE_CREDENTIAL_SYNC=false` in normal production runtime.

## Security Checklist

1. Never expose admin password in UI templates.
2. Keep `CLARIFAI_SECRET_KEY` set via environment in non-local deployments.
3. Rotate admin password after emergency resets.
4. Store recovery actions in team runbook with timestamp and operator name.
