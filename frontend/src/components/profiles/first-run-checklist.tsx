'use client';

import Link from 'next/link';
import { Check, Cloud, Plus } from 'lucide-react';
import { useRemotes } from '@/hooks/use-remotes';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { useTranslation } from '@/i18n';
import { cn } from '@/lib/utils';

/**
 * The dashboard before the first profile: what to do, in order. A profile
 * needs a remote, so step 1 is done as soon as one exists.
 */
export function FirstRunChecklist () {
  const { t } = useTranslation();
  const remotes = useRemotes();
  const hasRemote = (remotes.data?.length ?? 0) > 0;

  const steps = [
    {
      key:    'remote',
      done:   hasRemote,
      title:  t('dashboard.firstRun.remoteTitle'),
      hint:   t('dashboard.firstRun.remoteHint'),
      action: (
        <Button asChild variant={hasRemote ? 'outline' : 'default'} size="sm">
          <Link href="/remotes">
            <Cloud className="h-4 w-4" aria-hidden="true" />
            {hasRemote ? t('dashboard.firstRun.manageRemotes') : t('dashboard.firstRun.addRemote')}
          </Link>
        </Button>
      ),
    },
    {
      key:    'profile',
      done:   false,
      title:  t('dashboard.firstRun.profileTitle'),
      hint:   t('dashboard.noProfilesHint'),
      action: (
        <Button asChild variant={hasRemote ? 'default' : 'outline'} size="sm">
          <Link href="/profiles?create=1">
            <Plus className="h-4 w-4" aria-hidden="true" />
            {t('profiles.createFirst')}
          </Link>
        </Button>
      ),
    },
  ];

  return (
    <Card data-testid="first-profile-cta">
      <CardHeader>
        <CardTitle>{t('dashboard.noProfilesTitle')}</CardTitle>
        <CardDescription>{t('dashboard.firstRun.intro')}</CardDescription>
      </CardHeader>
      <CardContent>
        <ol className="space-y-4">
          {steps.map((step, i) => (
            <li key={step.key} className="flex gap-3" data-done={step.done || undefined}>
              <span
                className={cn(
                  'flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-sm font-medium',
                  step.done && 'border-primary bg-primary text-primary-foreground'
                )}
                aria-hidden="true"
              >
                {step.done ? <Check className="h-4 w-4" /> : i + 1}
              </span>
              <div className="min-w-0 flex-1 space-y-2">
                <p className={cn('font-medium', step.done && 'text-muted-foreground line-through')}>
                  {step.title}
                  {step.done && <span className="sr-only"> ({t('dashboard.firstRun.done')})</span>}
                </p>
                <p className="text-sm text-muted-foreground">{step.hint}</p>
                {step.action}
              </div>
            </li>
          ))}
        </ol>
      </CardContent>
    </Card>
  );
}
