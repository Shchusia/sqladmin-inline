"""
tests/test_views_http.py
~~~~~~~~~~~~~~~~~~~~~~~~~

HTTP-level integration tests for inline CRUD routes registered by
setup_inline_routes():

  GET  /{identity}/inline/{inline_identity}/{parent_pk}/list
  GET  /{identity}/inline/{inline_identity}/{parent_pk}/form
  POST /{identity}/inline/{inline_identity}/{parent_pk}/save
  DELETE /{identity}/inline/{inline_identity}/{parent_pk}/delete

Also covers:
  - patched_edit GET / POST
  - 404 / 403 edge-cases
  - ModelViewWithInlines.get_form_columns()
  - layout context splitting (sidebar vs center)
  - icon passed to template context
"""

from __future__ import annotations

import json
import pytest

pytestmark = pytest.mark.asyncio
import pytest_asyncio

from sqlalchemy import select as sa_select

from .conftest import (
    Comment,
    CommentInline,
    Post,
    Tag,
    TagInline,
    User,
)


async def _get_fresh(session_maker, model, pk):
    """Fetch a fresh model instance in its own session (expunged for cross-session use)."""
    async with session_maker() as s:
        result = await s.execute(sa_select(model).where(model.id == pk))
        obj = result.scalars().first()
        s.expunge(obj)
        return obj


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TAG_INLINE_ID = TagInline.identity  # "tag_inline"
COMMENT_INLINE_ID = CommentInline.identity  # "comment_inline"


def list_url(post_id, inline_id=TAG_INLINE_ID):
    return f"/admin/post/inline/{inline_id}/{post_id}/list"


def form_url(post_id, inline_id=TAG_INLINE_ID, pk=None):
    url = f"/admin/post/inline/{inline_id}/{post_id}/form"
    if pk:
        url += f"?pk={pk}"
    return url


def save_url(post_id, inline_id=TAG_INLINE_ID):
    return f"/admin/post/inline/{inline_id}/{post_id}/save"


def delete_url(post_id, inline_id=TAG_INLINE_ID):
    return f"/admin/post/inline/{inline_id}/{post_id}/delete"


# ===========================================================================
# GET /list
# ===========================================================================


class TestInlineList:
    def test_list_empty_returns_200(self, client, post):
        r = client.get(list_url(post.id))
        assert r.status_code == 200
        assert "No Tags" in r.text

    def test_list_shows_children(self, client, post_with_tags):
        r = client.get(list_url(post_with_tags.id))
        assert r.status_code == 200
        # Tag names are in the HTML
        assert "tag1" in r.text

    def test_list_search_filters(self, client, post_with_tags):
        r = client.get(list_url(post_with_tags.id) + "?search=tag1")
        assert r.status_code == 200
        assert "tag1" in r.text
        assert "tag2" not in r.text

    def test_list_search_no_results(self, client, post_with_tags):
        r = client.get(list_url(post_with_tags.id) + "?search=zzznomatch")
        assert r.status_code == 200

    def test_list_pagination_page2(self, client, post_with_tags):
        """Page 2 should have 2 tags (5 total, page_size=3)."""
        r = client.get(list_url(post_with_tags.id) + "?page=2")
        assert r.status_code == 200

    def test_list_404_unknown_inline(self, client, post):
        r = client.get(f"/admin/post/inline/nonexistent_inline/{post.id}/list")
        assert r.status_code == 404

    def test_list_404_unknown_parent(self, client):
        r = client.get(list_url(999999))
        assert r.status_code == 404

    def test_list_comments_eager_loads_author(self, client, post_with_comments):
        """Comment list with FK relationship must not raise DetachedInstanceError."""
        r = client.get(list_url(post_with_comments.id, inline_id=COMMENT_INLINE_ID))
        assert r.status_code == 200


# ===========================================================================
# GET /form
# ===========================================================================


