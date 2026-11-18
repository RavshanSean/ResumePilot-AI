import { useState } from 'react';
import { Sparkles, LoaderCircle } from 'lucide-react';
import App from './App';
import './auth.css';

type User = { id: string; email: string };
type Session = {
  access_token: string;
  user?: User;
};

export default function AuthGate() {
  const [session, setSession] = useState<Session | null>(null);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [register, setRegister] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function authenticate(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;

    setBusy(true);
    setError('');

    try {
      const response = await fetch(
        register ? '/api/auth/register' : '/api/auth/login',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email, password }),
        },
      );

      const result = await response.json();

      if (!response.ok) {
        throw new Error(
          typeof result.detail === 'string'
            ? result.detail
            : 'Authentication failed. Please try again.',
        );
      }

      setSession(result as Session);
      setPassword('');
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : 'Unable to connect to the server.',
      );
    } finally {
      setBusy(false);
    }
  }

  async function logout() {
    const token = session?.access_token;
    // Immediately discard the local credential.
    setSession(null);
    setPassword('');

    if (token) {
      try {
        await fetch('/api/auth/logout', {
          method: 'POST',
          headers: { Authorization: `Bearer ${token}` },
        });
      } catch {
        // Session expiration still limits abandoned credentials.
      }
    }
  }

  if (session) {
    return <>
      <div className="auth-session-bar">
        <span>Signed in as {session.user?.email || email}</span>
        <button type="button" onClick={logout}>Log out</button>
      </div>
      <App token={session.access_token} />
    </>;
  }

  return <main className="auth-page">
    <section className="auth-panel" aria-label="Account access">
      <div className="auth-logo">
        <Sparkles size={28} aria-hidden="true" />
        <h1>ResumePilot <span>AI</span></h1>
      </div>

      <h2>{register ? 'Create your account' : 'Welcome back'}</h2>
      <p className="auth-subtitle">
        {register
          ? 'Start reviewing resumes with human oversight.'
          : 'Sign in to your recruiting workspace.'}
      </p>

      <form onSubmit={authenticate}>
        <label htmlFor="auth-email">Email address</label>
        <input
          id="auth-email"
          type="email"
          autoComplete="email"
          maxLength={254}
          required
          value={email}
          onChange={event => setEmail(event.target.value)}
        />

        <label htmlFor="auth-password">Password</label>
        <input
          id="auth-password"
          type="password"
          autoComplete={register ? 'new-password' : 'current-password'}
          minLength={12}
          maxLength={1024}
          required
          value={password}
          onChange={event => setPassword(event.target.value)}
        />

        {register && <p className="auth-hint">
          Use at least 12 characters.
        </p>}

        {error && <p className="auth-error" role="alert">{error}</p>}

        <button className="auth-submit" type="submit" disabled={busy}>
          {busy && <LoaderCircle size={18} aria-hidden="true" />}
          {busy ? 'Please wait…' : register ? 'Create account' : 'Sign in'}
        </button>
      </form>

      <p className="auth-switch">
        {register ? 'Already have an account?' : 'New to ResumePilot?'}
        {' '}
        <button
          type="button"
          onClick={() => {
            setRegister(!register);
            setError('');
          }}
        >
          {register ? 'Sign in' : 'Create account'}
        </button>
      </p>
      <p className="auth-hint">
        Development authentication. No payments or automatic hiring decisions.
      </p>
    </section>
  </main>;
}
