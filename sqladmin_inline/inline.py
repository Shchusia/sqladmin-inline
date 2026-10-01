"""
sqladmin_inline.inline
~~~~~~~~~~~~~~~~~~~~~~

Django-style inline editing for sqladmin.

:class:`InlineModelAdmin` describes how a child model is listed and edited
inside the edit page of its parent model.  Every database operation works with
both async (``async_sessionmaker``) and sync (``sessionmaker``) session makers:
the actual work is written once against a sync :class:`~sqlalchemy.orm.Session`
and executed either through ``AsyncSession.run_sync`` or in a worker thread.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
import dataclasses
import decimal
import math
from typing import TYPE_CHECKING, Any, ClassVar, TypeVar
import uuid

import anyio.to_thread
from sqladmin.exceptions import InvalidModelError
from sqladmin.forms import ModelConverter, get_model_form
from sqladmin.helpers import (
    get_primary_keys,
    is_async_session_maker,
    prettify_class_name,
    slugify_class_name,
)
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import NoInspectionAvailable
from sqlalchemy.orm import Session, joinedload, selectinload
from wtforms import Form

if TYPE_CHECKING:
    from sqladmin._types import SESSION_MAKER

T = TypeVar("T")

EMPTY_VALUE = "—"
PK_SEPARATOR = ","
LAYOUTS = ("center", "sidebar")


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class InlinePage:
    """Paginated result container for inline child records.

    Attributes:
        rows: Child model instances of the current page.
        page: Current page number (1-indexed).
        page_size: Number of items per page.
        count: Total number of items matching the query.
    """

    rows: list[Any]
    page: int
    page_size: int
    count: int

    @property
    def total_pages(self) -> int:
        """Total number of pages (at least 1)."""
        return max(1, math.ceil(self.count / self.page_size))

    @property
    def has_previous(self) -> bool:
        """Whether a previous page exists."""
        return self.page > 1

    @property
    def has_next(self) -> bool:
        """Whether a next page exists."""
        return self.page < self.total_pages

    @property
    def first_index(self) -> int:
        """1-based index of the first row on this page (0 when empty)."""
        if self.count == 0:
            return 0
        return (self.page - 1) * self.page_size + 1

    @property
    def last_index(self) -> int:
        """1-based index of the last row on this page (0 when empty)."""
        return min(self.page * self.page_size, self.count)

    @property
    def remaining(self) -> int:
        """Number of rows after the current page."""
        return max(0, self.count - self.page * self.page_size)

    @property
    def page_range(self) -> list[int | None]:
        """Page numbers to render, with ``None`` as an ellipsis placeholder."""
        total = self.total_pages
        cur = self.page
        pages = {1, total, *range(max(1, cur - 2), min(total, cur + 2) + 1)}
        out: list[int | None] = []
        prev: int | None = None
        for p in sorted(pages):
            if prev is not None and p - prev > 1:
                out.append(None)
            out.append(p)
            prev = p
        return out


@dataclasses.dataclass
class InlineFormData:
    """Form data container for inline row operations (kept for compatibility)."""

    index: int
    data: dict[str, Any]
    pk: str | None = None
    delete: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _display_value(obj: Any, col_name: str) -> str:
    """Return a printable value for ``obj.col_name`` or :data:`EMPTY_VALUE`."""
    try:
        val = getattr(obj, col_name, None)
    except Exception:  # detached lazy relationship, broken property, ...
        return EMPTY_VALUE
    if val is None:
        return EMPTY_VALUE
    try:
        text = str(val)
    except Exception:
        return EMPTY_VALUE
    return text if text else EMPTY_VALUE


def _python_type(column: Any) -> type | None:
    for type_ in (column.type, getattr(column.type, "impl", None)):
        if type_ is None:
            continue
        try:
            return type_.python_type  # type: ignore[no-any-return]
        except NotImplementedError:
            continue
    return None


def _coerce_value(column: Any, raw: Any) -> Any:
    """Convert a raw (string) value to the python type of ``column``.

    Strict drivers such as asyncpg reject ``"5"`` for an INTEGER parameter,
    so primary keys coming from URLs/forms must be converted first.

    Raises:
        ValueError: if the value cannot be converted.
    """
    if raw is None:
        return None
    python_type = _python_type(column)
    if python_type is None or isinstance(raw, python_type):
        return raw
    if python_type in (int, float, decimal.Decimal, uuid.UUID, str):
        try:
            return python_type(str(raw).strip())
        except (TypeError, ValueError, decimal.InvalidOperation) as exc:
            raise ValueError(f"Invalid value {raw!r} for column {column.key}") from exc
    return raw


def _parse_pk(pk_str: Any, pk_columns: Sequence[Any]) -> dict[str, Any]:
    """Parse a comma-separated PK string into ``{column_key: typed_value}``.

    Extra parts are ignored.

    Raises:
        ValueError: if a part cannot be converted to the column type.
    """
    parts = str(pk_str).split(PK_SEPARATOR)
    return {
        col.key: _coerce_value(col, parts[i])
        for i, col in enumerate(pk_columns)
        if i < len(parts)
    }


def _pk_attr_keys(model: Any) -> list[tuple[str, Any]]:
    """Return ``[(attribute_key, column), ...]`` for the primary key of ``model``."""
    mapper = sa_inspect(model)
    return [(mapper.get_property_by_column(c).key, c) for c in mapper.primary_key]


def _identity_conditions(model: Any, pk_str: Any) -> list[Any]:
    """Build WHERE conditions selecting ``model`` by an encoded PK string.

    Raises:
        ValueError: if the PK is incomplete or has the wrong type.
    """
    parts = str(pk_str).split(PK_SEPARATOR)
    keys = _pk_attr_keys(model)
    if len(parts) < len(keys):
        raise ValueError(f"Incomplete primary key {pk_str!r}")
    return [
        getattr(model, key) == _coerce_value(col, parts[i])
        for i, (key, col) in enumerate(keys)
    ]


def encode_identity(obj: Any) -> str:
    """Encode the primary key of any mapped instance as a comma-separated string."""
    return PK_SEPARATOR.join(
        str(getattr(obj, key)) for key, _ in _pk_attr_keys(type(obj))
    )


async def run_in_session(session_maker: SESSION_MAKER, fn: Callable[[Session], T]) -> T:
    """Run ``fn(session)`` with a sync Session from an async or sync session maker."""
    if is_async_session_maker(session_maker):
        async with session_maker() as session:  # type: ignore[attr-defined]
            return await session.run_sync(fn)  # type: ignore[no-any-return]

    def _call() -> T:
        with session_maker() as session:  # type: ignore[attr-defined]
            return fn(session)

    return await anyio.to_thread.run_sync(_call)


# ---------------------------------------------------------------------------
# Metaclass
# ---------------------------------------------------------------------------


class InlineModelAdminMeta(type):
    """Metaclass that validates the model and fills derived attributes."""

    def __new__(mcs, name: str, bases: tuple, attrs: dict, **kwargs: Any) -> type:  # type: ignore[type-arg]
        cls = super().__new__(mcs, name, bases, attrs)
        model = kwargs.get("model")
        if not model:
            return cls
        try:
            mapper = sa_inspect(model)
        except NoInspectionAvailable as exc:
            raise InvalidModelError(
                f"Class {getattr(model, '__name__', model)} is not a SQLAlchemy model."
            ) from exc

        cls.model = model  # type: ignore[attr-defined]
        cls.pk_columns = list(get_primary_keys(model))  # type: ignore[attr-defined]
        cls.identity = attrs.get("identity") or (  # type: ignore[attr-defined]
            slugify_class_name(model.__name__) + "_inline"
        )
        cls.inline_label = attrs.get("inline_label") or (  # type: ignore[attr-defined]
            prettify_class_name(model.__name__) + "s"
        )

        layout = getattr(cls, "layout", "center")
        if layout not in LAYOUTS:
            raise ValueError(f"{name}.layout must be one of {LAYOUTS}, got {layout!r}")
        page_size = getattr(cls, "page_size", 5)
        if (
            isinstance(page_size, bool)
            or not isinstance(page_size, int)
            or page_size < 1
        ):
            raise ValueError(f"{name}.page_size must be a positive int")
        order_field = getattr(cls, "order_field", None)
        if order_field is not None and order_field not in mapper.column_attrs:
            raise ValueError(
                f"{name}.order_field {order_field!r} is not a column of {model.__name__}"
            )
        return cls

    @classmethod
    def get_display_value_safe(
        cls, obj: Any, col_name: str, session: Any = None
    ) -> str:
        """Deprecated alias of :meth:`InlineModelAdmin.get_display_value`."""
        return _display_value(obj, col_name)


# ---------------------------------------------------------------------------
# InlineModelAdmin
# ---------------------------------------------------------------------------


class InlineModelAdmin(metaclass=InlineModelAdminMeta):
    """Django-style inline configuration for editing related (child) models.

    Example::

        class TagInline(InlineModelAdmin, model=Tag):
            column_list = [Tag.name]
            column_searchable_list = [Tag.name]
            layout = "sidebar"
            order_field = "position"

    Attributes:
        model: SQLAlchemy model class (set via ``model=`` class keyword).
        identity: URL-safe identifier, default ``<model>_inline``.  Override it
            when one parent has two inlines of the same model.
        inline_label: Section title, default ``<Model>s``.
        fk_attr: Relationship or column on the child that points to the
            parent. Auto-detected when ``None``.
        column_list: Columns shown in the table (default: all non-PK columns).
        column_labels: ``{column: label}`` overrides.
        column_searchable_list: Columns used by the search box.
        column_default_sort: ``(column, descending)`` default ordering.
        order_field: Integer column used for drag-and-drop ordering.
        page_size: Rows per page / per "Load more" (default 5).
        can_create / can_edit / can_delete: Inline-level permissions.
        icon: Icon CSS class for the header, e.g. ``"fa-solid fa-tag"``.
        layout: ``"center"`` (below the form) or ``"sidebar"`` (right column).
        form_columns / form_excluded_columns / form_args / form_widget_args:
            Same meaning as on ``sqladmin.ModelView``.
    """

    # Set by the metaclass
    model: ClassVar[Any]
    pk_columns: ClassVar[list[Any]]
    identity: ClassVar[str]
    inline_label: ClassVar[str]

    # Configuration
    fk_attr: ClassVar[str | None] = None
    column_list: ClassVar[Sequence[Any]] = []
    column_labels: ClassVar[dict[Any, str]] = {}
    column_searchable_list: ClassVar[Sequence[Any]] = []
    column_default_sort: ClassVar[tuple[Any, bool] | None] = None
    order_field: ClassVar[str | None] = None
    page_size: ClassVar[int] = 5
    can_create: ClassVar[bool] = True
    can_edit: ClassVar[bool] = True
    can_delete: ClassVar[bool] = True

    icon: ClassVar[str | None] = None
    layout: ClassVar[str] = "center"

    form_columns: ClassVar[Sequence[Any]] = []
    form_excluded_columns: ClassVar[Sequence[Any]] = []
    form_args: ClassVar[dict[str, Any]] = {}
    form_widget_args: ClassVar[dict[str, Any]] = {}

    # -----------------------------------------------------------------------
    # Relationship to the parent
    # -----------------------------------------------------------------------

    @classmethod
    def _get_fk_attr(cls, parent_model: Any) -> str:
        """Return the child attribute (relationship or column) pointing to the parent.

        Raises:
            ValueError: if no FK to ``parent_model`` can be found.
        """
        if cls.fk_attr:
            return cls.fk_attr
        parent_cls = (
            parent_model if isinstance(parent_model, type) else type(parent_model)
        )
        mapper = sa_inspect(cls.model)
        parent_table: Any = sa_inspect(parent_cls).persist_selectable
        for rel in mapper.relationships:
            if rel.direction.name == "MANYTOONE":
                for remote_col, _ in rel.synchronize_pairs:
                    if remote_col.table is parent_table:
                        return rel.key  # type: ignore[no-any-return]
        for prop in mapper.column_attrs:
            for col in prop.columns:
                for fk in col.foreign_keys:
                    if fk.column.table is parent_table:
                        return prop.key  # type: ignore[no-any-return]
        raise ValueError(
            f"Cannot auto-detect FK from {cls.model.__name__} to "
            f"{parent_cls.__name__}. Set {cls.__name__}.fk_attr."
        )

    @classmethod
    def _fk_pairs(cls, parent_model: Any) -> list[tuple[str, str]]:
        """``[(child_attr, parent_attr), ...]`` linking a child row to its parent."""
        parent_cls = (
            parent_model if isinstance(parent_model, type) else type(parent_model)
        )
        fk_attr = cls._get_fk_attr(parent_cls)
        mapper = sa_inspect(cls.model)
        parent_mapper: Any = sa_inspect(parent_cls)

        rel = mapper.relationships.get(fk_attr)
        if rel is not None:
            return [
                (
                    mapper.get_property_by_column(local).key,
                    parent_mapper.get_property_by_column(remote).key,
                )
                for remote, local in rel.synchronize_pairs
            ]

        if fk_attr not in mapper.column_attrs:
            raise ValueError(
                f"{cls.__name__}.fk_attr {fk_attr!r} is neither a relationship "
                f"nor a column of {cls.model.__name__}"
            )
        for col in mapper.column_attrs[fk_attr].columns:
            for fk in col.foreign_keys:
                if fk.column.table is parent_mapper.persist_selectable:
                    return [
                        (fk_attr, parent_mapper.get_property_by_column(fk.column).key)
                    ]
        pk_key, _ = _pk_attr_keys(parent_cls)[0]
        return [(fk_attr, pk_key)]

    @classmethod
    def _parent_keys(cls, parent_model: Any) -> set[str]:
        """Child attribute names (columns and relationships) that point to the parent."""
        child_cols = {child for child, _ in cls._fk_pairs(parent_model)}
        mapper = sa_inspect(cls.model)
        keys = set(child_cols)
        for rel in mapper.relationships:
            if rel.direction.name != "MANYTOONE":
                continue
            locals_ = {
                mapper.get_property_by_column(c).key for _, c in rel.synchronize_pairs
            }
            if locals_ & child_cols:
                keys.add(rel.key)
        return keys

    @classmethod
    def _parent_conditions(cls, fk_attr: str | None, parent_obj: Any) -> list[Any]:
        """WHERE conditions selecting the children of ``parent_obj``.

        ``fk_attr`` is accepted for backward compatibility; the configured or
        auto-detected attribute is always used.
        """
        return [
            getattr(cls.model, child) == getattr(parent_obj, parent)
            for child, parent in cls._fk_pairs(type(parent_obj))
        ]

    # -----------------------------------------------------------------------
    # Column helpers
    # -----------------------------------------------------------------------

    @classmethod
    def _col_names(cls, seq: Iterable[Any]) -> list[str]:
        """Convert column objects/strings to attribute names."""
        return [c.key if hasattr(c, "key") else str(c) for c in seq]

    @classmethod
    def _display_columns(cls) -> list[str]:
        """Columns shown in the inline table."""
        if cls.column_list:
            return cls._col_names(cls.column_list)
        pk_names = {c.key for c in cls.pk_columns}
        return [
            p.key for p in sa_inspect(cls.model).column_attrs if p.key not in pk_names
        ]

    @classmethod
    def _search_columns(cls) -> list[str]:
        """Searchable column names (only real columns are kept)."""
        attrs = sa_inspect(cls.model).column_attrs
        return [n for n in cls._col_names(cls.column_searchable_list) if n in attrs]

    @classmethod
    def _fk_field_names(cls) -> list[str]:
        """Names of to-one relationships and their FK columns."""
        mapper = sa_inspect(cls.model)
        names: set[str] = set()
        for rel in mapper.relationships:
            if rel.direction.name == "MANYTOONE":
                names.add(rel.key)
                for _remote, local_col in rel.synchronize_pairs:
                    names.add(local_col.key)
        return sorted(names)

    @classmethod
    def _form_excluded(cls) -> list[str]:
        """Columns excluded from the form: explicit exclusions plus primary keys."""
        explicit = cls._col_names(cls.form_excluded_columns)
        pk_names = [c.key for c in cls.pk_columns]
        return sorted(set(explicit + pk_names))

    @classmethod
    def _form_only(cls) -> list[str] | None:
        """Explicit form columns, or ``None`` when not configured."""
        if cls.form_columns:
            return cls._col_names(cls.form_columns)
        return None

    @classmethod
    def _labels(cls) -> dict[str, str]:
        """``column_labels`` normalised to string keys."""
        return {
            (k.key if hasattr(k, "key") else str(k)): v
            for k, v in cls.column_labels.items()
        }

    @classmethod
    def _get_label(cls, col_name: str) -> str:
        """Human-readable label for a column."""
        return cls._labels().get(col_name, col_name.replace("_", " ").title())

    # -----------------------------------------------------------------------
    # Form
    # -----------------------------------------------------------------------

    @classmethod
    async def scaffold_form(cls, session_maker: SESSION_MAKER) -> type[Form]:
        """Build the WTForms class used by the add/edit modal."""
        only = cls._form_only()
        return await get_model_form(
            model=cls.model,
            session_maker=session_maker,
            only=only,
            exclude=None if only is not None else cls._form_excluded(),
            column_labels=cls._labels(),
            form_args=cls.form_args,
            form_widget_args=cls.form_widget_args,
            form_class=Form,
            form_overrides={},
            form_ajax_refs={},
            form_include_pk=False,
            form_converter=ModelConverter,
        )

    # -----------------------------------------------------------------------
    # Query helpers
    # -----------------------------------------------------------------------

    @classmethod
    def _search_condition(cls, search: str) -> Any | None:
        term = (search or "").strip()
        columns = cls._search_columns()
        if not term or not columns:
            return None
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        parts = []
        for name in columns:
            col = getattr(cls.model, name)
            expr = col if isinstance(col.type, String) else cast(col, String)
            parts.append(expr.ilike(f"%{escaped}%", escape="\\"))
        return or_(*parts)

    @classmethod
    def _order_by(cls) -> list[Any]:
        pk_order = [
            getattr(cls.model, key).asc() for key, _ in _pk_attr_keys(cls.model)
        ]
        if cls.order_field is not None:
            return [getattr(cls.model, cls.order_field).asc(), *pk_order]
        if cls.column_default_sort is not None:
            name, desc = cls.column_default_sort
            name = name.key if hasattr(name, "key") else str(name)
            col = getattr(cls.model, name, None)
            if col is not None:
                return [col.desc() if desc else col.asc(), *pk_order]
        return pk_order

    @classmethod
    def _eager_options(cls) -> list[Any]:
        mapper = sa_inspect(cls.model)
        options = []
        for name in cls._display_columns():
            rel = mapper.relationships.get(name)
            if rel is None:
                continue
            attr = getattr(cls.model, name)
            options.append(selectinload(attr) if rel.uselist else joinedload(attr))
        return options

    @classmethod
    def _select_by_pk(cls, pk_str: Any, parent_obj: Any | None) -> Any:
        """SELECT for one child; raises ``ValueError`` for a malformed PK."""
        stmt = select(cls.model).where(*_identity_conditions(cls.model, pk_str))
        if parent_obj is not None:
            stmt = stmt.where(*cls._parent_conditions(None, parent_obj))
        return stmt

    # -----------------------------------------------------------------------
    # Read
    # -----------------------------------------------------------------------

    @classmethod
    async def get_page(
        cls,
        session_maker: SESSION_MAKER,
        parent_obj: Any,
        page: int = 1,
        search: str = "",
    ) -> InlinePage:
        """Fetch one page of children of ``parent_obj``.

        A page beyond the last one is clamped to the last page (useful after
        deleting rows).
        """
        page = max(1, int(page))
        where = cls._parent_conditions(None, parent_obj)
        search_cond = cls._search_condition(search)
        if search_cond is not None:
            where.append(search_cond)
        options = cls._eager_options()
        order_by = cls._order_by()
        size = cls.page_size

        def _fetch(session: Session) -> InlinePage:
            total = session.execute(
                select(func.count()).select_from(cls.model).where(*where)
            ).scalar_one()
            current = min(page, max(1, math.ceil(total / size)))
            stmt = (
                select(cls.model)
                .where(*where)
                .options(*options)
                .order_by(*order_by)
                .offset((current - 1) * size)
                .limit(size)
            )
            rows: list[Any] = list(session.execute(stmt).unique().scalars().all())
            return InlinePage(rows=rows, page=current, page_size=size, count=total)

        return await run_in_session(session_maker, _fetch)

    @classmethod
    async def get_by_pk(
        cls,
        session_maker: SESSION_MAKER,
        pk_str: Any,
        parent_obj: Any | None = None,
        load: Iterable[str] = (),
    ) -> Any | None:
        """Fetch one child by PK (optionally only if it belongs to ``parent_obj``).

        To-one relationships are always eager-loaded, to-many relationships
        named in ``load`` too, so the object stays usable after the session
        is closed (e.g. to populate an edit form).
        """
        try:
            stmt = cls._select_by_pk(pk_str, parent_obj)
        except ValueError:
            return None
        mapper = sa_inspect(cls.model)
        extra = set(load)
        for rel in mapper.relationships:
            attr = getattr(cls.model, rel.key)
            if not rel.uselist:
                stmt = stmt.options(joinedload(attr))
            elif rel.key in extra:
                stmt = stmt.options(selectinload(attr))

        def _fetch(session: Session) -> Any | None:
            return session.execute(stmt).unique().scalars().first()

        return await run_in_session(session_maker, _fetch)

    @classmethod
    def encode_pk(cls, obj: Any) -> str:
        """Encode the child's primary key(s) as a comma-separated string."""
        return PK_SEPARATOR.join(str(getattr(obj, col.key)) for col in cls.pk_columns)

    # -----------------------------------------------------------------------
    # Write
    # -----------------------------------------------------------------------

    @classmethod
    def _resolve_related(cls, session: Session, target: Any, value: Any) -> Any | None:
        """Turn a form value (instance, PK string, ``None``) into an attached instance."""
        if value is None or value in ("", "__None"):
            return None
        if isinstance(value, target):
            return session.merge(value)
        try:
            conditions = _identity_conditions(target, value)
        except ValueError:
            return None
        return session.execute(select(target).where(*conditions)).scalars().first()

    @classmethod
    def _apply_data(cls, session: Session, obj: Any, data: dict[str, Any]) -> None:
        """Assign form data to ``obj``; relationship values are resolved to instances."""
        mapper = sa_inspect(cls.model)
        for key, value in data.items():
            rel = mapper.relationships.get(key)
            if rel is not None:
                target = rel.mapper.class_
                if rel.uselist:
                    values = value if isinstance(value, (list, tuple, set)) else [value]
                    resolved = [
                        cls._resolve_related(session, target, v) for v in values
                    ]
                    setattr(obj, key, [r for r in resolved if r is not None])
                else:
                    setattr(obj, key, cls._resolve_related(session, target, value))
            elif key in mapper.column_attrs:
                setattr(obj, key, value)
            # Unknown keys (e.g. "csrf_token") are ignored on purpose.

    @classmethod
    async def create_child(
        cls,
        session_maker: SESSION_MAKER,
        parent_obj: Any,
        data: dict[str, Any],
    ) -> Any:
        """Create a child linked to ``parent_obj``.

        The link to ``parent_obj`` always wins over a parent value in ``data``.
        With ``order_field`` configured and no value for it in ``data`` (e.g. the
        field is excluded from the form), the new row is appended at the end.
        """
        pairs = cls._fk_pairs(type(parent_obj))
        parent_keys = cls._parent_keys(type(parent_obj))
        clean = {k: v for k, v in data.items() if k not in parent_keys}
        parent_values = {child: getattr(parent_obj, parent) for child, parent in pairs}
        siblings = cls._parent_conditions(None, parent_obj)
        order_field = cls.order_field
        append = order_field is not None and clean.get(order_field) in (None, "")

        def _do(session: Session) -> Any:
            obj = cls.model()
            cls._apply_data(session, obj, clean)
            for key, value in parent_values.items():
                setattr(obj, key, value)
            if append:
                col = getattr(cls.model, order_field)  # type: ignore[arg-type]
                last = session.execute(select(func.max(col)).where(*siblings)).scalar()
                setattr(obj, order_field, (last or 0) + 1)  # type: ignore[arg-type]
            session.add(obj)
            session.commit()
            session.refresh(obj)
            return obj

        return await run_in_session(session_maker, _do)

    @classmethod
    async def update_child(
        cls,
        session_maker: SESSION_MAKER,
        pk: Any,
        data: dict[str, Any],
        parent_obj: Any | None = None,
    ) -> Any | None:
        """Update a child; returns ``None`` if it does not exist (for that parent)."""
        try:
            stmt = cls._select_by_pk(pk, parent_obj)
        except ValueError:
            return None

        def _do(session: Session) -> Any | None:
            obj = session.execute(stmt).scalars().first()
            if obj is None:
                return None
            cls._apply_data(session, obj, data)
            session.commit()
            session.refresh(obj)
            return obj

        return await run_in_session(session_maker, _do)

    @classmethod
    async def delete_children(
        cls,
        session_maker: SESSION_MAKER,
        pks: Iterable[Any],
        parent_obj: Any | None = None,
    ) -> int:
        """Delete several children in one transaction; returns how many were deleted."""
        statements = []
        for pk in pks:
            try:
                statements.append(cls._select_by_pk(pk, parent_obj))
            except ValueError:
                continue
        if not statements:
            return 0

        def _do(session: Session) -> int:
            deleted = 0
            for stmt in statements:
                obj = session.execute(stmt).scalars().first()
                if obj is not None:
                    session.delete(obj)
                    deleted += 1
            session.commit()
            return deleted

        return await run_in_session(session_maker, _do)

    @classmethod
    async def delete_child(
        cls,
        session_maker: SESSION_MAKER,
        pk_str: Any,
        parent_obj: Any | None = None,
    ) -> bool:
        """Delete one child; ``True`` if it existed."""
        return await cls.delete_children(session_maker, [pk_str], parent_obj) == 1

    # -----------------------------------------------------------------------
    # Drag-and-drop ordering
    # -----------------------------------------------------------------------

    @classmethod
    async def reorder_children(
        cls,
        session_maker: SESSION_MAKER,
        parent_obj: Any,
        ordered_pks: Sequence[Any],
    ) -> int:
        """Write ``order_field = 1..N`` following ``ordered_pks``, in one transaction.

        Only children of ``parent_obj`` are touched.  Returns the number of
        updated rows (0 when ``order_field`` is not configured).
        """
        if cls.order_field is None:
            return 0
        statements: list[Any] = []
        for pk in ordered_pks:
            try:
                statements.append(cls._select_by_pk(pk, parent_obj))
            except ValueError:
                statements.append(None)
        field = cls.order_field

        def _do(session: Session) -> int:
            updated = 0
            for position, stmt in enumerate(statements, start=1):
                if stmt is None:
                    continue
                obj = session.execute(stmt).scalars().first()
                if obj is not None:
                    setattr(obj, field, position)
                    updated += 1
            session.commit()
            return updated

        return await run_in_session(session_maker, _do)

    @classmethod
    async def reorder_child(
        cls,
        session_maker: SESSION_MAKER,
        pk_str: Any,
        new_position: int,
    ) -> bool:
        """Set ``order_field`` of a single child (not scoped to a parent)."""
        if cls.order_field is None:
            return False
        try:
            stmt = cls._select_by_pk(pk_str, None)
        except ValueError:
            return False
        field = cls.order_field

        def _do(session: Session) -> bool:
            obj = session.execute(stmt).scalars().first()
            if obj is None:
                return False
            setattr(obj, field, new_position)
            session.commit()
            return True

        return await run_in_session(session_maker, _do)

    # -----------------------------------------------------------------------
    # Display
    # -----------------------------------------------------------------------

    @classmethod
    def get_display_value(cls, obj: Any, col_name: str) -> str:
        """Printable value of a cell; ``"—"`` for missing/empty/unloadable values."""
        return _display_value(obj, col_name)