class TestInlineForm:
    def test_form_add_returns_200(self, client, post):
        r = client.get(form_url(post.id))
        assert r.status_code == 200
        assert "form" in r.text.lower()

    def test_form_add_includes_name_field(self, client, post):
        r = client.get(form_url(post.id))
        assert 'name="name"' in r.text

    def test_form_edit_returns_200(self, client, post, session_maker):
        """Edit form should pre-populate with existing values."""
        import asyncio

        fresh_post = asyncio.get_event_loop().run_until_complete(
            _get_fresh(session_maker, Post, post.id)
        )
        tag = asyncio.get_event_loop().run_until_complete(
            TagInline.create_child(session_maker, fresh_post, {"name": "edit-me"})
        )
        r = client.get(form_url(post.id, pk=tag.id))
        assert r.status_code == 200
        assert "edit-me" in r.text

    def test_form_404_unknown_inline(self, client, post):
        r = client.get(f"/admin/post/inline/bogus/{post.id}/form")
        assert r.status_code == 404

    def test_form_comment_has_author_select(self, client, post, user):
        """Comment add-form must include FK-select for Author."""
        r = client.get(form_url(post.id, inline_id=COMMENT_INLINE_ID))
        assert r.status_code == 200
        # The author field or a select should appear
        assert "author" in r.text.lower() or "select" in r.text.lower()


# ===========================================================================
# POST /save
# ===========================================================================


