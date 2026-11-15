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

- Responsive dark dashboard with job requirements and individual, multiple, drag-and-drop, or folder upload.
- PDF/DOCX validation: up to 30 files, 10 MB each. Folders are flattened; unrelated files are reported and skipped. Folder upload depends on browser support.
- Local extraction of PDF text, DOCX paragraphs and tables; per-file errors allow valid files to continue.
- Candidate search, review filters, evidence viewer, and manual Needs review / Shortlisted / Reviewed controls.
- Initial fictional examples are clearly labeled. Real uploads replace the examples and show extracted text, never invented qualification judgments.
- Email and cloud import are disabled and marked coming soon.

## Phase-one boundaries

AI qualification analysis is **not connected**. The Analyze button currently extracts resume text. Job requirements are submitted and validated but not used for AI matching yet. No scores, ranking, or automatic rejection is implemented. The UI labels demonstration data and extraction mode.

Uploads are processed in memory and not saved by the app. Evidence is limited to the first 2,000 extracted characters. Review decisions and job requirements are session-only and reset on page reload. Scanned images need OCR, which is not implemented. This development foundation has no authentication; keep the services on loopback and do not expose them publicly.

## Optional PostgreSQL

The current dashboard does not need a database. To prepare local PostgreSQL with Docker:

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

Backend tests exercise real generated PDF/DOCX parsing, malformed/unsupported/empty inputs, batch limits, and the demonstration disclosure. Frontend tests cover upload validation; the build checks TypeScript and bundles the app.
