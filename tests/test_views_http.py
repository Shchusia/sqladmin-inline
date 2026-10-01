"""HTTP endpoints and the edit/create pages (async + sync session makers)."""

from __future__ import annotations

import re

import httpx
import pytest

from sqladmin_inline import InlineModelAdmin, ModelViewWithInlines

from .conftest import (
    DB,
    AppFactory,
    Comment,
    Post,
    Tag,
    TagInline,
    UserAdmin,
    client_for,
    inline_url,
)


async def delete(
    client: httpx.AsyncClient, url: str, **kwargs: object
) -> httpx.Response:
    return await client.request("DELETE", url, **kwargs)  # type: ignore[arg-type]


class TestList:
    async def test_empty(self, db: DB, client: httpx.AsyncClient) -> None:
        r = await client.get(inline_url(db.post().id))
        assert r.status_code == 200
        assert "No Tags yet" in r.text and 'id="inline-tag_inline"' in r.text

    async def test_rows_pagination_search(
        self, db: DB, client: httpx.AsyncClient
    ) -> None:
        post = db.post()
        db.tags(post, 5)
        r = await client.get(inline_url(post.id))
        assert "tag1" in r.text and "tag4" not in r.text
        assert "Showing 1–3 of 5" in r.text and "(2 more)" in r.text
        r2 = await client.get(inline_url(post.id) + "?page=2")
        assert "tag4" in r2.text and "tag1" not in r2.text
        r3 = await client.get(inline_url(post.id) + "?search=tag5")
        assert "tag5" in r3.text and "tag1" not in r3.text
        r4 = await client.get(inline_url(post.id) + "?search=zzz")
        assert "No results for" in r4.text

    async def test_garbage_page_param(self, db: DB, client: httpx.AsyncClient) -> None:
        post = db.post()
        for value in ("abc", "-3", "0", "999"):
            assert (
                await client.get(inline_url(post.id) + f"?page={value}")
            ).status_code == 200

    async def test_search_is_escaped_in_html(
        self, db: DB, client: httpx.AsyncClient
    ) -> None:
        r = await client.get(inline_url(db.post().id) + "?search=<script>x</script>")
        assert "<script>x</script>" not in r.text
        assert "&lt;script&gt;" in r.text

    async def test_author_column(self, db: DB, client: httpx.AsyncClient) -> None:
        post, user = db.post(), db.user("Bob")
        db.comments(post, 1, author=user)
        r = await client.get(inline_url(post.id, "comment_inline"))
        assert r.status_code == 200 and "Bob" in r.text and "<th>Text</th>" in r.text

    @pytest.mark.parametrize(
        ("url", "status"),
        [
            ("/admin/post/inline/nope/{pk}/list", 404),
            ("/admin/post/inline/tag_inline/999999/list", 404),
            ("/admin/post/inline/tag_inline/abc/list", 404),
            ("/admin/nope/inline/tag_inline/{pk}/list", 404),
            ("/admin/user/inline/tag_inline/{pk}/list", 404),  # plain ModelView
        ],
    )
    async def test_not_found(
        self, db: DB, client: httpx.AsyncClient, url: str, status: int
    ) -> None:
        r = await client.get(url.format(pk=db.post().id))
        assert r.status_code == status
        assert "error" in r.json()


