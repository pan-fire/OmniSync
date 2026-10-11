'use client';

import { useEffect, useId, useState } from 'react';
import type { FormEvent } from 'react';
import { LogIn } from 'lucide-react';
import { useTranslation } from '@/i18n';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { LanguageSwitcher } from '@/components/layout/language-switcher';
import { useDocumentTitle } from '@/components/layout/page-header';
import { AUTH_LOGIN_PATH, AUTH_SESSION_PATH } from '@/lib/auth/config';
import { safeNextPath } from '@/lib/auth/next-path';
import { retryAfterSeconds } from '@/lib/api-error';

type Problem =
  | { kind: 'invalid' | 'misconfigured' | 'failed' }
  | { kind: 'throttled'; until: number };

export function LoginForm ({ next: rawNext }: { next?: string }) {
  const { t } = useTranslation();
  // "Log in · OmniSync", like the other pages' "<page> · OmniSync".
  useDocumentTitle(t('auth.submit'));
  const next = safeNextPath(rawNext);
  const headingId = useId();
  const errorId = useId();
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [now, setNow] = useState(() => Date.now());

  // A link from another site does not carry the SameSite=Strict cookie, so
  // the server could not tell this browser is logged in. Same-origin
  // requests do carry it: ask, and go on if so.
  useEffect(() => {
    let cancelled = false;
    fetch(AUTH_SESSION_PATH, { credentials: 'same-origin' })
      .then((res) => (res.ok ? res.json() : null))
      .then((body: { authenticated?: unknown } | null) => {
        if (!cancelled && body?.authenticated === true) window.location.replace(next);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [next]);

  const waitSeconds = problem?.kind === 'throttled' ? Math.max(0, Math.ceil((problem.until - now) / 1000)) : 0;

  useEffect(() => {
    if (problem?.kind !== 'throttled') return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [problem]);

  async function submit (event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting || waitSeconds > 0 || !password) return;
    setSubmitting(true);
    try {
      const res = await fetch(AUTH_LOGIN_PATH, {
        method:      'POST',
        credentials: 'same-origin',
        headers:     { 'Content-Type': 'application/json' },
        body:        JSON.stringify({ password }),
      });
      if (res.ok) {
        // A full load, so the layout renders with the session.
        window.location.assign(next);
        return;
      }
      const body: unknown = await res.json().catch(() => null);
      const seconds = retryAfterSeconds(body);
      // One clock reading for both, or a slow tick in between shows one
      // second more than the server asked for.
      const at = Date.now();
      setNow(at);
      if (res.status === 429 && seconds) {
        setProblem({ kind: 'throttled', until: at + seconds * 1000 });
      } else if (res.status === 401) {
        setPassword('');
        setProblem(seconds ? { kind: 'throttled', until: at + seconds * 1000 } : { kind: 'invalid' });
      } else {
        setProblem({ kind: res.status === 503 ? 'misconfigured' : 'failed' });
      }
    } catch {
      setProblem({ kind: 'failed' });
    } finally {
      setSubmitting(false);
    }
  }

  let message: string | null = null;
  if (problem?.kind === 'throttled') {
    message = waitSeconds > 0 ? t('auth.throttled', { count: waitSeconds }) : t('auth.invalid');
  } else if (problem) {
    message = t(`auth.${problem.kind}`);
  }

  return (
    <Card className="w-full max-w-sm">
      <CardHeader>
        <h1 id={headingId} className="text-xl font-semibold">{t('auth.title')}</h1>
        <CardDescription>{t('auth.description')}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <form aria-labelledby={headingId} className="space-y-4" onSubmit={submit} noValidate>
          <div className="space-y-2">
            <Label htmlFor="login-password">{t('auth.password')}</Label>
            <Input
              id="login-password"
              name="password"
              type="password"
              autoComplete="current-password"
              autoFocus
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              aria-invalid={problem ? true : undefined}
              aria-describedby={message ? errorId : undefined}
            />
          </div>
          <p id={errorId} role="alert" className="min-h-5 text-sm text-destructive">
            {message}
          </p>
          <Button type="submit" className="w-full gap-2" disabled={submitting || waitSeconds > 0 || !password}>
            <LogIn className="h-4 w-4 rtl:-scale-x-100" aria-hidden="true" />
            {submitting ? t('auth.submitting') : t('auth.submit')}
          </Button>
        </form>
        <LanguageSwitcher />
      </CardContent>
    </Card>
  );
}
