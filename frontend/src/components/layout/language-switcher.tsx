'use client';

import { Languages } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { useTranslation, type Locale } from '@/i18n';

const languages: { locale: Locale; label: string }[] = [
  { locale: 'en', label: 'English' },
  { locale: 'fa', label: 'فارسی' },
  { locale: 'de', label: 'Deutsch' },
];

export function LanguageSwitcher () {
  const { setLocale, t, locale } = useTranslation();

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="sm" className="w-full justify-start gap-2">
          <Languages className="h-4 w-4" aria-hidden="true" />
          {t('common.language')}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start">
        {languages.map((lang) => (
          <DropdownMenuItem
            key={lang.locale}
            lang={lang.locale}
            aria-current={lang.locale === locale ? 'true' : undefined}
            onClick={() => setLocale(lang.locale)}
          >
            {lang.label}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
