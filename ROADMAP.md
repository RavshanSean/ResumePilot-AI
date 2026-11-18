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
- [ ] Preflight credit availability and batch-level accounting
- [ ] Stripe hosted Checkout
- [ ] Verified Stripe webhooks and exactly-once credit grants
- [ ] Payment reconciliation and refunds
- [ ] Password recovery and email verification
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
