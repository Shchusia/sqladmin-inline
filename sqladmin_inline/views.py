# views.py
"""
sqladmin_inline.views
~~~~~~~~~~~~~~~~~~~~~~

ModelViewWithInlines class and HTTP route handlers for inline CRUD operations.
"""

from __future__ import annotations

from collections.abc import Sequence
import logging
from typing import Any, ClassVar

from sqladmin import ModelView
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select as sa_select
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from sqladmin_inline.inline import InlineModelAdmin, InlinePage

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ModelViewWithInlines
# ---------------------------------------------------------------------------


class ModelViewWithInlines(ModelView):
    """Base ModelView with Django-style inline support.

    Extends sqladmin.ModelView to include inline editing capabilities.
    Override create_template and edit_template to include inline sections.

    Attributes:
        inlines: List of InlineModelAdmin subclasses to display.
        create_template: Template for create view (default: sqladmin_inline/create.html).
        edit_template: Template for edit view (default: sqladmin_inline/edit.html).
    """

    inlines: ClassVar[Sequence[type[InlineModelAdmin]]] = []

    create_template: ClassVar[str] = "sqladmin_inline/create.html"
    edit_template: ClassVar[str] = "sqladmin_inline/edit.html"

    def _inline_relationship_names(self) -> list[str]:
        """Get to-many relationship names managed by inlines.

        Returns:
            List of relationship names to exclude from parent form.
        """
        if not self.inlines:
            return []
        try:
            mapper = sa_inspect(self.model)  # type: ignore[var-annotated]
        except Exception:
            return []
        inline_models = {il.model for il in self.inlines}
        excluded = []
        for rel in mapper.relationships:
            if (
                rel.direction.name in ("ONETOMANY", "MANYTOMANY")
                and rel.mapper.class_ in inline_models
            ):
                excluded.append(rel.key)
        return excluded

    def get_form_columns(self) -> list[str]:
        """Get form columns excluding to-many relationships managed by inlines."""
        base = super().get_form_columns()
        excluded = set(self._inline_relationship_names())
        return [c for c in base if c not in excluded]

    async def _build_inline_contexts(
        self, request: Request, parent_obj: Any | None = None
    ) -> list[dict[str, Any]]:
        """Build template contexts for all inlines.

        Args:
            request: Starlette request object.
            parent_obj: Parent model instance (None for create view).

        Returns:
            List of context dictionaries for each inline.
        """
        contexts = []
        for inline_cls in self.inlines:
            FormClass = await inline_cls.scaffold_form(self.session_maker)
            page = int(request.query_params.get(f"_il_{inline_cls.identity}_page", 1))
            search = request.query_params.get(f"_il_{inline_cls.identity}_search", "")

            if parent_obj is not None:
                pagination = await inline_cls.get_page(
                    self.session_maker, parent_obj, page=page, search=search
                )
            else:
                pagination = InlinePage(
                    rows=[], page=1, page_size=inline_cls.page_size, count=0
                )

            display_cols = inline_cls._display_columns()
            contexts.append(
                {
                    "inline_cls": inline_cls,
                    "identity": inline_cls.identity,
                    "prefix": _prefix(inline_cls),
                    "label": inline_cls.inline_label,
                    "display_columns": display_cols,
                    "column_labels": {
                        c: inline_cls._get_label(c) for c in display_cols
                    },
                    "pagination": pagination,
                    "search": search,
                    "search_enabled": bool(inline_cls._search_columns()),
                    "icon": getattr(inline_cls, "icon", None),
                    "layout": getattr(inline_cls, "layout", "center"),
                    "can_delete": inline_cls.can_delete,
                    "form_class": FormClass,
                    "parent_pk": _encode_parent_pk(parent_obj) if parent_obj else "",
                }
            )
        return contexts


# ---------------------------------------------------------------------------
# Admin Setup
# ---------------------------------------------------------------------------


