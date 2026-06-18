// UI-only state: sidebar collapse, etc. Not persisted (ephemeral per session).

import { create } from "zustand";

interface UiState {
  sidebarCollapsed: boolean;
  collapseSidebar: () => void;
  expandSidebar: () => void;
  toggleSidebar: () => void;
}

export const useUi = create<UiState>((set) => ({
  sidebarCollapsed: false,
  collapseSidebar: () => set({ sidebarCollapsed: true }),
  expandSidebar: () => set({ sidebarCollapsed: false }),
  toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
}));
