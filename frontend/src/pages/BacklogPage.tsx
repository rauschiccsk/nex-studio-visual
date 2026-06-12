import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { Plus, Pencil, Check, X, Trash2, Loader2 } from "lucide-react";

import { listProjectsApi } from "@/services/api/projects";
import { listVersions } from "@/services/api/versions";
import {
  listBacklogApi,
  createBacklogApi,
  updateBacklogApi,
  deleteBacklogApi,
} from "@/services/api/backlog";
import type { ProjectRead } from "@/types";
import type { Version } from "@/types/version";
import type {
  BacklogItemRead,
  BacklogPriority,
  BacklogStatus,
} from "@/types/backlog";

type View = "backlog" | "history";

const PRIORITIES: BacklogPriority[] = ["low", "medium", "high", "critical"];

const PRIORITY_CLS: Record<BacklogPriority, string> = {
  low: "bg-slate-700/40 text-slate-400",
  medium: "bg-sky-500/15 text-sky-400",
  high: "bg-amber-500/15 text-amber-400",
  critical: "bg-red-500/15 text-red-400",
};

const STATUS_CLS: Record<BacklogStatus, string> = {
  open: "bg-slate-700/40 text-slate-300",
  included: "bg-indigo-500/15 text-indigo-400",
  realized: "bg-green-500/15 text-green-400",
  rejected: "bg-red-500/10 text-red-500/80",
};

const STATUS_LABEL: Record<BacklogStatus, string> = {
  open: "Otvorené",
  included: "V verzii",
  realized: "Realizované",
  rejected: "Zamietnuté",
};

