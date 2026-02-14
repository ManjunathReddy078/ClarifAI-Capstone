# ClarifAI Workspace Cleanup & Organization Report

Date: 2026-02-14

## 1) Quick Findings

- Workspace is functionally organized, but contains runtime/cache/temp artifacts.
- Main size contributors:
  - `01_Code` (mostly due to local `venv` and Python cache files)
  - `07_Literature Survey/Refrences` (PDF dataset, expected)
- Diagrams are present and usable (`.drawio` + `.drawio.png` exports).
- One Office temporary lock file exists in presentations.
- Backup folder has duplicate old frontend assets.
- Current `.gitignore` excludes almost all academic deliverables and diagrams, so they will not go to GitHub.

---

## 2) Safe Delete List (Recommended)

### A. Runtime/Cache (delete now)
- `01_Code/backend/venv/`
- `01_Code/backend/__pycache__/`
- `01_Code/backend/routes/__pycache__/`
- `01_Code/backend/scripts/__pycache__/`
- Any `*.pyc` under `01_Code/backend/` outside `venv`

### B. Temporary Office lock files (delete only if present)
- Pattern: `03_Presentations/**/~$*.pptx`
- Note: Do **not** delete real files like `Phase-2 Presentation-1.pptx`.

### C. Optional duplicates (delete if not needed)
- `05_Research_Paper/Paper_Draft/ClarifAI_IEEE_Draft.zip` (duplicate package if you already keep `.tex` + final `.pdf`)

---

## 3) Move / Rename Recommendations

### A. Root cleanup
- Move `ClairfAI Design.pdf` to:
  - `02_Diagrams/Architecture/ClarifAI_Design.pdf`
- Rename typo while moving: `ClairfAI` -> `ClarifAI`

### B. Literature folder naming consistency
- Rename folder:
  - `07_Literature Survey/Refrences` -> `07_Literature Survey/References`

### C. Backup isolation (so active project stays clean)
- Keep backups, but move under a timestamped archive path:
  - `06_Backups/frontend/...` -> `06_Backups/archive_2026-02/frontend/...`
- Or delete `06_Backups/frontend` if all files are already represented in `01_Code/backend/templates` and `01_Code/backend/static`

---

## 4) Keep As-Is (Important)

- Core app code:
  - `01_Code/backend/*` (except `venv`, caches)
  - `01_Code/database/schema.sql`
- Main DB (for Drive backup only):
  - `01_Code/database/clarifai.db`
- Diagrams:
  - `02_Diagrams/*`
- Presentations:
  - `03_Presentations/Phase_1/*.pptx`
  - `03_Presentations/Phase_2/*.pptx` (except lock file)
- Research paper deliverables:
  - `05_Research_Paper/Paper_Draft/ClarifAI_IEEE_Draft.tex`
  - `05_Research_Paper/Paper_Draft/ClarifAI__Smart_Feedback_Evaluation_and_Knowledge_Sharing_System_Using_Explainable_Rule_Based_NLP.pdf`
  - `05_Research_Paper/Paper_Draft/figures/*`
  - `05_Research_Paper/Conference_Submission/Targeted_Conference_List_For_PPT.md`
- Literature references:
  - `07_Literature Survey/References/*.pdf` (after rename)

---

## 5) GitHub Push Readiness (Critical)

Current `.gitignore` excludes:
- `02_Diagrams/`
- `03_Presentations/`
- `04_Report/`
- `05_Research_Paper/`
- `06_Backups/`
- `07_Literature Survey/`
- `*.db`
- `*.csv`

This means your deliverables and diagrams will NOT be uploaded to GitHub.

### Recommended `.gitignore` strategy

#### If this is a **full academic repository** (code + docs + diagrams):
- Keep ignoring:
  - `venv/`
  - `__pycache__/`
  - `*.pyc`
  - Office temp files (`~$*.pptx`)
- Remove ignore rules for:
  - `02_Diagrams/`
  - `03_Presentations/`
  - `04_Report/`
  - `05_Research_Paper/`
  - `07_Literature Survey/`
- Keep DB protection:
  - keep `*.db` ignored (or ignore only `01_Code/database/clarifai.db`)

#### If this is a **code-only repository**:
- Keep current doc-folder ignores.
- Push documents/diagrams/presentations to Drive only.

---

## 6) Final Organized Target Structure (Suggested)

- `01_Code/` → executable source and DB schema only
- `02_Diagrams/` → architecture/ER/UML/process artifacts (+ exported PNGs)
- `03_Presentations/` → clean PPT versions only (no temp lock files)
- `04_Report/` → Drafts/Final populated
- `05_Research_Paper/` → Draft source, compiled PDF, conference shortlist
- `06_Backups/` → archived snapshots only (timestamped), optional exclusion from GitHub
- `07_Literature Survey/References/` → paper PDFs and review drafts

---

## 7) Action Priority (Do in this order)

1. Delete runtime/cache/temp files.
2. Move/rename root and literature typo paths.
3. Decide GitHub mode: full-academic vs code-only.
4. Update `.gitignore` accordingly.
5. Final manual check, then upload to Drive and push to GitHub.

---

## 8) Optional Next Step

If you want, I can automatically perform the cleanup + renames + `.gitignore` update in one safe patch/command sequence and leave only a final review step for you.
