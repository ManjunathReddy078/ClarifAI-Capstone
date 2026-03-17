"""
ClarifAI Demo Data Generator
==============================
Generates synthetic user records for the whitelist CSV and optionally
seeds the database with user accounts for demonstration purposes.

DISTRIBUTION (1670 total users):
    MCA 2nd year (Batch 2023, Sem 4): 370 students → Sections A, B, C, D, E
    MCA 1st year (Batch 2024, Sem 2): 350 students → Sections A, B, C, D
    BCA 1st year (Batch 2025, Sem 2): 300 students → Sections A, B
    BCA 2nd year (Batch 2024, Sem 4): 300 students → Sections A, B
    BCA 3rd year (Batch 2023, Sem 6): 300 students → Sections A, B
    Faculty MCA                      :  30 members
    Faculty BCA                      :  20 members
    ─────────────────────────────────────────────
    Total                            : 1670

PRN FORMAT:
    MCA students : PES1PG<YY>MC<NNN>  e.g. PES1PG24MC001
    BCA students : PES1UG<YY>CA<NNN>  e.g. PES1UG25CA001
    Faculty      : FAC<MC|CA><NNN>    e.g. FACMC001

USAGE:
    cd 01_Code\\backend

    # Generate whitelist.csv only (fresh file, replaces existing)
    py -3.11 scripts/generate_demo_data.py

    # Append to existing whitelist instead of replacing
    py -3.11 scripts/generate_demo_data.py --append

    # Also seed the database with user accounts (password: Demo@12345)
    py -3.11 scripts/generate_demo_data.py --seed-db

    # Both append and seed DB
    py -3.11 scripts/generate_demo_data.py --append --seed-db

NOTE:
    --seed-db creates User records with password "Demo@12345" and security
    answer "Demo" so you can log in as any seeded user during demo/testing.
    This requires Flask and the database to be accessible.
"""

import argparse
import csv
import os
import random
import sys
from pathlib import Path

# ── path setup so we can import Flask app when --seed-db is used ─────────────
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

# ── Name pools (South Indian + pan-Indian diversity) ─────────────────────────
MALE_FIRST = [
    "Arjun", "Karthik", "Rahul", "Siddharth", "Venkatesh", "Suresh", "Ramesh",
    "Pradeep", "Ganesh", "Ravi", "Arun", "Vijay", "Mohit", "Deepak", "Santosh",
    "Harsha", "Nikhil", "Akshay", "Rakesh", "Manoj", "Naveen", "Sriram",
    "Chaitanya", "Bala", "Srikanth", "Prasad", "Varun", "Vivek", "Ajay",
    "Anand", "Dinesh", "Girish", "Hari", "Jagadish", "Lokesh", "Manohar",
    "Naresh", "Pavan", "Ranjith", "Shiva", "Tejas", "Uday", "Vikram", "Yogesh",
    "Imran", "Bilal", "Mohammed", "Faisal", "Rohit", "Tarun", "Ashwin",
    "Bharat", "Chirag", "Dev", "Gaurav", "Hitesh", "Kiran", "Lalit",
    "Naga", "Omkar", "Puneet", "Rishab", "Sandeep", "Tushar", "Umesh",
]

FEMALE_FIRST = [
    "Priya", "Kavya", "Sravani", "Lakshmi", "Divya", "Ananya", "Pavani",
    "Sneha", "Swathi", "Varsha", "Ramya", "Keerthi", "Meghana", "Pooja",
    "Rekha", "Saranya", "Tanvi", "Vaishnavi", "Yamini", "Amulya", "Bhavana",
    "Chaitra", "Deeksha", "Esha", "Gayathri", "Harini", "Ishani", "Jyothi",
    "Nisha", "Akshitha", "Pallavi", "Reshma", "Shalini", "Teja", "Usha",
    "Apoorva", "Bindiya", "Chandana", "Druthi", "Farah", "Hemalatha",
    "Indira", "Jasmine", "Komal", "Leela", "Madhuri", "Namrata", "Parvathi",
    "Rashmi", "Sandhya", "Tejalatha", "Vanitha", "Zeenath", "Archana",
    "Bhargavi", "Chithra", "Dharini", "Fathima", "Geetha",
]

