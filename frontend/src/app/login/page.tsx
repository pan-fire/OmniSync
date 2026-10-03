import type { Metadata } from 'next';
import { LoginForm } from '@/components/auth/login-form';

export const metadata: Metadata = {
  title: 'OmniSync',
};

// Reached only while the login is on (src/proxy.ts redirects here, and
// away from here when it is off or the browser is logged in already).
export default async function LoginPage ({ searchParams }: { searchParams: Promise<{ next?: string | string[] }> }) {
  const { next } = await searchParams;
  return <LoginForm next={typeof next === 'string' ? next : undefined} />;
}
