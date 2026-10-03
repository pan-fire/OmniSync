'use client';

import { useTranslation } from '@/i18n';
import { useHealth } from '@/hooks/use-health';

/**
 * The release version at the foot of the sidebar: this web UI's own (from
 * package.json, inlined at build time by next.config.ts) and, when it
 * differs, the backend's (from GET /health), so a half-upgraded install is
 * visible.
 */
export function VersionInfo () {
  const { t } = useTranslation();
  const { data: health } = useHealth();
  const uiVersion = process.env.NEXT_PUBLIC_OMNISYNC_VERSION ?? '';
  const backendVersion = health?.version ?? '';
  const mismatch = backendVersion !== '' && uiVersion !== '' && backendVersion !== uiVersion;

  if (!uiVersion && !backendVersion) return null;
  return (
    // "OmniSync 0.9.0" reads left to right in every locale; in RTL it is
    // still aligned to the sidebar's start (the right): end of this LTR line.
    <p className="px-2 text-xs text-muted-foreground rtl:text-end" dir="ltr" data-testid="version-info">
      <span>{t('nav.version', { version: uiVersion || backendVersion })}</span>
      {mismatch && (
        <span title={t('nav.versionMismatch')}>
          {' · '}
          {t('nav.backendVersion', { version: backendVersion })}
        </span>
      )}
    </p>
  );
}
