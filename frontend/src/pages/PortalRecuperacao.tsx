/**
 * PortalRecuperacao — visualiza o efeito dos jobs proativos:
 *  • Recuperação de carrinho abandonado (sales.abandoned_cart)
 *  • Lembrete de recompra de contínuo (sales.continuous_refill_nudge)
 */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { PortalLayout } from "../components/PortalLayout";
import { Spinner } from "../components/Spinner";
import { getRecoveryStats, type RecoveryStats } from "../api/payments";

export function PortalRecuperacao() {
  const navigate = useNavigate();
  const [stats, setStats] = useState<RecoveryStats | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    getRecoveryStats()
      .then(setStats)
      .catch((e) => setError(e?.response?.data?.detail || "Não foi possível carregar."));
  }, []);

  return (
    <PortalLayout active="recuperacao">
      <header className="portal-page-header">
        <h1 className="portal-page-title">Recuperação Automática</h1>
        <p className="portal-page-subtitle">
          Carrinhos esquecidos e medicamentos contínuos que estão acabando viram
          mensagens proativas — sem você precisar lembrar de cada cliente.
          <br />
          💡 Para ligar/desligar e ajustar prazos (delay, horário silencioso,
          dias antes da reposição), use o cartão <strong>"Recuperação de
          Carrinho Abandonado"</strong> e <strong>"Lembrete de Recompra"</strong>
          {" "}em <em>Vendas › Recursos do seu Robô</em>.
        </p>
      </header>

      {error && <div className="form-error" style={{ marginBottom: 16 }}>{error}</div>}

      {!stats ? (
        <div className="portal-loading"><Spinner size={28} /></div>
      ) : (
        <>
          <h3 style={{ marginTop: 0 }}>🛍️ Carrinho abandonado</h3>
          <div className="cliente-stats" style={{ marginBottom: 24 }}>
            <Agg
              label="Aguardando recuperação"
              value={String(stats.carts_pending_recovery)}
              hint="Carrinhos com itens, parados > 4h, ainda sem nudge."
            />
            <Agg
              label="Recuperados (7d)"
              value={String(stats.carts_recovered_last_7d)}
              hint="Carrinhos que receberam mensagem proativa nos últimos 7 dias."
            />
          </div>

          <h3>💊 Recompra de medicamentos contínuos</h3>
          <div className="cliente-stats" style={{ marginBottom: 24 }}>
            <Agg
              label="Clientes em contínuo"
              value={String(stats.refill_clients_total)}
              hint="Clientes com pelo menos 1 medicamento contínuo cadastrado."
            />
            <Agg
              label="Lembretes enviados (30d)"
              value={String(stats.refills_nudged_last_30d)}
              hint="Nudges de recompra disparados nos últimos 30 dias."
            />
          </div>

          <section className="cliente-card">
            <h3 style={{ marginTop: 0 }}>Como funciona</h3>
            <ul style={{ margin: 0, paddingLeft: 20, lineHeight: 1.7 }}>
              <li>O sistema verifica carrinhos a cada hora e clientes em contínuo 1x por dia.</li>
              <li>Mensagens respeitam o <strong>horário silencioso</strong> (padrão: 21h–08h) e o <strong>máximo de tentativas</strong> por carrinho.</li>
              <li>Nenhum cliente recebe duas mensagens automáticas para o mesmo motivo no mesmo ciclo — o sistema marca o envio em <code>sent_recovery_at</code> e <code>last_nudge_at</code>.</li>
              <li>Para começar, ative <strong>"Memória de Clientes"</strong> e cadastre os contínuos na aba <strong>Memória</strong> de cada cliente.</li>
            </ul>
            <div style={{ marginTop: 12 }}>
              <button className="btn btn-primary" onClick={() => navigate("/portal/recursos")}>
                Configurar recursos →
              </button>
            </div>
          </section>
        </>
      )}
    </PortalLayout>
  );
}

function Agg({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="cliente-agg" title={hint}>
      <span className="cliente-agg__label">{label}</span>
      <span className="cliente-agg__value">{value}</span>
      {hint && <span style={{ fontSize: 11, color: "#9ca3af", marginTop: 2 }}>{hint}</span>}
    </div>
  );
}
