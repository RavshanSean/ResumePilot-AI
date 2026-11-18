import { expect, test } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import RecoveryPanel, { recoveryRoute } from './RecoveryPanel';

test('recognizes recovery routes only', () => {
  expect(recoveryRoute('/forgot-password')).toBe('forgot');
  expect(recoveryRoute('/reset-password')).toBe('reset');
  expect(recoveryRoute('/verify-email')).toBe('verify');
  expect(recoveryRoute('/')).toBeNull();
  expect(recoveryRoute('/unknown')).toBeNull();
});

test('forgot password shows email request form', () => {
  const html = renderToStaticMarkup(<RecoveryPanel mode="forgot" />);
  expect(html).toContain('Forgot your password?');
  expect(html).toContain('Request reset link');
  expect(html).toContain('type="email"');
  expect(html).not.toContain('New password');
});

test('reset password requires confirmation', () => {
  const html = renderToStaticMarkup(
    <RecoveryPanel mode="reset" token="test-token" />,
  );
  expect(html).toContain('Reset your password');
  expect(html).toContain('Confirm new password');
  expect(html).toContain('minLength="12"');
});

test('verification screen supports link confirmation', () => {
  const html = renderToStaticMarkup(
    <RecoveryPanel mode="verify" token="test-token" />,
  );
  expect(html).toContain('Verify your email');
  expect(html).toContain('Verify email');
  expect(html).toContain('Back to sign in');
});
