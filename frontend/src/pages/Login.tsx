import { useState, type FormEvent } from "react";
import { useAuth } from "../contexts/AuthContext";
import { Spinner } from "../components/Spinner";
import "./Login.css";

export function Login() {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await login(email, password);
    } catch {
      setError("E-mail ou senha incorretos. Tente novamente.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="login-page">
      {/* Dark hero tile */}
      <div className="login-hero">
        <div className="login-hero__content">
          <div className="login-logo" aria-label="FarmáciaSaaS">
            <svg width="48" height="48" viewBox="0 0 48 48" fill="none" aria-hidden="true">
              <rect width="48" height="48" rx="12" fill="#0066cc"/>
              <path d="M24 10C16.27 10 10 16.27 10 24s6.27 14 14 14 14-6.27 14-14S31.73 10 24 10zm0 4c2.66 0 5.1.87 7.06 2.33L14.33 30.06A10.02 10.02 0 0 1 14 28c0-5.52 4.48-10 10-10zm0 20c-2.66 0-5.1-.87-7.06-2.33l16.73-16.73c.84 1.96 1.33 4.1 1.33 7.06 0 5.52-4.48 10-10 10z" fill="white" opacity=".9"/>
              <rect x="21" y="16" width="6" height="16" rx="3" fill="white"/>
              <rect x="16" y="21" width="16" height="6" rx="3" fill="white"/>
            </svg>
          </div>
          <h1 className="login-hero__title">FarmáciaSaaS</h1>
          <p className="login-hero__sub">Painel de Controle</p>
        </div>
      </div>

      {/* Login form panel */}
      <div className="login-form-panel">
        <form className="login-form" onSubmit={handleSubmit} noValidate>
          <h2 className="login-form__heading">Entrar</h2>
          <p className="login-form__hint">Acesso restrito a administradores.</p>

          <div className="form-group" style={{ marginTop: 32 }}>
            <label htmlFor="email">E-mail</label>
            <input
              id="email"
              type="email"
              className="form-input"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="admin@farmacia.io"
              required
              autoComplete="email"
              autoFocus
            />
          </div>

          <div className="form-group">
            <label htmlFor="password">Senha</label>
            <input
              id="password"
              type="password"
              className="form-input"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              required
              autoComplete="current-password"
            />
          </div>

          {error && <p className="form-error">{error}</p>}

          <button
            type="submit"
            className="btn-primary login-form__submit"
            disabled={loading || !email || !password}
          >
            {loading ? <Spinner size={18} /> : null}
            {loading ? "Entrando…" : "Entrar"}
          </button>
        </form>

        <p className="login-legal">
          Sistema de uso exclusivo. Acessos são monitorados e registrados.
        </p>
      </div>
    </div>
  );
}
