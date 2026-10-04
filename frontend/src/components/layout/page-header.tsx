'use client';

import { PanelLeftOpen } from 'lucide-react';
import { useEffect } from 'react';
import type { ReactNode } from 'react';
import { useSidebarControls } from '@/components/layout/sidebar-controls';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { useTranslation } from '@/i18n';

/** The app shell moves focus here when the sidebar is collapsed. */
export const SIDEBAR_OPEN_BUTTON_ID = 'sidebar-open-button';

type PageHeaderProps = {
  title:           ReactNode;
  actions?:        ReactNode;
  beforeTitle?:    ReactNode;
  afterTitle?:     ReactNode;
  className?:      string;
  titleClassName?: string;
  /** Text for the browser tab; defaults to `title` when that is a string. */
  documentTitle?:  string;
};

export const APP_NAME = 'OmniSync';

/** Sets the browser tab title to "<page> · OmniSync" while mounted. */
export function useDocumentTitle (title: string | undefined) {
  useEffect(() => {
    if (!title) return;
    document.title = `${title} · ${APP_NAME}`;
    return () => { document.title = APP_NAME; };
  }, [title]);
}

export function PageHeader ({
  title,
  actions,
  beforeTitle,
  afterTitle,
  className,
  titleClassName,
  documentTitle,
}: PageHeaderProps) {
  const { isSidebarCollapsed, toggleSidebar } = useSidebarControls();
  const { t } = useTranslation();
  useDocumentTitle(documentTitle ?? (typeof title === 'string' ? title : undefined));

  // On a narrow screen the title wraps instead of being cut off (a cut-off
  // Latin name in a right-to-left page loses its start), the badges after
  // it move to the next line, and the actions wrap below the title.
  return (
    <div className={cn('flex flex-wrap items-center justify-between gap-2', className)}>
      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2">
        <div className="flex min-w-0 items-center gap-3">
          {isSidebarCollapsed && (
            <Button
              id={SIDEBAR_OPEN_BUTTON_ID}
              type="button"
              variant="ghost"
              size="icon"
              // Below md the mobile top bar has the menu button.
              className="hidden md:inline-flex"
              onClick={toggleSidebar}
              aria-label={t('nav.openSidebar')}
            >
              <PanelLeftOpen className="h-4 w-4 rtl:-scale-x-100" aria-hidden="true" />
            </Button>
          )}
          {beforeTitle}
          <h1 className={cn('min-w-0 text-xl font-bold wrap-break-word sm:text-2xl', titleClassName)}>{title}</h1>
        </div>
        {afterTitle}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}
