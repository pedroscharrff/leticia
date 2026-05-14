import { useState, type FormEvent } from "react";
import { useAuth } from "../contexts/AuthContext";
import { Spinner } from "../components/Spinner";
import { Link } from "react-router-dom";
import "./Login.css";

export function PortalLogin() {
  const { portalLogin } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await portalLogin(email, password);
    } catch {
      setError("E-mail ou senha incorretos. Tente novamente.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="login-page">
      <div className="login-hero" style={{ background: "var(--color-primary)" }}>
        <div className="login-hero__content">
          <div className="login-logo" aria-label="FarmáciaSaaS">
            <svg width="48" height="48" viewBox="0 0 48 48" fill="none" aria-hidden="true">
              <rect width="48" height="48" rx="12" fill="rgba(255,255,255,0.15)"/>
              <rect x="21" y="12" width="6" height="24" rx="3" fill="white"/>
              <rect x="12" y="21" width="24" height="6" rx="3" fill="white"/>
            </svg>
          </div>
          <h1 className="login-hero__title">Área da Farmácia</h1>
          <p className="login-hero__sub">Gerencie seu atendimento inteligente</p>
        </div>
      </div>

      <div className="login-form-panel">
        <form className="login-form" onSubmit={handleSubmit} noValidate>
          <h2 className="login-form__heading">Entrar</h2>
          <p className="login-form__hint">Acesso para proprietários de farmácias.</p>

          <div className="form-group" style={{ marginTop: 32 }}>
            <label htmlFor="email">E-mail</label>
            <input
              id="email"
              type="email"
              className="form-input"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="voce@farmacia.com"
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
          É administrador?{" "}
          <Link to="/login" style={{ color: "var(--color-primary)" }}>
            Acesse o painel admin
          </Link>
        </p>
      </div>
    </div>
  );
}
