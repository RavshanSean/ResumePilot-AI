import { fetchJobStatus, type JobStatus } from './jobStatus';

/** Explicit polling session; caller must stop it when the view unmounts. */
export function startJobPolling(
  requestId: string,
  accessToken: string,
  onStatus: (status: JobStatus) => void,
  onError: (error: Error) => void,
  options: { baseUrl?: string; intervalMs?: number; lookup?: typeof fetchJobStatus } = {},
): () => void {
  const intervalMs = options.intervalMs ?? 3000;
  if (!Number.isFinite(intervalMs) || intervalMs < 1000) throw new Error('Polling interval must be at least 1000 ms.');
  const lookup = options.lookup ?? fetchJobStatus;
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let controller: AbortController | undefined;
  const stop = () => {
    stopped = true;
    if (timer !== undefined) clearTimeout(timer);
    controller?.abort();
  };
  const tick = async () => {
    if (stopped) return;
    controller = new AbortController();
    try {
      const status = await lookup(requestId, accessToken, { baseUrl: options.baseUrl, signal: controller.signal });
      if (stopped) return;
      onStatus(status);
      if (status === 'completed' || status === 'interrupted') { stop(); return; }
      timer = setTimeout(tick, intervalMs);
    } catch (error) {
      if (stopped) return;
      stop();
      onError(error instanceof Error ? error : new Error('Unable to check job status.'));
    }
  };
  void tick();
  return stop;
}
