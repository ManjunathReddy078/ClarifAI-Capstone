import csv
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app import create_app
from assignment_sync_service import sync_preset_assignments_to_db
from models import (
    Checklist,
    ExperienceReport,
    ExperienceUpvote,
    FacultyAssignment,
    Feedback,
    KnowledgePost,
    ModerationLog,
    PendingFacultyFeedback,
    StudentExperience,
    SubjectOffering,
    db,
)


DATA_DIR = BASE_DIR / "data"
WHITELIST_PATH = DATA_DIR / "whitelist.csv"
SUBJECTS_PATH = DATA_DIR / "subjects.csv"
PRESET_ASSIGNMENTS_PATH = DATA_DIR / "faculty_subject_assignments.csv"
SECTIONS = ["A", "B", "C", "D", "E"]


def _is_yes(value: str) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "y", "active"}


def _load_whitelist() -> list[dict]:
    rows: list[dict] = []
    with WHITELIST_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows.append({(k or "").strip(): (v or "").strip() for k, v in row.items()})
    return rows


def _load_subjects() -> list[dict]:
    rows: list[dict] = []
    with SUBJECTS_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            clean = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
            if not _is_yes(clean.get("is_active", "YES")):
                continue
            rows.append(clean)
    return rows


def _safe_int(value: str) -> int | None:
    try:
        return int((value or "").strip())
    except (TypeError, ValueError):
        return None


def _build_offerings_and_mapping() -> tuple[list[dict], list[dict], list[str]]:
    whitelist = _load_whitelist()
    subjects = _load_subjects()

    faculty_pool = []
    for row in whitelist:
        if (row.get("role", "").strip().upper() != "FACULTY") or not _is_yes(row.get("allowed", "YES")):
            continue
        faculty_id = (row.get("faculty_id") or "").strip().upper()
        if not faculty_id:
            continue
        faculty_pool.append(
            {
                "faculty_id": faculty_id,
                "faculty_name": (row.get("full_name") or "").strip(),
                "faculty_email": (row.get("email") or "").strip().lower(),
            }
        )

    # Keep unique faculty IDs while preserving order.
    unique_faculty = []
    seen_faculty = set()
    for fac in faculty_pool:
        if fac["faculty_id"] in seen_faculty:
            continue
        seen_faculty.add(fac["faculty_id"])
        unique_faculty.append(fac)

    if not unique_faculty:
        raise RuntimeError("No allowed faculty rows found in whitelist.csv")

    semesters_by_course: dict[str, set[int]] = {}
    for row in whitelist:
        if (row.get("role", "").strip().upper() != "STUDENT") or not _is_yes(row.get("allowed", "YES")):
            continue
        course = (row.get("course") or "").strip().upper()
        sem = _safe_int(row.get("current_semester"))
        if not course or sem is None:
            continue
        semesters_by_course.setdefault(course, set()).add(sem)

    if not semesters_by_course:
        raise RuntimeError("No student semester coverage found in whitelist.csv")

    selected_subjects = []
    for subject in subjects:
        course = (subject.get("degree") or "").strip().upper()
        sem = _safe_int(subject.get("semester"))
        if not course or sem is None:
            continue
        if sem not in semesters_by_course.get(course, set()):
            continue
        selected_subjects.append(subject)

    offerings: list[dict] = []
    for subject in selected_subjects:
        course = (subject.get("degree") or "").strip().upper()
        sem = _safe_int(subject.get("semester"))
        subject_code = (subject.get("course_code") or "").strip().upper()
        subject_name = (subject.get("subject_name") or "").strip()
        if not course or sem is None or not subject_code or not subject_name:
            continue
        for section in SECTIONS:
            offerings.append(
                {
                    "course_code": course,
                    "semester_no": sem,
                    "section": section,
                    "subject_code": subject_code,
                    "subject_name": subject_name,
                    "is_active": True,
                }
            )

    offerings.sort(key=lambda row: (row["course_code"], row["semester_no"], row["subject_code"], row["section"]))

    mapping_rows: list[dict] = []
    assigned_counts = {fac["faculty_id"]: 0 for fac in unique_faculty}
    used_per_subject_group: dict[tuple[str, int, str], set[str]] = {}
    faculty_cursor = 0

    for offering in offerings:
        group_key = (offering["course_code"], offering["semester_no"], offering["subject_code"])
        used_per_subject_group.setdefault(group_key, set())
        used_ids = used_per_subject_group[group_key]

        chosen = None
        n = len(unique_faculty)
        for offset in range(n):
            idx = (faculty_cursor + offset) % n
            candidate = unique_faculty[idx]
            # Prefer not repeating same faculty for same subject across sections.
            if candidate["faculty_id"] in used_ids:
                continue
            chosen = candidate
            faculty_cursor = (idx + 1) % n
            break

        if chosen is None:
            chosen = unique_faculty[faculty_cursor % len(unique_faculty)]
            faculty_cursor = (faculty_cursor + 1) % len(unique_faculty)

        used_ids.add(chosen["faculty_id"])
        assigned_counts[chosen["faculty_id"]] += 1

        mapping_rows.append(
            {
                "course_code": offering["course_code"],
                "semester_no": str(offering["semester_no"]),
                "section": offering["section"],
                "subject_code": offering["subject_code"],
                "subject_name": offering["subject_name"],
                "faculty_id": chosen["faculty_id"],
                "faculty_name": chosen["faculty_name"],
                "faculty_email": chosen["faculty_email"],
                "is_active": "YES",
            }
        )

    unassigned_faculty = [fac_id for fac_id, count in assigned_counts.items() if count == 0]
    return offerings, mapping_rows, unassigned_faculty


