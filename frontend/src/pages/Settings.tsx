import { GlobalNav } from "../components/GlobalNav";
import { SubNav } from "../components/SubNav";
import { useAuth } from "../contexts/AuthContext";
import "./Settings.css";

export function Settings() {
  const { logout } = useAuth();

  return (
    <>
      <GlobalNav />
      <SubNav title="Configurações" />
      <main className="page-content settings">

        {/* Dark hero tile */}
        <section className="settings__hero tile-dark">
          <h1 className="settings__hero-title">Sistema</h1>
          <p className="settings__hero-sub">
            Configurações globais da plataforma FarmáciaSaaS.
          </p>
        </section>

        {/* Info cards grid */}
        <section className="settings__section">
          <h2 className="settings__section-title">Referências rápidas</h2>
          <div className="settings__info-grid">
            <div className="settings__info-card">
              <h3 className="settings__info-card-title">Webhook</h3>
              <p className="settings__info-card-body">
                Endpoint: <code>POST /webhook/&#123;tenant_id&#125;</code><br />
                Header: <code>X-Api-Key: &lt;chave-do-tenant&gt;</code><br />
                Body: <code>&#123; "phone", "message", "session_id?" &#125;</code>
              </p>
            </div>
            <div className="settings__info-card">
              <h3 className="settings__info-card-title">Autenticação Admin</h3>
              <p className="settings__info-card-body">
                Todas as rotas <code>/admin/*</code> exigem<br />
                <code>Authorization: Bearer &lt;jwt&gt;</code>.<br />
                JWT válido por 60 minutos.
              </p>
            </div>
            <div className="settings__info-card">
              <h3 className="settings__info-card-title">Rate Limiting</h3>
              <p className="settings__info-card-body">
                30 req/min por IP no webhook.<br />
                10 tentativas de login por minuto.<br />
                200 req/min nas demais rotas.
              </p>
            </div>
            <div className="settings__info-card">
              <h3 className="settings__info-card-title">Isolamento de Tenant</h3>
              <p className="settings__info-card-body">
                Cada farmácia tem um schema PostgreSQL separado.<br />
                Nenhum dado cruza entre tenants.<br />
                Sessões expiram em 30 minutos de inatividade.
              </p>
            </div>
          </div>
        </section>

        {/* Hash generator tip */}
        <section className="settings__section">
          <h2 className="settings__section-title">Gerar hash de senha admin</h2>
          <div className="settings__code-block">
            <pre>{`python -c "from security import hash_password; print(hash_password('sua_senha'))"`}</pre>
            <p className="settings__code-hint">
              Cole o resultado em <code>ADMIN_PASSWORD_HASH</code> no arquivo <code>.env</code>.
            </p>
          </div>
        </section>

        {/* Session */}
        <section className="settings__section settings__session">
          <h2 className="settings__section-title">Sessão</h2>
          <button className="btn-danger" style={{ width: "fit-content" }} onClick={logout}>
            Encerrar sessão
          </button>
        </section>

      </main>
    </>
  );
}