SURNAMES = [
    "Reddy", "Kumar", "Sharma", "Singh", "Nair", "Pillai", "Rao", "Patel",
    "Naidu", "Iyer", "Bhat", "Hegde", "Gowda", "Menon", "Joshi", "Verma",
    "Mishra", "Kapoor", "Gupta", "Chowdhury", "Dutta", "Shaik", "Khan",
    "Ahmed", "Ansari", "Syed", "Ali", "Bhatt", "Desai", "Mehta", "Agarwal",
    "Saxena", "Chatterjee", "Mukherjee", "Sen", "Venkataraman", "Subramaniam",
    "Rajan", "Krishnan", "Sundaram", "Srinivasan", "Balakrishnan", "Parthasarathy",
    "Natarajan", "Sivakumar", "Murugesh", "Ramakrishnan", "Chandrasekhar",
    "Banerjee", "Biswas", "Das", "Ghosh", "Mitra", "Roy", "Bose",
]

EMAIL_DOMAINS = ["gmail.com", "yahoo.com", "outlook.com", "hotmail.com"]


# ── PRN / Faculty ID builders ─────────────────────────────────────────────────
def mca_prn(batch_yy: str, serial: int) -> str:
    return f"PES1PG{batch_yy}MC{serial:03d}"


def bca_prn(batch_yy: str, serial: int) -> str:
    return f"PES1UG{batch_yy}CA{serial:03d}"


def make_faculty_id(course: str, serial: int) -> str:
    code = "MC" if course == "MCA" else "CA"
    return f"FAC{code}{serial:03d}"


# ── Name / e-mail helpers ─────────────────────────────────────────────────────
def pick_name(used_names: set) -> str:
    """Return a unique full name from the pools."""
    for _ in range(800):
        first = random.choice(
            MALE_FIRST if random.random() < 0.55 else FEMALE_FIRST
        )
        last = random.choice(SURNAMES)
        name = f"{first} {last}"
        if name not in used_names:
            used_names.add(name)
            return name
    # Fallback: append a number to guarantee uniqueness
    name = f"{random.choice(MALE_FIRST)}{random.randint(1, 9999)} {random.choice(SURNAMES)}"
    used_names.add(name)
    return name


def pick_email(name: str, serial: int, used_emails: set) -> str:
    """Derive a unique e-mail from the full name."""
    parts = name.lower().split()
    first, last = parts[0], parts[-1]
    domain = random.choice(EMAIL_DOMAINS)
    candidates = [
        f"{first}.{last}{serial}@{domain}",
        f"{first}{serial}@{domain}",
        f"{first[0]}{last}{serial}@{domain}",
        f"{first}.{last[:4]}{serial}@{domain}",
    ]
    for candidate in candidates:
        if candidate not in used_emails:
            used_emails.add(candidate)
            return candidate
    fallback = f"clarifai.user{serial:05d}@{domain}"
    used_emails.add(fallback)
    return fallback