def _reset_data_tables() -> None:
    # Order matters due to foreign keys.
    ModerationLog.query.delete(synchronize_session=False)
    Feedback.query.delete(synchronize_session=False)
    ExperienceReport.query.delete(synchronize_session=False)
    ExperienceUpvote.query.delete(synchronize_session=False)
    StudentExperience.query.delete(synchronize_session=False)
    KnowledgePost.query.delete(synchronize_session=False)
    Checklist.query.delete(synchronize_session=False)
    PendingFacultyFeedback.query.delete(synchronize_session=False)
    FacultyAssignment.query.delete(synchronize_session=False)
    SubjectOffering.query.delete(synchronize_session=False)
    db.session.commit()


def _seed_subject_offerings(offerings: list[dict]) -> None:
    for row in offerings:
        db.session.add(
            SubjectOffering(
                course_code=row["course_code"],
                semester_no=row["semester_no"],
                section=row["section"],
                subject_code=row["subject_code"],
                subject_name=row["subject_name"],
                is_active=True,
            )
        )
    db.session.commit()


def _write_mapping_csv(mapping_rows: list[dict]) -> None:
    fieldnames = [
        "course_code",
        "semester_no",
        "section",
        "subject_code",
        "subject_name",
        "faculty_id",
        "faculty_name",
        "faculty_email",
        "is_active",
    ]
    with PRESET_ASSIGNMENTS_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(mapping_rows)


def main() -> None:
    app = create_app()
    with app.app_context():
        offerings, mapping_rows, unassigned_faculty = _build_offerings_and_mapping()
        _reset_data_tables()
        _seed_subject_offerings(offerings)
        _write_mapping_csv(mapping_rows)
        sync_stats = sync_preset_assignments_to_db()

        print("Reset and seed completed.")
        print(f"Subject offerings seeded: {len(offerings)}")
        print("Faculty assignments table cleared and then re-synced from preset mapping where faculty users exist.")
        print(f"Preset mapping rows written: {len(mapping_rows)} -> {PRESET_ASSIGNMENTS_PATH}")
        print(
            "Preset assignment sync -> "
            f"created: {sync_stats['created']}, "
            f"already_mapped: {sync_stats['already_mapped']}, "
            f"missing_faculty: {sync_stats['missing_faculty']}, "
            f"missing_offering: {sync_stats['missing_offering']}"
        )
        if unassigned_faculty:
            print("Faculty with zero assigned rows:", ", ".join(unassigned_faculty))
        else:
            print("All whitelist faculty IDs received at least one assigned subject row.")


if __name__ == "__main__":
    main()
