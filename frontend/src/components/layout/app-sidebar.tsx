'use client';

import dynamic from 'next/dynamic';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useEffect, useState } from 'react';
import type { Ref } from 'react';
import { Dialog as DialogPrimitive } from 'radix-ui';
import {
  LayoutDashboard,
  Briefcase,
  Settings,
  FileText,
  AlertTriangle,
  Cloud,
  Bell,
  FolderSync,
  LogOut,
  Menu,
  PanelLeftClose,
  X,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { useTranslation } from '@/i18n';
import { Button } from '@/components/ui/button';
import { Separator } from '@/components/ui/separator';
import { LanguageSwitcher } from '@/components/layout/language-switcher';
import { VersionInfo } from '@/components/layout/version-info';
import { logout } from '@/lib/auth/client';

const ThemeSwitcher = dynamic(
  () => import('@/components/layout/theme-switcher').then((mod) => mod.ThemeSwitcher),
  { ssr: false }
);

const navItems = [
  { href: '/', icon: LayoutDashboard, labelKey: 'nav.dashboard' },
  { href: '/profiles', icon: FolderSync, labelKey: 'nav.profiles' },
  { href: '/jobs', icon: Briefcase, labelKey: 'nav.jobs' },
  { href: '/remotes', icon: Cloud, labelKey: 'nav.remotes' },
  { href: '/config', icon: Settings, labelKey: 'nav.config' },
  { href: '/logs', icon: FileText, labelKey: 'nav.logs' },
  { href: '/conflicts', icon: AlertTriangle, labelKey: 'nav.conflicts' },
  { href: '/notifications', icon: Bell, labelKey: 'nav.notifications' },
];

type SidebarBodyProps = {
  /** Label and icon of the button in the top row. */
  closeLabel:      string;
  closeIcon:       typeof X;
  onClose:         () => void;
  closeButtonRef?: Ref<HTMLButtonElement>;
  onNavigate?:     () => void;
  /** The optional login is on: offer to log out. */
  showLogout?:     boolean;
};

/** Ends the UI session (shown only when the login is on). */
function LogoutButton () {
  const { t } = useTranslation();
  const [pending, setPending] = useState(false);
  return (
    <Button
      type="button"
      variant="ghost"
      className="w-full justify-start gap-2"
      disabled={pending}
      onClick={() => {
        setPending(true);
        logout().catch(() => {});
      }}
    >
      <LogOut className="h-4 w-4 rtl:-scale-x-100" aria-hidden="true" />
      {pending ? t('auth.loggingOut') : t('auth.logout')}
    </Button>
  );
}

/** Brand, navigation and preferences: shared by the desktop sidebar and the mobile drawer. */
function SidebarBody ({ closeLabel, closeIcon: CloseIcon, onClose, closeButtonRef, onNavigate, showLogout }: SidebarBodyProps) {
  const pathname = usePathname();
  const { t } = useTranslation();

  return (
    <>
      <div className="flex h-14 items-center justify-between px-4">
        {/* Not a heading: each page has its own single <h1>. */}
        <span className="text-lg font-semibold">OmniSync</span>
        <Button
          ref={closeButtonRef}
          type="button"
          variant="ghost"
          size="icon"
          onClick={onClose}
          aria-label={closeLabel}
        >
          <CloseIcon className="h-4 w-4 rtl:-scale-x-100" aria-hidden="true" />
        </Button>
      </div>
      <Separator />
      <nav className="flex-1 space-y-1 overflow-y-auto p-2">
        {navItems.map((item) => {
          const isActive =
            pathname === item.href ||
            (item.href !== '/' && pathname.startsWith(item.href));
          return (
            <Button
              key={item.href}
              variant={isActive ? 'secondary' : 'ghost'}
              className={cn(
                'w-full justify-start gap-2',
                isActive && 'bg-sidebar-accent text-sidebar-accent-foreground'
              )}
              asChild
            >
              <Link href={item.href} aria-current={isActive ? 'page' : undefined} onClick={onNavigate}>
                <item.icon className="h-4 w-4" aria-hidden="true" />
                {t(item.labelKey)}
              </Link>
            </Button>
          );
        })}
      </nav>
      <Separator />
      <div className="space-y-2 p-2">
        <ThemeSwitcher />
        <LanguageSwitcher />
        {showLogout && <LogoutButton />}
        <VersionInfo />
      </div>
    </>
  );
}

type AppSidebarProps = {
  isCollapsed:     boolean;
  onToggle:        () => void;
  closeButtonRef?: Ref<HTMLButtonElement>;
  showLogout?:     boolean;
};

/** The desktop sidebar (md and up). Below md the MobileNav drawer is used instead. */
export function AppSidebar ({ isCollapsed, onToggle, closeButtonRef, showLogout }: AppSidebarProps) {
  const { t } = useTranslation();

  return (
    <aside
      // Collapsed to zero width: keep it out of the tab order and the
      // accessibility tree too.
      inert={isCollapsed}
      aria-hidden={isCollapsed || undefined}
      aria-label={t('nav.sidebar')}
      className={cn(
        'hidden h-screen shrink-0 flex-col border-e border-sidebar-border bg-sidebar text-sidebar-foreground transition-all duration-200 md:flex',
        isCollapsed ? 'w-0 overflow-hidden border-e-0' : 'w-64'
      )}
    >
      <SidebarBody
        closeLabel={t('nav.closeSidebar')}
        closeIcon={PanelLeftClose}
        onClose={onToggle}
        closeButtonRef={closeButtonRef}
        showLogout={showLogout}
      />
    </aside>
  );
}

/**
 * Below md there is no room for a fixed sidebar: a top bar with a menu
 * button opens the navigation as an off-canvas drawer. It is a modal
 * dialog, so focus is trapped inside, Esc closes it and focus returns to
 * the menu button.
 */
export function MobileNav ({ showLogout = false }: { showLogout?: boolean }) {
  const { t } = useTranslation();
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [openedAt, setOpenedAt] = useState(pathname);

  // Close when the route changes, also on back/forward navigation.
  if (open && openedAt !== pathname) {
    setOpen(false);
  }

  useEffect(() => {
    if (!open || typeof window.matchMedia !== 'function') return;
    // A drawer left open while the window grows past md would leave an
    // invisible modal trapping focus.
    const mq = window.matchMedia('(min-width: 768px)');
    const close = () => { if (mq.matches) setOpen(false); };
    mq.addEventListener('change', close);
    return () => mq.removeEventListener('change', close);
  }, [open]);

  return (
    <div className="flex h-14 shrink-0 items-center gap-2 border-b bg-sidebar px-2 text-sidebar-foreground md:hidden">
      <DialogPrimitive.Root
        open={open}
        onOpenChange={(next) => {
          setOpen(next);
          if (next) setOpenedAt(pathname);
        }}
      >
        <DialogPrimitive.Trigger asChild>
          <Button type="button" variant="ghost" size="icon" aria-label={t('nav.openMenu')}>
            <Menu className="h-5 w-5" aria-hidden="true" />
          </Button>
        </DialogPrimitive.Trigger>
        <DialogPrimitive.Portal>
          <DialogPrimitive.Overlay className="data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 fixed inset-0 z-50 bg-black/50 md:hidden" />
          <DialogPrimitive.Content
            aria-describedby={undefined}
            data-testid="mobile-nav"
            className="data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 fixed inset-y-0 start-0 z-50 flex w-72 max-w-[85vw] flex-col border-e border-sidebar-border bg-sidebar text-sidebar-foreground shadow-lg outline-none duration-200 md:hidden"
          >
            <DialogPrimitive.Title className="sr-only">{t('nav.sidebar')}</DialogPrimitive.Title>
            <SidebarBody
              closeLabel={t('nav.closeMenu')}
              closeIcon={X}
              onClose={() => setOpen(false)}
              onNavigate={() => setOpen(false)}
              showLogout={showLogout}
            />
          </DialogPrimitive.Content>
        </DialogPrimitive.Portal>
      </DialogPrimitive.Root>
      <span className="text-lg font-semibold">OmniSync</span>
    </div>
  );
}