class TestForm:
    async def test_add_form(self, db: DB, client: httpx.AsyncClient) -> None:
        post = db.post("Parent")
        r = await client.get(inline_url(post.id, "comment_inline", "form"))
        assert r.status_code == 200
        assert 'name="body"' in r.text and 'name="_child_pk" value=""' in r.text
        assert (
            f'data-save-url="/admin/post/inline/comment_inline/{post.id}/save"'
            in r.text
        )
        # the parent relationship is pre-selected
        assert re.search(rf'<option selected value="{post.id}">Parent</option>', r.text)

    async def test_edit_form_prefilled(self, db: DB, client: httpx.AsyncClient) -> None:
        post, user = db.post(), db.user("Bob")
        (c,) = db.comments(post, 1, author=user)
        r = await client.get(
            inline_url(post.id, "comment_inline", "form") + f"?pk={c.id}"
        )
        assert r.status_code == 200
        assert "Comment 1" in r.text
        assert f'<option selected value="{user.id}">Bob</option>' in r.text

    async def test_edit_form_of_other_parent(
        self, db: DB, client: httpx.AsyncClient
    ) -> None:
        a, b = db.post("a"), db.post("b")
        (t,) = db.tags(a, 1)
        r = await client.get(inline_url(b.id, action="form") + f"?pk={t.id}")
        assert r.status_code == 404

    async def test_permissions(self, db: DB, make_app: AppFactory) -> None:
        class ReadOnly(InlineModelAdmin, model=Tag):
            can_create = can_edit = can_delete = False
            order_field = "position"

        class Admin_(ModelViewWithInlines, model=Post):
            inlines = [ReadOnly]

        app, _ = make_app(Admin_)
        post = db.post()
        (t,) = db.tags(post, 1)
        async with client_for(app) as c:
            assert (await c.get(inline_url(post.id, action="form"))).status_code == 403
            assert (
                await c.get(inline_url(post.id, action="form") + f"?pk={t.id}")
            ).status_code == 403
            assert (
                await c.post(inline_url(post.id, action="save"), data={"name": "x"})
            ).status_code == 403
            r = await c.post(
                inline_url(post.id, action="save"),
                data={"name": "x", "_child_pk": t.id},
            )
            assert r.status_code == 403
            assert (
                await delete(
                    c, inline_url(post.id, action="delete"), json={"pks": [t.id]}
                )
            ).status_code == 403
            assert (
                await c.post(
                    inline_url(post.id, action="reorder"), json={"pks": [t.id]}
                )
            ).status_code == 403
            listing = await c.get(inline_url(post.id))
            assert "inline-add-btn" not in listing.text
            assert "inline-edit-btn" not in listing.text
            assert "inline-select-box" not in listing.text
            assert "data-reorder-url" not in listing.text
        assert len(db.all(Tag)) == 1


