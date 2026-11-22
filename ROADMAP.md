# ResumePilot AI Development Roadmap

## Completed foundations

- [x] React and FastAPI application
- [x] PDF/DOCX bulk upload
- [x] Evidence-based AI review priorities
- [x] Human-controlled hiring decisions
- [x] Ollama development integration
- [x] Account registration, login, and logout
- [x] Protected screening endpoint
- [x] Five trial credits per account
- [x] Atomic credit reservations and refunds
- [x] Account-specific credit balances
- [x] Duplicate request rejection
- [x] Development stale-reservation recovery

## Remaining work

- [ ] Robust whole-batch idempotency and retry handling
- [ ] Durable job queue and worker leases
- [ ] Safe recovery and reconciliation across worker crashes
- [x] Atomic preflight credit availability and batch reservation
- [x] Stripe test-mode Checkout backend (disabled by default)
- [x] Signature-verified paid Checkout event handling and duplicate grant protection (development)
- [ ] End-to-end Stripe test-mode integration and payment reconciliation
- [x] Read-only local payment consistency report (development)
- [ ] Stripe-side payment reconciliation and refunds
- [x] Password change and revoke-all-sessions endpoints
- [x] Expired session cleanup on startup
- [x] Password reset and email verification backend (SMTP delivery configurable; development)
- [ ] Production email provider setup, delivery testing, frontend recovery screens and verification enforcement
- [x] Persistent per-email login attempt lockout (development)
- [x] Direct-peer IP login failure limit (development)
- [ ] Trusted proxy configuration and production abuse controls
- [x] Stale login-attempt cleanup (development)
- [ ] Rate limits, security testing, and privacy hardening
- [ ] Production PostgreSQL and migrations
- [ ] Scalability, observability, and deployment
- [ ] Hiring compliance and human oversight review
- [ ] Activate and test real OpenAI API after application completion
- [ ] Commercial launch

## Rules

- OpenAI API activation is deferred until the application is finished.
- Stripe is not enabled yet.
- Never store card numbers or CVV.
- Never automatically reject applicants.
- The local SQLite credit system is not production-ready.
- Recovery is opt-in using RESUMEPILOT_RECOVER_STALE_ON_STARTUP=1
  and must only run when older workers are stopped.
- Development pricing and trial credit quantities may change.

## Pre-launch business and operations (AFTER coding is complete)

Complete before accepting live payments or launching publicly.

- [ ] Choose business structure and complete required registration
- [ ] Obtain EIN if needed
- [ ] Open a dedicated business bank account
- [ ] Create and verify live Stripe business account
- [ ] Configure Stripe payouts and payment methods
- [ ] Finalize pricing after AI, hosting, Stripe, and support costs
- [ ] Determine applicable sales-tax treatment and registrations
- [ ] Assess international VAT/GST obligations
- [ ] Configure tax collection where required
- [ ] Establish bookkeeping and tax reporting
- [ ] Configure production PostgreSQL, backups, and encryption
- [ ] Verify per-user database isolation
- [ ] Finalize resume retention and deletion policies
- [ ] Publish privacy, terms, and refund policies
- [ ] Complete hiring-AI compliance and security review
- [ ] Test payment reconciliation and refund workflows
- [ ] Approve and enable live payments only after launch checks

## Batch 15 account recovery UI

- [x] Forgot-password screen and request workflow
- [x] Reset-password screen and confirmation
- [x] Email verification screen and account status
- [x] Recovery UI regression tests
- [ ] Configure real SMTP delivery and sender authentication
- [ ] Production recovery abuse protection and delivery monitoring
- [ ] Decide and enforce verification policy after testing

## Batch 16: Safe replay
- [x] Persist completed analysis results for identical request IDs
- [x] Reject changed inputs and concurrent duplicate submissions
- [ ] Atomic journal and credit reservation, crash recovery, worker leases
- [ ] Privacy retention policy for stored candidate evidence

## Batch 17: Atomic claims and controlled crash reconciliation
- [x] Reserve credits and create batch journal in one SQLite transaction
- [x] Mark stale in-progress batches interrupted under controlled maintenance
- [x] Refund only still-reserved credits, once; preserve completed charges
- [x] Preserve completed response replay and reject interrupted request IDs
- [ ] Durable input storage, worker leases, heartbeats and automatic resume
- [ ] Production-safe multi-worker reconciliation and deployment orchestration

