'use client';

import { useState } from 'react';
import { useTranslation } from '@/i18n';
import { useConfig, useUpdateConfig } from '@/hooks/use-config';
import { useRemotes } from '@/hooks/use-remotes';
import { RemoteManager } from '@/components/config/remote-manager';
import { PageHelp } from '@/components/layout/page-help';
import { PageHeader } from '@/components/layout/page-header';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';

const LOG_LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'];
const DEFAULT_HISTORY_DAYS = 90;
const MAX_HISTORY_DAYS = 3650;

/** The entered history_days, or null when it is not a whole number in range. */
function parseHistoryDays (value: string): number | null {
  if (!/^\d+$/.test(value.trim())) return null;
  const days = Number(value);
  return days <= MAX_HISTORY_DAYS ? days : null;
}

export default function ConfigPage () {
  const { t } = useTranslation();
  const { data: config, isLoading: configLoading } = useConfig();
  const updateConfig = useUpdateConfig();
  const { data: remotes, isLoading: remotesLoading, isError: remotesError } = useRemotes();
  const [logLevel, setLogLevel] = useState<string | null>(null);
  const [historyDays, setHistoryDays] = useState<string | null>(null);

  if (configLoading || remotesLoading) {
    return <p className="text-muted-foreground p-4">{t('common.loading')}</p>;
  }

  const currentLevel = logLevel ?? config?.log_level ?? 'INFO';
  const savedDays = config?.history_days ?? DEFAULT_HISTORY_DAYS;
  const daysText = historyDays ?? String(savedDays);
  const days = parseHistoryDays(daysText);
  const unchanged = currentLevel === config?.log_level && days === savedDays;

  return (
    <div className="space-y-6">
      <PageHeader
        title={t('config.title')}
        actions={<PageHelp pageKey="config" />}
      />

      <Card>
        <CardHeader>
          <CardTitle>{t('config.globalSettings')}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div>
            <Label htmlFor="log-level">{t('config.logLevel')}</Label>
            <Select value={currentLevel} onValueChange={setLogLevel}>
              <SelectTrigger id="log-level" className="w-48">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {LOG_LEVELS.map((level) => (
                  <SelectItem key={level} value={level}>{level}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="history-days">{t('config.historyDays')}</Label>
            <Input
              id="history-days"
              type="number"
              min="0"
              max={MAX_HISTORY_DAYS}
              step="1"
              className="w-48"
              value={daysText}
              onChange={(e) => setHistoryDays(e.target.value)}
              aria-invalid={days === null || undefined}
              aria-describedby="history-days-help"
            />
            <p id="history-days-help" className="text-xs text-muted-foreground">
              {days === null
                ? <span className="text-destructive">{t('config.historyDaysInvalid')}</span>
                : t('config.historyDaysHelp')}
            </p>
          </div>
          <Button
            onClick={() => days !== null && updateConfig.mutate({ log_level: currentLevel, history_days: days })}
            disabled={updateConfig.isPending || days === null || unchanged}
          >
            {t('common.save')}
          </Button>
        </CardContent>
      </Card>

      {remotesError && <p className="text-destructive" role="alert">{t('remotes.loadFailed')}</p>}
      {!remotesError && (
      <RemoteManager remotes={remotes ?? []} />
      )}
    </div>
  );
}
