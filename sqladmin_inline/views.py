"""
sqladmin_inline.views
~~~~~~~~~~~~~~~~~~~~~

:class:`ModelViewWithInlines` and the HTTP endpoints used by inline sections.

Endpoints (relative to the admin mount point, e.g. ``/admin``)::

    GET    /{identity}/inline/{inline}/{parent_pk}/list     table fragment
    GET    /{identity}/inline/{inline}/{parent_pk}/form     add/edit form fragment
    POST   /{identity}/inline/{inline}/{parent_pk}/save     create or update
    DELETE /{identity}/inline/{inline}/{parent_pk}/delete   bulk delete
    POST   /{identity}/inline/{inline}/{parent_pk}/reorder  drag-and-drop order
    GET    /_inline/static/...                              bundled JS/CSS

Every endpoint goes through sqladmin's own ``login_required`` and the parent
view's ``is_accessible`` / ``can_edit`` / ``check_can_edit`` checks, exactly
like sqladmin's edit page.  No asset is loaded from the internet.
"""

from __future__ import annotations

from collections.abc import Sequence
import inspect
import json
import logging
import pathlib
import re
from typing import Any, ClassVar
from urllib.parse import quote

from jinja2 import ChoiceLoader, FileSystemLoader
from sqladmin import ModelView
from sqladmin.authentication import login_required
from sqladmin.helpers import get_object_identifier
from sqlalchemy import inspect as sa_inspect
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from sqladmin_inline.inline import InlineModelAdmin, InlinePage

logger = logging.getLogger(__name__)

PACKAGE_DIR = pathlib.Path(__file__).parent
TEMPLATES_DIR = PACKAGE_DIR / "templates"
STATICS_DIR = PACKAGE_DIR / "statics"
STATICS_ROUTE_NAME = "inline_statics"
STATICS_PATH = "/_inline/static"
STATE_ATTR = "sqladmin_inline_contexts"
_INSTALLED_ATTR = "_sqladmin_inline_installed"

# sqladmin >= 0.31 calls ModelView.edit_context(); older versions need the
# edit route to be wrapped instead.
HAS_CONTEXT_HOOKS = hasattr(ModelView, "edit_context")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _int_param(value: Any, default: int = 1) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


def _error(message: str, status_code: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status_code)


def _admin_root(request: Request) -> str:
    """Path prefix of the admin app (``/admin``, ``/proxy/admin`` ...).

    Paths (not absolute URLs) are used so the page keeps working behind
    reverse proxies / VPN gateways that rewrite host or scheme.
    """
    return str(request.scope.get("root_path", "")).rstrip("/")


def _prefix(inline_cls: type[InlineModelAdmin]) -> str:
    """Safe HTML id prefix for an inline section."""
    return re.sub(r"[^a-z0-9]+", "_", inline_cls.model.__name__.lower()).strip("_")


def _encode_parent_pk(obj: Any) -> str:
    """Encode the parent PK exactly like sqladmin does in its own URLs."""
    return str(get_object_identifier(obj))


def _inline_base_url(
    request: Request, parent_identity: str, inline_identity: str, parent_pk: str
) -> str:
    return (
        f"{_admin_root(request)}/{parent_identity}/inline/{inline_identity}/"
        f"{quote(parent_pk, safe='')}"
    )


async def _get_parent_by_pk(
    view: Any, pk_str: str, request: Request | None = None
) -> Any | None:
    """Load the parent through the view's own ``form_edit_query``.

    Custom filters a project puts into ``form_edit_query`` (multi-tenancy,
    soft-delete, ...) therefore apply to inline endpoints as well.
    """
    scope: dict[str, Any] = (
        dict(request.scope)
        if request is not None
        else {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "query_string": b"",
        }
    )
    scope["path_params"] = {**scope.get("path_params", {}), "pk": pk_str}
    sub_request = Request(scope)
    try:
        return await view.get_object_for_edit(sub_request)
    except (ValueError, TypeError, LookupError):
        return None