## Batch 18: Durable queue metadata and worker leases
- [x] SQLite-backed job records created atomically with batch and credit reservation
- [x] Exclusive queued-job claim with random fencing token
- [x] Lease heartbeat and token-checked completion
- [x] Controlled expired-lease maintenance without automatic rerun
- [x] Existing synchronous screening flow and frontend unchanged
- [ ] Secure durable upload storage and actual background worker execution
- [ ] Transactionally couple worker completion to candidate results and credit ledger
- [ ] Production-grade multiworker lease recovery and orchestration

## Batch 19: Private temporary resume storage foundation
- [x] Private local file permissions, opaque identifiers and per-account access checks
- [x] Bounded file sizes, integrity verification and expiration metadata
- [x] Controlled expired-file cleanup and regression tests
- [ ] Connect storage to asynchronous submission and worker execution
- [ ] Encrypted production storage, retention/deletion policy, orphan-file reconciliation
- [ ] Filesystem access controls, monitoring and backup/deletion verification

## Batch 20: Staged queue inputs (development)
- [x] Immutable, account-scoped ordered upload manifest linked to existing queued batch
- [x] Fingerprint and reserved-credit checks before staging; transactional manifest publication
- [x] Separate staged-job claim gated on manifest and file expiry
- [x] Preserve synchronous API and frontend; no worker execution or live payment/API activation
- [ ] Coordinate file deletion and expired manifests before retention cleanup
- [ ] Crash-orphan reconciliation and durable encryption at rest
- [ ] Wire staging into HTTP submission and implement fenced worker execution

## Batch 21: Fenced background-worker input preparation
- [x] Validate current worker lease before reading staged resume bytes
- [x] Verify integrity and batch fingerprint; recheck lease after disk I/O
- [x] Keep AI execution and credit settlement disabled pending atomic fenced commits
- [ ] Add worker execution, atomic results and credit settlement before activation

## Batch 22: Atomic fenced worker settlement
- [x] Transactionally settle per-file credits, batch replay response and queue completion
- [x] Reject stale or incorrect worker tokens and mismatched input counts
- [x] Roll back all changes on incomplete credit reservations
- [x] Preserve synchronous API and frontend without activating workers
- [ ] Validate AI outcomes in actual background worker and add lifecycle orchestration
- [ ] Ensure staged-file cleanup, crash reconciliation and production-safe encryption

## Batch 23: Explicit worker orchestration and controlled crash recovery
- [x] One-job worker coordinator with injected trusted processor and lease heartbeat callback
- [x] Fenced atomic settlement and no automatic retries on failures
- [x] Read-only recovery inspection and explicit stopped-worker reconciliation
- [x] Existing synchronous API and frontend unchanged; no background service activated
- [ ] Production worker process lifecycle, heartbeat scheduling and monitoring
- [ ] Staged input retention and cleanup coordination after terminal jobs
- [ ] HTTP asynchronous submission and authenticated job status polling

## Batch 24: Offline terminal staging retention
- [x] Explicit offline cleanup for completed/interrupted staged job manifests
- [x] Preserve batch replay and credit ledger during private upload deletion
- [x] Bounded orphan-file reconciliation with age and symlink safeguards
- [x] No automatic deletion scheduler, HTTP endpoint, frontend changes or live providers
- [ ] Production encryption at rest, secure deletion guarantees and retention policy

## Batch 25: Privacy audit and private-file hardening
- [x] Allowlisted numeric-only audit events for resume processing
- [x] Reject group/world-readable resume files and hardlinks on read
- [x] Reject symlinked ancestors in temporary storage path
- [x] Security regression tests
- [ ] Production encryption at rest and full operational log audit

## Batch 26: Recovery startup reliability
- [x] Initialize email verification and recovery tables on startup
- [x] Clean expired recovery tokens after initialization
- [x] Test fresh database, legacy migration and restart persistence
- [ ] Configure production SMTP separately

## Batch 27: Defensive resume parsing
- [x] Bound PDF page count and extracted PDF/DOCX text
- [x] Preserve specific safe validation messages for size, encryption, and text limits
- [x] Add regression tests for malformed, encrypted, and compressed-content documents
- [ ] Production-grade sandboxed parsing and CPU/memory limits remain future work

## Batch 28: AI matching reliability
- [x] Reject invalid AI timeout settings before network calls
- [x] Remove unverified model quotes from uncertain/not-met requirements
- [x] Offline tests for connection errors, timeouts, HTTP errors and malformed responses
- [ ] Production worker retry and reconciliation remain future work