# ── Core generation logic ─────────────────────────────────────────────────────
def generate_records() -> list[dict]:
    """
    Return a list of whitelist row dicts in the exact column order:
    serial_no, email, role, full_name, prn, faculty_id, section, course,
    batch_start_year, batch_end_year, admission_month, admission_year,
    current_semester, allowed
    """
    used_names: set = set()
    used_emails: set = set()
    records: list[dict] = []
    global_serial = 1

    # ── Student groups ────────────────────────────────────────────────────────
    # (total_count, course, batch_yy, sections, prn_generator_fn, current_semester)
    student_groups = [
        (370, "MCA", "23", ["A", "B", "C", "D", "E"], lambda s: mca_prn("23", s), 4),
        (350, "MCA", "24", ["A", "B", "C", "D"],       lambda s: mca_prn("24", s), 2),
        (300, "BCA", "25", ["A", "B"],                  lambda s: bca_prn("25", s), 2),
        (300, "BCA", "24", ["A", "B"],                  lambda s: bca_prn("24", s), 4),
        (300, "BCA", "23", ["A", "B"],                  lambda s: bca_prn("23", s), 6),
    ]

    for (total, course, batch_yy, sections, prn_fn, current_semester) in student_groups:
        per_section = total // len(sections)
        remainder   = total % len(sections)
        inner_serial = 1
        batch_start_year = 2000 + int(batch_yy)
        duration_years = 2 if course == "MCA" else 3
        batch_end_year = batch_start_year + duration_years

        for idx, section in enumerate(sections):
            count = per_section + (1 if idx < remainder else 0)
            for _ in range(count):
                name  = pick_name(used_names)
                email = pick_email(name, global_serial, used_emails)
                records.append({
                    "serial_no":  global_serial,
                    "email":      email,
                    "role":       "STUDENT",
                    "full_name":  name,
                    "prn":        prn_fn(inner_serial),
                    "faculty_id": "",
                    "section":    section,
                    "course":     course,
                    "batch_start_year": batch_start_year,
                    "batch_end_year": batch_end_year,
                    "admission_month": 7,
                    "admission_year": batch_start_year,
                    "current_semester": current_semester,
                    "allowed":    "YES",
                })
                global_serial += 1
                inner_serial  += 1

    # ── Faculty – MCA (30) ────────────────────────────────────────────────────
    for i in range(1, 31):
        name  = pick_name(used_names)
        email = pick_email(name, global_serial, used_emails)
        records.append({
            "serial_no":  global_serial,
            "email":      email,
            "role":       "FACULTY",
            "full_name":  name,
            "prn":        "",
            "faculty_id": make_faculty_id("MCA", i),
            "section":    "",
            "course":     "MCA",
            "batch_start_year": "",
            "batch_end_year": "",
            "admission_month": "",
            "admission_year": "",
            "current_semester": "",
            "allowed":    "YES",
        })
        global_serial += 1

    # ── Faculty – BCA (20) ────────────────────────────────────────────────────
    for i in range(1, 21):
        name  = pick_name(used_names)
        email = pick_email(name, global_serial, used_emails)
        records.append({
            "serial_no":  global_serial,
            "email":      email,
            "role":       "FACULTY",
            "full_name":  name,
            "prn":        "",
            "faculty_id": make_faculty_id("BCA", i),
            "section":    "",
            "course":     "BCA",
            "batch_start_year": "",
            "batch_end_year": "",
            "admission_month": "",
            "admission_year": "",
            "current_semester": "",
            "allowed":    "YES",
        })
        global_serial += 1

    return records  # 1670 records


# ── CSV writer ────────────────────────────────────────────────────────────────
FIELDNAMES = ["serial_no", "email", "role", "full_name",
              "prn", "faculty_id", "section", "course", "batch_start_year",
              "batch_end_year", "admission_month", "admission_year",
              "current_semester", "allowed"]


