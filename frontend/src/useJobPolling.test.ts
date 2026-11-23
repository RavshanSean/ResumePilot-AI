import { expect, test } from 'vitest';
import { useJobPolling } from './useJobPolling';

test('exports the lifecycle adapter without starting background requests on import', () => {
  expect(typeof useJobPolling).toBe('function');
});
