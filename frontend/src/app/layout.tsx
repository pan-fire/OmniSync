import type { Metadata } from 'next';
import { cookies } from 'next/headers';
import { Geist, Geist_Mono as GeistMono, Vazirmatn } from 'next/font/google';
import type { ReactNode } from 'react';
import { Providers } from '@/components/providers';
import { AppShell } from '@/components/layout/app-shell';
import { isAuthEnabled } from '@/lib/auth/config';
import { DEFAULT_LOCALE, LOCALE_COOKIE, SIDEBAR_COOKIE, localeDir, parseLocale } from '@/i18n/config';
import './globals.css';

const geistSans = Geist({
  variable: '--font-geist-sans',
  subsets:  ['latin'],
});

const geistMono = GeistMono({
  variable: '--font-geist-mono',
  subsets:  ['latin'],
});

// Geist has no Arabic-script glyphs: Persian uses Vazirmatn (see
// globals.css). Not preloaded, so English and German pages never fetch it;
// the variable is always set because the locale can change client-side.
const vazirmatn = Vazirmatn({
  variable: '--font-vazirmatn',
  subsets:  ['arabic'],
  preload:  false,
});

export const metadata: Metadata = {
  title:       'OmniSync',
  description: 'Cloud sync dashboard',
};

export default async function RootLayout ({
  children,
}: Readonly<{
  children: ReactNode;
}>) {
  // Read the UI preferences on the server so the first HTML already has the
  // right language, direction and sidebar state (no hydration mismatch).
  const cookieStore = await cookies();
  const locale = parseLocale(cookieStore.get(LOCALE_COOKIE)?.value) ?? DEFAULT_LOCALE;
  const sidebarCollapsed = cookieStore.get(SIDEBAR_COOKIE)?.value === '1';

  return (
    <html lang={locale} dir={localeDir(locale)} suppressHydrationWarning>
      <body
        className={`${geistSans.variable} ${geistMono.variable} ${vazirmatn.variable} antialiased`}
      >
        <Providers initialLocale={locale}>
          <AppShell initialCollapsed={sidebarCollapsed} authEnabled={isAuthEnabled()}>{children}</AppShell>
        </Providers>
      </body>
    </html>
  );
}
