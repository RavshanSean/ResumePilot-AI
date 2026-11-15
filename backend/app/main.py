"""Local resume parsing foundation. No AI judgments or automatic rejections."""
import io
import logging
import time
from pathlib import Path

import pymupdf
from docx import Document
from fastapi import FastAPI, File, HTTPException, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

app = FastAPI(title="ResumePilot AI", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], allow_methods=["GET", "POST"], allow_headers=["*"])
logger = logging.getLogger("uvicorn.error")
MAX_SIZE = 10 * 1024 * 1024
MAX_FILES = 30


def parse_resume(filename: str, data: bytes) -> str:
    extension = Path(filename).suffix.lower()
    if extension not in {".pdf", ".docx"}:
        raise ValueError("Only PDF and DOCX files are supported.")
    if not data or len(data) > MAX_SIZE:
        raise ValueError("Files must be nonempty and no larger than 10 MB.")
    try:
        if extension == ".pdf":
            with pymupdf.open(stream=data, filetype="pdf") as document:
                if document.needs_pass:
                    raise ValueError("Password-protected PDFs are not supported.")
                result = "\n".join(page.get_text() for page in document)
        else:
            document = Document(io.BytesIO(data))
            result = "\n".join([p.text for p in document.paragraphs] + [" ".join(c.text for c in row.cells) for table in document.tables for row in table.rows])
    except Exception as exc:
        raise ValueError("Unable to read this document. Check that it is a valid, unencrypted PDF or DOCX.") from exc
    if not result.strip():
        raise ValueError("No readable text found. Scanned PDFs require OCR, which is not available yet.")
    return result.strip()


@app.get("/api/health")
def health():
    return {"status": "ok", "analysis_mode": "demonstration", "ai_enabled": False, "persistence_enabled": False}


@app.post("/api/analyze")
async def analyze(files: list[UploadFile] = File(...), requirements: str = Form("")):
    if len(files) > MAX_FILES:
        raise HTTPException(400, "Upload up to 30 resumes at a time.")
    if len(requirements) > 20000:
        raise HTTPException(400, "Job requirements must be under 20,000 characters.")
    started = time.monotonic()
    candidates, errors = [], []
    for index, file in enumerate(files):
        try:
            data = await file.read(MAX_SIZE + 1)
            # Parsing is synchronous CPU work; keep the ASGI event loop responsive.
            content = await run_in_threadpool(parse_resume, file.filename or "", data)
            candidates.append({"id": str(index), "name": Path(file.filename or "Resume").stem.replace("_", " "), "filename": file.filename, "summary": "Resume text extracted successfully. Qualification assessment awaits human review; AI analysis is not connected.", "evidence": content[:2000], "status": "Needs review", "demo": True})
        except ValueError as exc:
            errors.append({"filename": file.filename, "message": str(exc)})
        except Exception as exc:
            # Do not expose parser internals or log applicant filenames/text.
            logger.error("Resume processing failed: batch_index=%d error_type=%s", index, type(exc).__name__)
            errors.append({"filename": file.filename, "message": "This resume could not be processed. Please try again."})
        finally:
            try:
                await file.close()
            except Exception as exc:
                logger.error("Upload cleanup failed: batch_index=%d error_type=%s", index, type(exc).__name__)
    logger.info("Resume batch complete: files=%d candidates=%d errors=%d duration_ms=%d",
                len(files), len(candidates), len(errors), int((time.monotonic() - started) * 1000))
    return {"mode": "demonstration", "notice": "Real document extraction only. No AI qualification judgments have been made.", "candidates": candidates, "errors": errors}
