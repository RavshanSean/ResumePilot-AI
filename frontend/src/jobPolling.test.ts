import { afterEach, expect, test, vi } from 'vitest';
import { startJobPolling } from './jobPolling';
import type { JobStatus } from './jobStatus';

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

test('polls sequentially and stops on completion', async () => {
  vi.useFakeTimers();
  const lookup = vi.fn().mockResolvedValueOnce('queued').mockResolvedValueOnce('processing').mockResolvedValueOnce('completed');
  const statuses: JobStatus[] = [];
  const errors = vi.fn();
  startJobPolling('job', 'token', status => statuses.push(status), errors, { lookup, intervalMs: 1000 });
  await vi.advanceTimersByTimeAsync(2500);
  expect(statuses).toEqual(['queued', 'processing', 'completed']);
  await vi.advanceTimersByTimeAsync(5000);
  expect(lookup).toHaveBeenCalledTimes(3);
  expect(errors).not.toHaveBeenCalled();
});

test('cancellation aborts request and suppresses late updates', async () => {
  let resolve!: (status: JobStatus) => void;
  const lookup = vi.fn(() => new Promise<JobStatus>(r => { resolve = r; }));
  const onStatus = vi.fn();
  const onError = vi.fn();
  const stop = startJobPolling('job', 'token', onStatus, onError, { lookup });
  expect(lookup).toHaveBeenCalledTimes(1);
  const signal = lookup.mock.calls.length && (lookup.mock.calls[0] as unknown as [unknown, unknown, { signal: AbortSignal }])[2].signal;
  stop();
  expect(signal).toBeInstanceOf(AbortSignal);
  expect((signal as AbortSignal).aborted).toBe(true);
  resolve('completed');
  await Promise.resolve();
  expect(onStatus).not.toHaveBeenCalled();
  expect(onError).not.toHaveBeenCalled();
});

test('stops after network or session errors without retrying', async () => {
  vi.useFakeTimers();
  const lookup = vi.fn().mockRejectedValue(new Error('Session expired. Please sign in again.'));
  const onError = vi.fn();
  startJobPolling('job', 'token', vi.fn(), onError, { lookup });
  await vi.advanceTimersByTimeAsync(10000);
  expect(lookup).toHaveBeenCalledTimes(1);
  expect(onError).toHaveBeenCalledTimes(1);
});

test('rejects unsafe polling intervals', () => {
  expect(() => startJobPolling('job', 'token', vi.fn(), vi.fn(), { intervalMs: 10 })).toThrow('at least 1000');
});
