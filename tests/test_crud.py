"""InlineModelAdmin database operations — run against async AND sync session makers."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from sqladmin_inline import InlineModelAdmin
from sqladmin_inline.inline import run_in_session

from .conftest import DB, Comment, CommentInline, Post, Tag, TagInline, User


class TestScaffoldForm:
    async def test_fields(self, db: DB) -> None:
        names = [f.name for f in (await TagInline.scaffold_form(db.session_maker))()]
        assert "name" in names and "post" in names
        assert "id" not in names and "position" not in names

    async def test_form_columns(self, db: DB) -> None:
        class BodyOnly(InlineModelAdmin, model=Comment):
            form_columns = [Comment.body]

        names = [f.name for f in (await BodyOnly.scaffold_form(db.session_maker))()]
        assert names == ["body"]


class TestGetPage:
    async def test_empty(self, db: DB) -> None:
        page = await TagInline.get_page(db.session_maker, db.post())
        assert (page.count, page.rows, page.page) == (0, [], 1)

    async def test_only_children_of_parent(self, db: DB) -> None:
        a, b = db.post("a"), db.post("b")
        db.tags(a, 2)
        db.tags(b, 4)
        page = await TagInline.get_page(db.session_maker, a)
        assert page.count == 2
        assert {t.post_id for t in page.rows} == {a.id}

    async def test_pagination_and_clamp(self, db: DB) -> None:
        post = db.post()
        db.tags(post, 5)
        p1 = await TagInline.get_page(db.session_maker, post, page=1)
        p2 = await TagInline.get_page(db.session_maker, post, page=2)
        far = await TagInline.get_page(db.session_maker, post, page=99)
        assert [len(p1.rows), len(p2.rows)] == [3, 2]
        assert far.page == 2 and len(far.rows) == 2
        assert (await TagInline.get_page(db.session_maker, post, page=0)).page == 1

    async def test_search(self, db: DB) -> None:
        post = db.post()
        db.tags(post, 5)
        db.add(Tag(name="100%_real", post_id=post.id))
        assert [
            t.name
            for t in (
                await TagInline.get_page(db.session_maker, post, search="tag3")
            ).rows
        ] == ["tag3"]
        assert (
            await TagInline.get_page(db.session_maker, post, search="zzz")
        ).count == 0
        # % and _ are literal characters, not wildcards
        assert (await TagInline.get_page(db.session_maker, post, search="%")).count == 1
        assert (await TagInline.get_page(db.session_maker, post, search="_")).count == 1
        assert (
            await TagInline.get_page(db.session_maker, post, search="  ")
        ).count == 6

    async def test_search_non_string_column(self, db: DB) -> None:
        class ByPosition(InlineModelAdmin, model=Tag):
            column_searchable_list = [Tag.position]

        post = db.post()
        db.add(
            Tag(name="a", position=12, post_id=post.id),
            Tag(name="b", position=7, post_id=post.id),
        )
        page = await ByPosition.get_page(db.session_maker, post, search="12")
        assert [t.name for t in page.rows] == ["a"]

    async def test_order_field_then_pk(self, db: DB) -> None:
        post = db.post()
        db.add(
            Tag(name="c", position=3, post_id=post.id),
            Tag(name="a", position=1, post_id=post.id),
            Tag(name="b", position=2, post_id=post.id),
        )
        page = await TagInline.get_page(db.session_maker, post)
        assert [t.name for t in page.rows] == ["a", "b", "c"]

    async def test_default_sort(self, db: DB) -> None:
        class Desc(InlineModelAdmin, model=Tag):
            column_default_sort = (Tag.name, True)

        class Unknown(InlineModelAdmin, model=Tag):
            column_default_sort = ("missing", False)

        post = db.post()
        db.tags(post, 3)
        assert [t.name for t in (await Desc.get_page(db.session_maker, post)).rows] == [
            "tag3",
            "tag2",
            "tag1",
        ]
        assert [
            t.name for t in (await Unknown.get_page(db.session_maker, post)).rows
        ] == ["tag1", "tag2", "tag3"]

    async def test_relationships_are_eager_loaded(self, db: DB) -> None:
        class WithTags(InlineModelAdmin, model=Post):
            fk_attr = "id"
            column_list = ["title", "tags"]

        user, post = db.user(), db.post()
        db.comments(post, 2, author=user)
        db.tags(post, 2)
        page = await CommentInline.get_page(db.session_maker, post)
        assert {c.author.name for c in page.rows} == {"Alice"}  # detached, no lazy load
        assert CommentInline.get_display_value(page.rows[0], "author") == "Alice"
        assert len(WithTags._eager_options()) == 1


class TestGetByPk:
    async def test_found_and_scoped(self, db: DB) -> None:
        user, a, b = db.user(), db.post("a"), db.post("b")
        (c,) = db.comments(a, 1, author=user)
        found = await CommentInline.get_by_pk(db.session_maker, str(c.id))
        assert found.author.name == "Alice"
        assert await CommentInline.get_by_pk(db.session_maker, c.id, a) is not None
        assert await CommentInline.get_by_pk(db.session_maker, c.id, b) is None

    async def test_missing_and_malformed(self, db: DB) -> None:
        assert await TagInline.get_by_pk(db.session_maker, "999") is None
        assert await TagInline.get_by_pk(db.session_maker, "abc") is None

    async def test_load_to_many(self, db: DB) -> None:
        class UserInline(InlineModelAdmin, model=User):
            fk_attr = "id"

        user, post = db.user(), db.post()
        db.comments(post, 2, author=user)
        found = await UserInline.get_by_pk(db.session_maker, user.id, load=["comments"])
        assert len(found.comments) == 2


class TestCreate:
    async def test_create_links_parent(self, db: DB) -> None:
        post = db.post()
        tag = await TagInline.create_child(db.session_maker, post, {"name": "new"})
        assert (tag.id is not None, tag.name, tag.post_id) == (True, "new", post.id)

    async def test_order_field_appends_to_the_end(self, db: DB) -> None:
        a, b = db.post("a"), db.post("b")
        db.add(
            Tag(name="x", position=4, post_id=a.id),
            Tag(name="y", position=9, post_id=b.id),
        )
        first = await TagInline.create_child(
            db.session_maker, b.__class__(id=999), {"name": "lonely"}
        )
        assert first.position == 1  # no siblings yet
        new = await TagInline.create_child(db.session_maker, a, {"name": "new"})
        assert new.position == 5  # max among *this* parent's tags + 1
        explicit = await TagInline.create_child(
            db.session_maker, a, {"name": "e", "position": 2}
        )
        assert explicit.position == 2

    async def test_parent_in_data_is_ignored(self, db: DB) -> None:
        post, other = db.post("p"), db.post("other")
        tag = await TagInline.create_child(
            db.session_maker,
            post,
            {"name": "x", "post": str(other.id), "post_id": other.id},
        )
        assert tag.post_id == post.id

    async def test_relationship_from_form_string(self, db: DB) -> None:
        """QuerySelectField data is a *string* PK: it must be resolved, not stored raw."""
        user, post = db.user(), db.post()
        c = await CommentInline.create_child(
            db.session_maker,
            post,
            {"body": "hi", "author": str(user.id), "csrf_token": "x"},
        )
        assert c.author_id == user.id and isinstance(c.author_id, int)

    @pytest.mark.parametrize("empty", [None, "", "__None", "not-a-pk"])
    async def test_relationship_empty_values(self, db: DB, empty: object) -> None:
        c = await CommentInline.create_child(
            db.session_maker, db.post(), {"body": "x", "author": empty}
        )
        assert c.author_id is None

    async def test_relationship_instance(self, db: DB) -> None:
        user, post = db.user(), db.post()
        c = await CommentInline.create_child(
            db.session_maker, post, {"body": "x", "author": user}
        )
        assert c.author_id == user.id


class TestUpdate:
    async def test_update(self, db: DB) -> None:
        user, post = db.user(), db.post()
        (c,) = db.comments(post, 1)
        updated = await CommentInline.update_child(
            db.session_maker, str(c.id), {"body": "new", "author": str(user.id)}, post
        )
        assert (updated.body, updated.author_id) == ("new", user.id)
        cleared = await CommentInline.update_child(
            db.session_maker, c.id, {"author": None}
        )
        assert cleared.author_id is None

    async def test_not_found_wrong_parent_malformed(self, db: DB) -> None:
        a, b = db.post("a"), db.post("b")
        (t,) = db.tags(a, 1)
        assert (
            await TagInline.update_child(db.session_maker, "999", {"name": "x"}) is None
        )
        assert (
            await TagInline.update_child(db.session_maker, t.id, {"name": "x"}, b)
            is None
        )
        assert (
            await TagInline.update_child(db.session_maker, "abc", {"name": "x"}) is None
        )
        assert db.get(Tag, t.id).name == "tag1"

    async def test_to_many_relationship(self, db: DB, sync_session: Session) -> None:
        class UserInline(InlineModelAdmin, model=User):
            fk_attr = "id"

        user, post = db.user(), db.post()
        c1, c2 = db.comments(post, 2)
        await UserInline.update_child(
            db.session_maker, user.id, {"comments": [str(c1.id), c2]}
        )
        assert {c.author_id for c in db.all(Comment)} == {user.id}
        await UserInline.update_child(
            db.session_maker, user.id, {"comments": str(c1.id)}
        )
        assert db.get(Comment, c2.id).author_id is None


class TestDelete:
    async def test_delete_many(self, db: DB) -> None:
        post = db.post()
        tags = db.tags(post, 4)
        n = await TagInline.delete_children(
            db.session_maker, [tags[0].id, str(tags[1].id), "999", "abc"], post
        )
        assert n == 2
        assert len(db.all(Tag)) == 2

    async def test_scoped_to_parent(self, db: DB) -> None:
        a, b = db.post("a"), db.post("b")
        (t,) = db.tags(a, 1)
        assert await TagInline.delete_child(db.session_maker, t.id, b) is False
        assert await TagInline.delete_child(db.session_maker, t.id, a) is True

    async def test_nothing(self, db: DB) -> None:
        assert await TagInline.delete_children(db.session_maker, []) == 0
        assert await TagInline.delete_child(db.session_maker, "999") is False


class TestReorder:
    async def test_reorder_children(self, db: DB) -> None:
        a, b = db.post("a"), db.post("b")
        t1, t2, t3 = db.tags(a, 3)
        (foreign,) = db.tags(b, 1)
        n = await TagInline.reorder_children(
            db.session_maker, a, [t3.id, "abc", t1.id, foreign.id, t2.id]
        )
        assert n == 3
        positions = {t.name: t.position for t in db.all(Tag, Tag.post_id == a.id)}
        assert positions == {"tag3": 1, "tag1": 3, "tag2": 5}
        assert db.get(Tag, foreign.id).position is None

    async def test_reorder_disabled(self, db: DB) -> None:
        post = db.post()
        (c,) = db.comments(post, 1)
        assert await CommentInline.reorder_children(db.session_maker, post, [c.id]) == 0
        assert await CommentInline.reorder_child(db.session_maker, c.id, 1) is False

    async def test_reorder_child(self, db: DB) -> None:
        (t,) = db.tags(db.post(), 1)
        assert await TagInline.reorder_child(db.session_maker, t.id, 7) is True
        assert db.get(Tag, t.id).position == 7
        assert await TagInline.reorder_child(db.session_maker, "999", 1) is False
        assert await TagInline.reorder_child(db.session_maker, "abc", 1) is False


async def test_run_in_session(db: DB) -> None:
    post = db.post("x")
    title = await run_in_session(db.session_maker, lambda s: s.get(Post, post.id).title)
    assert title == "x"