def setup_inline_routes(admin: Any) -> None:
    """Register inline CRUD API routes and inject template directory.

    Call this once after creating the Admin instance:

        admin = Admin(app, engine=engine)
        setup_inline_routes(admin)

    Args:
        admin: sqladmin.Admin instance.
    """
    import pathlib

    from jinja2 import ChoiceLoader, FileSystemLoader
    from starlette.routing import Route

    our_tmpl = str(pathlib.Path(__file__).parent / "templates")
    loader = admin.templates.env.loader
    loaders = loader.loaders if hasattr(loader, "loaders") else [loader]
    admin.templates.env.loader = ChoiceLoader(
        [FileSystemLoader(our_tmpl)] + list(loaders)
    )

    def _find_view(identity: str) -> Any:
        return admin._find_model_view(identity)

    def _find_inline(identity: str, inline_id: str) -> type[InlineModelAdmin] | None:
        view = _find_view(identity)
        if not hasattr(view, "inlines"):
            return None
        for cls in view.inlines:
            if cls.identity == inline_id:
                return cls  # type: ignore[no-any-return]
        return None

    async def inline_list(request: Request) -> Response:
        """GET handler for inline table fragment."""
        identity = request.path_params["identity"]
        inline_id = request.path_params["inline_identity"]
        parent_pk_str = request.path_params["parent_pk"]

        view = _find_view(identity)
        inline_cls = _find_inline(identity, inline_id)
        if inline_cls is None:
            return JSONResponse({"error": "inline not found"}, status_code=404)

        parent_obj = await _get_parent_by_pk(view, parent_pk_str)
        if parent_obj is None:
            return JSONResponse({"error": "parent not found"}, status_code=404)

        page = int(request.query_params.get("page", 1))
        search = request.query_params.get("search", "")

        pagination = await inline_cls.get_page(
            view.session_maker, parent_obj, page=page, search=search
        )
        display_cols = inline_cls._display_columns()
        FormClass = await inline_cls.scaffold_form(view.session_maker)

        ctx = {
            "request": request,
            "inline_cls": inline_cls,
            "identity": inline_id,
            "parent_identity": identity,
            "prefix": _prefix(inline_cls),
            "label": inline_cls.inline_label,
            "display_columns": display_cols,
            "column_labels": {c: inline_cls._get_label(c) for c in display_cols},
            "pagination": pagination,
            "search": search,
            "search_enabled": bool(inline_cls._search_columns()),
            "can_delete": inline_cls.can_delete,
            "form_class": FormClass,
            "parent_pk": parent_pk_str,
        }
        return await admin.templates.TemplateResponse(  # type: ignore[no-any-return]
            request, "sqladmin_inline/_inline_table.html", ctx
        )

    async def inline_form(request: Request) -> Response:
        """GET handler for inline add/edit modal form."""
        identity = request.path_params["identity"]
        inline_id = request.path_params["inline_identity"]
        parent_pk_str = request.path_params["parent_pk"]
        child_pk = request.query_params.get("pk", "")

        view = _find_view(identity)
        inline_cls = _find_inline(identity, inline_id)
        if inline_cls is None:
            return JSONResponse({"error": "inline not found"}, status_code=404)

        FormClass = await inline_cls.scaffold_form(view.session_maker)
        obj = None
        form_data = None

        if child_pk:
            obj = await inline_cls.get_by_pk(view.session_maker, child_pk)
            if obj:
                form_data = {}
                mapper = sa_inspect(inline_cls.model)

                for column in mapper.columns:
                    try:
                        form_data[column.key] = getattr(obj, column.key)
                    except Exception:  # noqa # nosec
                        logger.debug("")

                for rel in mapper.relationships:
                    if rel.direction.name == "MANYTOONE":
                        try:
                            related_obj = getattr(obj, rel.key)
                            if related_obj is not None:
                                form_data[rel.key] = related_obj
                        except Exception:
                            for _, local_col in rel.synchronize_pairs:
                                try:
                                    fk_val = getattr(obj, local_col.key, None)
                                    if fk_val is not None:
                                        form_data[rel.key] = fk_val
                                except Exception:  # noqa # nosec
                                    pass  # noqa # nosec

        form = FormClass(data=form_data) if form_data else FormClass()

        if form_data:
            print(f"Form data for {inline_cls.model.__name__}: {form_data.keys()}")
            for field in form:
                if field.type == "QuerySelectField":
                    print(f"  Field {field.name}: data={field.data}")

        ctx = {
            "request": request,
            "form": form,
            "inline_cls": inline_cls,
            "parent_identity": identity,
            "inline_identity": inline_id,
            "parent_pk": parent_pk_str,
            "child_pk": child_pk,
            "label": inline_cls.inline_label,
            "is_edit": bool(child_pk),
        }
        return await admin.templates.TemplateResponse(  # type: ignore[no-any-return]
            request, "sqladmin_inline/_inline_form.html", ctx
        )

    async def inline_save(request: Request) -> Response:
        """POST handler for saving inline record."""
        identity = request.path_params["identity"]
        inline_id = request.path_params["inline_identity"]
        parent_pk_str = request.path_params["parent_pk"]

        view = _find_view(identity)
        inline_cls = _find_inline(identity, inline_id)
        if inline_cls is None:
            return JSONResponse({"error": "inline not found"}, status_code=404)

        parent_obj = await _get_parent_by_pk(view, parent_pk_str)
        if parent_obj is None:
            return JSONResponse({"error": "parent not found"}, status_code=404)

        FormClass = await inline_cls.scaffold_form(view.session_maker)
        form_data = await request.form()
        child_pk = form_data.get("_child_pk", "")

        form = FormClass(form_data)

        if not form.validate():
            ctx = {
                "request": request,
                "form": form,
                "inline_cls": inline_cls,
                "parent_identity": identity,
                "inline_identity": inline_id,
                "parent_pk": parent_pk_str,
                "child_pk": child_pk,
                "label": inline_cls.inline_label,
                "is_edit": bool(child_pk),
            }
            return await admin.templates.TemplateResponse(  # type: ignore[no-any-return]
                request,
                "sqladmin_inline/_inline_form.html",
                ctx,
                status_code=422,
            )

        data = {k: v for k, v in form.data.items() if k != "csrf_token"}

        try:
            if child_pk:
                await inline_cls.update_child(view.session_maker, child_pk, data)
            else:
                await inline_cls.create_child(view.session_maker, parent_obj, data)
        except Exception as exc:
            logger.exception(exc)
            return JSONResponse({"error": str(exc)}, status_code=500)

        return JSONResponse({"ok": True})

    async def inline_delete(request: Request) -> Response:
        """DELETE handler for bulk inline record deletion."""
        identity = request.path_params["identity"]
        inline_id = request.path_params["inline_identity"]

        view = _find_view(identity)
        inline_cls = _find_inline(identity, inline_id)
        if inline_cls is None:
            return JSONResponse({"error": "inline not found"}, status_code=404)

        body = await request.json()
        pks: list[str] = [str(p) for p in body.get("pks", [])]
        deleted = 0
        for pk_str in pks:
            if await inline_cls.delete_child(view.session_maker, pk_str):
                deleted += 1

        return JSONResponse({"deleted": deleted})

    async def _build_inline_contexts(
        view: ModelViewWithInlines,
        request: Request,
        parent_obj: Any,
    ) -> list[dict[str, Any]]:
        """Build inline context for edit template."""
        contexts = []
        for inline_cls in view.inlines:
            FormClass = await inline_cls.scaffold_form(view.session_maker)
            display_cols = inline_cls._display_columns()
            parent_pk_str = _encode_parent_pk(parent_obj)

            page = int(request.query_params.get(f"_il_{inline_cls.identity}_page", 1))
            search = request.query_params.get(f"_il_{inline_cls.identity}_search", "")

            pagination = await inline_cls.get_page(
                view.session_maker, parent_obj, page=page, search=search
            )
            contexts.append(
                {
                    "inline_cls": inline_cls,
                    "identity": inline_cls.identity,
                    "parent_identity": view.identity,
                    "prefix": _prefix(inline_cls),
                    "label": inline_cls.inline_label,
                    "display_columns": display_cols,
                    "column_labels": {
                        c: inline_cls._get_label(c) for c in display_cols
                    },
                    "pagination": pagination,
                    "search": search,
                    "search_enabled": bool(inline_cls._search_columns()),
                    "icon": getattr(inline_cls, "icon", None),
                    "layout": getattr(inline_cls, "layout", "center"),
                    "can_delete": inline_cls.can_delete,
                    "form_class": FormClass,
                    "parent_pk": parent_pk_str,
                }
            )
        return contexts

    _original_edit_handler = admin.edit
    import logging as _logging

    from starlette.exceptions import HTTPException as _HTTPExc
    from starlette.responses import RedirectResponse as _RR
    from starlette.routing import Route as _Route

    _logger = _logging.getLogger(__name__)

    async def patched_edit(request: Request) -> Response:
        """Patched edit handler with inline support."""
        identity = request.path_params["identity"]
        view = _find_view(identity)

        if not hasattr(view, "inlines") or not view.inlines:
            return await _original_edit_handler(request)  # type: ignore[no-any-return]

        if not view.is_accessible(request):
            raise _HTTPExc(status_code=403)

        model_obj = await view.get_object_for_edit(request)
        if model_obj is None:
            raise _HTTPExc(status_code=404)

        can_edit = await view.check_can_edit(request, model_obj)
        if not can_edit:
            raise _HTTPExc(status_code=403)

        Form = await view.scaffold_form(view._form_edit_rules)

        if request.method == "GET":
            inline_contexts = await _build_inline_contexts(view, request, model_obj)
            context = {
                "obj": model_obj,
                "model_view": view,
                "form": Form(
                    obj=model_obj, data=admin._normalize_wtform_data(model_obj)
                ),
                "inline_contexts": inline_contexts,
            }
            return await admin.templates.TemplateResponse(  # type: ignore[no-any-return]
                request, view.edit_template, context
            )

        form_data = await admin._handle_form_data(request, model_obj)
        form = Form(form_data)

        inline_contexts = await _build_inline_contexts(view, request, model_obj)
        context = {
            "obj": model_obj,
            "model_view": view,
            "form": form,
            "inline_contexts": inline_contexts,
        }

        if not form.validate():
            return await admin.templates.TemplateResponse(  # type: ignore[no-any-return]
                request, view.edit_template, context, status_code=400
            )

        form_data_dict = admin._denormalize_wtform_data(form.data, model_obj)
        try:
            if view.save_as and form_data.get("save") == "Save as new":
                obj = await view.insert_model(request, form_data_dict)
            else:
                obj = await view.update_model(
                    request, pk=request.path_params["pk"], data=form_data_dict
                )
        except Exception as exc:
            _logger.exception(exc)
            context["error"] = str(exc)
            return await admin.templates.TemplateResponse(  # type: ignore[no-any-return]
                request, view.edit_template, context, status_code=400
            )

        url = admin.get_save_redirect_url(
            request=request, form=form_data, obj=obj, model_view=view
        )
        return _RR(url=url, status_code=302)

    existing_routes = list(admin.admin.router.routes)
    for i, route in enumerate(existing_routes):
        if getattr(route, "name", None) == "edit":
            existing_routes[i] = _Route(
                "/{identity}/edit/{pk:path}",
                endpoint=patched_edit,
                name="edit",
                methods=["GET", "POST"],
            )
            break
    admin.admin.router.routes = existing_routes

    new_routes = [
        Route(
            "/{identity}/inline/{inline_identity}/{parent_pk:path}/list",
            endpoint=inline_list,
            name="inline:list",
            methods=["GET"],
        ),
        Route(
            "/{identity}/inline/{inline_identity}/{parent_pk:path}/form",
            endpoint=inline_form,
            name="inline:form",
            methods=["GET"],
        ),
        Route(
            "/{identity}/inline/{inline_identity}/{parent_pk:path}/save",
            endpoint=inline_save,
            name="inline:save",
            methods=["POST"],
        ),
        Route(
            "/{identity}/inline/{inline_identity}/{parent_pk:path}/delete",
            endpoint=inline_delete,
            name="inline:delete",
            methods=["DELETE"],
        ),
    ]
    admin.admin.router.routes = list(admin.admin.router.routes) + new_routes


