import { Routes, Route, Navigate } from "react-router-dom";
import { useAuth } from "./contexts/AuthContext";

// Admin pages
import { Login }        from "./pages/Login";
import { Dashboard }    from "./pages/Dashboard";
import { Tenants }      from "./pages/Tenants";
import { TenantDetail } from "./pages/TenantDetail";
import { Settings }     from "./pages/Settings";
import { ChatTest }     from "./pages/ChatTest";

// Portal pages (farmácia)
import { PortalLogin }      from "./pages/PortalLogin";
import { PortalDashboard }  from "./pages/PortalDashboard";
import { PortalSkills }     from "./pages/PortalSkills";
import { PortalLogs }       from "./pages/PortalLogs";
import { PortalIntegracao } from "./pages/PortalIntegracao";
import { PortalBilling }    from "./pages/PortalBilling";
import { PortalEstoque }    from "./pages/PortalEstoque";
import { PortalClientes }   from "./pages/PortalClientes";
import { PortalCanais }     from "./pages/PortalCanais";
import { PortalTraces }    from "./pages/PortalTraces";
import { PortalLLMConfig } from "./pages/PortalLLMConfig";
import { PortalPersona }   from "./pages/PortalPersona";
import { PortalVendas }    from "./pages/PortalVendas";
import { PortalPedidos }   from "./pages/PortalPedidos";
import { PortalMensagensPedido } from "./pages/PortalMensagensPedido";
import { PortalClienteDetalhe } from "./pages/PortalClienteDetalhe";
import { AdminPersona }    from "./pages/AdminPersona";
import { Signup }           from "./pages/Signup";

function AdminRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, role } = useAuth();
  if (!isAuthenticated) return <Navigate to="/login" replace />;
  if (role !== "admin")  return <Navigate to="/portal/dashboard" replace />;
  return <>{children}</>;
}

function TenantRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, role } = useAuth();
  if (!isAuthenticated) return <Navigate to="/portal/login" replace />;
  if (role !== "tenant") return <Navigate to="/dashboard" replace />;
  return <>{children}</>;
}

export function App() {
  return (
    <Routes>
      {/* ── Admin ─────────────────────────────────────────────────────── */}
      <Route path="/login"       element={<Login />} />
      <Route path="/dashboard"   element={<AdminRoute><Dashboard /></AdminRoute>} />
      <Route path="/tenants"     element={<AdminRoute><Tenants /></AdminRoute>} />
      <Route path="/tenants/:id" element={<AdminRoute><TenantDetail /></AdminRoute>} />
      <Route path="/settings"    element={<AdminRoute><Settings /></AdminRoute>} />
      <Route path="/chat-test"   element={<AdminRoute><ChatTest /></AdminRoute>} />

      {/* ── Onboarding ───────────────────────────────────────────────── */}
      <Route path="/signup" element={<Signup />} />

      {/* ── Portal da Farmácia ────────────────────────────────────────── */}
      <Route path="/portal/login"      element={<PortalLogin />} />
      <Route path="/portal/dashboard"  element={<TenantRoute><PortalDashboard /></TenantRoute>} />
      <Route path="/portal/skills"     element={<TenantRoute><PortalSkills /></TenantRoute>} />
      <Route path="/portal/canais"     element={<TenantRoute><PortalCanais /></TenantRoute>} />
      <Route path="/portal/estoque"    element={<TenantRoute><PortalEstoque /></TenantRoute>} />
      <Route path="/portal/clientes"   element={<TenantRoute><PortalClientes /></TenantRoute>} />
      <Route path="/portal/clientes/:id" element={<TenantRoute><PortalClienteDetalhe /></TenantRoute>} />
      <Route path="/portal/logs"       element={<TenantRoute><PortalLogs /></TenantRoute>} />
      <Route path="/portal/integracao" element={<TenantRoute><PortalIntegracao /></TenantRoute>} />
      <Route path="/portal/billing"    element={<TenantRoute><PortalBilling /></TenantRoute>} />
      <Route path="/portal/traces"     element={<TenantRoute><PortalTraces /></TenantRoute>} />
      <Route path="/portal/ia-config"  element={<TenantRoute><PortalLLMConfig /></TenantRoute>} />
      <Route path="/portal/persona"    element={<TenantRoute><PortalPersona /></TenantRoute>} />
      <Route path="/portal/vendas"     element={<TenantRoute><PortalVendas /></TenantRoute>} />
      <Route path="/portal/pedidos"    element={<TenantRoute><PortalPedidos /></TenantRoute>} />
      <Route path="/portal/pedidos/mensagens" element={<TenantRoute><PortalMensagensPedido /></TenantRoute>} />

      {/* ── Admin: persona/prompts of any tenant ─────────────────────── */}
      <Route path="/tenants/:id/persona" element={<AdminRoute><AdminPersona /></AdminRoute>} />

      {/* ── Fallback ──────────────────────────────────────────────────── */}
      <Route path="*" element={<Navigate to="/login" replace />} />
    </Routes>
  );
}
