# ResumePilot AI

A fully local, human-led resume screening foundation. React + TypeScript + Vite + Tailwind CSS frontend, FastAPI backend, PyMuPDF / python-docx parsing, and optional PostgreSQL scaffolding. No paid services, cloud upload, or automatic applicant rejection.

## Start locally

Prerequisites: Node.js 22+, Python 3.12+. Run these commands from the repository root in two terminals.

**Backend**

```sh
python3 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.txt
cd backend
.venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

API: http://127.0.0.1:8000 · interactive API docs: http://127.0.0.1:8000/docs

**Frontend**

```sh
cd frontend
npm install
npm run dev
```

Open http://127.0.0.1:5173. Vite proxies `/api` to the backend on port 8000. Keep both processes running; stop them with Ctrl+C.

## What works

- Minimal responsive page with job requirements, multiple PDF/DOCX uploads, and folder selection. No sidebar or fictional candidates.
- Upload validation: up to 30 files per batch, 10 MB each. Unrelated folder files are reported and skipped. Folder selection depends on browser support.
- Local extraction of PDF text, DOCX paragraphs and tables; per-file errors allow valid files to continue.
- Two manual priority categories: green **Review First** and red **Needs Review**. All parsed resumes initially enter Needs Review; these categories do not imply qualification or rejection.
- Both categories allow evidence inspection, downloading the original PDF/DOCX, opening original PDFs in a browser, and recording an Undecided / Shortlisted / Reviewed decision. DOCX originals can be inspected with a local document application.
- Original files stay associated with their batch positions, including duplicate filenames in different folders and batches with parsing errors.

## Phase-one boundaries

AI qualification analysis is **not connected**. The Analyze Resumes button currently extracts resume text. Job requirements are submitted and validated but not used for AI matching yet. No automatic scores, ranking, or rejection is implemented.

The API accepts batches with the existing 30-file limit. Tests cover rejecting over-limit batches; throughput and larger-scale performance have not been benchmarked. There is no claim of support for 1,000 resumes.

Uploads are processed locally and not saved by the app. The frontend retains original File objects for inspection and releases object URLs when their cards unmount. Evidence is limited to the first 2,000 extracted characters; consult the original for full context. Review decisions, original-file access, and job requirements reset on page reload. Re-running analysis replaces results and resets decisions. Scanned images need OCR, which is not implemented. This development foundation has no authentication; keep the services on loopback and do not expose them publicly.

## Optional PostgreSQL

The current interface does not need a database. To prepare local PostgreSQL with Docker:

```sh
docker compose up -d db
```

`backend/app/database.py` supplies an optional connectivity check; no tables or candidate persistence are implemented. Default local development credentials are defined in `compose.yaml`. The named Docker volume retains database data. Stop with `docker compose stop db`.

## Future local Ollama integration

Ollama is not required to run phase one. Install Ollama separately and download a locally supported open-source model (for example `ollama pull qwen2.5:7b`). `backend/.env.example` documents reserved database and Ollama settings; these settings are not automatically loaded or connected to analysis yet. Model downloads require internet; inference is intended to remain local. Future integration must quote resume evidence, disclose uncertainty, and preserve human decisions.

## Checks

```sh
cd backend
.venv/bin/python -m pytest -q
```

```sh
cd frontend
npm test
npm run build
```

Backend tests exercise real generated PDF/DOCX parsing, malformed/unsupported/empty inputs, batch limits, and the demonstration disclosure. Frontend tests cover upload validation, correct original-file association after partial failures, manual priority/decision updates, the empty initial interface, and escaped evidence in both categories. The build checks TypeScript and bundles the app. Component checks use server rendering; they do not replace browser interaction or visual testing.
