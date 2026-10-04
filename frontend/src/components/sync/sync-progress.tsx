'use client';

import { useTranslation, type Locale } from '@/i18n';
import { formatBytes } from '@/lib/format';
import { cn } from '@/lib/utils';
import type { SyncProgress } from '@/types';
import { PathText } from '@/components/shared/path-text';

/** "1 h 5 min", "3 min 20 s", "12 s" in the locale's units. */
export function formatDuration (seconds: number, locale: Locale): string {
  const unit = (value: number, u: 'hour' | 'minute' | 'second') =>
    new Intl.NumberFormat(locale, { style: 'unit', unit: u, unitDisplay: 'narrow' }).format(value);
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h > 0) return m > 0 ? `${unit(h, 'hour')} ${unit(m, 'minute')}` : unit(h, 'hour');
  if (m > 0) return s % 60 > 0 && m < 10 ? `${unit(m, 'minute')} ${unit(s % 60, 'second')}` : unit(m, 'minute');
  return unit(s, 'second');
}

/** Share of the bytes done, 0..100, or null while the total is unknown. */
export function progressPercent (progress: SyncProgress): number | null {
  if (progress.total_bytes > 0) return Math.min(100, Math.round((progress.bytes / progress.total_bytes) * 100));
  if (progress.files_total > 0) return Math.min(100, Math.round((progress.files_done / progress.files_total) * 100));
  return null;
}

interface SyncProgressViewProps {
  progress:   SyncProgress | null | undefined;
  /** One line (the dashboard row); otherwise also the files in flight. */
  compact?:   boolean;
  className?: string;
}

/** A running sync's progress bar, speed and time left (rclone's stats, refreshed while the status polls). */
export function SyncProgressView ({ progress, compact, className }: SyncProgressViewProps) {
  const { t, locale } = useTranslation();
  if (!progress) return null;
  const percent = progressPercent(progress);
  const summary = [
    progress.total_bytes > 0
      ? t('progress.bytes', { done: formatBytes(progress.bytes, locale), total: formatBytes(progress.total_bytes, locale) })
      : null,
    progress.files_total > 0 ? t('progress.files', { done: progress.files_done, count: progress.files_total }) : null,
    progress.speed > 0 ? t('progress.speed', { speed: formatBytes(progress.speed, locale) }) : null,
    progress.eta_seconds != null && progress.eta_seconds > 0
      ? t('progress.eta', { time: formatDuration(progress.eta_seconds, locale) })
      : null,
  ].filter(Boolean).join(' · ');

  return (
    <div className={cn('space-y-1', className)} data-testid="sync-progress">
      <div
        role="progressbar"
        aria-label={t('progress.label')}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent ?? undefined}
        aria-valuetext={summary || t('progress.starting')}
        className="h-2 w-full overflow-hidden rounded-full bg-muted"
      >
        <div
          className={cn('h-full rounded-full bg-primary transition-[width]', percent == null && 'w-1/3 motion-safe:animate-pulse')}
          style={percent != null ? { width: `${percent}%` } : undefined}
        />
      </div>
      <p className="text-xs text-muted-foreground tabular-nums">
        {percent != null && <span className="font-medium text-foreground">{percent}% </span>}
        {summary || t('progress.starting')}
      </p>
      {!compact && progress.current_files.length > 0 && (
        <ul className="space-y-0.5 text-xs text-muted-foreground" aria-label={t('progress.current')}>
          {progress.current_files.map((f) => (
            <li key={f.name} className="flex min-w-0 justify-between gap-2">
              <span className="truncate" title={f.name}><PathText>{f.name}</PathText></span>
              <span className="shrink-0 tabular-nums">
                {f.percentage != null ? `${f.percentage}%` : ''}
                {f.size != null ? ` / ${formatBytes(f.size, locale)}` : ''}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
