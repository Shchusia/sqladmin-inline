# Changelog

## 0.1.0

### Security
- Inline endpoints now go through sqladmin's `login_required`. Previously, with an
  `authentication_backend` configured, they (and the replaced edit route) were reachable
  **without logging in**, including create/update/delete.
- Inline endpoints apply the parent view's `is_accessible`, `can_edit`, `check_can_edit`
  and `form_edit_query`.
- Update/delete/reorder only touch children of the parent in the URL.

### Offline / VPN
- No CDN: SortableJS is bundled and served by the admin app; Font Awesome comes from
  sqladmin's bundled build. Tests fail if any page references an external host.

### Fixes
- Add / Edit / Delete did nothing on sqladmin 0.21-0.27: those versions load Bootstrap 4.6
  after Bootstrap 5, so `window.bootstrap.Modal` has no `getOrCreateInstance`. Modals and
  collapsing are now handled by the package itself (only Bootstrap CSS classes are used),
  so the UI works with any Bootstrap JS on the page. Escape / backdrop click close modals.
- sqladmin 0.21-0.27 threw "Cannot read properties of null (reading 'checked')" on every
  edit/create page (their `data-toggle="buttons"` around submit inputs + Bootstrap 4.6).
  The inline script removes that attribute before Bootstrap's load handler runs; the script
  is now also included on the create page.
- A slow form response no longer overwrites a modal that was closed or re-opened meanwhile.
- Templates rewritten for Bootstrap 5 (Tabler), which sqladmin uses: collapsing,
  modal close buttons and spacing classes now work.
- Header controls (Add, Search, Clear) no longer collapse the section.
- Primary keys and FK select values are converted to the column type (strict drivers
  such as asyncpg rejected the previous string values).
- Sync `sessionmaker` is fully supported (previously several operations returned `None`).
- URLs respect `Admin(base_url=...)` and proxy `root_path` (no hard-coded `/admin`).
- Invalid `?page=` values no longer cause a 500; pages past the end are clamped.
- `%` and `_` in the search box are matched literally; non-string columns are searchable.
- Removed debug `print()` calls.
- Depends on `sqlalchemy[asyncio]`: with SQLAlchemy 2.1 `greenlet` is no longer installed
  by default and sqladmin failed to import in a fresh environment.

### Packaging
- `statics/**/*` added to package data (the wheel previously shipped only templates, so
  bundled JS/CSS would be missing after `pip install`).
- `__version__` is read from the installed package metadata (single source: pyproject).
- pytest-asyncio configured (`asyncio_mode = "auto"`); `aiosqlite`/`fastapi` added to the
  `tests` group; Taskfile `test` no longer wraps pytest-cov in `coverage run`.

### Changes
- With `order_field`, a new row is appended after the last sibling when the order field
  is not part of the form.
- `edit.html`/`create.html` extend sqladmin's own templates (keeps i18n, form media and
  future sqladmin changes).
- sqladmin >= 0.31: uses the `edit_context` hook instead of replacing the edit route.
- `setup_inline_routes()` is idempotent.
- Reorder endpoint moved to `.../{parent_pk}/reorder` and runs in one transaction.
- Saving returns `{"ok": true, "pk": "..."}`; DB errors return 400 with `{"error": ...}`.
- New: `InlineModelAdmin.identity` can be overridden; config is validated at class
  creation (`layout`, `page_size`, `order_field`).
- New helpers: `delete_children()`, `reorder_children()`; `get_by_pk()`, `update_child()`,
  `delete_child()` accept an optional `parent_obj`.
- The inline context no longer contains `form_class` (it scaffolded a form, with DB
  queries, for every section on every page load).
