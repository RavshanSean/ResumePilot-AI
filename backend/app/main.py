
"""ResumePilot AI: local, evidence-based resume screening API."""

import io
from app.batch_journal import (
    initialize_batch_journal, fingerprint_batch, begin_batch,
    complete_batch, abandon_unreserved_batch,
    claim_and_reserve, recover_interrupted_batches,
)
import logging
import time
from pathlib import Path

import pymupdf
from docx import Document
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

from app.matcher import MatchingError, fallback_result, match_resume
from app.privacy_logging import audit_event
from app.auth import router as auth_router, initialize_database, current_user, cleanup_expired_sessions, cleanup_old_login_attempts, initialize_email_verification
from app.account_recovery import (router as recovery_router, initialize_recovery, cleanup_expired_tokens)
from app.billing import router as billing_router, initialize_billing
from app.credits import (
    router as credits_router,
    initialize_credits,
    reserve,
    reserve_batch,
    finish,
    recover_stale_reservations,
)
from contextlib import asynccontextmanager
import os

@asynccontextmanager
async def lifespan(app):
    initialize_database()
    initialize_email_verification()
    initialize_recovery()
    cleanup_expired_tokens()
    cleanup_expired_sessions()
    cleanup_old_login_attempts()
    initialize_credits()
    initialize_batch_journal()
    from app.job_queue import initialize_job_queue
    initialize_job_queue()
    from app.temporary_storage import initialize_temporary_storage
    initialize_temporary_storage()
    from app.job_queue import initialize_job_inputs
    initialize_job_inputs()
    initialize_billing()
    if os.getenv("RESUMEPILOT_RECOVER_STALE_ON_STARTUP") == "1":
        recover_interrupted_batches()
    yield


app = FastAPI(
    title="ResumePilot AI",
    version="0.3.0",
    lifespan=lifespan,
)
app.include_router(auth_router)
app.include_router(recovery_router)
app.include_router(credits_router)
app.include_router(billing_router)
from app.job_status import router as job_status_router
app.include_router(job_status_router)
from app.async_routes import router as async_router
app.include_router(async_router)


def screening_access(
    authorization: str | None = Header(default=None),
):
    # Legacy tests may bypass authentication only inside pytest.
    # Production always requires a valid session.
    if (
        os.getenv("PYTEST_CURRENT_TEST")
        and os.getenv("RESUMEPILOT_TEST_BYPASS_AUTH") == "1"
    ):
        return {"id": "test-only", "email": "test@example.invalid"}

    return current_user(authorization)


SCREENING_DEPENDENCIES = [Depends(screening_access)]


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

logger = logging.getLogger("uvicorn.error")

MAX_SIZE = 10 * 1024 * 1024
MAX_FILES = 30
MAX_REQUIREMENTS = 20000
MAX_EXTRACTED_CHARS = 100_000
MAX_PDF_PAGES = 100


def parse_resume(filename: str, data: bytes) -> str:
    """Extract readable text from a PDF or DOCX resume."""

    extension = Path(filename).suffix.lower()

    if extension not in {".pdf", ".docx"}:
        raise ValueError("Only PDF and DOCX files are supported.")

    if not data or len(data) > MAX_SIZE:
        raise ValueError(
            "Files must be nonempty and no larger than 10 MB."
        )

    try:
        if extension == ".pdf":
            with pymupdf.open(stream=data, filetype="pdf") as document:
                if document.needs_pass:
                    raise ValueError("Password-protected PDFs are not supported.")
                if len(document) > MAX_PDF_PAGES:
                    raise ValueError("PDF has too many pages (maximum 100).")
                chunks = []
                total = 0
                for page in document:
                    text = page.get_text()
                    total += len(text) + 1
                    if total > MAX_EXTRACTED_CHARS:
                        raise ValueError("Resume text exceeds the 100,000-character limit.")
                    chunks.append(text)
                result = "\n".join(chunks)
        else:
            document = Document(io.BytesIO(data))
            chunks = []
            total = 0
            # Do not accumulate unbounded extracted text from compressed DOCX files.
            for paragraph in document.paragraphs:
                text = paragraph.text
                total += len(text) + 1
                if total > MAX_EXTRACTED_CHARS:
                    raise ValueError("Resume text exceeds the 100,000-character limit.")
                chunks.append(text)
            for table in document.tables:
                for row in table.rows:
                    text = " ".join(cell.text for cell in row.cells)
                    total += len(text) + 1
                    if total > MAX_EXTRACTED_CHARS:
                        raise ValueError("Resume text exceeds the 100,000-character limit.")
                    chunks.append(text)
            result = "\n".join(chunks)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(
            "Unable to read this document. "
            "Check that it is a valid, unencrypted PDF or DOCX."
        ) from exc

    if not result.strip():
        raise ValueError(
            "No readable text found. "
            "Scanned PDFs require OCR, which is not available yet."
        )

    return result.strip()


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "analysis_mode": "ai",
        "ai_enabled": True,
        "persistence_enabled": False,
        "human_review_required": True,
    }