def _form_relationships(inline_cls: type[InlineModelAdmin], form: Any) -> list[str]:
    mapper = sa_inspect(inline_cls.model)
    return [f.name for f in form if f.name in mapper.relationships]


def _normalize(admin: Any, obj: Any) -> dict[str, Any]:
    fn = getattr(admin, "_normalize_wtform_data", None)
    return fn(obj) if fn else {}


def _denormalize(admin: Any, data: dict[str, Any], obj: Any) -> dict[str, Any]:
    fn = getattr(admin, "_denormalize_wtform_data", None)
    return fn(data, obj) if fn else dict(data)


async def build_inline_context(
    view: Any,
    inline_cls: type[InlineModelAdmin],
    request: Request,
    parent_obj: Any | None,
    *,
    page: int | None = None,
    search: str | None = None,
) -> dict[str, Any]:
    """Template context for one inline section."""
    if page is None:
        page = _int_param(request.query_params.get(f"_il_{inline_cls.identity}_page"))
    if search is None:
        search = request.query_params.get(f"_il_{inline_cls.identity}_search", "")

    if parent_obj is not None:
        pagination = await inline_cls.get_page(
            view.session_maker, parent_obj, page=page, search=search
        )
        parent_pk = _encode_parent_pk(parent_obj)
    else:
        pagination = InlinePage(
            rows=[], page=1, page_size=inline_cls.page_size, count=0
        )
        parent_pk = ""

    display_cols = inline_cls._display_columns()
    base_url = _inline_base_url(request, view.identity, inline_cls.identity, parent_pk)
    return {
        "inline_cls": inline_cls,
        "identity": inline_cls.identity,
        "parent_identity": view.identity,
        "prefix": _prefix(inline_cls),
        "label": inline_cls.inline_label,
        "icon": inline_cls.icon,
        "layout": inline_cls.layout,
        "display_columns": display_cols,
        "column_labels": {c: inline_cls._get_label(c) for c in display_cols},
        "pagination": pagination,
        "search": search,
        "search_enabled": bool(inline_cls._search_columns()),
        "can_create": inline_cls.can_create,
        "can_edit": inline_cls.can_edit,
        "can_delete": inline_cls.can_delete,
        "order_field": inline_cls.order_field,
        "sortable": bool(inline_cls.order_field) and inline_cls.can_edit,
        "column_default_sort": inline_cls.column_default_sort,
        "parent_pk": parent_pk,
        "base_url": base_url,
    }


# ---------------------------------------------------------------------------
# ModelViewWithInlines
# ---------------------------------------------------------------------------


