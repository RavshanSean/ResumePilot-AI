import { afterEach, expect, test, vi } from 'vitest';
import { startJobPolling } from './jobPolling';
import type { JobStatus } from './jobStatus';

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

test('stopping twice aborts active lookup and never emits a late result', async () => {
  let resolve!: (value: JobStatus) => void;
  const lookup = vi.fn((_id: string, _token: string, _options?: { baseUrl?: string; signal?: AbortSignal }) =>
    new Promise<JobStatus>(r => { resolve = r; }));
  const onStatus = vi.fn();
  const onError = vi.fn();
  const stop = startJobPolling('job', 'token', onStatus, onError, { lookup });
  const signal = lookup.mock.calls[0][2]?.signal;
  expect(signal?.aborted).toBe(false);
  stop();
  stop();
  expect(signal?.aborted).toBe(true);
  resolve('completed');
  await Promise.resolve();
  expect(onStatus).not.toHaveBeenCalled();
  expect(onError).not.toHaveBeenCalled();
});

test('stopping after queued response cancels the next scheduled poll', async () => {
  vi.useFakeTimers();
  const lookup = vi.fn().mockResolvedValue('queued');
  const onStatus = vi.fn();
  const stop = startJobPolling('job', 'token', onStatus, vi.fn(), { lookup, intervalMs: 1000 });
  await vi.advanceTimersByTimeAsync(0);
  expect(onStatus).toHaveBeenCalledWith('queued');
  stop();
  await vi.advanceTimersByTimeAsync(5000);
  expect(lookup).toHaveBeenCalledTimes(1);
});

test('interrupted status is terminal and never triggers another poll', async () => {
  vi.useFakeTimers();
  const lookup = vi.fn().mockResolvedValue('interrupted');
  const onStatus = vi.fn();
  startJobPolling('job', 'token', onStatus, vi.fn(), { lookup, intervalMs: 1000 });
  await vi.advanceTimersByTimeAsync(5000);
  expect(onStatus).toHaveBeenCalledTimes(1);
  expect(onStatus).toHaveBeenCalledWith('interrupted');
  expect(lookup).toHaveBeenCalledTimes(1);
});

test('a rejected request after cancellation cannot report an error', async () => {
  let reject!: (reason: Error) => void;
  const lookup = vi.fn(() => new Promise<JobStatus>((_resolve, r) => { reject = r; }));
  const onError = vi.fn();
  const stop = startJobPolling('job', 'token', vi.fn(), onError, { lookup });
  stop();
  reject(new Error('late network error'));
  await Promise.resolve();
  expect(onError).not.toHaveBeenCalled();
});
