# inline.py
"""
sqladmin_inlines.inline
~~~~~~~~~~~~~~~~~~~~~~~

Django-style inline editing for sqladmin.

Provides InlineModelAdmin class that allows editing related models
directly from the parent model's create/edit pages.
"""

from __future__ import annotations

from collections.abc import Sequence
import dataclasses
import math
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
)

from sqladmin.exceptions import InvalidModelError
from sqladmin.forms import ModelConverter, get_model_form
from sqladmin.helpers import (
    get_primary_keys,
    prettify_class_name,
    slugify_class_name,
)
from sqlalchemy import func, or_
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select as sa_select
from sqlalchemy.exc import NoInspectionAvailable
from wtforms import Form

if TYPE_CHECKING:
    from sqladmin._types import SESSION_MAKER


# ---------------------------------------------------------------------------
# Data Containers
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class InlinePage:
    """Paginated result container for inline child records.

    Attributes:
        rows: List of child model instances for current page.
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
        """Total number of pages available."""
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
    def page_range(self) -> list[Any]:
        """Generate pagination range with ellipsis placeholders.

        Returns:
            List of page numbers with None as ellipsis placeholder.
        """
        total = self.total_pages
        cur = self.page
        pages: set[Any] = set()
        pages.update([1, total])
        for p in range(max(1, cur - 2), min(total, cur + 2) + 1):
            pages.add(p)
        result = sorted(pages)
        out: list[Any] = []
        prev = None
        for p in result:
            if prev is not None and p - prev > 1:
                out.append(None)
            out.append(p)
            prev = p
        return out


@dataclasses.dataclass
class InlineFormData:
    """Form data container for inline row operations.

    Attributes:
        index: Row index in the formset.
        data: Form field values.
        pk: Primary key value (for existing records).
        delete: Whether this row should be deleted.
    """

    index: int
    data: dict[str, Any]
    pk: str | None = None
    delete: bool = False


# ---------------------------------------------------------------------------
# Metaclass
# ---------------------------------------------------------------------------


class InlineModelAdminMeta(type):
    """Metaclass for InlineModelAdmin that auto-detects model metadata."""

    @classmethod
    def get_display_value_safe(
        cls, obj: Any, col_name: str, session: Any = None
    ) -> str:
        """Safely get display value with optional lazy loading support.

        Args:
            obj: Model instance to extract value from.
            col_name: Name of the attribute to display.
            session: Optional SQLAlchemy session for lazy loading.

        Returns:
            String representation of the value or "—" if unavailable.
        """
        try:
            val = getattr(obj, col_name, "")

            if val == "" and hasattr(obj.__class__, col_name):
                mapper = sa_inspect(obj.__class__)
                if col_name in mapper.relationships:
                    if session and hasattr(obj, "_sa_instance_state"):
                        try:
                            from sqlalchemy.orm import object_session

                            # obj_session = object_session(obj) or session
                            val = getattr(obj, col_name)
                        except Exception:
                            return "—"
                    else:
                        return "—"

            if val is None:
                return "—"

            if hasattr(val, "__str__"):
                str_val = str(val)
                return str_val if str_val else "—"

            return str(val)
        except Exception:
            return "—"

    def __new__(mcs, name: str, bases: tuple, attrs: dict, **kwargs: Any) -> type:  # type: ignore[type-arg]
        """Create new InlineModelAdmin class and validate SQLAlchemy model."""
        cls: type[InlineModelAdmin] = super().__new__(mcs, name, bases, attrs)  # type: ignore[assignment]
        model = kwargs.get("model")
        if not model:
            return cls
        try:
            sa_inspect(model)
        except NoInspectionAvailable as exc:
            raise InvalidModelError(
                f"Class {model.__name__} is not a SQLAlchemy model."
            ) from exc
        cls.model = model
        cls.pk_columns = get_primary_keys(model)  # type: ignore[assignment]
        cls.identity = slugify_class_name(model.__name__) + "_inline"
        cls.inline_label = attrs.get(
            "inline_label", prettify_class_name(model.__name__) + "s"
        )
        return cls

    @classmethod
    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        if cls.model:  # type: ignore[attr-defined]
            mapper = sa_inspect(cls.model)  # type: ignore[attr-defined]
            # Set pk_columns
            cls.pk_columns = mapper.primary_key  # type: ignore[attr-defined]
            # Set default identity if not provided
            if not hasattr(cls, "identity"):
                cls.identity = slugify_class_name(cls.__name__)  # type: ignore[attr-defined]
            # Set default label if not provided
            if not hasattr(cls, "inline_label"):
                cls.inline_label = prettify_class_name(cls.model.__name__) + "s"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# InlineModelAdmin
# ---------------------------------------------------------------------------


class InlineModelAdmin(metaclass=InlineModelAdminMeta):
    """Django-style inline configuration for editing related models.

    Configure how child models are displayed and edited within a parent form.

    Attributes:
        model: SQLAlchemy model class (set via metaclass).
        pk_columns: Primary key columns (set via metaclass).
        identity: Unique identifier for this inline (set via metaclass).
        inline_label: Display label for the inline section.

        fk_attr: Explicit foreign key attribute name (auto-detected if None).
        column_list: Columns to display in the inline table.
        column_labels: Custom labels for displayed columns.
        column_searchable_list: Columns to include in search.
        page_size: Number of rows per page (default: 5).
        can_delete: Whether to allow deletion (default: True).

        form_columns: Columns to include in the form.
        form_excluded_columns: Columns to exclude from the form.
        form_args: WTForms field arguments.
        form_widget_args: WTForms widget arguments.
    """

    # Set by metaclass
    model: ClassVar[Any]
    pk_columns: ClassVar[list[Any]]
    identity: ClassVar[str]
    inline_label: ClassVar[str]

    # User configuration
    fk_attr: ClassVar[str | None] = None
    column_list: ClassVar[Sequence[Any]] = []
    column_labels: ClassVar[dict[str, str]] = {}
    column_searchable_list: ClassVar[Sequence[Any]] = []
    page_size: ClassVar[int] = 5
    can_delete: ClassVar[bool] = True

    form_columns: ClassVar[Sequence[Any]] = []
    form_excluded_columns: ClassVar[Sequence[Any]] = []
    form_args: ClassVar[dict[str, Any]] = {}
    form_widget_args: ClassVar[dict[str, Any]] = {}

    # -----------------------------------------------------------------------
    # Foreign Key Detection
    # -----------------------------------------------------------------------

    @classmethod
    def _get_fk_attr(cls, parent_model: Any) -> str:
        """Auto-detect or return explicitly set foreign key attribute.

        Args:
            parent_model: Parent model class to find relationship to.

        Returns:
            Name of the foreign key attribute.

        Raises:
            ValueError: If foreign key cannot be auto-detected.
        """
        if cls.fk_attr:
            return cls.fk_attr
        mapper = sa_inspect(cls.model)
        parent_mapper = sa_inspect(  # type: ignore[var-annotated]
            parent_model if isinstance(parent_model, type) else type(parent_model)
        )
        # parent_mapper = sa_inspect(parent_model)
        parent_table = parent_mapper.persist_selectable
        for rel in mapper.relationships:
            if rel.direction.name == "MANYTOONE":
                for remote_col, _ in rel.synchronize_pairs:
                    if remote_col.table == parent_table:
                        return rel.key  # type: ignore[no-any-return]
        for col in mapper.columns:
            for fk in col.foreign_keys:
                if fk.column.table == parent_table:
                    return col.key  # type: ignore[no-any-return]
        raise ValueError(
            f"Cannot auto-detect FK from {cls.model.__name__} to "
            f"{parent_model.__name__}. Set InlineModelAdmin.fk_attr."
        )

    # -----------------------------------------------------------------------
    # Column Helpers
    # -----------------------------------------------------------------------

    @classmethod
    def _col_names(cls, seq: Sequence[Any]) -> list[str]:
        """Convert column objects to their string names."""
        return [c.key if hasattr(c, "key") else str(c) for c in seq]

    @classmethod
    def _display_columns(cls) -> list[str]:
        """Get list of columns to display in the inline table."""
        if cls.column_list:
            return cls._col_names(cls.column_list)
        mapper = sa_inspect(cls.model)
        pk_names = {c.key for c in cls.pk_columns}
        return [c.key for c in mapper.columns if c.key not in pk_names]

    @classmethod
    def _search_columns(cls) -> list[str]:
        """Get list of columns that are searchable."""
        return cls._col_names(cls.column_searchable_list)

    @classmethod
    def _fk_field_names(cls) -> list[str]:
        """Return column names that are foreign keys to any parent."""
        mapper = sa_inspect(cls.model)
        names: set[Any] = set()
        for rel in mapper.relationships:
            if rel.direction.name == "MANYTOONE":
                names.add(rel.key)
                for _remote, local_col in rel.synchronize_pairs:
                    names.add(local_col.key)
        return list(names)

    @classmethod
    def _form_excluded(cls) -> list[str]:
        """Get columns to exclude from the form.

        Excludes primary keys but keeps foreign keys for relationship editing.
        """
        explicit = cls._col_names(cls.form_excluded_columns)
        pk_names = [c.key for c in cls.pk_columns]
        return list(set(explicit + pk_names))

    @classmethod
    def _get_label(cls, col_name: str) -> str:
        """Get human-readable label for a column."""
        return cls.column_labels.get(col_name, col_name.replace("_", " ").title())

    # -----------------------------------------------------------------------
    # Form Creation
    # -----------------------------------------------------------------------

    @classmethod
    async def scaffold_form(cls, session_maker: SESSION_MAKER) -> type[Form]:
        """Create WTForm class for inline editing.

        Args:
            session_maker: SQLAlchemy session maker for async/ sync access.

        Returns:
            WTForms Form class configured for this inline model.
        """
        only = cls._form_only()
        exclude = None
        if only is None:
            exclude = cls._form_excluded()

        return await get_model_form(
            model=cls.model,
            session_maker=session_maker,
            only=only,
            exclude=exclude,
            column_labels=cls.column_labels,
            form_args=cls.form_args,
            form_widget_args=cls.form_widget_args,
            form_class=Form,
            form_overrides={},
            form_ajax_refs={},
            form_include_pk=False,
            form_converter=ModelConverter,
        )

    @classmethod
    def _form_only(cls) -> list[str] | None:
        """Get explicitly included form columns.

        Returns:
            List of column names to include, or None if not specified.
        """
        if cls.form_columns:
            return cls._col_names(cls.form_columns)
        return None

    # -----------------------------------------------------------------------
    # Query Helpers
    # -----------------------------------------------------------------------

    @classmethod
    def _parent_conditions(cls, fk_attr: str, parent_obj: Any) -> list[Any]:
        """Build SQLAlchemy filter conditions for parent relationship.

        Args:
            fk_attr: Foreign key attribute name.
            parent_obj: Parent model instance.

        Returns:
            List of filter conditions for querying child records.
        """
        mapper = sa_inspect(cls.model)
        rel = mapper.relationships.get(fk_attr)
        if rel is not None:
            conditions = []
            for remote_col, local_col in rel.synchronize_pairs:
                pk_name = remote_col.key
                fk_col = getattr(cls.model, local_col.key)
                conditions.append(fk_col == getattr(parent_obj, pk_name))
            return conditions
        col_attr = getattr(cls.model, fk_attr)
        parent_pk = _get_parent_pk(parent_obj)
        return [col_attr == parent_pk]

    # -----------------------------------------------------------------------
    # Paginated List
    # -----------------------------------------------------------------------

    @classmethod
    async def get_page(
        cls,
        session_maker: SESSION_MAKER,
        parent_obj: Any,
        page: int = 1,
        search: str = "",
    ) -> InlinePage:
        """Fetch paginated child records for the given parent.

        Args:
            session_maker: SQLAlchemy session maker.
            parent_obj: Parent model instance.
            page: Page number (1-indexed).
            search: Optional search term.

        Returns:
            InlinePage containing paginated results.
        """
        from sqladmin.helpers import is_async_session_maker
        from sqlalchemy.orm import joinedload, selectinload

        fk_attr = cls._get_fk_attr(type(parent_obj))
        cond = cls._parent_conditions(fk_attr, parent_obj)

        search_cond = None
        if search and cls._search_columns():
            parts = []
            for col_name in cls._search_columns():
                col = getattr(cls.model, col_name, None)
                if col is not None:
                    parts.append(col.ilike(f"%{search}%"))
            if parts:
                search_cond = or_(*parts)

        offset = (page - 1) * cls.page_size

        mapper = sa_inspect(cls.model)
        display_cols = cls._display_columns()
        eager_options = []

        for col_name in display_cols:
            rel = mapper.relationships.get(col_name)
            if rel is not None:
                if rel.direction.name in ("MANYTOONE", "ONETOONE"):
                    eager_options.append(joinedload(getattr(cls.model, col_name)))
                else:
                    eager_options.append(selectinload(getattr(cls.model, col_name)))

        async def _fetch(session: Any) -> tuple[list[Any], int]:
            count_stmt = sa_select(func.count()).select_from(cls.model).where(*cond)
            if search_cond is not None:
                count_stmt = count_stmt.where(search_cond)
            total = (await session.execute(count_stmt)).scalar() or 0

            row_stmt = sa_select(cls.model).where(*cond)
            if search_cond is not None:
                row_stmt = row_stmt.where(search_cond)

            for opt in eager_options:
                row_stmt = row_stmt.options(opt)

            row_stmt = row_stmt.offset(offset).limit(cls.page_size)
            rows = list((await session.execute(row_stmt)).scalars().all())
            return rows, total

        if is_async_session_maker(session_maker):
            async with session_maker() as session:
                rows, total = await _fetch(session)
        else:
            import anyio

            _r: list[Any] = []

            def _sync() -> None:
                import asyncio

                with session_maker() as s:
                    _r.extend(asyncio.get_event_loop().run_until_complete(_fetch(s)))

            await anyio.to_thread.run_sync(_sync)
            rows, total = _r[0], _r[1]

        return InlinePage(rows=rows, page=page, page_size=cls.page_size, count=total)

    # -----------------------------------------------------------------------
    # Single Row Operations
    # -----------------------------------------------------------------------

    @classmethod
    async def get_by_pk(cls, session_maker: SESSION_MAKER, pk_str: str) -> Any | None:
        """Fetch a single child record by its primary key."""
        from sqladmin.helpers import is_async_session_maker
        from sqlalchemy.orm import joinedload, selectinload

        pk_vals = _parse_pk(pk_str, cls.pk_columns)

        async def _fetch(session: Any) -> Any | None:
            conditions = [getattr(cls.model, k) == v for k, v in pk_vals.items()]

            stmt = sa_select(cls.model).where(*conditions)

            mapper = sa_inspect(cls.model)
            for rel in mapper.relationships:
                if rel.direction.name == "MANYTOONE":
                    stmt = stmt.options(joinedload(getattr(cls.model, rel.key)))

            result = await session.execute(stmt)
            return result.unique().scalars().first()

        if is_async_session_maker(session_maker):
            async with session_maker() as session:
                return await _fetch(session)
        return None

    @classmethod
    def encode_pk(cls, obj: Any) -> str:
        """Encode model instance primary key(s) into a string.

        Args:
            obj: Model instance.

        Returns:
            Comma-separated primary key string.
        """
        return ",".join(str(getattr(obj, col.key)) for col in cls.pk_columns)

    # -----------------------------------------------------------------------
    # CRUD Operations
    # -----------------------------------------------------------------------

    @classmethod
    async def create_child(
        cls,
        session_maker: SESSION_MAKER,
        parent_obj: Any,
        data: dict[str, Any],
    ) -> Any:
        """Create a new child record linked to the parent.

        Args:
            session_maker: SQLAlchemy session maker.
            parent_obj: Parent model instance.
            data: Form data dictionary.

        Returns:
            Created child model instance.
        """
        from sqladmin.helpers import is_async_session_maker

        fk_attr = cls._get_fk_attr(type(parent_obj))
        mapper = sa_inspect(cls.model)

        async def _do(session: Any) -> Any:
            prepared_data = {}
            for k, v in data.items():
                if (
                    k in mapper.relationships
                    and mapper.relationships[k].direction.name == "MANYTOONE"
                ):
                    for _, local_col in mapper.relationships[k].synchronize_pairs:
                        prepared_data[local_col.key] = v
                else:
                    prepared_data[k] = v

            obj = cls.model(**prepared_data)
            _set_fk(obj, fk_attr, parent_obj, mapper)

            session.add(obj)
            await session.commit()
            return obj

        if is_async_session_maker(session_maker):
            async with session_maker() as session:
                return await _do(session)

        return None

    @classmethod
    async def update_child(
        cls,
        session_maker: SESSION_MAKER,
        pk: Any,
        data: dict[str, Any],
    ) -> Any | None:
        """Update an existing child record.

        Args:
            session_maker: SQLAlchemy session maker.
            pk: Primary key value(s) as string.
            data: Form data dictionary.

        Returns:
            Updated child model instance or None.
        """
        from sqladmin.helpers import is_async_session_maker

        mapper = sa_inspect(cls.model)

        async def _do(session: Any) -> Any | None:
            obj = await session.get(cls.model, _parse_pk(pk, cls.pk_columns))
            if obj:
                for k, v in data.items():
                    if (
                        k in mapper.relationships
                        and mapper.relationships[k].direction.name == "MANYTOONE"
                    ):
                        for _, local_col in mapper.relationships[k].synchronize_pairs:
                            setattr(obj, local_col.key, v)
                            break
                    else:
                        setattr(obj, k, v)
                await session.commit()
            return obj

        if is_async_session_maker(session_maker):
            async with session_maker() as session:
                return await _do(session)
        return None

    @classmethod
    async def delete_child(
        cls,
        session_maker: SESSION_MAKER,
        pk_str: str,
    ) -> bool:
        """Delete a child record by its primary key.

        Args:
            session_maker: SQLAlchemy session maker.
            pk_str: Comma-separated primary key value(s).

        Returns:
            True if deleted, False if not found.
        """
        from sqladmin.helpers import is_async_session_maker

        pk_vals = _parse_pk(pk_str, cls.pk_columns)

        async def _do(session: Any) -> bool:
            conditions = [getattr(cls.model, k) == v for k, v in pk_vals.items()]
            result = await session.execute(sa_select(cls.model).where(*conditions))
            obj = result.scalars().first()
            if obj is None:
                return False
            await session.delete(obj)
            await session.commit()
            return True

        if is_async_session_maker(session_maker):
            async with session_maker() as session:
                return await _do(session)
        return False

    # -----------------------------------------------------------------------
    # Value Formatting
    # -----------------------------------------------------------------------

    @classmethod
    def get_display_value(cls, obj: Any, col_name: str) -> str:
        """Get safe display value for a column.

        Args:
            obj: Model instance.
            col_name: Column name to display.

        Returns:
            String representation or "—" if unavailable.
        """
        try:
            val = getattr(obj, col_name, "")

            if val is None:
                return "—"

            if hasattr(val, "__str__"):
                str_val = str(val)
                if str_val:
                    return str_val
                return "—"

            return str(val)
        except Exception:
            return "—"


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------


def _parse_pk(pk_str: Any, pk_columns: list[Any]) -> dict[str, Any]:
    """Parse a comma-separated PK string into a column-value dictionary."""
    parts = str(pk_str).split(",")
    return {col.key: parts[i] for i, col in enumerate(pk_columns) if i < len(parts)}


def _get_parent_pk(parent_obj: Any) -> Any:
    """Extract primary key value(s) from a parent object."""
    pks = get_primary_keys(type(parent_obj))
    if len(pks) == 1:
        return getattr(parent_obj, pks[0].key)
    return tuple(getattr(parent_obj, pk.key) for pk in pks)


def _set_fk(child_obj: Any, fk_attr: str, parent_obj: Any, mapper: Any) -> None:
    """Set foreign key attribute on child object."""
    rel = mapper.relationships.get(fk_attr)
    if rel is not None:
        setattr(child_obj, fk_attr, parent_obj)
    else:
        setattr(child_obj, fk_attr, _get_parent_pk(parent_obj))
