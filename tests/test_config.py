"""InlineModelAdmin class configuration, introspection and pure helpers."""

from __future__ import annotations

import decimal
import uuid

import pytest
from sqladmin.exceptions import InvalidModelError
from sqlalchemy import Column, Integer, Numeric, String, Uuid
from sqlalchemy.orm import DeclarativeBase

from sqladmin_inline import InlineModelAdmin
from sqladmin_inline.inline import (
    EMPTY_VALUE,
    InlineModelAdminMeta,
    _coerce_value,
    _display_value,
    _identity_conditions,
    _parse_pk,
    encode_identity,
)

from .conftest import Attachment, Comment, CommentInline, Post, Tag, TagInline, User


class TestMetaclass:
    def test_model_metadata(self) -> None:
        assert TagInline.model is Tag
        assert [c.key for c in TagInline.pk_columns] == ["id"]
        assert TagInline.identity == "tag_inline"
        assert TagInline.inline_label == "Tags"

    def test_defaults(self) -> None:
        class Plain(InlineModelAdmin, model=Tag):
            pass

        assert Plain.inline_label == "Tags"
        assert Plain.icon is None
        assert Plain.layout == "center"
        assert Plain.page_size == 5
        assert Plain.can_create and Plain.can_edit and Plain.can_delete

    def test_identity_can_be_overridden(self) -> None:
        class Second(InlineModelAdmin, model=Tag):
            identity = "tags_second"

        assert Second.identity == "tags_second"

    def test_base_class_without_model(self) -> None:
        class Abstract(InlineModelAdmin):
            page_size = 10

        assert not hasattr(Abstract, "identity")

    def test_not_a_model(self) -> None:
        with pytest.raises(InvalidModelError):

            class Bad(InlineModelAdmin, model=str):
                pass

    @pytest.mark.parametrize(
        ("attrs", "message"),
        [
            ({"layout": "left"}, "layout"),
            ({"page_size": 0}, "page_size"),
            ({"page_size": True}, "page_size"),
            ({"order_field": "nope"}, "order_field"),
        ],
    )
    def test_invalid_configuration(self, attrs: dict, message: str) -> None:
        with pytest.raises(ValueError, match=message):
            InlineModelAdminMeta("Bad", (InlineModelAdmin,), attrs, model=Tag)


class TestForeignKey:
    def test_auto_detect_relationship(self) -> None:
        assert TagInline._get_fk_attr(Post) == "post"
        assert CommentInline._get_fk_attr(Post()) == "post"

    def test_auto_detect_plain_column(self) -> None:
        class AttachmentInline(InlineModelAdmin, model=Attachment):
            pass

        assert AttachmentInline._get_fk_attr(Post) == "post_id"
        assert AttachmentInline._fk_pairs(Post) == [("post_id", "id")]

    def test_explicit_fk_attr(self) -> None:
        class Explicit(InlineModelAdmin, model=Tag):
            fk_attr = "post_id"

        assert Explicit._get_fk_attr(Post) == "post_id"
        assert Explicit._fk_pairs(Post) == [("post_id", "id")]
        assert Explicit._parent_keys(Post) == {"post_id", "post"}

    def test_explicit_fk_column_without_fk_constraint(self) -> None:
        class Loose(InlineModelAdmin, model=Tag):
            fk_attr = "position"

        assert Loose._fk_pairs(Post) == [("position", "id")]

    def test_bad_fk_attr(self) -> None:
        class Broken(InlineModelAdmin, model=Tag):
            fk_attr = "missing"

        with pytest.raises(ValueError, match="neither a relationship"):
            Broken._fk_pairs(Post)

    def test_unrelated_parent(self) -> None:
        with pytest.raises(ValueError, match="Cannot auto-detect"):
            TagInline._get_fk_attr(User)

    def test_pairs_and_parent_keys(self) -> None:
        assert TagInline._fk_pairs(Post) == [("post_id", "id")]
        assert TagInline._parent_keys(Post) == {"post", "post_id"}
        # author points to User, not to Post
        assert CommentInline._parent_keys(Post) == {"post", "post_id"}

    def test_parent_conditions(self) -> None:
        cond = TagInline._parent_conditions(None, Post(id=7))
        assert len(cond) == 1
        assert (
            str(cond[0].compile(compile_kwargs={"literal_binds": True}))
            == "tags.post_id = 7"
        )


