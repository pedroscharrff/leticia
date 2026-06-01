/**
 * PortalRecuperacao — visualiza o efeito dos jobs proativos e permite
 * disparo manual em lote com seleção, cancelamento e undo.
 *
 *  • Tabela de carrinhos com checkbox por linha + "selecionar todos"
 *  • Botão "Disparar para X selecionados" (ou "Disparar para todos")
 *  • Painel de batch ativo com barra de progresso + Cancelar
 *  • Histórico de batches recentes com botão Desfazer
 *
 * Backend (api/routers/payments.py): processa async via Celery
 * (jobs.process_recovery_batch) com rate-limit de ~5 msg/s e check de
 * cancelamento entre envios. Undo reverte sent_recovery_at/recovery_attempts
 * nos carrinhos — não desentrega mensagens já enviadas.
 */
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { PortalLayout } from "../components/PortalLayout";
import { Spinner } from "../components/Spinner";
import {
  getRecoveryStats, listCarts,
  triggerRecovery, getBatch, listBatches, cancelBatch, undoBatch,
  type RecoveryStats, type CartRow,
  type RecoveryBatch,
} from "../api/payments";

const STATUS_LABEL: Record<CartRow["status"], { text: string; color: string }> = {
  in_progress: { text: "Em andamento",      color: "#0ea5e9" },
  pending:     { text: "Aguardando nudge",  color: "#f59e0b" },
  recovered:   { text: "Mensagem enviada",  color: "#22c55e" },
};

const BATCH_STATUS_LABEL: Record<RecoveryBatch["status"], { text: string; color: string }> = {
  queued:    { text: "Aguardando",  color: "#9ca3af" },
  running:   { text: "Disparando",  color: "#0ea5e9" },
  completed: { text: "Concluído",   color: "#22c55e" },
  cancelled: { text: "Cancelado",   color: "#f59e0b" },
  undone:    { text: "Desfeito",    color: "#6b7280" },
  failed:    { text: "Falhou",      color: "#ef4444" },
};

function fmtBRL(n: number) {
  return n.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
}
function fmtWhen(iso: string | null) {
  if (!iso) return "—";
  const d = new Date(iso);
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60)     return "agora";
  if (diff < 3600)   return `há ${Math.floor(diff / 60)} min`;
  if (diff < 86400)  return `há ${Math.floor(diff / 3600)} h`;
  return d.toLocaleDateString("pt-BR") + " " + d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
}

