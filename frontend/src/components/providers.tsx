'use client';

import { useState } from 'react';
import type { ReactNode } from 'react';
import { QueryClientProvider } from '@tanstack/react-query';
import { ThemeProvider } from 'next-themes';
import { I18nProvider, type Locale } from '@/i18n';
import { Toaster } from '@/components/ui/sonner';
import { createQueryClient } from '@/config/query-client';

export function Providers ({ children, initialLocale }: { children: ReactNode; initialLocale?: Locale }) {
  const [queryClient] = useState(() => createQueryClient());

  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider
        attribute="class"
        defaultTheme="system"
        enableSystem
        disableTransitionOnChange
      >
        <I18nProvider initialLocale={initialLocale}>
          {children}
          <Toaster />
        </I18nProvider>
      </ThemeProvider>
    </QueryClientProvider>
  );
}
