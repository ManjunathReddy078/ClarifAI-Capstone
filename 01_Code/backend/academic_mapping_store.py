import csv
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
PRESET_ASSIGNMENTS_PATH = DATA_DIR / "faculty_subject_assignments.csv"


def _clean_row(row: dict) -> dict:
    return {(key or "").strip(): (value or "").strip() for key, value in (row or {}).items()}


def _is_truthy(value: str) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "y", "active"}


def load_preset_assignments(active_only: bool = True) -> list[dict]:
    if not PRESET_ASSIGNMENTS_PATH.exists():
        return []

    rows: list[dict] = []
    with PRESET_ASSIGNMENTS_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            row = _clean_row(raw)
            if not row:
                continue
            if active_only and row.get("is_active") and not _is_truthy(row.get("is_active", "")):
                continue
            row["course_code"] = row.get("course_code", "").upper()
            row["section"] = row.get("section", "").upper()
            row["subject_code"] = row.get("subject_code", "").upper()
            row["faculty_id"] = row.get("faculty_id", "").upper()
            row["faculty_email"] = row.get("faculty_email", "").lower()
            rows.append(row)
    return rows


def find_assignment(course_code: str, semester_no: int, section: str, subject_code: str) -> dict | None:
    course = (course_code or "").strip().upper()
    sem_text = str(semester_no or "").strip()
    sec = (section or "").strip().upper()
    subject = (subject_code or "").strip().upper()

    for row in load_preset_assignments(active_only=True):
        if row.get("course_code") != course:
            continue
        if row.get("semester_no", "").strip() != sem_text:
            continue
        if row.get("section") != sec:
            continue
        if row.get("subject_code") != subject:
            continue
        return row
    return None


def list_assignments_for_slot(course_code: str, semester_no: int, section: str) -> list[dict]:
    course = (course_code or "").strip().upper()
    sem_text = str(semester_no or "").strip()
    sec = (section or "").strip().upper()
    return [
        row
        for row in load_preset_assignments(active_only=True)
        if row.get("course_code") == course
        and row.get("semester_no", "").strip() == sem_text
        and row.get("section") == sec
    ]
