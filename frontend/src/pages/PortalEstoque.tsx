import { useEffect, useState, useRef } from "react";
import { PortalLayout } from "../components/PortalLayout";
import { Spinner } from "../components/Spinner";
import { Badge } from "../components/Badge";
import {
  listProducts, createProduct, updateProduct, deleteProduct, importProductsCsv,
  importProductsXlsx, previewImport, listPdvTemplates, configureGoogleSheets,
  triggerSync, type Product, type ImportPreview, type PdvTemplate,
} from "../api/portal";
import "./PortalEstoque.css";

type ImportKind = "csv" | "xlsx";

export function PortalEstoque() {
  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [showModal, setShowModal] = useState(false);
  const [editing, setEditing] = useState<Product | null>(null);
  const [syncLoading, setSyncLoading] = useState("");
  const [importMsg, setImportMsg] = useState("");
  const [error, setError] = useState("");

  const fileRef = useRef<HTMLInputElement>(null);
  const [pendingKind, setPendingKind] = useState<ImportKind>("csv");
  const [previewFile, setPreviewFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [previewMapping, setPreviewMapping] = useState<Record<string, string>>({});
  const [previewTemplate, setPreviewTemplate] = useState<string>("");
  const [previewLoading, setPreviewLoading] = useState(false);

  const [showSheetsModal, setShowSheetsModal] = useState(false);
  const [templates, setTemplates] = useState<PdvTemplate[]>([]);
  const [sheetsUrl, setSheetsUrl] = useState("");
  const [sheetsGid, setSheetsGid] = useState("0");
  const [sheetsTemplate, setSheetsTemplate] = useState<string>("");
  const [sheetsDeactivate, setSheetsDeactivate] = useState(false);
  const [sheetsSaving, setSheetsSaving] = useState(false);

  const [form, setForm] = useState({ name: "", sku: "", category: "", price: "", stock_qty: "0", unit: "un" });

  async function load(q?: string) {
    setLoading(true);
    try {
      const data = await listProducts({ q });
      setProducts(data);
    } catch { setError("Erro ao carregar produtos"); }
    finally { setLoading(false); }
  }

  useEffect(() => { load(); }, []);

  useEffect(() => {
    listPdvTemplates().then(setTemplates).catch(() => {});
  }, []);

  function openNew() {
    setEditing(null);
    setForm({ name: "", sku: "", category: "", price: "", stock_qty: "0", unit: "un" });
    setShowModal(true);
  }

  function openEdit(p: Product) {
    setEditing(p);
    setForm({ name: p.name, sku: p.sku ?? "", category: p.category ?? "", price: String(p.price ?? ""), stock_qty: String(p.stock_qty), unit: p.unit });
    setShowModal(true);
  }

  async function saveProduct() {
    const payload = {
      name: form.name,
      sku: form.sku || undefined,
      category: form.category || undefined,
      price: form.price ? parseFloat(form.price) : undefined,
      stock_qty: parseInt(form.stock_qty, 10),
      unit: form.unit,
      tags: [],
    };
    try {
      if (editing) {
        const updated = await updateProduct(editing.id, payload);
        setProducts((ps) => ps.map((p) => (p.id === updated.id ? updated : p)));
      } else {
        const created = await createProduct(payload);
        setProducts((ps) => [created, ...ps]);
      }
      setShowModal(false);
    } catch { setError("Erro ao salvar produto"); }
  }

  async function handleDelete(id: string) {
    if (!window.confirm("Arquivar este produto?")) return;
    await deleteProduct(id);
    setProducts((ps) => ps.filter((p) => p.id !== id));
  }

  function pickFile(kind: ImportKind) {
    setPendingKind(kind);
    if (fileRef.current) {
      fileRef.current.accept = kind === "csv" ? ".csv" : ".xlsx,.xlsm";
      fileRef.current.value = "";
      fileRef.current.click();
    }
  }

  async function handleFilePicked(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setPreviewFile(file);
    setPreview(null);
    setPreviewMapping({});
    setPreviewTemplate("");
    setPreviewLoading(true);
    setImportMsg("");
    try {
      const p = await previewImport(file);
      setPreview(p);
      setPreviewMapping(p.suggested_mapping);
    } catch (err: any) {
      setImportMsg(`Erro lendo arquivo: ${err?.response?.data?.detail ?? err.message}`);
      setPreviewFile(null);
    } finally {
      setPreviewLoading(false);
    }
  }

  function applyTemplate(tplId: string) {
    setPreviewTemplate(tplId);
    if (!tplId) return;
    const tpl = templates.find((t) => t.id === tplId);
    if (tpl) setPreviewMapping(tpl.field_mapping);
  }

  async function confirmImport() {
    if (!previewFile) return;
    setImportMsg("Importando…");
    try {
      const mapping = previewMapping;
      const result = pendingKind === "csv"
        ? await importProductsCsv(previewFile, mapping)
        : await importProductsXlsx(previewFile, { mapping });
      if (result.errors?.length) {
        setImportMsg(
          `Importados: ${result.records_upd} de ${result.records_in}. ` +
          `Primeiro erro: ${result.errors[0]}`,
        );
      } else {
        setImportMsg(`Importados: ${result.records_upd} de ${result.records_in}.`);
      }
      setPreviewFile(null);
      setPreview(null);
      load();
    } catch (err: any) {
      setImportMsg(`Erro na importação: ${err?.response?.data?.detail ?? err.message}`);
    }
  }

  async function handleSync(type: string) {
    setSyncLoading(type);
    try {
      const result = await triggerSync(type);
      setImportMsg(`Sync ${type}: ${result.records_upd} atualizados`);
      load();
    } catch { setImportMsg("Erro ao sincronizar"); }
    finally { setSyncLoading(""); }
  }

  async function saveGoogleSheets() {
    if (!sheetsUrl.trim()) {
      setImportMsg("Cole a URL da planilha do Google Sheets");
      return;
    }
    setSheetsSaving(true);
    setImportMsg("Conectando ao Google Sheets…");
    try {
      const result = await configureGoogleSheets({
        sheet_url: sheetsUrl.trim(),
        gid: sheetsGid || "0",
        template_id: sheetsTemplate || null,
        deactivate_missing: sheetsDeactivate,
        sync_now: true,
      });
      if (result.errors?.length) {
        // Mostra o primeiro erro completo (geralmente é o que importa).
        setImportMsg(`Erro do Google Sheets: ${result.errors[0]}`);
      } else {
        setImportMsg(
          `Google Sheets sincronizado: ${result.records_upd} de ${result.records_in}` +
          (result.records_deactivated ? ` · ${result.records_deactivated} desativados` : ""),
        );
        setShowSheetsModal(false);
      }
      load();
    } catch (err: any) {
      setImportMsg(`Erro: ${err?.response?.data?.detail ?? err.message}`);
    } finally {
      setSheetsSaving(false);
    }
  }

  const mappableFields = [
    "sku", "name", "barcode", "brand", "category", "description",
    "price", "stock_qty", "unit", "principio_ativo", "fabricante",
  ];

  return (
    <PortalLayout active="estoque">
      <div className="estoque-page">
        <div className="estoque-header">
          <h1 className="page-title">Estoque</h1>
          <div className="estoque-actions">
            <input
              className="search-input"
              placeholder="Buscar produto…"
              value={search}
              onChange={(e) => { setSearch(e.target.value); load(e.target.value); }}
            />
            <button className="btn btn--secondary btn--sm" onClick={() => pickFile("csv")}>
              Importar CSV
            </button>
            <button className="btn btn--secondary btn--sm" onClick={() => pickFile("xlsx")}>
              Importar Excel
            </button>
            <button className="btn btn--secondary btn--sm" onClick={() => setShowSheetsModal(true)}>
              Google Sheets
            </button>
            <input ref={fileRef} type="file" hidden onChange={handleFilePicked} />
            <button className="btn btn--secondary btn--sm" onClick={() => handleSync("rest_api")} disabled={syncLoading === "rest_api"}>
              {syncLoading === "rest_api" ? "Sincronizando…" : "Sincronizar API"}
            </button>
            <button className="btn btn--secondary btn--sm" onClick={() => handleSync("google_sheets")} disabled={syncLoading === "google_sheets"}>
              {syncLoading === "google_sheets" ? "Sincronizando…" : "Sync Sheets"}
            </button>
            <button className="btn btn--primary btn--sm" onClick={openNew}>
              + Novo produto
            </button>
          </div>
        </div>

        {error && <div className="error-banner">{error}</div>}
        {importMsg && <div className="info-banner">{importMsg}</div>}

        {loading ? <Spinner /> : (
          <table className="products-table">
            <thead>
              <tr><th>Nome</th><th>SKU</th><th>Categoria</th><th>Preço</th><th>Estoque</th><th>Origem</th><th></th></tr>
            </thead>
            <tbody>
              {products.map((p) => (
                <tr key={p.id}>
                  <td className="product-name">{p.name}</td>
                  <td>{p.sku ?? "—"}</td>
                  <td>{p.category ?? "—"}</td>
                  <td>{p.price != null ? `R$ ${p.price.toFixed(2)}` : "—"}</td>
                  <td>{p.stock_qty} {p.unit}</td>
                  <td><Badge variant={p.source === "manual" ? "gray" : "green"}>{p.source}</Badge></td>
                  <td className="actions">
                    <button className="btn-icon" onClick={() => openEdit(p)}>✏️</button>
                    <button className="btn-icon" onClick={() => handleDelete(p.id)}>🗑️</button>
                  </td>
                </tr>
              ))}
              {products.length === 0 && (
                <tr><td colSpan={7} className="empty-row">Nenhum produto cadastrado</td></tr>
              )}
            </tbody>
          </table>
        )}
      </div>

      {showModal && (
        <div className="modal-overlay" onClick={() => setShowModal(false)}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <h2>{editing ? "Editar produto" : "Novo produto"}</h2>
            <div className="form-grid">
              {(["name", "sku", "category", "price", "stock_qty", "unit"] as const).map((field) => (
                <label key={field} className="form-label">
                  <span>{field}</span>
                  <input
                    className="form-input"
                    value={form[field]}
                    onChange={(e) => setForm((f) => ({ ...f, [field]: e.target.value }))}
                  />
                </label>
              ))}
            </div>
            <div className="modal-footer">
              <button className="btn btn--secondary" onClick={() => setShowModal(false)}>Cancelar</button>
              <button className="btn btn--primary" onClick={saveProduct}>Salvar</button>
            </div>
          </div>
        </div>
      )}

      {/* Modal de preview antes do import (CSV ou Excel) */}
      {(previewFile || previewLoading) && (
        <div className="modal-overlay" onClick={() => { if (!previewLoading) { setPreviewFile(null); setPreview(null); } }}>
          <div className="modal-box modal-box--wide" onClick={(e) => e.stopPropagation()}>
            <h2>Pré-visualizar importação ({pendingKind.toUpperCase()})</h2>
            {previewLoading && <Spinner />}
            {preview && (
              <>
                <p className="muted">
                  {preview.total_rows} linha(s) detectada(s). Confira o mapeamento antes de importar.
                </p>
                <label className="form-label">
                  <span>Template de PDV (opcional)</span>
                  <select
                    className="form-input"
                    value={previewTemplate}
                    onChange={(e) => applyTemplate(e.target.value)}
                  >
                    <option value="">— Auto-detectado —</option>
                    {templates.map((t) => (
                      <option key={t.id} value={t.id}>{t.label}</option>
                    ))}
                  </select>
                </label>

                <div className="mapping-grid">
                  {mappableFields.map((f) => (
                    <label key={f} className="form-label">
                      <span>{f}</span>
                      <select
                        className="form-input"
                        value={previewMapping[f] ?? ""}
                        onChange={(e) => setPreviewMapping((m) => ({ ...m, [f]: e.target.value }))}
                      >
                        <option value="">— não mapear —</option>
                        {preview.headers.map((h) => (
                          <option key={h} value={h}>{h}</option>
                        ))}
                      </select>
                    </label>
                  ))}
                </div>

                <details>
                  <summary>Ver amostra ({preview.rows.length} primeiras linhas)</summary>
                  <table className="products-table">
                    <thead>
                      <tr>{preview.headers.map((h) => <th key={h}>{h}</th>)}</tr>
                    </thead>
                    <tbody>
                      {preview.rows.map((r, i) => (
                        <tr key={i}>
                          {preview.headers.map((h) => (
                            <td key={h}>{r[h] == null ? "—" : String(r[h])}</td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </details>
              </>
            )}
            <div className="modal-footer">
              <button className="btn btn--secondary" onClick={() => { setPreviewFile(null); setPreview(null); }}>
                Cancelar
              </button>
              <button className="btn btn--primary" onClick={confirmImport} disabled={!preview}>
                Importar {preview ? `${preview.total_rows} linhas` : ""}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Modal Google Sheets */}
      {showSheetsModal && (
        <div className="modal-overlay" onClick={() => !sheetsSaving && setShowSheetsModal(false)}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <h2>Conectar Google Sheets</h2>
            <p className="muted">
              <b>Recomendado:</b> use <b>Arquivo → Compartilhar → Publicar na web</b> na sua
              planilha, escolha a aba, formato <b>CSV</b>, e cole aqui a URL gerada
              (termina em <code>/pub?output=csv</code>). Esse caminho é estável para
              servidores em produção.
              <br /><br />
              Alternativa: cole a URL normal da planilha (precisa estar com
              "Qualquer pessoa com o link — Leitor"). Em alguns servidores o Google
              limita esse tipo de acesso e o caminho acima é mais confiável.
            </p>
            <label className="form-label">
              <span>URL da planilha (pub ou edit)</span>
              <input
                className="form-input"
                placeholder="https://docs.google.com/spreadsheets/d/e/.../pub?output=csv"
                value={sheetsUrl}
                onChange={(e) => setSheetsUrl(e.target.value)}
              />
            </label>
            <label className="form-label">
              <span>GID da aba (default: 0)</span>
              <input
                className="form-input"
                value={sheetsGid}
                onChange={(e) => setSheetsGid(e.target.value)}
              />
            </label>
            <label className="form-label">
              <span>Template do PDV</span>
              <select
                className="form-input"
                value={sheetsTemplate}
                onChange={(e) => setSheetsTemplate(e.target.value)}
              >
                <option value="">— Nenhum (colunas já em português) —</option>
                {templates.map((t) => (
                  <option key={t.id} value={t.id}>{t.label} — {t.description}</option>
                ))}
              </select>
            </label>
            <label className="form-label" style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
              <input
                type="checkbox"
                checked={sheetsDeactivate}
                onChange={(e) => setSheetsDeactivate(e.target.checked)}
              />
              <span>Desativar produtos que sumirem da planilha</span>
            </label>
            <div className="modal-footer">
              <button className="btn btn--secondary" onClick={() => setShowSheetsModal(false)} disabled={sheetsSaving}>
                Cancelar
              </button>
              <button className="btn btn--primary" onClick={saveGoogleSheets} disabled={sheetsSaving}>
                {sheetsSaving ? "Sincronizando…" : "Salvar e sincronizar"}
              </button>
            </div>
          </div>
        </div>
      )}
    </PortalLayout>
  );
}
