'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { usePathname } from 'next/navigation';
import { LOGIN_PATH } from '@/lib/auth/config';
import type { ReactNode } from 'react';
import { AppSidebar, MobileNav } from '@/components/layout/app-sidebar';
import { SIDEBAR_OPEN_BUTTON_ID } from '@/components/layout/page-header';
import { useTranslation } from '@/i18n';
import { SidebarControlsProvider } from '@/components/layout/sidebar-controls';
import { SIDEBAR_COOKIE, writePreferenceCookie } from '@/i18n/config';

export const MAIN_CONTENT_ID = 'main-content';

type AppShellProps = {
  children:          ReactNode;
  /** From the sidebar cookie, read by the server layout. */
  initialCollapsed?: boolean;
  /** The optional login is on (OMNISYNC_UI_PASSWORD_HASH): show a logout button. */
  authEnabled?:      boolean;
};

export function AppShell ({ children, initialCollapsed = false, authEnabled = false }: AppShellProps) {
  const { t } = useTranslation();
  const pathname = usePathname();
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(initialCollapsed);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  // Set by a toggle so focus follows the button that replaces the one the
  // user just pressed (that one disappears or becomes inert).
  const focusAfterToggle = useRef(false);

  const setCollapsed = useCallback((collapsed: boolean) => {
    setIsSidebarCollapsed(collapsed);
    writePreferenceCookie(SIDEBAR_COOKIE, collapsed ? '1' : '0');
  }, []);

  const toggleSidebar = useCallback(() => {
    focusAfterToggle.current = true;
    setCollapsed(!isSidebarCollapsed);
  }, [isSidebarCollapsed, setCollapsed]);

  useEffect(() => {
    if (!focusAfterToggle.current) return;
    focusAfterToggle.current = false;
    if (isSidebarCollapsed) {
      const openButton = document.getElementById(SIDEBAR_OPEN_BUTTON_ID);
      (openButton ?? document.getElementById(MAIN_CONTENT_ID))?.focus();
    } else {
      closeButtonRef.current?.focus();
    }
  }, [isSidebarCollapsed]);

  // The login page stands alone: no navigation to pages it cannot open yet.
  if (pathname === LOGIN_PATH) {
    return (
      <main id={MAIN_CONTENT_ID} tabIndex={-1} className="flex min-h-dvh items-center justify-center p-4 outline-none">
        {children}
      </main>
    );
  }

  return (
    <div className="flex h-dvh">
      <a
        href={`#${MAIN_CONTENT_ID}`}
        className="sr-only focus:not-sr-only focus:fixed focus:start-2 focus:top-2 focus:z-[100] focus:rounded-md focus:bg-background focus:px-4 focus:py-2 focus:text-sm focus:font-medium focus:shadow-lg focus:ring-2 focus:ring-ring"
      >
        {t('nav.skipToContent')}
      </a>
      <AppSidebar
        isCollapsed={isSidebarCollapsed}
        onToggle={toggleSidebar}
        closeButtonRef={closeButtonRef}
        showLogout={authEnabled}
      />
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <MobileNav showLogout={authEnabled} />
        <SidebarControlsProvider
          value={{
            isSidebarCollapsed,
            toggleSidebar,
          }}
        >
          <main
            id={MAIN_CONTENT_ID}
            tabIndex={-1}
            className="flex-1 overflow-auto p-4 outline-none md:p-6"
          >
            {children}
          </main>
        </SidebarControlsProvider>
      </div>
    </div>
  );
}
