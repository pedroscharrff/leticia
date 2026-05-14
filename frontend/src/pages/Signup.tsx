import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../contexts/AuthContext";
import "./Login.css"; // reuse login styles

interface SignupResp {
  tenant_id: string;
  access_token: string;
  api_key: string;
  message: string;
}

export function Signup() {
  const { loginWithToken } = useAuth();
  const [form, setForm] = useState({
    pharmacy_name: "",
    owner_name: "",
    owner_email: "",
    owner_password: "",
    callback_url: "https://",
    plan: "basic",
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const res = await api.post<SignupResp>("/signup", form);
      loginWithToken(res.data.access_token);
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(msg ?? "Erro ao criar conta. Verifique os dados.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-card__logo">
          <svg width="32" height="32" viewBox="0 0 28 28" fill="none">
            <rect width="28" height="28" rx="7" fill="var(--color-primary)"/>
            <rect x="12" y="6" width="4" height="16" rx="2" fill="white"/>
            <rect x="6" y="12" width="16" height="4" rx="2" fill="white"/>
          </svg>
        </div>
        <h1 className="login-card__title">Criar sua farmácia</h1>
        <p className="login-card__sub">7 dias grátis — sem cartão de crédito</p>

        <form onSubmit={handleSubmit} className="login-form">
          {error && <div className="login-error">{error}</div>}

          <label className="login-label">
            <span>Nome da farmácia</span>
            <input className="login-input" required
              value={form.pharmacy_name}
              onChange={(e) => setForm((f) => ({ ...f, pharmacy_name: e.target.value }))} />
          </label>
          <label className="login-label">
            <span>Seu nome</span>
            <input className="login-input" required
              value={form.owner_name}
              onChange={(e) => setForm((f) => ({ ...f, owner_name: e.target.value }))} />
          </label>
          <label className="login-label">
            <span>E-mail</span>
            <input className="login-input" type="email" required
              value={form.owner_email}
              onChange={(e) => setForm((f) => ({ ...f, owner_email: e.target.value }))} />
          </label>
          <label className="login-label">
            <span>Senha (mín. 8 caracteres)</span>
            <input className="login-input" type="password" required minLength={8}
              value={form.owner_password}
              onChange={(e) => setForm((f) => ({ ...f, owner_password: e.target.value }))} />
          </label>
          <label className="login-label">
            <span>Plano</span>
            <select className="login-input"
              value={form.plan}
              onChange={(e) => setForm((f) => ({ ...f, plan: e.target.value }))}>
              <option value="basic">Básico — R$ 97/mês (500 msg)</option>
              <option value="pro">Pro — R$ 297/mês (2.000 msg)</option>
              <option value="enterprise">Enterprise — R$ 697/mês (ilimitado)</option>
            </select>
          </label>
          <label className="login-label">
            <span>URL de callback do WhatsApp</span>
            <input className="login-input" required
              value={form.callback_url}
              onChange={(e) => setForm((f) => ({ ...f, callback_url: e.target.value }))} />
          </label>

          <button className="login-btn" type="submit" disabled={loading}>
            {loading ? "Criando conta…" : "Criar conta grátis"}
          </button>
        </form>

        <p className="login-footer">
          Já tem conta? <Link to="/portal/login">Entrar</Link>
        </p>
      </div>
    </div>
  );
}
