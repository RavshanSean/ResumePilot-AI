/** Authenticated, read-only status lookup. No background polling is started here. */
export type JobStatus = 'queued' | 'processing' | 'completed' | 'interrupted';
const validStatuses = new Set<JobStatus>(['queued', 'processing', 'completed', 'interrupted']);

export async function fetchJobStatus(
  requestId: string,
  accessToken: string,
  options: { baseUrl?: string; signal?: AbortSignal } = {},
): Promise<JobStatus> {
  if (!requestId || requestId.length > 100 || requestId.includes('/') || requestId.includes('?') || requestId.includes('#')) {
    throw new Error('Invalid job request ID.');
  }
  if (!accessToken.trim()) {
    throw new Error('Authentication required.');
  }
  const baseUrl = (options.baseUrl ?? '').replace(/\/$/, '');
  const response = await fetch(`${baseUrl}/api/jobs/${encodeURIComponent(requestId)}/status`, {
    method: 'GET',
    headers: { Authorization: `Bearer ${accessToken}` },
    signal: options.signal,
  });
  if (response.status === 401) throw new Error('Session expired. Please sign in again.');
  if (response.status === 404) throw new Error('Job not found.');
  if (!response.ok) throw new Error('Unable to check job status. Please try again.');
  const data: unknown = await response.json();
  if (typeof data !== 'object' || data === null || !('status' in data) ||
      !validStatuses.has((data as { status: JobStatus }).status)) {
    throw new Error('Invalid job status response.');
  }
  return (data as { status: JobStatus }).status;
}
