/**
 * User admin page — list, detail, create and edit.
 *
 * Wires the Feat 6 User CRUD surface against the backend REST router
 * mounted at ``/api/v1/users`` (see ``backend/api/routes/users.py``).
 * The page is self-contained: it owns its own local state rather than
 * reaching for a Zustand store because DESIGN.md § 3.3 does not define
 * a dedicated ``userStore`` — the ``authStore`` only tracks the
 * currently authenticated user, not the full user registry.  When a
 * global store is added in a later feat this page can switch over
 * without changing its visible surface.
 *
 * User flow (single-page, four modes):
 *
 *   - ``list``   — paginated table with role + active filters, plus
 *     row-level "View", "Edit" and "Delete" actions.
 *   - ``detail`` — read-only view of a single user.  The
 *     ``password_hash`` field is masked to avoid leaking the stored
 *     bcrypt digest through the UI.
 *   - ``create`` — form that ``POST``s a new user.
 *   - ``edit``   — form that ``PATCH``es the mutable fields of an
 *     existing user.  The password hash field is optional on edit; an
 *     empty value means "do not change the password".
 *
 * All network errors are surfaced inline via the ``ApiError.message``
 * propagated from ``services/api.ts``.
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../services/api";
import type {
  PaginatedResponse,
  UserCreate,
  UserRead,
  UserRole,
  UserUpdate,
} from "../types";

/** REST prefix for the User router (see backend/main.py). */
const ENDPOINT = "/users";

/** Page size used by the list view.  Matches the backend default. */
const PAGE_SIZE = 20;

/** Finite mode state keeps the render logic explicit and linter-friendly. */
type Mode =
  | { kind: "list" }
  | { kind: "detail"; id: string }
  | { kind: "create" }
  | { kind: "edit"; id: string };

/** Shape of the mutable fields in the create / edit forms. */
interface UserFormState {
  username: string;
  email: string;
  password_hash: string;
  role: UserRole;
  is_active: boolean;
}

/** Tri-state filter for the ``is_active`` flag on the list view. */
type ActiveFilter = "" | "true" | "false";

/** Selectable roles; mirrors the ``UserRole`` literal union. */
const ROLE_OPTIONS: readonly UserRole[] = ["ri", "ha", "shu"] as const;

/** Human-readable label for each role value. */
const ROLE_LABELS: Record<UserRole, string> = {
  ri: "ri — Director / Senior",
  ha: "ha — Medior",
  shu: "shu — Junior",
};

/** Fresh-form defaults for the create mode. */
const EMPTY_FORM: UserFormState = {
  username: "",
  email: "",
  password_hash: "",
  role: "shu",
  is_active: true,
};

/** Tailwind helper for role pills. */
function roleBadgeClass(role: UserRole): string {
  switch (role) {
    case "ri":
      return "bg-indigo-100 text-indigo-800";
    case "ha":
      return "bg-sky-100 text-sky-800";
    case "shu":
      return "bg-gray-100 text-gray-800";
  }
}

/** Tailwind helper for the active / inactive pill. */
function activeBadgeClass(isActive: boolean): string {
  return isActive
    ? "bg-emerald-100 text-emerald-800"
    : "bg-amber-100 text-amber-800";
}

/** Format an ISO timestamp as a locale date-time string, tolerant of bad input. */
function formatTimestamp(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) {
    return iso;
  }
  return parsed.toLocaleString();
}

