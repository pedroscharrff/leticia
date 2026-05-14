import { createContext, useContext, useState, useCallback, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { login as apiLogin, portalLogin as apiPortalLogin } from "../api/auth";

type Role = "admin" | "tenant" | null;

interface AuthState {
  isAuthenticated: boolean;
  role: Role;
  tenantId: string | null;
  userEmail: string | null;
}

interface AuthContextValue extends AuthState {
  login: (email: string, password: string) => Promise<void>;
  portalLogin: (email: string, password: string) => Promise<void>;
  loginWithToken: (token: string) => void;
  logout: () => void;
}

function decodeToken(token: string): { role?: string; tenant_id?: string; sub?: string } {
  try {
    return JSON.parse(atob(token.split(".")[1]));
  } catch {
    return {};
  }
}

function readStoredAuth(): AuthState {
  const token = localStorage.getItem("access_token");
  if (!token) return { isAuthenticated: false, role: null, tenantId: null, userEmail: null };
  const payload = decodeToken(token);
  return {
    isAuthenticated: true,
    role: (payload.role as Role) ?? null,
    tenantId: payload.tenant_id ?? null,
    userEmail: payload.sub ?? null,
  };
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>(readStoredAuth);
  const navigate = useNavigate();

  const login = useCallback(async (email: string, password: string) => {
    const data = await apiLogin(email, password);
    localStorage.setItem("access_token", data.access_token);
    setState(readStoredAuth());
    navigate("/dashboard");
  }, [navigate]);

  const portalLogin = useCallback(async (email: string, password: string) => {
    const data = await apiPortalLogin(email, password);
    localStorage.setItem("access_token", data.access_token);
    setState(readStoredAuth());
    navigate("/portal/dashboard");
  }, [navigate]);

  const loginWithToken = useCallback((token: string) => {
    localStorage.setItem("access_token", token);
    setState(readStoredAuth());
    navigate("/portal/dashboard");
  }, [navigate]);

  const logout = useCallback(() => {
    localStorage.removeItem("access_token");
    setState({ isAuthenticated: false, role: null, tenantId: null, userEmail: null });
    const isPortal = window.location.pathname.startsWith("/portal");
    navigate(isPortal ? "/portal/login" : "/login");
  }, [navigate]);

  return (
    <AuthContext.Provider value={{ ...state, login, portalLogin, loginWithToken, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be inside AuthProvider");
  return ctx;
}