register_inline_globals = setup_inline_routes


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------


def _prefix(inline_cls: type[InlineModelAdmin]) -> str:
    """Generate a safe HTML ID prefix for an inline section."""
    import re

    return re.sub(r"[^a-z0-9]+", "_", inline_cls.model.__name__.lower()).strip("_")


def _encode_parent_pk(obj: Any) -> str:
    """Encode parent primary key(s) into a comma-separated string."""
    from sqladmin.helpers import get_primary_keys

    pks = get_primary_keys(type(obj))
    return ",".join(str(getattr(obj, pk.key)) for pk in pks)


async def _get_parent_by_pk(view: Any, pk_str: str) -> Any | None:
    """Fetch parent model instance by its encoded primary key."""
    from sqladmin.helpers import is_async_session_maker

    pk_cols = list(view.pk_columns)
    parts = str(pk_str).split(",")
    pk_vals = {col.key: parts[i] for i, col in enumerate(pk_cols) if i < len(parts)}

    async def _fetch(session: Any) -> Any | None:
        conditions = [getattr(view.model, k) == v for k, v in pk_vals.items()]
        result = await session.execute(sa_select(view.model).where(*conditions))
        return result.scalars().first()

    if is_async_session_maker(view.session_maker):
        async with view.session_maker() as session:
            return await _fetch(session)
    return None