class ModelViewWithInlines(ModelView):
    """``sqladmin.ModelView`` with Django-style inline sections on the edit page.

    Attributes:
        inlines: ``InlineModelAdmin`` subclasses to show on the edit page.
    """

    inlines: ClassVar[Sequence[type[InlineModelAdmin]]] = []

    create_template: ClassVar[str] = "sqladmin_inline/create.html"
    edit_template: ClassVar[str] = "sqladmin_inline/edit.html"

    def _inline_relationship_names(self) -> list[str]:
        """To-many relationships of the parent that are managed by inlines."""
        if not self.inlines:
            return []
        try:
            mapper: Any = sa_inspect(self.model)
        except Exception:  # pragma: no cover - model is validated by sqladmin
            return []
        inline_models = {il.model for il in self.inlines}
        return [
            rel.key
            for rel in mapper.relationships
            if rel.direction.name in ("ONETOMANY", "MANYTOMANY")
            and rel.mapper.class_ in inline_models
        ]

    def get_form_columns(self) -> list[str]:
        """Form columns without the relationships edited through inlines."""
        excluded = set(self._inline_relationship_names())
        return [c for c in super().get_form_columns() if c not in excluded]

    def find_inline(self, inline_identity: str) -> type[InlineModelAdmin] | None:
        """Return the inline class registered under ``inline_identity``."""
        for inline_cls in self.inlines:
            if inline_cls.identity == inline_identity:
                return inline_cls
        return None

    async def _build_inline_contexts(
        self, request: Request, parent_obj: Any | None = None
    ) -> list[dict[str, Any]]:
        """Template contexts for all inlines (empty pages when ``parent_obj`` is None)."""
        return [
            await build_inline_context(self, inline_cls, request, parent_obj)
            for inline_cls in self.inlines
        ]

    async def edit_context(self, request: Request) -> dict[str, Any]:
        """sqladmin >= 0.31 hook: add ``inline_contexts`` to the edit page."""
        parent_hook = getattr(super(), "edit_context", None)
        context = dict(await parent_hook(request)) if parent_hook else {}
        if not self.inlines:
            return context
        parent_obj = await self.get_object_for_edit(request)
        if parent_obj is not None:
            context["inline_contexts"] = await self._build_inline_contexts(
                request, parent_obj
            )
        return context


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def setup_inline_routes(admin: Any) -> None:
    """Register inline endpoints, bundled static files and templates.

    Call once after creating the ``Admin``::

        admin = Admin(app, engine)
        setup_inline_routes(admin)
        admin.add_view(PostAdmin)

    Calling it again on the same ``Admin`` is a no-op.
    """
    if getattr(admin, _INSTALLED_ATTR, False):
        return

    # Templates ---------------------------------------------------------------
    env = admin.templates.env
    loader = env.loader
    loaders = list(loader.loaders) if isinstance(loader, ChoiceLoader) else [loader]
    env.loader = ChoiceLoader([FileSystemLoader(str(TEMPLATES_DIR)), *loaders])

    # Helpers -----------------------------------------------------------------

    def _protect(handler: Any) -> Any:
        """Run ``handler`` behind sqladmin's authentication (login_required)."""

        @login_required
        async def _guarded(_admin: Any, request: Request) -> Response:
            return await handler(request)  # type: ignore[no-any-return]

        async def endpoint(request: Request) -> Response:
            return await _guarded(admin, request)  # type: ignore[no-any-return]

        endpoint.__name__ = handler.__name__
        endpoint.__doc__ = handler.__doc__
        return endpoint

    async def _resolve(
        request: Request,
    ) -> tuple[Any, type[InlineModelAdmin], Any] | Response:
        """Find view, inline and parent; enforce the parent's edit permissions."""
        identity = request.path_params["identity"]
        try:
            view = admin._find_model_view(identity)
        except HTTPException:
            return _error("view not found", 404)

        if not await _maybe_await(view.is_accessible(request)):
            return _error("forbidden", 403)
        if not getattr(view, "can_edit", True):
            return _error("editing the parent is not allowed", 403)

        find = getattr(view, "find_inline", None)
        inline_cls = find(request.path_params["inline_identity"]) if find else None
        if inline_cls is None:
            return _error("inline not found", 404)

        parent_obj = await _get_parent_by_pk(
            view, request.path_params["parent_pk"], request
        )
        if parent_obj is None:
            return _error("parent not found", 404)

        check = getattr(view, "check_can_edit", None)
        if check is not None and not await _maybe_await(check(request, parent_obj)):
            return _error("forbidden", 403)
        return view, inline_cls, parent_obj

    async def _render_form(
        request: Request,
        view: Any,
        inline_cls: type[InlineModelAdmin],
        form: Any,
        child_pk: str,
        status_code: int = 200,
    ) -> Response:
        parent_pk = request.path_params["parent_pk"]
        ctx = {
            "form": form,
            "inline_cls": inline_cls,
            "parent_identity": view.identity,
            "inline_identity": inline_cls.identity,
            "parent_pk": parent_pk,
            "child_pk": child_pk,
            "label": inline_cls.inline_label,
            "is_edit": bool(child_pk),
            "base_url": _inline_base_url(
                request, view.identity, inline_cls.identity, parent_pk
            ),
        }
        return await _maybe_await(  # type: ignore[no-any-return]
            admin.templates.TemplateResponse(
                request,
                "sqladmin_inline/_inline_form.html",
                ctx,
                status_code=status_code,
            )
        )

    # Handlers ----------------------------------------------------------------

    async def inline_list(request: Request) -> Response:
        """GET: the inline table (one card) as an HTML fragment."""
        resolved = await _resolve(request)
        if isinstance(resolved, Response):
            return resolved
        view, inline_cls, parent_obj = resolved
        ctx = await build_inline_context(
            view,
            inline_cls,
            request,
            parent_obj,
            page=_int_param(request.query_params.get("page")),
            search=request.query_params.get("search", ""),
        )
        return await _maybe_await(  # type: ignore[no-any-return]
            admin.templates.TemplateResponse(
                request, "sqladmin_inline/_inline_table.html", {"ctx": ctx}
            )
        )

    async def inline_form(request: Request) -> Response:
        """GET: add (no ``pk``) or edit (``?pk=``) form as an HTML fragment."""
        resolved = await _resolve(request)
        if isinstance(resolved, Response):
            return resolved
        view, inline_cls, parent_obj = resolved
        child_pk = request.query_params.get("pk", "")

        if child_pk and not inline_cls.can_edit:
            return _error("editing is not allowed", 403)
        if not child_pk and not inline_cls.can_create:
            return _error("creating is not allowed", 403)

        FormClass = await inline_cls.scaffold_form(view.session_maker)
        if child_pk:
            rels = _form_relationships(inline_cls, FormClass())
            obj = await inline_cls.get_by_pk(
                view.session_maker, child_pk, parent_obj, load=rels
            )
            if obj is None:
                return _error("record not found", 404)
            form = FormClass(obj=obj, data=_normalize(admin, obj))
        else:
            form = FormClass()
            # Pre-select the parent if the form exposes the parent relationship.
            for key in inline_cls._parent_keys(type(parent_obj)):
                if key in form and key in sa_inspect(inline_cls.model).relationships:
                    form[key].data = parent_obj
        return await _render_form(request, view, inline_cls, form, child_pk)

    async def inline_save(request: Request) -> Response:
        """POST: create or update a child. 422 + form HTML on validation errors."""
        resolved = await _resolve(request)
        if isinstance(resolved, Response):
            return resolved
        view, inline_cls, parent_obj = resolved

        form_data = await request.form()
        child_pk = str(form_data.get("_child_pk", "") or "")
        if child_pk and not inline_cls.can_edit:
            return _error("editing is not allowed", 403)
        if not child_pk and not inline_cls.can_create:
            return _error("creating is not allowed", 403)

        FormClass = await inline_cls.scaffold_form(view.session_maker)
        form = FormClass(form_data)
        if not form.validate():
            return await _render_form(request, view, inline_cls, form, child_pk, 422)

        data = _denormalize(admin, form.data, inline_cls.model)
        data.pop("csrf_token", None)
        try:
            if child_pk:
                obj = await inline_cls.update_child(
                    view.session_maker, child_pk, data, parent_obj
                )
                if obj is None:
                    return _error("record not found", 404)
            else:
                obj = await inline_cls.create_child(
                    view.session_maker, parent_obj, data
                )
        except Exception as exc:  # DB constraint errors, etc.
            logger.exception("sqladmin-inline: saving %s failed", inline_cls.__name__)
            return _error(str(exc), 400)
        return JSONResponse({"ok": True, "pk": inline_cls.encode_pk(obj)})

    async def _json_pks(request: Request) -> list[str] | Response:
        try:
            body = await request.json()
        except (ValueError, json.JSONDecodeError):
            return _error("invalid JSON body", 400)
        pks = body.get("pks") if isinstance(body, dict) else None
        if not isinstance(pks, list):
            return _error('body must be {"pks": [...]}', 400)
        return [str(p) for p in pks]

    async def inline_delete(request: Request) -> Response:
        """DELETE: bulk delete ``{"pks": [...]}`` children of this parent."""
        resolved = await _resolve(request)
        if isinstance(resolved, Response):
            return resolved
        view, inline_cls, parent_obj = resolved
        if not inline_cls.can_delete:
            return _error("deletion is not allowed", 403)
        pks = await _json_pks(request)
        if isinstance(pks, Response):
            return pks
        deleted = await inline_cls.delete_children(view.session_maker, pks, parent_obj)
        return JSONResponse({"deleted": deleted})

    async def inline_reorder(request: Request) -> Response:
        """POST: ``{"pks": [...]}`` in the new order -> order_field = 1..N."""
        resolved = await _resolve(request)
        if isinstance(resolved, Response):
            return resolved
        view, inline_cls, parent_obj = resolved
        if inline_cls.order_field is None:
            return _error("reordering is not enabled for this inline", 404)
        if not inline_cls.can_edit:
            return _error("editing is not allowed", 403)
        pks = await _json_pks(request)
        if isinstance(pks, Response):
            return pks
        updated = await inline_cls.reorder_children(view.session_maker, parent_obj, pks)
        return JSONResponse({"ok": True, "updated": updated})

    # Legacy sqladmin (< 0.31): no edit_context hook -> wrap the edit route ----

    def _wrap_edit(route: Route) -> Route:
        original = route.endpoint

        async def _prepare(request: Request) -> None:
            view = admin._find_model_view(request.path_params["identity"])
            if not getattr(view, "inlines", None):
                return
            if not await _maybe_await(view.is_accessible(request)):
                return  # the original handler raises 403
            parent_obj = await view.get_object_for_edit(request)
            if parent_obj is not None:
                setattr(
                    request.state,
                    STATE_ATTR,
                    await view._build_inline_contexts(request, parent_obj),
                )

        guarded_prepare = _protect(_prepare)

        async def edit_with_inlines(request: Request) -> Response:
            early = await guarded_prepare(request)
            if isinstance(early, Response):  # login redirect
                return early
            return await original(request)  # type: ignore[no-any-return]

        return Route(
            route.path,
            endpoint=edit_with_inlines,
            name=route.name,
            methods=route.methods,
        )

    # Registration --------------------------------------------------------------

    routes = list(admin.admin.router.routes)
    if not HAS_CONTEXT_HOOKS:
        routes = [
            _wrap_edit(r) if isinstance(r, Route) and r.name == "edit" else r
            for r in routes
        ]

    base = "/{identity}/inline/{inline_identity}/{parent_pk:path}"
    inline_routes = [
        Mount(
            STATICS_PATH,
            app=StaticFiles(directory=str(STATICS_DIR)),
            name=STATICS_ROUTE_NAME,
        ),
        Route(
            f"{base}/list", _protect(inline_list), name="inline:list", methods=["GET"]
        ),
        Route(
            f"{base}/form", _protect(inline_form), name="inline:form", methods=["GET"]
        ),
        Route(
            f"{base}/save", _protect(inline_save), name="inline:save", methods=["POST"]
        ),
        Route(
            f"{base}/delete",
            _protect(inline_delete),
            name="inline:delete",
            methods=["DELETE"],
        ),
        Route(
            f"{base}/reorder",
            _protect(inline_reorder),
            name="inline:reorder",
            methods=["POST"],
        ),
    ]
    # Put our routes first so generic "/{identity}/..." routes never shadow them.
    admin.admin.router.routes = inline_routes + routes
    setattr(admin, _INSTALLED_ATTR, True)


#: Backward-compatible alias.
register_inline_globals = setup_inline_routes