@app.post("/api/analyze")
async def analyze(
    files: list[UploadFile] = File(...),
    requirements: str = Form(""),
    request_id: str = Form(""),
    user=Depends(screening_access),
):
    """Parse resumes, evaluate essentials, and suggest review priority."""

    if not files:
        raise HTTPException(400, "Upload at least one resume.")

    if len(files) > MAX_FILES:
        raise HTTPException(
            status_code=400,
            detail="Upload up to 30 resumes at a time.",
        )

    if len(requirements) > MAX_REQUIREMENTS:
        raise HTTPException(
            status_code=400,
            detail="Job requirements must be under 20,000 characters.",
        )

    if not requirements.strip():
        raise HTTPException(
            status_code=400,
            detail="Enter essential job requirements before analysis.",
        )

    if user["id"] != "test-only":
        if not request_id or len(request_id) > 100:
            raise HTTPException(
                400, "A unique analysis request ID is required."
            )

    chargeable = user["id"] != "test-only"

    # Reserve every file's maximum charge in one transaction.
    # Insufficient balances fail before any resume reaches AI.
    if chargeable:
        uploads = []
        for file in files:
            data = await file.read(MAX_SIZE + 1)
            uploads.append((file.filename or "Resume", data))
            await file.seek(0)
        fingerprint = fingerprint_batch(requirements, uploads)
        replay = claim_and_reserve(user["id"], request_id, fingerprint, len(files))
        if replay is not None:
            for file in files:
                await file.close()
            return replay

    started = time.monotonic()

    candidates = []
    errors = []

    for index, file in enumerate(files):
        reserved = chargeable
        credit_request = f"{request_id}:{index}"
        filename = file.filename or "Resume"

        try:
            data = await file.read(MAX_SIZE + 1)

            content = await run_in_threadpool(
                parse_resume,
                filename,
                data,
            )

            try:
                assessment = await match_resume(
                    requirements=requirements,
                    resume_text=content,
                )

                ai_completed = True
                if reserved:
                    finish(user["id"], credit_request, True)
                    reserved = False

            except MatchingError:
                if reserved:
                    finish(user["id"], credit_request, False)
                    reserved = False
                audit_event("ai_matching_unavailable", batch_index=index)

                assessment = fallback_result(requirements)
                ai_completed = False

            candidates.append({
                "id": str(index),
                "name": Path(filename).stem.replace("_", " "),
                "filename": filename,
                "summary": assessment["summary"],
                "evidence": content[:2000],
                "status": assessment["category"],
                "category": assessment["category"],
                "requirements": assessment["requirements"],
                "supported_count": assessment["supported_count"],
                "total_requirements": assessment["total_requirements"],
                "human_review_required": True,
                "ai_completed": ai_completed,
                "demo": False,
            })

        except HTTPException:
            if reserved:
                finish(user["id"], credit_request, False)
                reserved = False
            raise

        except ValueError as exc:
            if locals().get("reserved", False):
                finish(user["id"], credit_request, False)
                reserved = False
            errors.append({
                "filename": filename,
                "message": str(exc),
            })

        except Exception as exc:
            if locals().get("reserved", False):
                finish(user["id"], credit_request, False)
                reserved = False
            audit_event("resume_processing_failed", batch_index=index)

            errors.append({
                "filename": filename,
                "message": (
                    "This resume could not be processed. "
                    "Please try again."
                ),
            })

        finally:
            try:
                await file.close()

            except Exception as exc:
                audit_event("upload_cleanup_failed", batch_index=index)

    audit_event(
        "resume_batch_completed",
        files=len(files), candidates=len(candidates), errors=len(errors),
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    # Preserve the established numeric-only operational summary for existing
    # log consumers and resilience tests. Never log filenames or resume text.
    logger.info(
        "Resume batch complete: files=%d candidates=%d errors=%d duration_ms=%d",
        len(files), len(candidates), len(errors),
        int((time.monotonic() - started) * 1000),
    )

    response = {
        "mode": "ai",
        "notice": (
            "AI-assisted review priorities only. "
            "All hiring decisions require human review."
        ),
        "candidates": candidates,
        "errors": errors,
    }

    if chargeable:
        complete_batch(user["id"], request_id, response)
    return response
