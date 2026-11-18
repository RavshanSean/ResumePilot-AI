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
- [x] Read-only local payment consistency report (development)\n- [ ] Stripe-side payment reconciliation and refunds
- [x] Password change and revoke-all-sessions endpoints\n- [x] Expired session cleanup on startup\n- [ ] Password recovery and email verification
- [x] Persistent per-email login attempt lockout (development)\n- [ ] IP/device-aware abuse controls and production rate limiting\n- [ ] Rate limits, security testing, and privacy hardening
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
