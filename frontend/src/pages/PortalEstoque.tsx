import { useEffect, useState, useRef } from "react";
import { PortalLayout } from "../components/PortalLayout";
import { Spinner } from "../components/Spinner";
import { Badge } from "../components/Badge";
import {
  listProducts, createProduct, updateProduct, deleteProduct, importProductsCsv,
  triggerSync, type Product,
} from "../api/portal";
import "./PortalEstoque.css";

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

  async function handleCsvImport(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setImportMsg("Importando…");
    try {
      const result = await importProductsCsv(file);
      setImportMsg(`Importados: ${result.records_upd} de ${result.records_in}${result.errors.length ? ` (${result.errors.length} erros)` : ""}`);
      load();
    } catch { setImportMsg("Erro na importação"); }
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
            <button className="btn btn--secondary btn--sm" onClick={() => fileRef.current?.click()}>
              Importar CSV
            </button>
            <input ref={fileRef} type="file" accept=".csv" hidden onChange={handleCsvImport} />
            <button className="btn btn--secondary btn--sm" onClick={() => handleSync("rest_api")} disabled={syncLoading === "rest_api"}>
              {syncLoading === "rest_api" ? "Sincronizando…" : "Sincronizar API"}
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
    </PortalLayout>
  );
}
