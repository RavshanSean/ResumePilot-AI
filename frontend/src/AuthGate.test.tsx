import { expect, test } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import AuthGate from './AuthGate';

test('login screen is shown before authentication', () => {
  const html = renderToStaticMarkup(<AuthGate />);
  expect(html).toContain('Welcome back');
  expect(html).toContain('Sign in');
  expect(html).toContain('Create account');
  expect(html).toContain('type="password"');
  expect(html).not.toContain('Analyze Resumes');
});