class TestColumns:
    def test_display_columns(self) -> None:
        assert TagInline._display_columns() == ["name"]

        class All(InlineModelAdmin, model=Tag):
            pass

        assert All._display_columns() == ["name", "position", "post_id"]

    def test_search_columns_skip_non_columns(self) -> None:
        class S(InlineModelAdmin, model=Comment):
            column_searchable_list = [Comment.body, "author", "missing"]

        assert S._search_columns() == ["body"]

    def test_labels(self) -> None:
        assert CommentInline._get_label("body") == "Text"
        assert CommentInline._get_label("author_id") == "Author Id"

    def test_form_helpers(self) -> None:
        assert TagInline._form_excluded() == ["id", "position"]
        assert TagInline._form_only() is None

        class Only(InlineModelAdmin, model=Tag):
            form_columns = [Tag.name]

        assert Only._form_only() == ["name"]
        assert CommentInline._fk_field_names() == [
            "author",
            "author_id",
            "post",
            "post_id",
        ]

    def test_col_names(self) -> None:
        assert TagInline._col_names([Tag.name, "post_id"]) == ["name", "post_id"]


class TestPrimaryKeys:
    def test_parse_pk_coerces_to_column_type(self) -> None:
        assert _parse_pk("42", TagInline.pk_columns) == {"id": 42}
        assert _parse_pk(42, TagInline.pk_columns) == {"id": 42}
        assert _parse_pk("42,ignored", TagInline.pk_columns) == {"id": 42}

    def test_parse_pk_invalid(self) -> None:
        with pytest.raises(ValueError):
            _parse_pk("abc", TagInline.pk_columns)

    def test_identity_conditions(self) -> None:
        class B(DeclarativeBase):
            pass

        class Pair(B):
            __tablename__ = "pair"
            a = Column(Integer, primary_key=True)
            b = Column(String, primary_key=True)

        with pytest.raises(ValueError, match="Incomplete"):
            _identity_conditions(Pair, "1")
        with pytest.raises(ValueError, match="Invalid value"):
            _identity_conditions(Tag, "")
        assert len(_identity_conditions(Pair, "1,x")) == 2
        assert encode_identity(Pair(a=1, b="x")) == "1,x"

    def test_encode(self) -> None:
        assert TagInline.encode_pk(Tag(id=5)) == "5"
        assert encode_identity(Post(id=9)) == "9"

    def test_coerce_types(self) -> None:
        class B(DeclarativeBase):
            pass

        u = uuid.uuid4()
        cols = {
            "s": Column("s", String),
            "u": Column("u", Uuid),
            "d": Column("d", Numeric),
            "i": Column("i", Integer),
        }
        assert _coerce_value(cols["s"], 5) == "5"
        assert _coerce_value(cols["u"], str(u)) == u
        assert _coerce_value(cols["d"], "1.5") == decimal.Decimal("1.5")
        assert _coerce_value(cols["i"], None) is None
        with pytest.raises(ValueError):
            _coerce_value(cols["u"], "not-a-uuid")

    def test_coerce_unknown_python_type(self) -> None:
        from sqlalchemy.types import NullType, TypeDecorator

        class Opaque(TypeDecorator):
            impl = NullType
            cache_ok = True

        assert _coerce_value(Column("x", Opaque()), "raw") == "raw"


class TestDisplayValue:
    def test_values(self) -> None:
        assert _display_value(Tag(name="py"), "name") == "py"
        assert _display_value(Tag(name=None), "name") == EMPTY_VALUE
        assert _display_value(Tag(name=""), "name") == EMPTY_VALUE
        assert _display_value(Tag(), "missing") == EMPTY_VALUE
        assert TagInline.get_display_value(Tag(position=0), "position") == "0"

    def test_broken_values(self) -> None:
        class Boom:
            @property
            def prop(self) -> str:
                raise RuntimeError("detached")

            @property
            def weird(self) -> object:
                class NoStr:
                    def __str__(self) -> str:
                        raise RuntimeError

                return NoStr()

        assert _display_value(Boom(), "prop") == EMPTY_VALUE
        assert _display_value(Boom(), "weird") == EMPTY_VALUE

    def test_deprecated_alias(self) -> None:
        assert (
            InlineModelAdminMeta.get_display_value_safe(Comment(), "author")
            == EMPTY_VALUE
        )
