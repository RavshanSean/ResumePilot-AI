export const MAX_FILES = 30;
export type ReviewCategory = 'Review First' | 'Needs Review';
export type Decision = 'Undecided' | 'Shortlisted' | 'Reviewed';
export type ParsedCandidate = {
  id: string;
  name: string;
  filename: string;
  summary: string;
  evidence: string;
};
export type Candidate = ParsedCandidate & {
  original: File;
  category: ReviewCategory;
  decision: Decision;
};

// The API returns the original batch index as its id, even after per-file errors.
// Do not match by filename: folders may contain resumes with identical names.
export function attachOriginals(parsed: ParsedCandidate[], batch: File[]): Candidate[] {
  return parsed.map(candidate => {
    const index = Number(candidate.id);
    if (!Number.isInteger(index) || index < 0 || !batch[index]) {
      throw new Error('The API returned an invalid resume reference. Please try again.');
    }
    return { ...candidate, original: batch[index], category: 'Needs Review', decision: 'Undecided' };
  });
}

export function updateReview(candidates: Candidate[], id: string, changes: Partial<Pick<Candidate, 'category' | 'decision'>>) {
  return candidates.map(candidate => candidate.id === id ? { ...candidate, ...changes } : candidate);
}
