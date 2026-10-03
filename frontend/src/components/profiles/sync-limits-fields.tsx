'use client';

import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { useTranslation } from '@/i18n';
import type { Profile, SyncWindow } from '@/types';

/** The bandwidth limit and sync window fields of the profile form. */
export interface SyncLimitsValue {
  bwlimit:  string;
  windowOn: boolean;
  /** 0 = Monday ... 6 = Sunday */
  days:     number[];
  start:    string;
  end:      string;
}

export function initialSyncLimits (profile?: Profile): SyncLimitsValue {
  const w = profile?.sync_window;
  return {
    bwlimit:  profile?.bwlimit ?? '',
    windowOn: !!w,
    days:     w?.days ?? [0, 1, 2, 3, 4, 5, 6],
    start:    w?.start ?? '22:00',
    end:      w?.end ?? '06:00',
  };
}

// rclone --bwlimit as rclone 1.75.1 accepts it (the server checks the same):
// a rate (10M, 512k, off; upload:download as 10M:1M) or a timetable of
// "[Day-]HH:MM,rate" entries, e.g. "08:00,512k 19:00,10M 23:00,off".
const RATE = '(?:off|(?:\\d+(?:\\.\\d*)?|\\.\\d+)(?:b|[kmgtpe](?:ib?)?)?)';
const RATES = `${RATE}(?::${RATE})?`;
const DAY = '(?:mon|tue|wed|thu|fri|sat|sun|monday|tuesday|wednesday|thursday|friday|saturday|sunday)';
const ENTRY = `(?:${DAY}-)?(?:[01]\\d|2[0-3]):[0-5]\\d,${RATES}`;
const BWLIMIT = new RegExp(`^(?:${RATES}|${ENTRY}(?: +${ENTRY})*)$`, 'i');
const HHMM = /^(?:[01]\d|2[0-3]):[0-5]\d$/;

export function isValidBwlimit (value: string): boolean {
  const v = value.trim().split(/\s+/).join(' ');
  return v === '' || (v.length <= 500 && BWLIMIT.test(v));
}

/** Error keys (i18n) for the fields; `rcloneArgs` is the flags textarea, one per line. */
export function syncLimitsErrors (value: SyncLimitsValue, rcloneArgs: string): Record<string, string> {
  const errors: Record<string, string> = {};
  if (!isValidBwlimit(value.bwlimit)) errors.bwlimit = 'syncLimits.bwlimitInvalid';
  else if (value.bwlimit.trim() && rcloneArgs.split('\n').some((a) => /^--bwlimit(=|$)/.test(a.trim().split(/\s/)[0]))) {
    errors.bwlimit = 'syncLimits.bwlimitTwice';
  }
  if (value.windowOn) {
    if (!HHMM.test(value.start) || !HHMM.test(value.end) || value.start === value.end) {
      errors.sync_window = 'syncLimits.windowInvalid';
    } else if (value.days.length === 0) {
      errors.sync_window = 'syncLimits.windowNoDays';
    }
  }
  return errors;
}

export function syncLimitsPayload (value: SyncLimitsValue): { bwlimit: string | null, sync_window: SyncWindow | null } {
  const bwlimit = value.bwlimit.trim().split(/\s+/).join(' ');
  return {
    bwlimit:     bwlimit || null,
    sync_window: value.windowOn ? { days: [...value.days].sort((a, b) => a - b), start: value.start, end: value.end } : null,
  };
}

// 2026-10-05 is a Monday: weekday names in the user's language.
const MONDAY = Date.UTC(2026, 9, 5, 12);

interface SyncLimitsFieldsProps {
  value:    SyncLimitsValue;
  onChange: (value: SyncLimitsValue) => void;
  errors:   Record<string, string>;
}

export function SyncLimitsFields ({ value, onChange, errors }: SyncLimitsFieldsProps) {
  const { t, locale } = useTranslation();
  const weekday = new Intl.DateTimeFormat(locale, { weekday: 'short', timeZone: 'UTC' });
  const set = (patch: Partial<SyncLimitsValue>) => onChange({ ...value, ...patch });
  const toggleDay = (day: number) =>
    set({ days: value.days.includes(day) ? value.days.filter((d) => d !== day) : [...value.days, day] });

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="profile-bwlimit">{t('syncLimits.bwlimit')}</Label>
        <Input
          id="profile-bwlimit"
          dir="ltr"
          value={value.bwlimit}
          onChange={(e) => set({ bwlimit: e.target.value })}
          placeholder="08:00,512k 19:00,10M 23:00,off"
          {...(errors.bwlimit ? { 'aria-invalid': true, 'aria-describedby': 'profile-bwlimit-error' } : {})}
        />
        {errors.bwlimit
          ? <p id="profile-bwlimit-error" className="text-xs text-destructive">{t(errors.bwlimit)}</p>
          : <p className="text-xs text-muted-foreground">{t('syncLimits.bwlimitHelp')}</p>}
      </div>

      <div className="space-y-2 rounded-xl border p-3">
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="text-sm font-medium" id="profile-window-label">{t('syncLimits.window')}</div>
            <div className="text-xs text-muted-foreground">{t('syncLimits.windowHelp')}</div>
          </div>
          <Switch
            checked={value.windowOn}
            onCheckedChange={(on) => set({ windowOn: on })}
            aria-labelledby="profile-window-label"
          />
        </div>
        {value.windowOn && (
          <div className="space-y-3 pt-1">
            <div className="flex flex-wrap gap-1" role="group" aria-label={t('syncLimits.days')}>
              {[0, 1, 2, 3, 4, 5, 6].map((day) => (
                <button
                  key={day}
                  type="button"
                  aria-pressed={value.days.includes(day)}
                  onClick={() => toggleDay(day)}
                  className={`rounded-md border px-2 py-1 text-xs ${value.days.includes(day) ? 'border-primary bg-primary text-primary-foreground' : 'hover:bg-muted'}`}
                >
                  {weekday.format(new Date(MONDAY + day * 86_400_000))}
                </button>
              ))}
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label htmlFor="profile-window-start">{t('syncLimits.from')}</Label>
                <Input id="profile-window-start" type="time" value={value.start} onChange={(e) => set({ start: e.target.value })} />
              </div>
              <div className="space-y-1">
                <Label htmlFor="profile-window-end">{t('syncLimits.until')}</Label>
                <Input id="profile-window-end" type="time" value={value.end} onChange={(e) => set({ end: e.target.value })} />
              </div>
            </div>
            {errors.sync_window
              ? <p id="profile-window-error" className="text-xs text-destructive">{t(errors.sync_window)}</p>
              : <p className="text-xs text-muted-foreground">{t('syncLimits.windowNote')}</p>}
          </div>
        )}
      </div>
    </div>
  );
}