function UserPage() {
  // ------------------------------------------------------------------ state
  const [mode, setMode] = useState<Mode>({ kind: "list" });

  const [items, setItems] = useState<UserRead[]>([]);
  const [total, setTotal] = useState(0);
  const [skip, setSkip] = useState(0);
  const [roleFilter, setRoleFilter] = useState<UserRole | "">("");
  const [activeFilter, setActiveFilter] = useState<ActiveFilter>("");

  const [detail, setDetail] = useState<UserRead | null>(null);
  const [form, setForm] = useState<UserFormState>(EMPTY_FORM);

  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // --------------------------------------------------------------- fetchers
  const loadList = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<PaginatedResponse<UserRead>>(ENDPOINT, {
        params: {
          skip,
          limit: PAGE_SIZE,
          role: roleFilter || undefined,
          is_active:
            activeFilter === "" ? undefined : activeFilter === "true",
        },
      });
      setItems(response.items);
      setTotal(response.total);
    } catch (exc) {
      const message =
        exc instanceof ApiError ? exc.message : "Failed to load users.";
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }, [skip, roleFilter, activeFilter]);

  const loadDetail = useCallback(async (id: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<UserRead>(`${ENDPOINT}/${id}`);
      setDetail(response);
    } catch (exc) {
      const message =
        exc instanceof ApiError ? exc.message : "Failed to load user.";
      setError(message);
      setDetail(null);
    } finally {
      setIsLoading(false);
    }
  }, []);

  // -------------------------------------------------------------- effects
  useEffect(() => {
    if (mode.kind === "list") {
      void loadList();
    }
  }, [mode, loadList]);

  useEffect(() => {
    if (mode.kind === "detail") {
      void loadDetail(mode.id);
    }
  }, [mode, loadDetail]);

  useEffect(() => {
    // Seed the edit form with the current row whenever edit mode opens.
    if (mode.kind !== "edit") {
      return;
    }
    let cancelled = false;
    (async () => {
      setIsLoading(true);
      setError(null);
      try {
        const row = await api.get<UserRead>(`${ENDPOINT}/${mode.id}`);
        if (cancelled) {
          return;
        }
        setForm({
          username: row.username,
          email: row.email,
          // Never pre-fill the existing hash — an empty value on PATCH
          // means "leave password unchanged".
          password_hash: "",
          role: row.role,
          is_active: row.is_active,
        });
      } catch (exc) {
        if (cancelled) {
          return;
        }
        const message =
          exc instanceof ApiError ? exc.message : "Failed to load user.";
        setError(message);
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [mode]);

  // ------------------------------------------------------------- handlers
  const openList = () => {
    setDetail(null);
    setForm(EMPTY_FORM);
    setError(null);
    setMode({ kind: "list" });
  };

  const openCreate = () => {
    setForm(EMPTY_FORM);
    setError(null);
    setMode({ kind: "create" });
  };

  const openDetail = (id: string) => {
    setError(null);
    setMode({ kind: "detail", id });
  };

  const openEdit = (id: string) => {
    setError(null);
    setMode({ kind: "edit", id });
  };

  const handleDelete = async (id: string) => {
    if (
      !window.confirm(
        "Delete this user? Prefer deactivating via Edit → Active = false unless the user has no references.",
      )
    ) {
      return;
    }
    setError(null);
    try {
      await api.delete(`${ENDPOINT}/${id}`);
      await loadList();
    } catch (exc) {
      const message =
        exc instanceof ApiError ? exc.message : "Failed to delete user.";
      setError(message);
    }
  };

  const handleCreate = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setIsSaving(true);
    setError(null);
    try {
      const payload: UserCreate = {
        username: form.username.trim(),
        email: form.email.trim(),
        password_hash: form.password_hash.trim(),
        role: form.role,
        is_active: form.is_active,
      };
      await api.post<UserRead>(ENDPOINT, payload);
      setSkip(0);
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError ? exc.message : "Failed to create user.";
      setError(message);
    } finally {
      setIsSaving(false);
    }
  };

  const handleUpdate = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (mode.kind !== "edit") {
      return;
    }
    setIsSaving(true);
    setError(null);
    try {
      // PATCH-style payload: only include fields the operator changed.
      // The password hash is omitted unless the operator typed a value —
      // the backend's ``UserUpdate`` schema treats missing fields as "no
      // change" per DESIGN.md § 3.4.
      const payload: UserUpdate = {
        username: form.username.trim(),
        email: form.email.trim(),
        role: form.role,
        is_active: form.is_active,
      };
      const trimmedHash = form.password_hash.trim();
      if (trimmedHash.length > 0) {
        payload.password_hash = trimmedHash;
      }
      await api.patch<UserRead>(`${ENDPOINT}/${mode.id}`, payload);
      openList();
    } catch (exc) {
      const message =
        exc instanceof ApiError ? exc.message : "Failed to update user.";
      setError(message);
    } finally {
      setIsSaving(false);
    }
  };

  // ---------------------------------------------------------- derived data
  const totalPages = useMemo(() => {
    if (total === 0) {
      return 1;
    }
    return Math.max(1, Math.ceil(total / PAGE_SIZE));
  }, [total]);
  const currentPage = Math.floor(skip / PAGE_SIZE) + 1;

  // ---------------------------------------------------------------- render
  return (
    <section className="space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-xl font-semibold text-gray-900">Users</h2>
          <p className="text-sm text-gray-600">
            System-wide user registry — roles and activation state drive
            authentication and project membership.
          </p>
        </div>
        {mode.kind === "list" && (
          <button
            type="button"
            className="btn-primary"
            onClick={openCreate}
            aria-label="Create new user"
          >
            New User
          </button>
        )}
      </header>

      {error && (
        <div
          role="alert"
          className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800"
        >
          {error}
        </div>
      )}

      {mode.kind === "list" && (
        <UserList
          items={items}
          total={total}
          isLoading={isLoading}
          roleFilter={roleFilter}
          onRoleFilterChange={(value) => {
            setSkip(0);
            setRoleFilter(value);
          }}
          activeFilter={activeFilter}
          onActiveFilterChange={(value) => {
            setSkip(0);
            setActiveFilter(value);
          }}
          currentPage={currentPage}
          totalPages={totalPages}
          onPreviousPage={() => setSkip(Math.max(0, skip - PAGE_SIZE))}
          onNextPage={() => {
            if (skip + PAGE_SIZE < total) {
              setSkip(skip + PAGE_SIZE);
            }
          }}
          onView={openDetail}
          onEdit={openEdit}
          onDelete={handleDelete}
        />
      )}

      {mode.kind === "detail" && (
        <UserDetail
          user={detail}
          isLoading={isLoading}
          onBack={openList}
          onEdit={() => openEdit(mode.id)}
        />
      )}

      {(mode.kind === "create" || mode.kind === "edit") && (
        <UserForm
          form={form}
          mode={mode.kind}
          isSaving={isSaving}
          isLoading={isLoading && mode.kind === "edit"}
          onChange={setForm}
          onCancel={openList}
          onSubmit={mode.kind === "create" ? handleCreate : handleUpdate}
        />
      )}
    </section>
  );
}

/* -------------------------------------------------------------------------- */
/*                              Sub-components                                */
/* -------------------------------------------------------------------------- */

interface UserListProps {
  items: UserRead[];
  total: number;
  isLoading: boolean;
  roleFilter: UserRole | "";
  onRoleFilterChange: (value: UserRole | "") => void;
  activeFilter: ActiveFilter;
  onActiveFilterChange: (value: ActiveFilter) => void;
  currentPage: number;
  totalPages: number;
  onPreviousPage: () => void;
  onNextPage: () => void;
  onView: (id: string) => void;
  onEdit: (id: string) => void;
  onDelete: (id: string) => void;
}

function UserList({
  items,
  total,
  isLoading,
  roleFilter,
  onRoleFilterChange,
  activeFilter,
  onActiveFilterChange,
  currentPage,
  totalPages,
  onPreviousPage,
  onNextPage,
  onView,
  onEdit,
  onDelete,
}: UserListProps) {
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <label
          htmlFor="role-filter"
          className="text-sm font-medium text-gray-700"
        >
          Role:
        </label>
        <select
          id="role-filter"
          value={roleFilter}
          onChange={(event) =>
            onRoleFilterChange(event.target.value as UserRole | "")
          }
          className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm"
        >
          <option value="">All</option>
          {ROLE_OPTIONS.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>

        <label
          htmlFor="active-filter"
          className="text-sm font-medium text-gray-700"
        >
          Active:
        </label>
        <select
          id="active-filter"
          value={activeFilter}
          onChange={(event) =>
            onActiveFilterChange(event.target.value as ActiveFilter)
          }
          className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm"
        >
          <option value="">Any</option>
          <option value="true">Active only</option>
          <option value="false">Inactive only</option>
        </select>

        <span className="ml-auto text-xs text-gray-500">
          {total} user{total === 1 ? "" : "s"} total
        </span>
      </div>

      <div className="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Username
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Email
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Role
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Active
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Created
              </th>
              <th
                scope="col"
                className="px-4 py-2 text-right text-xs font-semibold uppercase tracking-wide text-gray-600"
              >
                Actions
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200">
            {isLoading && (
              <tr>
                <td
                  colSpan={6}
                  className="px-4 py-6 text-center text-sm text-gray-500"
                >
                  Loading users…
                </td>
              </tr>
            )}
            {!isLoading && items.length === 0 && (
              <tr>
                <td
                  colSpan={6}
                  className="px-4 py-6 text-center text-sm text-gray-500"
                >
                  No users match the current filter.
                </td>
              </tr>
            )}
            {!isLoading &&
              items.map((item) => (
                <tr key={item.id} className="hover:bg-gray-50">
                  <td className="px-4 py-2 text-sm font-medium text-gray-900">
                    {item.username}
                  </td>
                  <td className="px-4 py-2 text-sm text-gray-700">
                    {item.email}
                  </td>
                  <td className="px-4 py-2">
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${roleBadgeClass(item.role)}`}
                    >
                      {item.role}
                    </span>
                  </td>
                  <td className="px-4 py-2">
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${activeBadgeClass(item.is_active)}`}
                    >
                      {item.is_active ? "active" : "inactive"}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-xs text-gray-500">
                    {formatTimestamp(item.created_at)}
                  </td>
                  <td className="px-4 py-2 text-right text-sm">
                    <div className="flex justify-end gap-2">
                      <button
                        type="button"
                        className="text-primary-700 hover:underline"
                        onClick={() => onView(item.id)}
                      >
                        View
                      </button>
                      <button
                        type="button"
                        className="text-primary-700 hover:underline"
                        onClick={() => onEdit(item.id)}
                      >
                        Edit
                      </button>
                      <button
                        type="button"
                        className="text-red-700 hover:underline"
                        onClick={() => onDelete(item.id)}
                      >
                        Delete
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between text-sm text-gray-600">
        <span>
          Page {currentPage} of {totalPages}
        </span>
        <div className="flex gap-2">
          <button
            type="button"
            className="btn-secondary"
            onClick={onPreviousPage}
            disabled={currentPage <= 1 || isLoading}
          >
            Previous
          </button>
          <button
            type="button"
            className="btn-secondary"
            onClick={onNextPage}
            disabled={currentPage >= totalPages || isLoading}
          >
            Next
          </button>
        </div>
      </div>
    </div>
  );
}

interface UserDetailProps {
  user: UserRead | null;
  isLoading: boolean;
  onBack: () => void;
  onEdit: () => void;
}

function UserDetail({ user, isLoading, onBack, onEdit }: UserDetailProps) {
  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white p-6 text-sm text-gray-600">
        Loading user…
      </div>
    );
  }
  if (!user) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-gray-600">User not found.</p>
        <button type="button" className="btn-secondary" onClick={onBack}>
          Back to list
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-4 rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
      <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            ID
          </dt>
          <dd className="break-all font-mono text-sm text-gray-900">
            {user.id}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Role
          </dt>
          <dd>
            <span
              className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${roleBadgeClass(user.role)}`}
            >
              {ROLE_LABELS[user.role]}
            </span>
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Username
          </dt>
          <dd className="text-sm text-gray-900">{user.username}</dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Email
          </dt>
          <dd className="break-all text-sm text-gray-900">{user.email}</dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Active
          </dt>
          <dd>
            <span
              className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${activeBadgeClass(user.is_active)}`}
            >
              {user.is_active ? "active" : "inactive"}
            </span>
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Password hash
          </dt>
          <dd className="font-mono text-xs text-gray-500">
            {/* Masked deliberately — the bcrypt digest is present on the
                API response but must not surface in the UI. */}
            •••••••••••• (hidden)
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Created at
          </dt>
          <dd className="text-sm text-gray-900">
            {formatTimestamp(user.created_at)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Updated at
          </dt>
          <dd className="text-sm text-gray-900">
            {formatTimestamp(user.updated_at)}
          </dd>
        </div>
      </dl>

      <div className="flex gap-2 pt-2">
        <button type="button" className="btn-primary" onClick={onEdit}>
          Edit
        </button>
        <button type="button" className="btn-secondary" onClick={onBack}>
          Back to list
        </button>
      </div>
    </div>
  );
}

