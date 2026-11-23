import { useEffect, useState } from 'react';
import { startJobPolling } from './jobPolling';
import type { JobStatus } from './jobStatus';

/** React lifecycle adapter. Inactive until a caller supplies a job ID and token. */
export function useJobPolling(requestId: string | null, accessToken: string, enabled = true) {
  const [status, setStatus] = useState<JobStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setStatus(null);
    setError(null);
    if (!enabled || !requestId || !accessToken.trim()) return;
    return startJobPolling(requestId, accessToken, setStatus, cause => setError(cause.message));
  }, [requestId, accessToken, enabled]);

  return { status, error };
}