export default function BacklogPage() {
  const { slug } = useParams<{ slug: string }>();

  const [project, setProject] = useState<ProjectRead | null>(null);
  const [items, setItems] = useState<BacklogItemRead[]>([]);
  const [versions, setVersions] = useState<Version[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [view, setView] = useState<View>("backlog");

  // New-requirement form.
  const [showNew, setShowNew] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [newPriority, setNewPriority] = useState<BacklogPriority>("medium");
  const [creating, setCreating] = useState(false);

  // Per-row interaction.
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [editDesc, setEditDesc] = useState("");
  const [editPriority, setEditPriority] = useState<BacklogPriority>("medium");
  const [assigningId, setAssigningId] = useState<string | null>(null);
  const [assignVersionId, setAssignVersionId] = useState<string>("");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [rowError, setRowError] = useState<Record<string, string>>({});

  function reloadBacklog(projectId: string) {
    return listBacklogApi({ project_id: projectId, limit: 200 }).then((res) =>
      setItems(res.items),
    );
  }

  useEffect(() => {
    if (!slug) return;
    let cancelled = false;
    setLoading(true);
    listProjectsApi({ limit: 100 })
      .then((res) => {
        if (cancelled) return;
        const found = res.items.find((p) => p.slug === slug);
        if (!found) {
          setError("Projekt nebol nájdený.");
          return;
        }
        setProject(found);
        return Promise.all([
          listBacklogApi({ project_id: found.id, limit: 200 }),
          listVersions(found.id),
        ]).then(([backlogRes, vers]) => {
          if (cancelled) return;
          setItems(backlogRes.items);
          setVersions(vers);
        });
      })
      .catch(() => {
        if (!cancelled) setError("Nepodarilo sa načítať backlog.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [slug]);

  const versionNumber = useMemo(() => {
    const m: Record<string, string> = {};
    for (const v of versions) m[v.id] = v.version_number;
    return m;
  }, [versions]);

  // Assignable = unreleased versions (a released version already shipped; realize fires AT release).
  const assignableVersions = useMemo(
    () => versions.filter((v) => v.status !== "released"),
    [versions],
  );

  const backlogItems = items.filter((i) => i.status === "open" || i.status === "included");
  const realizedItems = items.filter((i) => i.status === "realized");

  // realized grouped by version_id (preserve version_number order).
  const historyGroups = useMemo(() => {
    const groups: Record<string, BacklogItemRead[]> = {};
    for (const it of realizedItems) {
      const key = it.version_id ?? "__none__";
      (groups[key] ||= []).push(it);
    }
    return Object.entries(groups).sort(([a], [b]) =>
      (versionNumber[a] ?? "").localeCompare(versionNumber[b] ?? ""),
    );
  }, [realizedItems, versionNumber]);

  async function handleCreate() {
    if (!project || !newTitle.trim()) return;
    setCreating(true);
    try {
      await createBacklogApi({
        project_id: project.id,
        title: newTitle.trim(),
        description: newDesc.trim() || null,
        priority: newPriority,
      });
      await reloadBacklog(project.id);
      setNewTitle("");
      setNewDesc("");
      setNewPriority("medium");
      setShowNew(false);
    } catch {
      setRowError((p) => ({ ...p, __new__: "Nepodarilo sa vytvoriť požiadavku." }));
    } finally {
      setCreating(false);
    }
  }

  function startEdit(it: BacklogItemRead) {
    setEditingId(it.id);
    setEditTitle(it.title);
    setEditDesc(it.description ?? "");
    setEditPriority(it.priority);
    setAssigningId(null);
  }

  async function handleSaveEdit(id: string) {
    if (!project || !editTitle.trim()) return;
    setBusyId(id);
    try {
      await updateBacklogApi(id, {
        title: editTitle.trim(),
        description: editDesc.trim() || null,
        priority: editPriority,
      });
      await reloadBacklog(project.id);
      setEditingId(null);
    } catch {
      setRowError((p) => ({ ...p, [id]: "Uloženie zlyhalo." }));
    } finally {
      setBusyId(null);
    }
  }

  async function handleAssign(id: string) {
    if (!project || !assignVersionId) return;
    setBusyId(id);
    try {
      await updateBacklogApi(id, { version_id: assignVersionId });
      await reloadBacklog(project.id);
      setAssigningId(null);
      setAssignVersionId("");
    } catch {
      setRowError((p) => ({ ...p, [id]: "Priradenie zlyhalo." }));
    } finally {
      setBusyId(null);
    }
  }

  async function handleReject(id: string) {
    if (!project) return;
    setBusyId(id);
    try {
      await updateBacklogApi(id, { status: "rejected" });
      await reloadBacklog(project.id);
    } catch {
      setRowError((p) => ({ ...p, [id]: "Zamietnutie zlyhalo." }));
    } finally {
      setBusyId(null);
    }
  }

  async function handleDelete(id: string) {
    if (!project) return;
    setBusyId(id);
    try {
      await deleteBacklogApi(id);
      await reloadBacklog(project.id);
    } catch {
      setRowError((p) => ({ ...p, [id]: "Zmazanie zlyhalo." }));
    } finally {
      setBusyId(null);
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20 text-slate-500 text-sm gap-2">
        <Loader2 className="w-4 h-4 animate-spin" /> Načítavam…
      </div>
    );
  }

  if (error || !project) {
    return (
      <div className="p-6 max-w-5xl mx-auto">
        <div className="rounded-lg bg-red-500/10 border border-red-500/30 p-4 text-sm text-red-400">
          {error || "Projekt nebol nájdený."}
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 max-w-5xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-1">
        <h1 className="text-base font-bold text-slate-100">Zásobník</h1>
        <button
          onClick={() => setShowNew((v) => !v)}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-white bg-primary-600 hover:bg-primary-500 rounded-lg transition-colors"
        >
          <Plus size={14} /> Nová požiadavka
        </button>
      </div>
      <p className="text-xs text-slate-600 mb-4">
        Budúce zákaznícke požiadavky pre <span className="text-slate-400">{project.name}</span> (REQ-N).
        Priradením k verzii sa stanú jej požiadavkami; po vydaní verzie sa automaticky realizujú.
      </p>

      {/* New-requirement form */}
      {showNew && (
        <div className="rounded-lg border border-slate-700 bg-slate-900 p-4 mb-4 space-y-3">
          <input
            value={newTitle}
            onChange={(e) => setNewTitle(e.target.value)}
            placeholder="Názov požiadavky"
            className="w-full bg-slate-800 border border-slate-700 rounded px-3 py-1.5 text-sm text-slate-100 focus:outline-none focus:border-primary-500"
          />
          <textarea
            value={newDesc}
            onChange={(e) => setNewDesc(e.target.value)}
            placeholder="Popis (voliteľný)"
            rows={2}
            className="w-full bg-slate-800 border border-slate-700 rounded px-3 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-primary-500"
          />
          <div className="flex items-center gap-3">
            <select
              value={newPriority}
              onChange={(e) => setNewPriority(e.target.value as BacklogPriority)}
              className="bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-xs text-slate-100 focus:outline-none focus:border-primary-500"
            >
              {PRIORITIES.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
            <button
              onClick={handleCreate}
              disabled={creating || !newTitle.trim()}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-white bg-primary-600 hover:bg-primary-500 disabled:opacity-40 rounded transition-colors"
            >
              {creating ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />} Vytvoriť
            </button>
            {rowError.__new__ && <span className="text-xs text-red-400">{rowError.__new__}</span>}
          </div>
        </div>
      )}

      {/* View tabs */}
      <div className="flex items-center gap-0 border-b border-slate-800 mb-4">
        {(["backlog", "history"] as View[]).map((t) => (
          <button
            key={t}
            onClick={() => setView(t)}
            className={`px-4 py-2 text-xs font-medium border-b-2 -mb-px transition-colors ${
              view === t
                ? "border-primary-500 text-primary-400"
                : "border-transparent text-slate-500 hover:text-slate-300"
            }`}
          >
            {t === "backlog" ? `Zásobník (${backlogItems.length})` : `História (${realizedItems.length})`}
          </button>
        ))}
      </div>

      {/* Backlog view */}
      {view === "backlog" &&
        (backlogItems.length === 0 ? (
          <div className="rounded-xl border border-dashed border-slate-800 p-10 text-center text-sm text-slate-500">
            Žiadne otvorené požiadavky. Pridaj prvú cez „Nová požiadavka".
          </div>
        ) : (
          <div className="rounded-lg border border-slate-700 bg-slate-900 divide-y divide-slate-800">
            {backlogItems.map((it) => (
              <div key={it.id} className="p-4">
                {editingId === it.id ? (
                  <div className="space-y-2">
                    <input
                      value={editTitle}
                      onChange={(e) => setEditTitle(e.target.value)}
                      className="w-full bg-slate-800 border border-slate-700 rounded px-3 py-1.5 text-sm text-slate-100 focus:outline-none focus:border-primary-500"
                    />
                    <textarea
                      value={editDesc}
                      onChange={(e) => setEditDesc(e.target.value)}
                      rows={2}
                      className="w-full bg-slate-800 border border-slate-700 rounded px-3 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-primary-500"
                    />
                    <div className="flex items-center gap-2">
                      <select
                        value={editPriority}
                        onChange={(e) => setEditPriority(e.target.value as BacklogPriority)}
                        className="bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-xs text-slate-100 focus:outline-none focus:border-primary-500"
                      >
                        {PRIORITIES.map((p) => (
                          <option key={p} value={p}>
                            {p}
                          </option>
                        ))}
                      </select>
                      <button
                        onClick={() => handleSaveEdit(it.id)}
                        disabled={busyId === it.id}
                        className="flex items-center gap-1 px-2.5 py-1.5 text-xs text-white bg-primary-600 hover:bg-primary-500 disabled:opacity-40 rounded"
                      >
                        <Check size={13} /> Uložiť
                      </button>
                      <button
                        onClick={() => setEditingId(null)}
                        className="flex items-center gap-1 px-2.5 py-1.5 text-xs text-slate-300 bg-slate-700 hover:bg-slate-600 rounded"
                      >
                        <X size={13} /> Zrušiť
                      </button>
                    </div>
                  </div>
                ) : (
                  <>
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-xs font-mono text-slate-500">REQ-{it.number}</span>
                          <span className={`text-[10px] px-1.5 py-0.5 rounded ${PRIORITY_CLS[it.priority]}`}>
                            {it.priority}
                          </span>
                          <span className={`text-[10px] px-1.5 py-0.5 rounded ${STATUS_CLS[it.status]}`}>
                            {STATUS_LABEL[it.status]}
                            {it.status === "included" && it.version_id && versionNumber[it.version_id]
                              ? ` · ${versionNumber[it.version_id]}`
                              : ""}
                          </span>
                        </div>
                        <div className="text-sm text-slate-200 mt-1">{it.title}</div>
                        {it.description && (
                          <div className="text-xs text-slate-500 mt-0.5">{it.description}</div>
                        )}
                      </div>
                      <div className="flex items-center gap-1.5 shrink-0">
                        <button
                          onClick={() => {
                            setAssigningId(assigningId === it.id ? null : it.id);
                            setAssignVersionId(it.version_id ?? "");
                            setEditingId(null);
                          }}
                          className="px-2 py-1 text-[11px] text-indigo-400 bg-indigo-500/10 hover:bg-indigo-500/20 rounded"
                        >
                          Priradiť k verzii
                        </button>
                        <button
                          onClick={() => startEdit(it)}
                          title="Upraviť"
                          className="p-1.5 text-slate-400 hover:text-slate-200 hover:bg-slate-800 rounded"
                        >
                          <Pencil size={13} />
                        </button>
                        <button
                          onClick={() => handleReject(it.id)}
                          disabled={busyId === it.id}
                          title="Zamietnuť"
                          className="px-2 py-1 text-[11px] text-red-400 bg-red-500/10 hover:bg-red-500/20 disabled:opacity-40 rounded"
                        >
                          Zamietnuť
                        </button>
                        {it.status === "open" && (
                          <button
                            onClick={() => handleDelete(it.id)}
                            disabled={busyId === it.id}
                            title="Zmazať"
                            className="p-1.5 text-slate-500 hover:text-red-400 hover:bg-slate-800 disabled:opacity-40 rounded"
                          >
                            <Trash2 size={13} />
                          </button>
                        )}
                      </div>
                    </div>
                    {assigningId === it.id && (
                      <div className="flex items-center gap-2 mt-2">
                        <select
                          value={assignVersionId}
                          onChange={(e) => setAssignVersionId(e.target.value)}
                          className="bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-xs text-slate-100 focus:outline-none focus:border-primary-500"
                        >
                          <option value="">— vyber verziu —</option>
                          {assignableVersions.map((v) => (
                            <option key={v.id} value={v.id}>
                              {v.version_number} ({v.status})
                            </option>
                          ))}
                        </select>
                        <button
                          onClick={() => handleAssign(it.id)}
                          disabled={busyId === it.id || !assignVersionId}
                          className="flex items-center gap-1 px-2.5 py-1.5 text-xs text-white bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 rounded"
                        >
                          <Check size={13} /> Priradiť
                        </button>
                      </div>
                    )}
                    {rowError[it.id] && <div className="text-xs text-red-400 mt-1">{rowError[it.id]}</div>}
                  </>
                )}
              </div>
            ))}
          </div>
        ))}

      {/* História view */}
      {view === "history" &&
        (realizedItems.length === 0 ? (
          <div className="rounded-xl border border-dashed border-slate-800 p-10 text-center text-sm text-slate-500">
            Zatiaľ nič realizované. Požiadavky sa realizujú po vydaní verzie, ku ktorej sú priradené.
          </div>
        ) : (
          <div className="space-y-4">
            {historyGroups.map(([versionId, group]) => (
              <div key={versionId} className="rounded-lg border border-slate-800 bg-slate-900">
                <div className="px-4 py-2 border-b border-slate-800 text-xs font-semibold text-green-400">
                  {versionNumber[versionId] ?? "Neznáma verzia"}
                  <span className="text-slate-600 font-normal"> · {group.length} realizovaných</span>
                </div>
                <div className="divide-y divide-slate-800">
                  {group.map((it) => (
                    <div key={it.id} className="px-4 py-2.5 flex items-center gap-2">
                      <span className="text-xs font-mono text-slate-500">REQ-{it.number}</span>
                      <span className="text-sm text-slate-300">{it.title}</span>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        ))}
    </div>
  );
}
