"""Module-level helpers of sqladmin_inline.views (ported from the 0.0.x suite)."""

from __future__ import annotations

import re

import pytest

import sqladmin_inline
from sqladmin_inline.views import (
    _encode_parent_pk,
    _get_parent_by_pk,
    _int_param,
    _prefix,
)

from .conftest import DB, CommentInline, Post, PostAdmin, TagInline, fake_request


class TestPrefix:
    def test_values(self) -> None:
        assert _prefix(TagInline) == "tag"
        assert _prefix(CommentInline) == "comment"
        assert re.fullmatch(r"[a-z0-9_]+", _prefix(TagInline))


def test_encode_parent_pk() -> None:
    assert _encode_parent_pk(Post(id=7)) == "7"
    assert isinstance(_encode_parent_pk(Post(id=42)), str)


@pytest.mark.parametrize(
    ("raw", "expected"), [("3", 3), ("0", 1), ("-2", 1), ("x", 1), (None, 1)]
)
def test_int_param(raw: object, expected: int) -> None:
    assert _int_param(raw) == expected


class TestGetParentByPk:
    async def test_existing(self, db: DB, admin_view: PostAdmin) -> None:
        post = db.post()
        found = await _get_parent_by_pk(admin_view, str(post.id))
        assert found is not None and found.id == post.id

    async def test_with_request(self, db: DB, admin_view: PostAdmin) -> None:
        post = db.post()
        found = await _get_parent_by_pk(admin_view, str(post.id), fake_request())
        assert found.id == post.id

    @pytest.mark.parametrize("pk", ["999999", "abc"])
    async def test_missing_or_malformed(
        self, db: DB, admin_view: PostAdmin, pk: str
    ) -> None:
        assert await _get_parent_by_pk(admin_view, pk) is None


async def test_context_has_all_template_keys(db: DB, admin_view: PostAdmin) -> None:
    post = db.post()
    required = {
        "inline_cls",
        "identity",
        "parent_identity",
        "prefix",
        "label",
        "icon",
        "layout",
        "display_columns",
        "column_labels",
        "pagination",
        "search",
        "search_enabled",
        "can_create",
        "can_edit",
        "can_delete",
        "order_field",
        "sortable",
        "column_default_sort",
        "parent_pk",
        "base_url",
    }
    for ctx in await admin_view._build_inline_contexts(fake_request(), post):
        assert required <= ctx.keys()


def test_version_is_exposed() -> None:
    assert isinstance(sqladmin_inline.__version__, str)