export function PortalRecuperacao() {
  const navigate = useNavigate();
  const [stats, setStats]       = useState<RecoveryStats | null>(null);
  const [carts, setCarts]       = useState<CartRow[] | null>(null);
  const [batches, setBatches]   = useState<RecoveryBatch[]>([]);
  const [activeBatch, setActiveBatch] = useState<RecoveryBatch | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [error, setError]       = useState("");
  const [busy, setBusy]         = useState(false);
  const pollRef = useRef<number | null>(null);

  async function refresh() {
    try {
      const [s, c, b] = await Promise.all([
        getRecoveryStats(), listCarts(), listBatches(),
      ]);
      setStats(s);
      setCarts(c);
      setBatches(b);
      // Limpa seleção que não existe mais
      setSelected(prev => {
        const valid = new Set(c.map(r => r.session_key));
        return new Set([...prev].filter(k => valid.has(k)));
      });
      // Pega batch ativo (queued/running) se houver
      const live = b.find(x => x.status === "queued" || x.status === "running");
      setActiveBatch(live || null);
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Não foi possível carregar.");
    }
  }

  // Carga inicial
  useEffect(() => { refresh(); }, []);

  // Polling do batch ativo (a cada 2s enquanto vivo)
  useEffect(() => {
    if (!activeBatch) {
      if (pollRef.current) { window.clearInterval(pollRef.current); pollRef.current = null; }
      return;
    }
    if (pollRef.current) return; // já tem poll rodando
    pollRef.current = window.setInterval(async () => {
      try {
        const fresh = await getBatch(activeBatch.id);
        setActiveBatch(fresh);
        if (fresh.status !== "queued" && fresh.status !== "running") {
          // Terminou: recarrega tudo (carrinhos podem ter mudado de status)
          await refresh();
        }
      } catch {
        // best-effort; ignora erro transitório
      }
    }, 2000);
    return () => {
      if (pollRef.current) { window.clearInterval(pollRef.current); pollRef.current = null; }
    };
  }, [activeBatch?.id]);

  function toggleOne(key: string) {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }

  function toggleAll(keysVisible: string[]) {
    setSelected(prev => {
      const allSelected = keysVisible.every(k => prev.has(k));
      const next = new Set(prev);
      if (allSelected) keysVisible.forEach(k => next.delete(k));
      else keysVisible.forEach(k => next.add(k));
      return next;
    });
  }

  async function handleTrigger(scope: "selected" | "all") {
    if (activeBatch) {
      setError("Já existe um disparo em andamento.");
      return;
    }
    const keys = scope === "selected" ? Array.from(selected) : undefined;
    const count = scope === "selected" ? keys!.length : (carts?.length || 0);
    if (!count) {
      setError(scope === "selected"
        ? "Selecione ao menos um carrinho."
        : "Nenhum carrinho elegível.");
      return;
    }
    const msg = `Disparar mensagem para ${count} carrinho(s) agora? `
              + "Ignora delay e horário silencioso. Você pode cancelar antes do fim "
              + "ou desfazer depois (reverte o marcador, não a mensagem entregue).";
    if (!window.confirm(msg)) return;

    setBusy(true); setError("");
    try {
      const result = await triggerRecovery(keys);
      // Pega o batch recém-criado pra começar o polling
      const fresh = await getBatch(result.batch_id);
      setActiveBatch(fresh);
      // Atualiza listas
      const [b] = await Promise.all([listBatches()]);
      setBatches(b);
      setSelected(new Set());
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Falha ao disparar.");
    } finally {
      setBusy(false);
    }
  }

  async function handleCancel() {
    if (!activeBatch) return;
    if (!window.confirm("Cancelar o disparo em andamento? Mensagens já enviadas não serão desfeitas.")) return;
    try {
      const fresh = await cancelBatch(activeBatch.id);
      setActiveBatch(fresh);
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Falha ao cancelar.");
    }
  }

  async function handleUndo(batchId: string) {
    if (!window.confirm(
      "Desfazer este disparo? Isso libera os carrinhos para serem notificados de novo pelo robô — "
      + "MAS não cancela mensagens já entregues no WhatsApp."
    )) return;
    try {
      await undoBatch(batchId);
      await refresh();
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Falha ao desfazer.");
    }
  }

  const visibleKeys = (carts || []).map(c => c.session_key);
  const allVisibleSelected = visibleKeys.length > 0
    && visibleKeys.every(k => selected.has(k));

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

      {error && (
        <div className="form-error" style={{ marginBottom: 16 }}>
          {error}
          <button onClick={() => setError("")} style={{ float: "right",
            background: "none", border: "none", color: "inherit", cursor: "pointer" }}>×</button>
        </div>
      )}

      {!stats ? (
        <div className="portal-loading"><Spinner size={28} /></div>
      ) : (
        <>
          <h3 style={{ marginTop: 0 }}>🛍️ Carrinho abandonado</h3>
          <div className="cliente-stats" style={{ marginBottom: 24 }}>
            <Agg label="Aguardando recuperação"
                 value={String(stats.carts_pending_recovery)}
                 hint="Carrinhos com itens, parados > 4h, ainda sem nudge." />
            <Agg label="Recuperados (7d)"
                 value={String(stats.carts_recovered_last_7d)}
                 hint="Carrinhos que receberam mensagem proativa nos últimos 7 dias." />
          </div>

          {/* Painel do batch ativo (com barra de progresso + cancelar) */}
          {activeBatch && <ActiveBatchPanel batch={activeBatch} onCancel={handleCancel} />}

          {/* Card de disparo manual */}
          <section className="cliente-card" style={{ marginBottom: 24 }}>
            <div style={{ display: "flex", justifyContent: "space-between",
                          alignItems: "center", gap: 12, flexWrap: "wrap" }}>
              <div>
                <h3 style={{ margin: 0 }}>Disparo manual</h3>
                <p style={{ margin: "4px 0 0", fontSize: 13, color: "#9ca3af" }}>
                  Ignora delay, horário silencioso e limite de tentativas. Você pode
                  cancelar enquanto roda ou desfazer depois (reverte o marcador no
                  carrinho — não desentrega a mensagem).
                </p>
              </div>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <button
                  className="btn"
                  onClick={() => handleTrigger("selected")}
                  disabled={busy || !!activeBatch || selected.size === 0}
                  title={selected.size === 0 ? "Selecione carrinhos na tabela abaixo" : ""}
                >
                  Disparar para selecionados ({selected.size})
                </button>
                <button
                  className="btn btn-primary"
                  onClick={() => handleTrigger("all")}
                  disabled={busy || !!activeBatch || !carts || carts.length === 0}
                >
                  Disparar para todos ({(carts || []).length})
                </button>
              </div>
            </div>
          </section>

          {/* Tabela de carrinhos */}
          <h3>📋 Carrinhos</h3>
          {!carts ? (
            <div className="portal-loading"><Spinner size={20} /></div>
          ) : carts.length === 0 ? (
            <div className="cliente-card" style={{ color: "#9ca3af" }}>
              Nenhum carrinho com itens no momento.
            </div>
          ) : (
            <div className="cliente-card" style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                <thead>
                  <tr style={{ textAlign: "left", color: "#9ca3af",
                               borderBottom: "1px solid #2a2a2a" }}>
                    <th style={{ padding: "8px 6px", width: 32 }}>
                      <input
                        type="checkbox"
                        checked={allVisibleSelected}
                        onChange={() => toggleAll(visibleKeys)}
                        title="Selecionar/desmarcar todos"
                      />
                    </th>
                    <th style={{ padding: "8px 6px" }}>Cliente</th>
                    <th style={{ padding: "8px 6px" }}>Telefone</th>
                    <th style={{ padding: "8px 6px", textAlign: "right" }}>Itens</th>
                    <th style={{ padding: "8px 6px", textAlign: "right" }}>Subtotal</th>
                    <th style={{ padding: "8px 6px" }}>Última atividade</th>
                    <th style={{ padding: "8px 6px" }}>Status</th>
                    <th style={{ padding: "8px 6px", textAlign: "right" }}>Tentativas</th>
                  </tr>
                </thead>
                <tbody>
                  {carts.map((c) => {
                    const s = STATUS_LABEL[c.status];
                    const isSel = selected.has(c.session_key);
                    return (
                      <tr key={c.session_key}
                          onClick={() => toggleOne(c.session_key)}
                          style={{ borderBottom: "1px solid #1f1f1f",
                                   cursor: "pointer",
                                   background: isSel ? "rgba(14,165,233,0.06)" : undefined }}>
                        <td style={{ padding: "8px 6px" }}>
                          <input type="checkbox" checked={isSel} readOnly
                                 onClick={(e) => e.stopPropagation()}
                                 onChange={() => toggleOne(c.session_key)} />
                        </td>
                        <td style={{ padding: "8px 6px" }}>
                          {c.customer_name || <span style={{ color: "#6b7280" }}>—</span>}
                        </td>
                        <td style={{ padding: "8px 6px", fontFamily: "monospace" }}>
                          {c.phone || <span style={{ color: "#6b7280" }}>—</span>}
                        </td>
                        <td style={{ padding: "8px 6px", textAlign: "right" }}>{c.items_count}</td>
                        <td style={{ padding: "8px 6px", textAlign: "right" }}>{fmtBRL(c.subtotal)}</td>
                        <td style={{ padding: "8px 6px" }}>{fmtWhen(c.updated_at)}</td>
                        <td style={{ padding: "8px 6px" }}>
                          <span style={{
                            display: "inline-block", padding: "2px 8px",
                            borderRadius: 10, fontSize: 11, fontWeight: 600,
                            background: `${s.color}22`, color: s.color,
                          }}>{s.text}</span>
                          {c.sent_recovery_at && (
                            <div style={{ fontSize: 11, color: "#6b7280", marginTop: 2 }}>
                              enviado {fmtWhen(c.sent_recovery_at)}
                            </div>
                          )}
                        </td>
                        <td style={{ padding: "8px 6px", textAlign: "right" }}>{c.recovery_attempts}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {/* Histórico de batches recentes */}
          {batches.length > 0 && (
            <>
              <h3 style={{ marginTop: 32 }}>🗂️ Histórico de disparos</h3>
              <div className="cliente-card" style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                  <thead>
                    <tr style={{ textAlign: "left", color: "#9ca3af",
                                 borderBottom: "1px solid #2a2a2a" }}>
                      <th style={{ padding: "8px 6px" }}>Quando</th>
                      <th style={{ padding: "8px 6px" }}>Operador</th>
                      <th style={{ padding: "8px 6px" }}>Status</th>
                      <th style={{ padding: "8px 6px", textAlign: "right" }}>Enviados</th>
                      <th style={{ padding: "8px 6px", textAlign: "right" }}>Falhas</th>
                      <th style={{ padding: "8px 6px", textAlign: "right" }}>Ignorados</th>
                      <th style={{ padding: "8px 6px", textAlign: "right" }}>Total</th>
                      <th style={{ padding: "8px 6px" }}>Ações</th>
                    </tr>
                  </thead>
                  <tbody>
                    {batches.map(b => {
                      const s = BATCH_STATUS_LABEL[b.status];
                      const canUndo = b.status === "completed" || b.status === "cancelled";
                      return (
                        <tr key={b.id} style={{ borderBottom: "1px solid #1f1f1f" }}>
                          <td style={{ padding: "8px 6px" }}>{fmtWhen(b.created_at)}</td>
                          <td style={{ padding: "8px 6px" }}>{b.actor_email || "—"}</td>
                          <td style={{ padding: "8px 6px" }}>
                            <span style={{
                              display: "inline-block", padding: "2px 8px",
                              borderRadius: 10, fontSize: 11, fontWeight: 600,
                              background: `${s.color}22`, color: s.color,
                            }}>{s.text}</span>
                          </td>
                          <td style={{ padding: "8px 6px", textAlign: "right" }}>{b.sent}</td>
                          <td style={{ padding: "8px 6px", textAlign: "right" }}>{b.failed}</td>
                          <td style={{ padding: "8px 6px", textAlign: "right" }}>{b.skipped}</td>
                          <td style={{ padding: "8px 6px", textAlign: "right" }}>{b.total}</td>
                          <td style={{ padding: "8px 6px" }}>
                            {canUndo ? (
                              <button className="btn btn-sm"
                                      onClick={() => handleUndo(b.id)}
                                      disabled={b.sent === 0}
                                      title={b.sent === 0 ? "Nenhum envio para desfazer" : ""}>
                                Desfazer
                              </button>
                            ) : (
                              <span style={{ color: "#6b7280", fontSize: 11 }}>—</span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </>
          )}

          <h3 style={{ marginTop: 32 }}>💊 Recompra de medicamentos contínuos</h3>
          <div className="cliente-stats" style={{ marginBottom: 24 }}>
            <Agg label="Clientes em contínuo"
                 value={String(stats.refill_clients_total)}
                 hint="Clientes com pelo menos 1 medicamento contínuo cadastrado." />
            <Agg label="Lembretes enviados (30d)"
                 value={String(stats.refills_nudged_last_30d)}
                 hint="Nudges de recompra disparados nos últimos 30 dias." />
          </div>

          <section className="cliente-card">
            <h3 style={{ marginTop: 0 }}>Como funciona</h3>
            <ul style={{ margin: 0, paddingLeft: 20, lineHeight: 1.7 }}>
              <li>O sistema verifica carrinhos a cada hora e clientes em contínuo 1x por dia.</li>
              <li>Mensagens respeitam o <strong>horário silencioso</strong> (padrão: 21h–08h em Brasília) e o <strong>máximo de tentativas</strong> por carrinho.</li>
              <li>Nenhum cliente recebe duas mensagens automáticas para o mesmo motivo no mesmo ciclo — o sistema marca o envio em <code>sent_recovery_at</code> e <code>last_nudge_at</code>.</li>
              <li>O <strong>disparo manual</strong> ignora esses controles — use só quando quiser uma campanha pontual.</li>
              <li><strong>Desfazer</strong> reverte o marcador de envio nos carrinhos para que o robô possa notificá-los de novo. <em>Não</em> apaga a mensagem que já chegou no WhatsApp.</li>
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

function ActiveBatchPanel({ batch, onCancel }: { batch: RecoveryBatch; onCancel: () => void }) {
  const processed = batch.sent + batch.failed + batch.skipped;
  const pct = batch.total ? Math.round((processed / batch.total) * 100) : 0;
  const statusLabel = BATCH_STATUS_LABEL[batch.status];

  return (
    <section className="cliente-card" style={{ marginBottom: 24,
        border: "1px solid rgba(14,165,233,0.3)",
        background: "rgba(14,165,233,0.05)" }}>
      <div style={{ display: "flex", justifyContent: "space-between",
                    alignItems: "center", gap: 12, marginBottom: 8 }}>
        <div>
          <strong>Disparo em andamento</strong>{" "}
          <span style={{ color: "#9ca3af", fontSize: 12 }}>
            ({statusLabel.text} · {batch.actor_email || "—"})
          </span>
        </div>
        {!batch.cancel_requested ? (
          <button className="btn btn-sm" onClick={onCancel}>Cancelar</button>
        ) : (
          <span style={{ fontSize: 12, color: "#f59e0b" }}>Cancelamento solicitado…</span>
        )}
      </div>
      <div style={{ height: 8, background: "#1f1f1f", borderRadius: 4, overflow: "hidden" }}>
        <div style={{ height: "100%", width: `${pct}%`,
                      background: "#0ea5e9", transition: "width .4s" }} />
      </div>
      <div style={{ marginTop: 6, fontSize: 12, color: "#9ca3af" }}>
        {processed} / {batch.total} processados · {batch.sent} enviados ·{" "}
        {batch.failed} falhas · {batch.skipped} ignorados
      </div>
    </section>
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