class TestSave:
    async def test_create(self, db: DB, client: httpx.AsyncClient) -> None:
        post = db.post()
        r = await client.post(
            inline_url(post.id, action="save"),
            data={"name": "via-http", "post": str(post.id)},
        )
        assert r.status_code == 200 and r.json()["ok"] is True
        (tag,) = db.all(Tag)
        assert (tag.name, tag.post_id, r.json()["pk"]) == (
            "via-http",
            post.id,
            str(tag.id),
        )

    async def test_create_comment_with_author(
        self, db: DB, client: httpx.AsyncClient
    ) -> None:
        post, user = db.post(), db.user()
        r = await client.post(
            inline_url(post.id, "comment_inline", "save"),
            data={"body": "hello", "post": str(post.id), "author": str(user.id)},
        )
        assert r.status_code == 200, r.text
        (c,) = db.all(Comment)
        assert (c.author_id, c.post_id) == (user.id, post.id)

    async def test_update(self, db: DB, client: httpx.AsyncClient) -> None:
        post = db.post()
        (t,) = db.tags(post, 1)
        r = await client.post(
            inline_url(post.id, action="save"),
            data={"name": "renamed", "_child_pk": str(t.id), "post": str(post.id)},
        )
        assert r.status_code == 200
        assert db.get(Tag, t.id).name == "renamed"

    async def test_update_child_of_other_parent(
        self, db: DB, client: httpx.AsyncClient
    ) -> None:
        a, b = db.post("a"), db.post("b")
        (t,) = db.tags(a, 1)
        r = await client.post(
            inline_url(b.id, action="save"),
            data={"name": "hacked", "_child_pk": str(t.id), "post": str(b.id)},
        )
        assert r.status_code == 404
        assert db.get(Tag, t.id).name == "tag1"

    async def test_validation_error_returns_form(
        self, db: DB, client: httpx.AsyncClient
    ) -> None:
        post = db.post()
        r = await client.post(
            inline_url(post.id, action="save"), data={"_child_pk": ""}
        )
        assert r.status_code == 422
        assert 'id="inline-modal-form"' in r.text and "is-invalid" in r.text
        assert db.all(Tag) == []

    async def test_database_error(
        self, db: DB, make_app: AppFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def boom(*args: object, **kwargs: object) -> None:
            raise RuntimeError("constraint failed")

        monkeypatch.setattr(TagInline, "create_child", boom)
        app, _ = make_app()
        post = db.post()
        async with client_for(app) as c:
            r = await c.post(
                inline_url(post.id, action="save"),
                data={"name": "x", "post": str(post.id)},
            )
        assert r.status_code == 400 and r.json() == {"error": "constraint failed"}


class TestDelete:
    async def test_bulk(self, db: DB, client: httpx.AsyncClient) -> None:
        post = db.post()
        tags = db.tags(post, 3)
        r = await delete(
            client,
            inline_url(post.id, action="delete"),
            json={"pks": [tags[0].id, str(tags[1].id), 999]},
        )
        assert r.json() == {"deleted": 2}
        assert [t.name for t in db.all(Tag)] == ["tag3"]

    async def test_other_parent_is_untouched(
        self, db: DB, client: httpx.AsyncClient
    ) -> None:
        a, b = db.post("a"), db.post("b")
        (t,) = db.tags(a, 1)
        r = await delete(
            client, inline_url(b.id, action="delete"), json={"pks": [t.id]}
        )
        assert r.json() == {"deleted": 0}
        assert len(db.all(Tag)) == 1

    @pytest.mark.parametrize("body", [b"not json", b'{"pks": 5}', b"[1, 2]"])
    async def test_bad_body(
        self, db: DB, client: httpx.AsyncClient, body: bytes
    ) -> None:
        r = await delete(
            client,
            inline_url(db.post().id, action="delete"),
            content=body,
            headers={"content-type": "application/json"},
        )
        assert r.status_code == 400


class TestReorder:
    async def test_reorder(self, db: DB, client: httpx.AsyncClient) -> None:
        post = db.post()
        t1, t2, t3 = db.tags(post, 3)
        r = await client.post(
            inline_url(post.id, action="reorder"), json={"pks": [t3.id, t1.id, t2.id]}
        )
        assert r.json() == {"ok": True, "updated": 3}
        listing = await client.get(inline_url(post.id))
        assert (
            listing.text.index("tag3")
            < listing.text.index("tag1")
            < listing.text.index("tag2")
        )

    async def test_not_enabled_and_bad_body(
        self, db: DB, client: httpx.AsyncClient
    ) -> None:
        post = db.post()
        assert (
            await client.post(
                inline_url(post.id, "comment_inline", "reorder"), json={"pks": []}
            )
        ).status_code == 404
        assert (
            await client.post(inline_url(post.id, action="reorder"), content=b"{")
        ).status_code == 400


class TestEditPage:
    async def test_get(self, db: DB, client: httpx.AsyncClient) -> None:
        post = db.post("Hello")
        db.tags(post, 2)
        r = await client.get(f"/admin/post/edit/{post.id}")
        assert r.status_code == 200
        html = r.text
        for snippet in (
            'id="inline-tag_inline"',
            'id="inline-comment_inline"',
            "fa-solid fa-tag",
            "tag1",
            'id="inline-modal"',
            "js/sqladmin-inline.js",
            "css/sqladmin-inline.css",
        ):
            assert snippet in html, snippet
        # sidebar layout: comments (center) in the left column, tags in the right one
        left, right = (
            html.index("inline-main-column"),
            html.index("inline-sidebar-column"),
        )
        assert (
            left
            < html.index('id="inline-comment_inline"')
            < right
            < html.index('id="inline-tag_inline"')
        )
        # the parent form is sqladmin's own, inlines are outside of it
        assert html.index("</form>") < html.index('id="inline-comment_inline"')
        # relationships edited inline are removed from the parent form
        assert 'name="tags"' not in html and 'name="comments"' not in html

    async def test_center_only(self, db: DB, make_app: AppFactory) -> None:
        class Center(InlineModelAdmin, model=Tag):
            identity = "center_tags"

        class Admin_(ModelViewWithInlines, model=Post):
            inlines = [Center]

        app, _ = make_app(Admin_)
        post = db.post()
        async with client_for(app) as c:
            html = (await c.get(f"/admin/post/edit/{post.id}")).text
        assert 'id="inline-center_tags"' in html
        assert "inline-sidebar-column" not in html

    async def test_without_inlines(self, db: DB, make_app: AppFactory) -> None:
        class Admin_(ModelViewWithInlines, model=Post):
            pass

        app, _ = make_app(Admin_)
        post = db.post()
        async with client_for(app) as c:
            r = await c.get(f"/admin/post/edit/{post.id}")
        assert r.status_code == 200 and "inline-section" not in r.text

    async def test_page_query_params(self, db: DB, client: httpx.AsyncClient) -> None:
        post = db.post()
        db.tags(post, 5)
        r = await client.get(
            f"/admin/post/edit/{post.id}?_il_tag_inline_page=2&_il_tag_inline_search=tag"
        )
        assert "tag4" in r.text and "tag1" not in r.text

    async def test_post_saves_and_redirects(
        self, db: DB, client: httpx.AsyncClient
    ) -> None:
        post = db.post()
        r = await client.post(
            f"/admin/post/edit/{post.id}", data={"title": "Updated", "save": "Save"}
        )
        assert r.status_code == 302
        assert db.get(Post, post.id).title == "Updated"

    async def test_post_invalid_rerenders_with_inlines(
        self, db: DB, client: httpx.AsyncClient
    ) -> None:
        post = db.post()
        r = await client.post(
            f"/admin/post/edit/{post.id}", data={"title": "", "save": "Save"}
        )
        assert r.status_code == 400
        assert 'id="inline-tag_inline"' in r.text

    async def test_missing_parent(self, client: httpx.AsyncClient) -> None:
        assert (await client.get("/admin/post/edit/999999")).status_code == 404


async def test_create_page_hint(client: httpx.AsyncClient) -> None:
    r = await client.get("/admin/post/create")
    assert r.status_code == 200
    assert "Related items" in r.text and "Tags, Comments" in r.text
    assert "js/sqladmin-inline.js" in r.text


async def test_setup_is_idempotent(db: DB, make_app: AppFactory) -> None:
    from sqladmin_inline import setup_inline_routes

    _, admin = make_app()
    before = len(admin.admin.router.routes)
    setup_inline_routes(admin)
    assert len(admin.admin.router.routes) == before


async def test_build_inline_contexts(db: DB, admin_view: ModelViewWithInlines) -> None:
    from .conftest import fake_request

    post = db.post()
    contexts = await admin_view._build_inline_contexts(fake_request(), post)
    assert [c["identity"] for c in contexts] == ["tag_inline", "comment_inline"]
    assert contexts[0]["base_url"] == f"/post/inline/tag_inline/{post.id}"
    assert contexts[0]["sortable"] is True and contexts[1]["sortable"] is False
    empty = await admin_view._build_inline_contexts(fake_request(), None)
    assert all(c["pagination"].count == 0 and c["parent_pk"] == "" for c in empty)
    assert admin_view.find_inline("nope") is None
    assert admin_view._inline_relationship_names() == ["tags", "comments"]
    assert UserAdmin  # imported for app setup


async def test_legacy_edit_route_wrapper(
    db: DB, make_app: AppFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """sqladmin < 0.31 has no edit_context hook: the edit route is wrapped instead."""
    from sqladmin.authentication import AuthenticationBackend

    import sqladmin_inline.views as views

    async def no_hook(self: object, request: object) -> dict:
        return {}

    class Deny(AuthenticationBackend):
        async def login(self, request: object) -> bool:
            return False

        async def logout(self, request: object) -> bool:
            return True

        async def authenticate(self, request: object) -> bool:
            return False

    monkeypatch.setattr(views, "HAS_CONTEXT_HOOKS", False)
    monkeypatch.setattr(ModelViewWithInlines, "edit_context", no_hook)

    class Hidden(ModelViewWithInlines, model=Post):
        inlines = [TagInline]

        def is_accessible(self, request: object) -> bool:
            return False

    class NoInlines(ModelViewWithInlines, model=Post):
        pass

    post = db.post()
    db.tags(post, 2)

    app, _ = make_app()
    async with client_for(app) as c:
        r = await c.get(f"/admin/post/edit/{post.id}")
        assert (
            r.status_code == 200
            and 'id="inline-tag_inline"' in r.text
            and "tag1" in r.text
        )
        assert (await c.get("/admin/post/edit/999999")).status_code == 404

    for view, status in ((Hidden, 403), (NoInlines, 200)):
        app, _ = make_app(view)
        async with client_for(app) as c:
            assert (await c.get(f"/admin/post/edit/{post.id}")).status_code == status

    app, _ = make_app(authentication_backend=Deny(secret_key="x"))
    async with client_for(app) as c:
        assert (await c.get(f"/admin/post/edit/{post.id}")).status_code == 302