def write_whitelist(records: list[dict], path: Path, append: bool = False) -> None:
    mode = "a" if append else "w"
    write_header = (not append) or (not path.exists()) or (path.stat().st_size == 0)

    with path.open(mode, newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
        # If appending, adjust serial_no to continue from existing last value
        if append and path.exists():
            existing = _count_existing_rows(path)
            for rec in records:
                rec["serial_no"] = existing + rec["serial_no"]
        writer.writerows(records)

    total = len(records)
    print(f"[OK] Wrote {total} records to {path}")


def _count_existing_rows(path: Path) -> int:
    """Return the number of data rows (excluding header) already in the file."""
    with path.open("r", encoding="utf-8") as fh:
        return max(0, sum(1 for _ in fh) - 1)


# ── DB seeder ─────────────────────────────────────────────────────────────────
DEMO_PASSWORD       = "Demo@12345"
DEMO_SECURITY_Q     = "What is your birth city?"
DEMO_SECURITY_ANS   = "Demo"


def seed_database(records: list[dict]) -> None:
    """Create User rows for every whitelist record (skips if email already exists)."""
    os.chdir(BACKEND_DIR)
    from app import create_app
    from models import User, db

    app = create_app()
    with app.app_context():
        new_count = 0
        skip_count = 0
        for rec in records:
            email = rec["email"].lower()
            if User.query.filter_by(email=email).first():
                skip_count += 1
                continue

            user = User(
                unique_user_code=_gen_user_code(rec, User),
                full_name=rec["full_name"],
                email=email,
                role=rec["role"].lower(),
                prn=rec.get("prn") or None,
                faculty_id=rec.get("faculty_id") or None,
                section=rec.get("section") or None,
                course=rec.get("course", "MCA"),
                security_question=DEMO_SECURITY_Q,
                security_answer_hash="",
                is_active=True,
            )
            user.set_password(DEMO_PASSWORD)
            user.set_security_answer(DEMO_SECURITY_ANS)
            db.session.add(user)
            new_count += 1

            # Commit in batches to avoid large transactions
            if new_count % 100 == 0:
                db.session.commit()
                print(f"  ...committed {new_count} users so far")

        db.session.commit()
        print(f"[OK] DB seeded: {new_count} new users added, {skip_count} skipped (already exist).")
        print(f"     Demo login password : {DEMO_PASSWORD}")
        print(f"     Demo security answer: {DEMO_SECURITY_ANS}")


def _resolve_user_code_prefix(role: str, course: str) -> str:
    role_value = (role or "").strip().upper()
    course_value = (course or "").strip().upper()
    if role_value == "FACULTY":
        return "CAICAF"
    if role_value == "STUDENT" and course_value == "BCA":
        return "CAIBCAS"
    return "CAIMCAS"


def _gen_user_code(rec: dict, User) -> str:
    """Generate a unique role/course-prefixed code with a 4-digit suffix."""
    import random as _r

    prefix = _resolve_user_code_prefix(rec.get("role", ""), rec.get("course", "MCA"))
    for _ in range(20000):
        code = f"{prefix}{_r.randint(0, 9999):04d}"
        if not User.query.filter_by(unique_user_code=code).first():
            return code
    # Graceful fallback using serial number while preserving prefix contract.
    return f"{prefix}{int(rec['serial_no']) % 10000:04d}"


# ── Entry point ───────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate ClarifAI demo whitelist and (optionally) seed the database."
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append new records to existing whitelist.csv instead of replacing it.",
    )
    parser.add_argument(
        "--seed-db",
        action="store_true",
        help="Also create User rows in the database (password: Demo@12345).",
    )
    args = parser.parse_args()

    whitelist_path = BACKEND_DIR / "data" / "whitelist.csv"

    print("=== ClarifAI Demo Data Generator ===")
    print(f"Output file : {whitelist_path}")
    print(f"Mode        : {'append' if args.append else 'overwrite (fresh)'}")
    print(f"Seed DB     : {'yes' if args.seed_db else 'no'}")
    print()

    if not args.append and whitelist_path.exists():
        ans = input(
            "WARNING: This will OVERWRITE the existing whitelist.csv.\n"
            "Type YES to continue, or anything else to abort: "
        ).strip()
        if ans.upper() != "YES":
            print("Aborted.")
            sys.exit(0)

    random.seed(42)          # fixed seed → reproducible output
    records = generate_records()

    write_whitelist(records, whitelist_path, append=args.append)
    print(f"  Students : {sum(1 for r in records if r['role'] == 'STUDENT')}")
    print(f"  Faculty  : {sum(1 for r in records if r['role'] == 'FACULTY')}")
    print()

    if args.seed_db:
        print("Seeding database...")
        seed_database(records)

    print("\nDone. Next step: run the Flask app and verify registration works for a seeded user.")


if __name__ == "__main__":
    main()
