# Seed Data Rules

This project uses two seed assets:

1. `data/whitelist.csv` for allowed registration identities.
2. Optional DB seeding via `scripts/generate_demo_data.py --seed-db`.

## Whitelist Final Columns

1. `serial_no`
2. `email`
3. `role` (`STUDENT` or `FACULTY`)
4. `full_name`
5. `prn` (required for students)
6. `faculty_id` (required for faculty)
7. `section` (required for students)
8. `course` (`MCA` or `BCA`)
9. `allowed` (`YES` recommended)

## Generation Distribution (Current Script)

- MCA students: 720 total (370 + 350)
- BCA students: 900 total (300 + 300 + 300)
- MCA faculty: 30
- BCA faculty: 20
- Grand total: 1670 rows

## Determinism and Reproducibility

- Generator uses fixed `random.seed(42)`.
- Running overwrite mode with same script version produces repeatable output patterns.

## Recommended Safe Workflow

From `01_Code/backend`:

1. Backup current file.

```powershell
Copy-Item data/whitelist.csv data/whitelist.backup.csv
```

2. Generate fresh whitelist.

```powershell
.\venv\Scripts\python.exe scripts/generate_demo_data.py
```

3. (Optional) Seed DB users with demo credentials.

```powershell
.\venv\Scripts\python.exe scripts/generate_demo_data.py --seed-db
```

4. Run smoke validation.

```powershell
.\venv\Scripts\python.exe scripts/final_smoke_test.py
```

## Constraints to Keep Stable

1. Do not change column names without updating route validators.
2. Keep email unique globally.
3. Keep `prn` and `faculty_id` mutually exclusive by role.
4. Keep `allowed=YES` for active demo users.
