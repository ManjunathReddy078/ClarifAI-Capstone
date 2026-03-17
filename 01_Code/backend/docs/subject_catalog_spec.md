# Subject Catalog Specification

File: `01_Code/backend/data/subjects.csv`

## Current Final Columns

1. `serial_no` (integer, unique, positive)
2. `degree` (enum: `MCA`, `BCA`)
3. `semester` (integer-like text, positive)
4. `course_code` (uppercase code, unique per subject)
5. `subject_name` (human-readable title)
6. `subject_type` (enum: `CORE`, `LAB`, `ELECTIVE`, `PROJECT`)
7. `is_active` (enum: `YES`/`NO`)

## Runtime Behavior

- Student review forms load only rows where `is_active` is truthy (`YES`, `TRUE`, `1`).
- Student course (`MCA` or `BCA`) must match the row `degree`.
- Selected `course_code` is validated server-side before feedback create/update.
- `subject_name` and `semester` are resolved from the catalog and persisted into feedback rows.

## Data Hygiene Rules

1. Keep `course_code` uppercase and stable once used in production.
2. Do not delete historical subjects; set `is_active=NO` when retired.
3. Avoid duplicate rows for the same `course_code`.
4. Keep semester values consistent (`1`..`6` etc.) to preserve sorting.

## Quick Validation Command

From `01_Code/backend`:

```powershell
.\venv\Scripts\python.exe -c "import csv; from pathlib import Path; p=Path('data/subjects.csv'); rows=list(csv.DictReader(p.open(encoding='utf-8'))); print('rows=',len(rows)); print('degrees=',sorted({r['degree'] for r in rows})); print('types=',sorted({r['subject_type'] for r in rows}))"
```
