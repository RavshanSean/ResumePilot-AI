import { useEffect, useRef, useState } from 'react';
import { FileText, FolderOpen, LoaderCircle, Sparkles, UploadCloud, X } from 'lucide-react';
import { fileError } from './files';
import { attachOriginals, MAX_FILES, updateReview } from './screening';
import type { Candidate, Decision, ReviewCategory } from './screening';

export function CandidateCard({ candidate, onChange }: {
  candidate: Candidate;
  onChange: (changes: Partial<Pick<Candidate, 'category' | 'decision'>>) => void;
}) {
  const [originalUrl, setOriginalUrl] = useState('');
  useEffect(() => {
    const url = URL.createObjectURL(candidate.original);
    setOriginalUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [candidate.original]);
  return <article className="candidate-card">
    <div className="candidate-heading"><FileText size={18} aria-hidden="true"/><h3>{candidate.filename}</h3></div>
    <details>
      <summary>Inspect resume & evidence</summary>
      {originalUrl && <a className="original-link" href={originalUrl} download={candidate.filename}>Download original resume</a>}
      {originalUrl && /\.pdf$/i.test(candidate.filename) && <a className="original-link" href={originalUrl} target="_blank" rel="noopener noreferrer">Open original PDF</a>}
      <p className="hint">{candidate.summary}</p>
      {candidate.requirements?.map((item, index) =>
        <p className="hint" key={index}>
          <strong>{item.requirement}</strong>: {item.status.replace('_', ' ')}
          {item.evidence ? ` | Evidence: "${item.evidence}"` : ''}
        </p>
      )}
      <h4>Qualification evidence · extracted text</h4>
      <pre>{candidate.evidence}</pre>
      <p className="hint">First 2,000 characters. AI findings are review priorities, not hiring decisions. Verify all evidence against the original.</p>
    </details>
    <div className="review-controls">
      <label>Review priority<select value={candidate.category} onChange={e => onChange({ category: e.target.value as ReviewCategory })}><option>Needs Review</option><option>Review First</option></select></label>
      <label>Manual decision<select value={candidate.decision} onChange={e => onChange({ decision: e.target.value as Decision })}><option>Undecided</option><option>Shortlisted</option><option>Reviewed</option></select></label>
    </div>
  </article>;
}

export default function App({ token = '' }: { token?: string }) {
  const [files, setFiles] = useState<File[]>([]);
  const [requirements, setRequirements] = useState('');
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [errors, setErrors] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [credits, setCredits] = useState<number | null>(null);

  async function refreshCredits() {
    if (!token) return;
    try {
      const response = await fetch('/api/credits/balance', {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (response.ok) {
        const result = await response.json();
        setCredits(result.balance);
      }
    } catch {
      // Balance is informational; backend enforces all credit limits.
    }
  }

  useEffect(() => {
    void refreshCredits();
  }, [token]);
  const input = useRef<HTMLInputElement>(null);
  const folder = useRef<HTMLInputElement>(null);

  function addFiles(incoming: FileList | null) {
    if (!incoming || busy) return;
    const next = [...files], issues: string[] = [];
    Array.from(incoming).forEach(file => {
      const issue = fileError(file);
      if (issue) issues.push(issue);
      else if (!next.some(f => f.name === file.name && f.size === file.size && f.lastModified === file.lastModified && f.webkitRelativePath === file.webkitRelativePath)) next.push(file);
    });
    if (next.length > MAX_FILES) issues.push(`Up to ${MAX_FILES} files per batch. Extra files were not added.`);
    setFiles(next.slice(0, MAX_FILES));
    setErrors(issues);
  }

  async function analyze() {
    if (!files.length || !requirements.trim() || busy) return;
    setBusy(true);
    setErrors([]);
    const batch = [...files];
    const data = new FormData();
    batch.forEach(file => data.append('files', file));
    data.append('requirements', requirements);
    data.append('request_id', crypto.randomUUID());
    try {
      const response = await fetch('/api/analyze', {
        method: 'POST',
        body: data,
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        const message = typeof payload?.detail === 'string'
          ? payload.detail
          : 'Could not process resumes. Check the backend and upload limits.';
        throw new Error(message);
      }
      const result = await response.json();
      setCandidates(attachOriginals(result.candidates, batch));
      setErrors(result.errors.map((error: { filename: string; message: string }) => `${error.filename}: ${error.message}`));
    } catch (error) {
      setErrors([error instanceof Error ? error.message : 'Could not connect to the local API.']);
    } finally {
      setBusy(false);
      await refreshCredits();
    }
  }

  return <main className="workspace">
    <header className="brand"><span className="brand-icon"><Sparkles size={25} aria-hidden="true"/></span><h1>ResumePilot <span>AI</span></h1></header>
    <section className="inputs" aria-label="Resume screening inputs">
      {credits !== null && <p className="disclosure" role="status">
        Available AI analysis credits: <strong>{credits}</strong>
      </p>}
      <label className="job-label" htmlFor="requirements">Job requirements</label>
      <textarea id="requirements" maxLength={20000} disabled={busy} value={requirements} onChange={e => setRequirements(e.target.value)} placeholder="Describe the role, essential skills, and experience…"/>
      <div className="dropzone" onDragOver={e => e.preventDefault()} onDrop={e => { e.preventDefault(); addFiles(e.dataTransfer.files); }}>
        <UploadCloud size={30} aria-hidden="true"/>
        <h2>Drop your resumes here</h2>
        <p>PDF / DOCX · 10 MB per file · Up to {MAX_FILES} per batch</p>
        <div className="upload-actions">
          <button className="secondary" disabled={busy} onClick={() => input.current?.click()}><FileText size={16}/>Choose files</button>
          <button className="secondary" disabled={busy} onClick={() => folder.current?.click()}><FolderOpen size={16}/>Choose folder</button>
        </div>
        <input ref={input} aria-label="Upload resume files" type="file" multiple accept=".pdf,.docx" hidden onChange={e => { addFiles(e.target.files); e.target.value = ''; }}/>
        <input ref={folder} aria-label="Upload resume folder" type="file" multiple hidden {...{ webkitdirectory: '', directory: '' }} onChange={e => { addFiles(e.target.files); e.target.value = ''; }}/>
      </div>
      {!!files.length && <ul className="file-list" aria-label="Selected resumes">{files.map((file, index) => <li key={index}><FileText size={15} aria-hidden="true"/><span>{file.webkitRelativePath || file.name}</span><button aria-label={`Remove ${file.webkitRelativePath || file.name}`} disabled={busy} onClick={() => setFiles(current => current.filter((_, i) => i !== index))}><X size={16}/></button></li>)}</ul>}
      <button className="analyze" disabled={!files.length || !requirements.trim() || busy} onClick={analyze}>{busy ? <LoaderCircle className="spin" size={19}/> : <Sparkles size={19}/>} {busy ? 'Analyzing resumes…' : 'Analyze Resumes'}</button>
      <p className="disclosure" role="status">AI-assisted screening. Human review is required for every candidate. Enter one essential requirement per line.</p>
      {!!errors.length && <div className="errors" role="alert">{errors.map((error, i) => <p key={i}>{error}</p>)}</div>}
    </section>
    <section className="results" aria-label="Resume review results" aria-busy={busy}>
      {(['Review First', 'Needs Review'] as const).map(category => <section className={`result-category ${category === 'Review First' ? 'green' : 'red'}`} key={category} aria-label={category}>
        <h2><span className="category-dot"/>{category}</h2>
        {candidates.filter(candidate => candidate.category === category).map(candidate => <CandidateCard key={candidate.id} candidate={candidate} onChange={changes => setCandidates(current => updateReview(current, candidate.id, changes))}/>)}
        {!candidates.some(candidate => candidate.category === category) && <p className="empty">{category === 'Review First' ? 'Candidates with evidence supporting every essential requirement appear here.' : 'Candidates requiring further review appear here.'}</p>}
      </section>)}
    </section>
    <p className="review-note">AI suggests review priorities. You control all decisions. No automatic rejections. Reviews reset on reload.</p>
  </main>;
}
