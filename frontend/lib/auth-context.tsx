"use client";

import * as React from "react";

import { api, setAccessToken, setUnauthorizedHandler } from "@/lib/api";
import { ACTIVE_WORKSPACE_KEY } from "@/lib/config";
import type { AuthResponse, LoginPayload, RegisterPayload, User, Workspace } from "@/lib/types";

type AuthStatus = "loading" | "authenticated" | "anonymous";

type AuthContextValue = {
  status: AuthStatus;
  user: User | null;
  workspaces: Workspace[];
  activeWorkspace: Workspace | null;
  setActiveWorkspace: (workspaceId: string) => void;
  login: (payload: LoginPayload) => Promise<void>;
  register: (payload: RegisterPayload) => Promise<void>;
  logout: () => Promise<void>;
  logoutAll: () => Promise<void>;
  reloadUser: () => Promise<void>;
  reloadWorkspaces: () => Promise<void>;
  /** True when the active workspace's role grants `code` (or the user is superuser). */
  hasPermission: (code: string) => boolean;
};

const AuthContext = React.createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = React.useState<AuthStatus>("loading");
  const [user, setUser] = React.useState<User | null>(null);
  const [workspaces, setWorkspaces] = React.useState<Workspace[]>([]);
  const [activeWorkspaceId, setActiveWorkspaceId] = React.useState<string | null>(null);

  const loadWorkspaces = React.useCallback(async () => {
    const list = await api.workspaces.list();
    setWorkspaces(list);
    return list;
  }, []);

  const applyAuth = React.useCallback(
    (data: AuthResponse) => {
      setAccessToken(data.access_token);
      setUser(data.user);
      setStatus("authenticated");
    },
    [],
  );

  const clearAuth = React.useCallback(() => {
    setAccessToken(null);
    setUser(null);
    setWorkspaces([]);
    setStatus("anonymous");
  }, []);

  // Bootstrap: try to exchange the HttpOnly refresh cookie for a session.
  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await api.auth.refresh();
        if (cancelled) return;
        applyAuth(data);
        try {
          await loadWorkspaces();
        } catch {
          /* workspaces are non-fatal on bootstrap */
        }
      } catch {
        // No valid refresh cookie — the visitor is simply not signed in yet.
        if (cancelled) return;
        clearAuth();
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [applyAuth, clearAuth, loadWorkspaces]);

  // Let the API client drop auth state when a refresh ultimately fails.
  React.useEffect(() => {
    setUnauthorizedHandler(() => clearAuth());
    return () => setUnauthorizedHandler(null);
  }, [clearAuth]);

  // Restore the selected workspace from localStorage once workspaces load.
  React.useEffect(() => {
    if (typeof window === "undefined") return;
    const stored = window.localStorage.getItem(ACTIVE_WORKSPACE_KEY);
    if (stored) setActiveWorkspaceId(stored);
  }, []);

  const login = React.useCallback(
    async (payload: LoginPayload) => {
      const data = await api.auth.login(payload);
      applyAuth(data);
      await loadWorkspaces();
    },
    [applyAuth, loadWorkspaces],
  );

  const register = React.useCallback(
    async (payload: RegisterPayload) => {
      const data = await api.auth.register(payload);
      applyAuth(data);
      await loadWorkspaces();
    },
    [applyAuth, loadWorkspaces],
  );

  const logout = React.useCallback(async () => {
    try {
      await api.auth.logout();
    } finally {
      clearAuth();
    }
  }, [clearAuth]);

  const logoutAll = React.useCallback(async () => {
    try {
      await api.auth.logoutAll();
    } finally {
      clearAuth();
    }
  }, [clearAuth]);

  const reloadUser = React.useCallback(async () => {
    const fresh = await api.auth.me();
    setUser(fresh);
  }, []);

  const reloadWorkspaces = React.useCallback(async () => {
    await loadWorkspaces();
  }, [loadWorkspaces]);

  const setActiveWorkspace = React.useCallback((workspaceId: string) => {
    setActiveWorkspaceId(workspaceId);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(ACTIVE_WORKSPACE_KEY, workspaceId);
    }
  }, []);

  const activeWorkspace = React.useMemo<Workspace | null>(() => {
    if (!workspaces.length) return null;
    const match = workspaces.find((workspace) => workspace.id === activeWorkspaceId);
    return match ?? workspaces[0];
  }, [workspaces, activeWorkspaceId]);

  const hasPermission = React.useCallback(
    (code: string) => {
      if (user?.is_superuser) return true;
      return activeWorkspace?.permissions.includes(code) ?? false;
    },
    [activeWorkspace, user],
  );

  const value: AuthContextValue = {
    status,
    user,
    workspaces,
    activeWorkspace,
    setActiveWorkspace,
    login,
    register,
    logout,
    logoutAll,
    reloadUser,
    reloadWorkspaces,
    hasPermission,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = React.useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside <AuthProvider>");
  return context;
}