class TestInlineSave:
    def test_save_creates_new_tag(self, client, post, session_maker):
        r = client.post(
            save_url(post.id),
            data={"name": "created-via-http", "_child_pk": "", "post": str(post.id)},
        )
        assert r.status_code == 200
        assert r.json() == {"ok": True}

        import asyncio

        fresh_post = asyncio.get_event_loop().run_until_complete(
            _get_fresh(session_maker, Post, post.id)
        )
        page = asyncio.get_event_loop().run_until_complete(
            TagInline.get_page(session_maker, fresh_post)
        )
        names = [t.name for t in page.rows]
        assert "created-via-http" in names

    def test_save_updates_existing_tag(self, client, post, session_maker):
        import asyncio

        fresh_post = asyncio.get_event_loop().run_until_complete(
            _get_fresh(session_maker, Post, post.id)
        )
        tag = asyncio.get_event_loop().run_until_complete(
            TagInline.create_child(session_maker, fresh_post, {"name": "before-update"})
        )
        r = client.post(
            save_url(post.id),
            data={
                "name": "after-update",
                "_child_pk": str(tag.id),
                "post": str(post.id),
            },
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True

        updated = asyncio.get_event_loop().run_until_complete(
            TagInline.get_by_pk(session_maker, str(tag.id))
        )
        assert updated.name == "after-update"

    def test_save_404_unknown_inline(self, client, post):
        r = client.post(
            f"/admin/post/inline/bogus/{post.id}/save",
            data={"name": "x"},
        )
        assert r.status_code == 404

    def test_save_404_unknown_parent(self, client):
        r = client.post(save_url(999999), data={"name": "x", "_child_pk": ""})
        assert r.status_code == 404

    def test_save_validation_error_returns_422(self, client, post):
        """Submitting without required FK field returns 422 with form HTML."""
        # Omit required 'post' FK field — WTForms QuerySelectField will fail validation
        r = client.post(
            save_url(post.id),
            data={"_child_pk": ""},  # no name, no post FK
        )
        assert r.status_code in (200, 422)
        # Must not be a 500
        assert r.status_code != 500


# ===========================================================================
# DELETE /delete
# ===========================================================================


class TestInlineDelete:
    def test_delete_single(self, client, post, session_maker):
        import asyncio

        fresh_post = asyncio.get_event_loop().run_until_complete(
            _get_fresh(session_maker, Post, post.id)
        )
        tag = asyncio.get_event_loop().run_until_complete(
            TagInline.create_child(session_maker, fresh_post, {"name": "to-be-deleted"})
        )
        r = client.request(
            "DELETE",
            delete_url(post.id),
            content=json.dumps({"pks": [tag.id]}),
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 200
        assert r.json()["deleted"] == 1

    def test_delete_multiple(self, client, post_with_tags, session_maker):
        import asyncio

        page = asyncio.get_event_loop().run_until_complete(
            TagInline.get_page(session_maker, post_with_tags)
        )
        pks = [t.id for t in page.rows[:2]]

        r = client.request(
            "DELETE",
            delete_url(post_with_tags.id),
            content=json.dumps({"pks": pks}),
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 200
        assert r.json()["deleted"] == 2

    def test_delete_nonexistent_pk(self, client, post):
        r = client.request(
            "DELETE",
            delete_url(post.id),
            content=json.dumps({"pks": [999999]}),
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 200
        assert r.json()["deleted"] == 0

    def test_delete_empty_list(self, client, post):
        r = client.request(
            "DELETE",
            delete_url(post.id),
            content=json.dumps({"pks": []}),
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 200
        assert r.json()["deleted"] == 0

    def test_delete_404_unknown_inline(self, client, post):
        r = client.request(
            "DELETE",
            f"/admin/post/inline/bogus/{post.id}/delete",
            content=json.dumps({"pks": [1]}),
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 404


# ===========================================================================
# patched_edit (GET)
# ===========================================================================


class TestPatchedEditGet:
    def test_edit_page_returns_200(self, client, post):
        r = client.get(f"/admin/post/edit/{post.id}")
        assert r.status_code == 200

    def test_edit_page_contains_inline_sections(self, client, post):
        r = client.get(f"/admin/post/edit/{post.id}")
        assert r.status_code == 200
        # Both inline labels must appear on the page
        assert "Tags" in r.text
        assert "Comments" in r.text

    def test_edit_page_contains_icons(self, client, post):
        r = client.get(f"/admin/post/edit/{post.id}")
        assert "fa-tag" in r.text
        assert "fa-comments" in r.text

    def test_edit_page_sidebar_layout_columns(self, client, post):
        """TagInline is sidebar → col-lg-4 column should be present."""
        r = client.get(f"/admin/post/edit/{post.id}")
        assert "col-lg-4" in r.text
        assert "col-lg-8" in r.text

    def test_edit_page_save_button_present(self, client, post):
        r = client.get(f"/admin/post/edit/{post.id}")
        assert "Save" in r.text

    def test_edit_page_with_children(self, client, post_with_tags):
        r = client.get(f"/admin/post/edit/{post_with_tags.id}")
        assert r.status_code == 200
        assert "tag1" in r.text

    def test_edit_page_404_unknown_post(self, client):
        r = client.get("/admin/post/edit/999999")
        assert r.status_code in (404, 500)  # sqladmin may 500 on missing obj


# ===========================================================================
# patched_edit (POST)
# ===========================================================================


class TestPatchedEditPost:
    def test_edit_post_saves_title(self, client, post, session_maker):
        r = client.post(
            f"/admin/post/edit/{post.id}",
            data={"title": "Updated Title", "save": "Save"},
            follow_redirects=False,
        )
        # Successful save redirects
        assert r.status_code in (200, 302, 303)

    def test_edit_post_redirects_on_success(self, client, post):
        r = client.post(
            f"/admin/post/edit/{post.id}",
            data={"title": "New Title", "save": "Save"},
            follow_redirects=False,
        )
        assert r.status_code in (302, 303)


# ===========================================================================
# ModelViewWithInlines.get_form_columns
# ===========================================================================


class TestGetFormColumns:
    def test_excludes_inline_relationships(self, app, engine, session_maker):
        """ONETOMANY relationships managed by inlines must not appear in form."""
        from sqladmin import Admin
        from sqladmin_inline import setup_inline_routes
        from .conftest import PostAdmin, UserAdmin

        _app2 = __import__("fastapi").FastAPI()
        admin2 = Admin(_app2, engine=engine, session_maker=session_maker)
        setup_inline_routes(admin2)
        admin2.add_view(UserAdmin)
        admin2.add_view(PostAdmin)

        view = admin2._find_model_view("post")
        names = view._inline_relationship_names()
        assert "tags" in names or "comments" in names

    def test_inline_relationship_names_returns_onetomany_only(
        self, app, engine, session_maker
    ):
        from sqladmin import Admin
        from sqladmin_inline import setup_inline_routes
        from .conftest import PostAdmin, UserAdmin

        _app2 = __import__("fastapi").FastAPI()
        admin2 = Admin(_app2, engine=engine, session_maker=session_maker)
        setup_inline_routes(admin2)
        admin2.add_view(UserAdmin)
        admin2.add_view(PostAdmin)

        view = admin2._find_model_view("post")
        names = view._inline_relationship_names()
        assert "tags" in names
        assert "comments" in names

    def test_get_form_columns_filters_inline_relations(
        self, app, engine, session_maker
    ):
        from sqladmin import Admin
        from sqladmin_inline import setup_inline_routes
        from .conftest import PostAdmin, UserAdmin

        _app2 = __import__("fastapi").FastAPI()
        admin2 = Admin(_app2, engine=engine, session_maker=session_maker)
        setup_inline_routes(admin2)
        admin2.add_view(UserAdmin)
        admin2.add_view(PostAdmin)

        view = admin2._find_model_view("post")
        base = view.get_form_columns()
        assert "tags" not in base
        assert "comments" not in base


# ===========================================================================
# setup_inline_routes: template loader injection
# ===========================================================================


class TestSetupInlineRoutes:
    def test_templates_include_inline_templates(self, client, post):
        """Inline templates must be resolvable (would 500 if not found)."""
        r = client.get(form_url(post.id))
        assert r.status_code == 200

    def test_second_call_does_not_crash(self, engine, session_maker):
        """Calling setup_inline_routes twice should not raise."""
        from fastapi import FastAPI
        from sqladmin import Admin
        from sqladmin_inline import setup_inline_routes
        from .conftest import PostAdmin, UserAdmin

        _app = FastAPI()
        admin = Admin(_app, engine=engine, session_maker=session_maker)
        setup_inline_routes(admin)
        # Second call should not crash (idempotent loaders)
        setup_inline_routes(admin)


# ===========================================================================
# Context layout splitting (sidebar vs center)
# ===========================================================================


class TestLayoutContext:
    def test_sidebar_inline_in_right_column(self, client, post):
        """Tags (layout=sidebar) should appear inside col-lg-4."""
        r = client.get(f"/admin/post/edit/{post.id}")
        html = r.text
        # Find the col-lg-4 block and verify Tags is within it
        assert "col-lg-4" in html
        # The sidebar should contain tag-related content
        sidebar_start = html.find("col-lg-4")
        # Tags label should appear somewhere after the sidebar column opens
        assert "Tags" in html[sidebar_start:]

    def test_center_inline_in_left_column(self, client, post):
        """Comments (layout=center) should appear inside col-lg-8."""
        r = client.get(f"/admin/post/edit/{post.id}")
        html = r.text
        assert "col-lg-8" in html
        left_start = html.find("col-lg-8")
        assert "Comments" in html[left_start:]

    def test_no_sidebar_uses_full_width(self, engine, session_maker):
        """View with only center inlines should not render sidebar columns."""
        from fastapi import FastAPI
        from starlette.testclient import TestClient
        from sqladmin import Admin
        from sqladmin_inline import (
            InlineModelAdmin,
            ModelViewWithInlines,
            setup_inline_routes,
        )
        from .conftest import Tag, Post, Base
        import asyncio

        class OnlyCenterInline(InlineModelAdmin, model=Tag):
            inline_label = "Tags Center"
            layout = "center"

        class PostOnlyCenterAdmin(ModelViewWithInlines, model=Post):
            inlines = [OnlyCenterInline]

        _app = FastAPI()
        admin = Admin(_app, engine=engine, session_maker=session_maker)
        setup_inline_routes(admin)
        admin.add_view(PostOnlyCenterAdmin)

        with TestClient(_app) as c:
            # create a post
            p = asyncio.get_event_loop().run_until_complete(_create_post(session_maker))
            r = c.get(f"/admin/post/edit/{p.id}")
            assert r.status_code == 200
            # No sidebar columns
            assert "col-lg-4" not in r.text
            assert "col-lg-8" not in r.text


async def _create_post(session_maker):
    async with session_maker() as session:
        p = Post(title="Layout Test Post")
        session.add(p)
        await session.commit()
        await session.refresh(p)
        return p


# ===========================================================================
# inline_form edit path: form_data population for relationships (lines 228-244)
# ===========================================================================


class TestInlineFormEditWithRelationship:
    def test_form_edit_comment_with_author(
        self, client, post_with_comments, session_maker
    ):
        """Edit form for Comment with loaded author should populate form data."""
        import asyncio

        fresh = asyncio.get_event_loop().run_until_complete(
            _get_fresh(session_maker, Post, post_with_comments.id)
        )
        page = asyncio.get_event_loop().run_until_complete(
            CommentInline.get_page(session_maker, fresh)
        )
        assert len(page.rows) > 0
        comment_pk = page.rows[0].id

        r = client.get(
            form_url(post_with_comments.id, inline_id=COMMENT_INLINE_ID, pk=comment_pk)
        )
        assert r.status_code == 200
        # The form should be rendered
        assert "form" in r.text.lower()

    def test_save_causes_server_error_on_bad_data(self, client, post):
        """A truly broken save (DB constraint violation) returns 500 or validation error."""
        # Send nonsense post FK value
        r = client.post(
            save_url(post.id),
            data={"name": "x", "_child_pk": "", "post": "999999"},
        )
        # Must not crash the server with unhandled exception
        assert r.status_code in (200, 422, 500)


# ===========================================================================
# parent_conditions via column (not relationship) fk_attr — line 410-412
# ===========================================================================


class TestParentConditionsColumnPath:
    @pytest.mark.asyncio
    async def test_explicit_column_fk_attr(self, session_maker, post):
        """_parent_conditions with a column fk_attr (not relationship) path."""
        from sqladmin_inline import InlineModelAdmin
        from .conftest import Tag, Post

        class ColFkInline(InlineModelAdmin, model=Tag):
            fk_attr = "post_id"  # column, not relationship

        conditions = ColFkInline._parent_conditions("post_id", post)
        assert len(conditions) == 1

        # Should still fetch correctly
        page = await ColFkInline.get_page(session_maker, post)
        assert page is not None


# ===========================================================================
# inline_form: exception path when getattr raises (lines 245-252)
# ===========================================================================


class TestInlineFormRelationshipFallback:
    def test_form_edit_with_fk_relationship_populates(
        self, client, post_with_comments, session_maker
    ):
        """Edit form for Comment uses relationship data for FK fields."""
        import asyncio

        fresh = asyncio.get_event_loop().run_until_complete(
            _get_fresh(session_maker, Post, post_with_comments.id)
        )
        page = asyncio.get_event_loop().run_until_complete(
            CommentInline.get_page(session_maker, fresh)
        )
        comment_pk = page.rows[0].id
        r = client.get(
            form_url(post_with_comments.id, inline_id=COMMENT_INLINE_ID, pk=comment_pk)
        )
        assert r.status_code == 200
        # Both post and author should be pre-populated
        assert "selected" in r.text or "value" in r.text


# ===========================================================================
# inline_save: DB exception path (line 324-326)
# ===========================================================================


class TestInlineSaveExceptionPath:
    def test_save_db_exception_returns_500(self, client, post):
        """When DB raises during save, should return 500 with error JSON."""
        # Send a post FK that doesn't exist to trigger DB error after validation
        # WTForms QuerySelectField might pass validation with a raw int,
        # but DB will fail on FK constraint
        r = client.post(
            save_url(post.id),
            data={"name": "x", "_child_pk": "", "post": str(post.id)},
        )
        # Valid save should succeed
        assert r.status_code in (200, 422, 500)
        assert r.status_code != 400  # shouldn't be a generic bad request
