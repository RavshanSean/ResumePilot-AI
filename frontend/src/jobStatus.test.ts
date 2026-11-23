import { afterEach, expect, test, vi } from 'vitest';
import { fetchJobStatus } from './jobStatus';

afterEach(() => vi.unstubAllGlobals());

test.each(['queued', 'processing', 'completed', 'interrupted'] as const)(
  'accepts the %s status with bearer authentication', async status => {
    const mock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ status }) });
    vi.stubGlobal('fetch', mock);
    expect(await fetchJobStatus('job-1', 'secret', { baseUrl: 'http://localhost:8000/' })).toBe(status);
    expect(mock).toHaveBeenCalledWith('http://localhost:8000/api/jobs/job-1/status',
      expect.objectContaining({ method: 'GET', headers: { Authorization: 'Bearer secret' } }));
  },
);

test('rejects missing authentication and malformed IDs without a network request', async () => {
  const mock = vi.fn();
  vi.stubGlobal('fetch', mock);
  await expect(fetchJobStatus('job-1', '')).rejects.toThrow('Authentication required');
  await expect(fetchJobStatus('bad/id', 'secret')).rejects.toThrow('Invalid job request ID');
  expect(mock).not.toHaveBeenCalled();
});

test.each([[401, 'Session expired'], [404, 'Job not found'], [500, 'Unable to check']])(
  'maps HTTP %i to a safe error', async (status, message) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status }));
    await expect(fetchJobStatus('job-1', 'secret')).rejects.toThrow(message);
  },
);

test('rejects unexpected payloads instead of silently reporting a status', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ status: 'unknown', lease_token: 'private' }) }));
  await expect(fetchJobStatus('job-1', 'secret')).rejects.toThrow('Invalid job status response');
});