interface UserFormProps {
  form: UserFormState;
  mode: "create" | "edit";
  isSaving: boolean;
  isLoading: boolean;
  onChange: (form: UserFormState) => void;
  onCancel: () => void;
  onSubmit: (event: React.FormEvent<HTMLFormElement>) => void;
}

function UserForm({
  form,
  mode,
  isSaving,
  isLoading,
  onChange,
  onCancel,
  onSubmit,
}: UserFormProps) {
  const isEdit = mode === "edit";
  const patch = (fragment: Partial<UserFormState>) =>
    onChange({ ...form, ...fragment });

  if (isLoading) {
    return (
      <div className="rounded-md border border-gray-200 bg-white p-6 text-sm text-gray-600">
        Loading user…
      </div>
    );
  }

  return (
    <form
      onSubmit={onSubmit}
      className="space-y-4 rounded-lg border border-gray-200 bg-white p-6 shadow-sm"
    >
      <h3 className="text-lg font-semibold text-gray-900">
        {isEdit ? "Edit user" : "Create user"}
      </h3>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <label
            htmlFor="username"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Username
          </label>
          <input
            id="username"
            type="text"
            value={form.username}
            onChange={(event) => patch({ username: event.target.value })}
            required
            minLength={1}
            maxLength={50}
            placeholder="e.g. zoltan"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div>
          <label
            htmlFor="email"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Email
          </label>
          <input
            id="email"
            type="email"
            value={form.email}
            onChange={(event) => patch({ email: event.target.value })}
            required
            minLength={1}
            maxLength={255}
            placeholder="e.g. zoltan@isnex.ai"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div className="sm:col-span-2">
          <label
            htmlFor="password_hash"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Password hash
            <span className="ml-1 text-xs font-normal text-gray-500">
              (bcrypt digest, e.g. ``$2b$12$…``
              {isEdit ? " — leave blank to keep current password" : ""})
            </span>
          </label>
          <input
            id="password_hash"
            type="text"
            value={form.password_hash}
            onChange={(event) => patch({ password_hash: event.target.value })}
            required={!isEdit}
            minLength={isEdit ? 0 : 1}
            maxLength={255}
            placeholder="$2b$12$…"
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 font-mono text-xs text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          />
        </div>

        <div>
          <label
            htmlFor="role"
            className="mb-1 block text-sm font-medium text-gray-700"
          >
            Role
          </label>
          <select
            id="role"
            value={form.role}
            onChange={(event) =>
              patch({ role: event.target.value as UserRole })
            }
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-primary-500 focus:ring-primary-500"
          >
            {ROLE_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {ROLE_LABELS[option]}
              </option>
            ))}
          </select>
        </div>

        <div className="flex items-end">
          <label
            htmlFor="is_active"
            className="inline-flex items-center gap-2 text-sm font-medium text-gray-700"
          >
            <input
              id="is_active"
              type="checkbox"
              checked={form.is_active}
              onChange={(event) => patch({ is_active: event.target.checked })}
              className="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
            />
            Active
            <span className="text-xs font-normal text-gray-500">
              (inactive users cannot authenticate)
            </span>
          </label>
        </div>
      </div>

      <div className="flex justify-end gap-2 pt-2">
        <button
          type="button"
          className="btn-secondary"
          onClick={onCancel}
          disabled={isSaving}
        >
          Cancel
        </button>
        <button type="submit" className="btn-primary" disabled={isSaving}>
          {isSaving ? "Saving…" : isEdit ? "Save changes" : "Create"}
        </button>
      </div>
    </form>
  );
}

export default UserPage;
