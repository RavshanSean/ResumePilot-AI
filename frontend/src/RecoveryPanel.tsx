import { useEffect, useState } from 'react';
import { Sparkles } from 'lucide-react';
import './auth.css';

type Mode = 'forgot' | 'reset' | 'verify';

export function recoveryRoute(path: string): Mode | null {
  if (path === '/forgot-password') return 'forgot';
  if (path === '/reset-password') return 'reset';
  if (path === '/verify-email') return 'verify';
  return null;
}

async function postJson(path: string, body: object) {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(
      typeof payload.detail === 'string'
        ? payload.detail
        : 'Request failed. Please try again.',
    );
  }
  return payload;
}

export default function RecoveryPanel({
  mode,
  token: suppliedToken,
}: {
  mode: Mode;
  token?: string;
}) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [complete, setComplete] = useState(false);
  const [token] = useState(() => {
    if (suppliedToken !== undefined) return suppliedToken;
    if (typeof window === 'undefined') return '';
    return new URLSearchParams(window.location.search).get('token') || '';
  });

  useEffect(() => {
    if (typeof window !== 'undefined' && mode !== 'forgot') {
      // Remove sensitive tokens from the visible URL and browser history.
      window.history.replaceState(null, '', window.location.pathname);
    }
  }, [mode]);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || complete) return;
    setBusy(true);
    setError('');
    setMessage('');
    try {
      if (mode === 'forgot') {
        const result = await postJson('/api/auth/forgot-password', { email });
        setMessage(result.message);
        setComplete(true);
      } else if (mode === 'reset') {
        if (!token) throw new Error('Reset link is missing its token.');
        if (password !== confirm) {
          throw new Error('Passwords do not match.');
        }
        const result = await postJson('/api/auth/reset-password', {
          token,
          new_password: password,
        });
        setMessage(result.message);
        setPassword('');
        setConfirm('');
        setComplete(true);
      } else {
        if (!token) throw new Error('Verification link is missing its token.');
        const result = await postJson('/api/auth/verify-email', { token });
        setMessage(result.email_verified
          ? 'Your email is verified. You can sign in.'
          : 'Verification could not be completed.');
        setComplete(true);
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Request failed.');
    } finally {
      setBusy(false);
    }
  }

  const heading = mode === 'forgot'
    ? 'Forgot your password?'
    : mode === 'reset'
      ? 'Reset your password'
      : 'Verify your email';

  return <main className="auth-page">
    <section className="auth-panel" aria-label="Account recovery">
      <div className="auth-logo">
        <Sparkles size={28} aria-hidden="true" />
        <h1>ResumePilot <span>AI</span></h1>
      </div>
      <h2>{heading}</h2>
      <p className="auth-subtitle">
        {mode === 'forgot'
          ? 'Enter your account email to request a reset link.'
          : mode === 'reset'
            ? 'Choose a new password of at least 12 characters.'
            : 'Confirm your email address using the verification link.'}
      </p>
      {!complete && <form onSubmit={submit}>
        {mode === 'forgot' && <>
          <label htmlFor="recovery-email">Email address</label>
          <input id="recovery-email" type="email" required
            autoComplete="email" maxLength={254}
            value={email} onChange={e => setEmail(e.target.value)} />
        </>}
        {mode === 'reset' && <>
          <label htmlFor="recovery-password">New password</label>
          <input id="recovery-password" type="password" required
            minLength={12} maxLength={1024} autoComplete="new-password"
            value={password} onChange={e => setPassword(e.target.value)} />
          <label htmlFor="recovery-confirm">Confirm new password</label>
          <input id="recovery-confirm" type="password" required
            minLength={12} maxLength={1024} autoComplete="new-password"
            value={confirm} onChange={e => setConfirm(e.target.value)} />
        </>}
        {error && <p className="auth-error" role="alert">{error}</p>}
        <button className="auth-submit" type="submit" disabled={busy}>
          {busy ? 'Please wait…' : mode === 'forgot'
            ? 'Request reset link'
            : mode === 'reset' ? 'Reset password' : 'Verify email'}
        </button>
      </form>}
      {message && <p role="status" className="auth-hint">{message}</p>}
      {complete && <p className="auth-hint">
        {mode === 'forgot'
          ? 'If no email arrives, check spam and confirm email delivery is configured.'
          : 'You can now return to the sign-in screen.'}
      </p>}
      <p className="auth-switch">
        <a href="/">Back to sign in</a>
      </p>
      <p className="auth-hint">
        Recovery links expire and can be used only once.
      </p>
    </section>
  </main>;
}
