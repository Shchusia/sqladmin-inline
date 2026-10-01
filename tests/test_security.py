"""Inline endpoints must be protected exactly like sqladmin's own edit page."""

from __future__ import annotations

from typing import Any

import pytest
from sqladmin.authentication import AuthenticationBackend
from starlette.requests import Request

from sqladmin_inline import ModelViewWithInlines

from .conftest import (
    DB,
    AppFactory,
    CommentInline,
    Post,
    Tag,
    TagInline,
    client_for,
    inline_url,
)


class Backend(AuthenticationBackend):
    def __init__(self, allow: bool) -> None:
        super().__init__(secret_key="test-secret")
        self.allow = allow

    async def login(self, request: Request) -> bool:
        return self.allow

    async def logout(self, request: Request) -> bool:
        return True

    async def authenticate(self, request: Request) -> bool:
        return self.allow


def requests_for(post_id: int, tag_id: int) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        ("GET", inline_url(post_id), {}),
        ("GET", inline_url(post_id, action="form"), {}),
        (
            "POST",
            inline_url(post_id, action="save"),
            {"data": {"name": "x", "post": str(post_id)}},
        ),
        ("DELETE", inline_url(post_id, action="delete"), {"json": {"pks": [tag_id]}}),
        ("POST", inline_url(post_id, action="reorder"), {"json": {"pks": [tag_id]}}),
    ]


async def test_unauthenticated_requests_are_redirected(
    db: DB, make_app: AppFactory
) -> None:
    app, _ = make_app(authentication_backend=Backend(allow=False))
    post = db.post()
    (tag,) = db.tags(post, 1)
    async with client_for(app) as c:
        assert (await c.get(f"/admin/post/edit/{post.id}")).status_code == 302
        for method, url, kwargs in requests_for(post.id, tag.id):
            r = await c.request(method, url, **kwargs)
            assert r.status_code == 302, (method, url)
            assert r.headers["location"].endswith("/admin/login")
    assert [t.name for t in db.all(Tag)] == ["tag1"]  # nothing created or deleted


async def test_authenticated_requests_work(db: DB, make_app: AppFactory) -> None:
    app, _ = make_app(authentication_backend=Backend(allow=True))
    post = db.post()
    (tag,) = db.tags(post, 1)
    async with client_for(app) as c:
        assert (await c.get(f"/admin/post/edit/{post.id}")).status_code == 200
        for method, url, kwargs in requests_for(post.id, tag.id):
            assert (await c.request(method, url, **kwargs)).status_code == 200, (
                method,
                url,
            )


def view_with(**overrides: Any) -> type[ModelViewWithInlines]:
    return type(
        "RestrictedPostAdmin",
        (ModelViewWithInlines,),
        {"inlines": [TagInline, CommentInline], **overrides},
        model=Post,
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"is_accessible": lambda self, request: False},
        {"can_edit": False},
        {"check_can_edit": lambda self, request, model: _false()},
    ],
    ids=["is_accessible", "can_edit", "check_can_edit"],
)
async def test_parent_permissions_apply(
    db: DB, make_app: AppFactory, overrides: dict
) -> None:
    app, _ = make_app(view_with(**overrides))
    post = db.post()
    (tag,) = db.tags(post, 1)
    async with client_for(app) as c:
        for method, url, kwargs in requests_for(post.id, tag.id):
            assert (await c.request(method, url, **kwargs)).status_code == 403, (
                method,
                url,
            )
    assert [t.name for t in db.all(Tag)] == ["tag1"]


async def _false() -> bool:
    return False


async def test_form_edit_query_filters_apply(db: DB, make_app: AppFactory) -> None:
    """A tenant filter in form_edit_query hides the parent from inline endpoints too."""
    from sqlalchemy import select

    def form_edit_query(self: Any, request: Request) -> Any:
        return select(Post).where(
            Post.id == int(request.path_params["pk"]), Post.title != "hidden"
        )

    app, _ = make_app(view_with(form_edit_query=form_edit_query))
    visible, hidden = db.post("visible"), db.post("hidden")
    async with client_for(app) as c:
        assert (await c.get(inline_url(visible.id))).status_code == 200
        assert (await c.get(inline_url(hidden.id))).status_code == 404
