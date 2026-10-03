'use client';

import { createContext, useContext } from 'react';
import type { ReactNode } from 'react';

type SidebarControlsValue = {
  isSidebarCollapsed: boolean;
  toggleSidebar:      () => void;
};

const SidebarControlsContext = createContext<SidebarControlsValue | null>(null);

type SidebarControlsProviderProps = {
  value:    SidebarControlsValue;
  children: ReactNode;
};

export function SidebarControlsProvider ({ value, children }: SidebarControlsProviderProps) {
  return (
    <SidebarControlsContext.Provider value={value}>
      {children}
    </SidebarControlsContext.Provider>
  );
}

export function useSidebarControls () {
  const context = useContext(SidebarControlsContext);
  if (!context) {
    throw new Error('useSidebarControls must be used within SidebarControlsProvider');
  }
  return context;
}
