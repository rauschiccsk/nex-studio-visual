import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { FolderOpen, Loader2, Plus, Trash2, KeyRound, Pencil } from "lucide-react";

import { listCustomers, createCustomer, updateCustomer, deleteCustomer } from "@/services/api/customers";
import { ApiError } from "@/services/api";
import { humanizeApiError } from "@/services/apiError";
import { useActiveContextStore } from "@/store/activeContextStore";
import type { CustomerRead } from "@/types/customer";

/**
 * Zákazníci — the per-project customer registry (CR-V2-025, design §3.2).
 *
 * Project-scoped: lists the pinned project's customers and adds new ones via a
 * single form. Internal apps register ICC s.r.o. through this same form — there
 * is no internal/external branch in the UI.
 *
 * Secret handling (CLAUDE.md §4/§5, OQ-5): the form has a write-only "secret"
 * field whose value is sent to the backend credentials store and NEVER read
 * back. The list shows only a "secret recorded" badge derived from
 * `has_secret`; the secret value never re-enters the browser.
 */
export default function CustomersPage() {
  const navigate = useNavigate();
  const selectedProject = useActiveContextStore((s) => s.selectedProject);
  const slug = selectedProject?.slug;

  const [items, setItems] = useState<CustomerRead[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [customerSlug, setCustomerSlug] = useState("");
  const [subdomain, setSubdomain] = useState("");
  const [integrations, setIntegrations] = useState("");
  const [notes, setNotes] = useState("");
  const [secret, setSecret] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  // obs #6: when non-null the form is in EDIT mode for this customer (submits via updateCustomer/PATCH
  // instead of createCustomer/POST); null = add mode.
  const [editingCustomerId, setEditingCustomerId] = useState<string | null>(null);

  const load = useCallback(() => {
    if (!slug) return;
    setLoading(true);
    setLoadError(null);
    listCustomers(slug)
      .then(setItems)
      .catch((err) => {
        if (err instanceof ApiError) {
          setLoadError(humanizeApiError(err, "Načítanie zlyhalo").message);
        } else {
          setLoadError("Sieťová chyba pri načítavaní zákazníkov.");
        }
      })
      .finally(() => setLoading(false));
  }, [slug]);

  useEffect(() => {
    load();
  }, [load]);

  function resetForm() {
    setName("");
    setCustomerSlug("");
    setSubdomain("");
    setIntegrations("");
    setNotes("");
    setSecret("");
    setFormError(null);
    setEditingCustomerId(null);
  }

  // obs #6: load an existing customer's fields into the form and switch it to edit mode. The secret is
  // write-only (never echoed back by the API) so it is intentionally left BLANK — leaving it blank on submit
  // keeps the stored secret unchanged; typing a value rotates it.
  function startEdit(c: CustomerRead) {
    setEditingCustomerId(c.id);
    setName(c.name);
    setCustomerSlug(c.slug);
    setSubdomain(c.subdomain ?? "");
    setIntegrations(c.integrations ? JSON.stringify(c.integrations, null, 2) : "");
    setNotes(c.notes ?? "");
    setSecret("");
    setFormError(null);
    setShowForm(true);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!slug) return;
    setFormError(null);

    let integrationsParsed: Record<string, unknown> | null = null;
    if (integrations.trim()) {
      try {
        integrationsParsed = JSON.parse(integrations);
      } catch {
        setFormError("Integrácie musia byť platný JSON (alebo prázdne).");
        return;
      }
    }

    setSubmitting(true);
    const payload = {
      name: name.trim(),
      slug: customerSlug.trim(),
      subdomain: subdomain.trim() || null,
      integrations: integrationsParsed,
      notes: notes.trim() || null,
      // Write-only: a blank secret sends null, which the PATCH treats as "leave the stored secret unchanged"
      // (the backend skips null fields) — so editing never wipes an existing secret; a typed value rotates it.
      secret: secret ? secret : null,
    };
    try {
      if (editingCustomerId) {
        await updateCustomer(editingCustomerId, payload);
      } else {
        await createCustomer(slug, payload);
      }
      resetForm();
      setShowForm(false);
      load();
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 409) {
          setFormError("Zákazník s týmto slug už v projekte existuje.");
        } else if (err.status === 403) {
          setFormError("Pridanie zákazníka je dostupné len pre rolu Manažér.");
        } else {
          setFormError(humanizeApiError(err, "Uloženie zlyhalo").message);
        }
      } else {
        setFormError("Sieťová chyba pri ukladaní.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDelete(c: CustomerRead) {
    if (!window.confirm(`Odstrániť zákazníka ${c.name}? Odstráni sa aj jeho uložený secret.`)) return;
    setLoadError(null);
    try {
      await deleteCustomer(c.id);
      load();
    } catch (err) {
      // Audit Theme 2: the delete used to swallow the error and reload → the customer reappeared with NO
      // feedback (the manager couldn't tell it failed or why). Surface the reason instead of silently reloading.
      setLoadError(humanizeApiError(err, `Zákazníka „${c.name}" sa nepodarilo odstrániť`).message);
    }
  }

  // No project pinned — project-scoped page, mirror the Vývoj empty state.
  if (!selectedProject) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-4 p-6 text-center">
        <FolderOpen className="h-10 w-10 text-[var(--color-text-muted)]" />
        <h2 className="text-sm font-semibold text-[var(--color-text-secondary)]">Nemáš vybraný projekt</h2>
        <p className="max-w-md text-xs text-[var(--color-text-muted)]">
          Zákazníci sú viazaní na projekt. Otvor <span className="font-mono">Projekty</span> a pripni projekt.
        </p>
        <button
          onClick={() => navigate("/projects")}
          className="rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-500"
        >
          Otvoriť Projekty
        </button>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-4xl p-6">
      <div className="mb-1 flex items-center justify-between">
        <h1 className="text-base font-bold text-[var(--color-text-primary)]">Zákazníci</h1>
        <button
          onClick={() => {
            resetForm();
            setShowForm((v) => !v);
          }}
          className="flex items-center gap-1.5 rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-500"
        >
          <Plus className="h-3.5 w-3.5" /> Pridať zákazníka
        </button>
      </div>
      <p className="mb-4 text-xs text-[var(--color-text-muted)]">
        Register zákazníkov projektu <span className="text-[var(--color-text-secondary)]">{selectedProject.name}</span>.
        Každý zákazník beží na vlastnej UAT + PROD inštancii. Interné aplikácie pridaj ako{" "}
        <span className="font-mono">ICC s.r.o.</span> cez ten istý formulár.
      </p>

      {/* Add form */}
      {showForm && (
        <form
          onSubmit={handleSubmit}
          className="mb-5 space-y-3 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-canvas)] p-4"
        >
          <div className="text-xs font-semibold text-[var(--color-text-secondary)]">
            {editingCustomerId ? "Upraviť zákazníka" : "Nový zákazník"}
          </div>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            <label className="block text-xs">
              <span className="text-[var(--color-text-secondary)]">Názov *</span>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
                placeholder="ICC s.r.o."
                className="mt-1 w-full rounded border border-[var(--color-border-default)] bg-[var(--color-surface)] px-2 py-1.5 text-sm text-[var(--color-text-primary)]"
              />
            </label>
            <label className="block text-xs">
              <span className="text-[var(--color-text-secondary)]">Slug *</span>
              <input
                value={customerSlug}
                onChange={(e) => setCustomerSlug(e.target.value)}
                required
                placeholder="icc"
                className="mt-1 w-full rounded border border-[var(--color-border-default)] bg-[var(--color-surface)] px-2 py-1.5 text-sm font-mono text-[var(--color-text-primary)]"
              />
            </label>
            <label className="block text-xs">
              <span className="text-[var(--color-text-secondary)]">Subdoména</span>
              <input
                value={subdomain}
                onChange={(e) => setSubdomain(e.target.value)}
                placeholder="icc"
                className="mt-1 w-full rounded border border-[var(--color-border-default)] bg-[var(--color-surface)] px-2 py-1.5 text-sm font-mono text-[var(--color-text-primary)]"
              />
            </label>
            <label className="block text-xs">
              <span className="text-[var(--color-text-secondary)]">Secret (per-zákazník)</span>
              <input
                type="password"
                value={secret}
                onChange={(e) => setSecret(e.target.value)}
                autoComplete="new-password"
                placeholder={
                  editingCustomerId
                    ? "ponechaj prázdne = bez zmeny; vyplň = rotuj secret"
                    : "uloží sa do credentials store; nezobrazí sa späť"
                }
                className="mt-1 w-full rounded border border-[var(--color-border-default)] bg-[var(--color-surface)] px-2 py-1.5 text-sm text-[var(--color-text-primary)]"
              />
            </label>
          </div>
          <label className="block text-xs">
            <span className="text-[var(--color-text-secondary)]">Integrácie (JSON, voliteľné)</span>
            <textarea
              value={integrations}
              onChange={(e) => setIntegrations(e.target.value)}
              rows={2}
              placeholder='{"erp": "nex-genesis"}'
              className="mt-1 w-full rounded border border-[var(--color-border-default)] bg-[var(--color-surface)] px-2 py-1.5 font-mono text-xs text-[var(--color-text-primary)]"
            />
          </label>
          <label className="block text-xs">
            <span className="text-[var(--color-text-secondary)]">Poznámka</span>
            <textarea
              lang="sk"
              spellCheck={false}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={2}
              className="mt-1 w-full rounded border border-[var(--color-border-default)] bg-[var(--color-surface)] px-2 py-1.5 text-xs text-[var(--color-text-primary)]"
            />
          </label>

          {formError && (
            <div className="rounded bg-[var(--color-state-error-bg)] px-3 py-2 text-xs text-[var(--color-state-error-fg)]">
              {formError}
            </div>
          )}

          <div className="flex items-center gap-2">
            <button
              type="submit"
              disabled={submitting || !name.trim() || !customerSlug.trim()}
              className="flex items-center gap-1.5 rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-500 disabled:opacity-40"
            >
              {submitting && <Loader2 className="h-3.5 w-3.5 animate-spin" />} Uložiť
            </button>
            <button
              type="button"
              onClick={() => {
                resetForm();
                setShowForm(false);
              }}
              className="rounded-lg border border-[var(--color-border-default)] px-3 py-1.5 text-xs text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
            >
              Zrušiť
            </button>
          </div>
        </form>
      )}

      {/* List */}
      {loading ? (
        <div className="flex items-center gap-2 py-12 text-sm text-[var(--color-text-muted)]">
          <Loader2 className="h-4 w-4 animate-spin" /> Načítavam…
        </div>
      ) : loadError ? (
        <div className="rounded-lg bg-[var(--color-state-error-bg)] px-3 py-2 text-sm text-[var(--color-state-error-fg)]">
          {loadError}
        </div>
      ) : items.length === 0 ? (
        <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-canvas)] p-6 text-center text-sm text-[var(--color-text-muted)]">
          Zatiaľ žiadni zákazníci. Pridaj prvého cez „Pridať zákazníka“.
        </div>
      ) : (
        <div className="divide-y divide-[var(--color-border-default)] overflow-hidden rounded-lg border border-[var(--color-border-default)] bg-[var(--color-canvas)]">
          {items.map((c) => (
            <div key={c.id} className="flex items-center justify-between gap-3 p-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-medium text-[var(--color-text-primary)]">{c.name}</span>
                  <span className="font-mono text-xs text-[var(--color-text-muted)]">{c.slug}</span>
                  {c.has_secret && (
                    <span className="flex items-center gap-1 rounded bg-[var(--color-surface)] px-1.5 py-0.5 text-[10px] text-[var(--color-text-muted)]">
                      <KeyRound className="h-3 w-3" /> secret uložený
                    </span>
                  )}
                </div>
                {c.subdomain && (
                  <div className="mt-0.5 font-mono text-[11px] text-[var(--color-text-muted)]">{c.subdomain}</div>
                )}
              </div>
              <div className="flex flex-shrink-0 items-center gap-1">
                <button
                  onClick={() => startEdit(c)}
                  title="Upraviť zákazníka"
                  className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface)] hover:text-[var(--color-text-primary)]"
                >
                  <Pencil className="h-4 w-4" />
                </button>
                <button
                  onClick={() => handleDelete(c)}
                  title="Odstrániť zákazníka"
                  className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-state-error-bg)] hover:text-[var(--color-state-error-fg)]"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
