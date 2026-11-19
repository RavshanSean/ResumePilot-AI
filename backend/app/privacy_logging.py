"""Metadata-only, allowlisted operational audit events."""
import logging

EVENTS = frozenset(('resume_batch_completed','resume_processing_failed','ai_matching_unavailable','upload_cleanup_failed'))
FIELDS = frozenset(('files','candidates','errors','duration_ms','batch_index'))

def audit_event(event, **counts):
    if type(event) is not str or event not in EVENTS:
        raise ValueError('Unknown audit event.')
    if not counts.keys() <= FIELDS or any(type(v) is not int or not 0 <= v <= 1000000000 for v in counts.values()):
        raise ValueError('Audit event accepts only bounded numeric metadata.')
    message = 'privacy_audit event=' + event
    message += ''.join(' %s=%d' % (key, counts[key]) for key in sorted(counts))
    logging.getLogger('resumepilot.audit').info(message)
