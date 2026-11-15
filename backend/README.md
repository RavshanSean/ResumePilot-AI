# Local backend

From the project root, run:

```sh
./backend/start.sh
```

The launcher uses `backend/.venv`, works independently of the shell's working directory, and serves only on `127.0.0.1:8000`, the target of the existing Vite proxy. Keep this terminal running alongside the frontend. Stop it with Ctrl+C. It intentionally does not download packages or start external services.

If the environment is missing, install it first:

```sh
python3 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.txt
```

Check availability directly and through the running frontend:

```sh
curl --fail http://127.0.0.1:8000/api/health
curl --fail http://127.0.0.1:5173/api/health
```

If the frontend works but Analyze returns a generic error, check both commands. A refused connection on port 8000 and HTTP 500 through port 5173 mean the proxy has no reachable backend; this does not establish a document parsing failure. Backend startup and access logs appear in its terminal.

Only local text extraction is implemented. Ollama qualification matching is not connected. The existing limits remain 30 resumes per request, 10 MB per file, and 20,000 characters of job requirements.

## Regression and live workflow tests

Run the backend suite (all documents are generated synthetically):

```sh
cd backend
.venv/bin/python -m pytest -q
```

With the backend and the existing Vite dev server running, additionally run the same integration contract over real HTTP, directly and through the unchanged frontend proxy:

```sh
cd backend
RESUMEPILOT_LIVE_TESTS=1 .venv/bin/python -m pytest -q
```

The live tests use only `127.0.0.1:8000` and `127.0.0.1:5173`. They check three-PDF batches, mixed PDF/DOCX batches, duplicate filenames, partial parsing failures, response IDs used for original-file access, DOCX tables, CORS, and validation limits. Live cases skip by default when servers may be absent. These verify the HTTP upload-to-results contract, not browser rendering or performance at large scale.

Batch logs report counts, elapsed time, and failure types without applicant filenames or document contents. Unexpected per-file failures return a safe error while remaining files continue. Synchronous parsing runs in a worker thread to leave the API event loop available; batches remain sequential and subject to the existing limits.
