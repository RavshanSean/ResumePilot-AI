import { expect, test } from 'vitest';
import { attachOriginals, updateReview } from './screening';

const batch = [new File(['first'], 'resume.pdf'), new File(['second'], 'resume.pdf')];
const parsed = { id: '1', name: 'resume', filename: 'resume.pdf', summary: 'Extracted', evidence: 'Evidence from second file' };

test('preserves the correct original after parsing errors and duplicate filenames', () => {
  const [candidate] = attachOriginals([parsed], batch);
  expect(candidate.original).toBe(batch[1]);
  expect(candidate.evidence).toBe(parsed.evidence);
  expect(candidate.category).toBe('Needs Review');
  expect(candidate.decision).toBe('Undecided');
});

test('only manual updates prioritize candidates and record decisions', () => {
  const initial = attachOriginals([{ ...parsed, id: '0' }, parsed], batch);
  const reviewed = updateReview(initial, '1', { category: 'Review First', decision: 'Shortlisted' });
  expect(reviewed[0]).toBe(initial[0]);
  expect(reviewed[1].category).toBe('Review First');
  expect(reviewed[1].decision).toBe('Shortlisted');
  expect(reviewed[1].original).toBe(batch[1]);
  expect(initial[1].category).toBe('Needs Review');
  expect(updateReview(reviewed, '1', { category: 'Needs Review' })[1].decision).toBe('Shortlisted');
});

test('rejects invalid API references instead of linking the wrong resume', () => {
  for (const id of ['-1', '30', 'no-id', '0.5']) {
    expect(() => attachOriginals([{ ...parsed, id }], batch)).toThrow('invalid resume reference');
  }
});


test('preserves verified AI review priorities and human control', () => {
  const [candidate] = attachOriginals([{
    ...parsed,
    category: 'Review First',
    ai_completed: true,
    supported_count: 2,
    total_requirements: 2,
    requirements: [
      { requirement: 'Python', status: 'supported', evidence: 'Python' },
      { requirement: 'FastAPI', status: 'supported', evidence: 'FastAPI' },
    ],
  }], batch);

  expect(candidate.category).toBe('Review First');
  expect(candidate.ai_completed).toBe(true);
  expect(candidate.decision).toBe('Undecided');
  expect(candidate.requirements).toHaveLength(2);

  const reviewed = updateReview([candidate], candidate.id, {
    category: 'Needs Review',
    decision: 'Reviewed',
  });

  expect(reviewed[0].category).toBe('Needs Review');
  expect(reviewed[0].decision).toBe('Reviewed');
});

test('missing or unrecognized AI category defaults to human review', () => {
  const [candidate] = attachOriginals([{
    ...parsed,
    category: 'Needs Review',
    ai_completed: false,
  }], batch);

  expect(candidate.category).toBe('Needs Review');
  expect(candidate.decision).toBe('Undecided');
});
