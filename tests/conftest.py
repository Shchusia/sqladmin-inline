"""
tests/conftest.py
~~~~~~~~~~~~~~~~~

Shared fixtures for sqladmin-inlines test suite.

All tests use in-memory SQLite via aiosqlite.
The `app` fixture spins up a full FastAPI + sqladmin stack
with inline routes registered, so HTTP-level tests are realistic.
"""

from __future__ import annotations

from typing import AsyncGenerator, List, Optional

import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from starlette.testclient import TestClient

from sqladmin import Admin, ModelView
from sqladmin_inline import InlineModelAdmin, ModelViewWithInlines, setup_inline_routes


# ---------------------------------------------------------------------------
# SQLAlchemy models (used across all test modules)
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    pass


class User(Base):
    """FK target — used to test relationship select fields in inlines."""

    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    comments: Mapped[List["Comment"]] = relationship("Comment", back_populates="author")

    def __str__(self) -> str:  # noqa: D105
        return self.name


class Post(Base):
    __tablename__ = "posts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    tags: Mapped[List["Tag"]] = relationship(
        "Tag", back_populates="post", cascade="all, delete-orphan"
    )
    comments: Mapped[List["Comment"]] = relationship(
        "Comment", back_populates="post", cascade="all, delete-orphan"
    )

    def __str__(self) -> str:  # noqa: D105
        return self.title


class Tag(Base):
    __tablename__ = "tags"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"))
    post: Mapped["Post"] = relationship("Post", back_populates="tags")

    def __str__(self) -> str:  # noqa: D105
        return self.name


class Comment(Base):
    """Has a FK to both Post (parent) and User (editable FK-select)."""

    __tablename__ = "comments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    body: Mapped[str] = mapped_column(Text)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"))
    author_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    post: Mapped["Post"] = relationship("Post", back_populates="comments")
    author: Mapped[Optional["User"]] = relationship("User", back_populates="comments")

    def __str__(self) -> str:  # noqa: D105
        return self.body[:40]


# ---------------------------------------------------------------------------
# Inline + Admin view definitions
# ---------------------------------------------------------------------------


class TagInline(InlineModelAdmin, model=Tag):
    inline_label = "Tags"
    icon = "fa fa-tag"
    layout = "sidebar"
    column_list = [Tag.name]
    column_searchable_list = [Tag.name]
    page_size = 3
    can_delete = True


class CommentInline(InlineModelAdmin, model=Comment):
    inline_label = "Comments"
    icon = "fa fa-comments"
    layout = "center"
    column_list = [Comment.body, Comment.author]
    column_searchable_list = [Comment.body]
    page_size = 3
    can_delete = True


class UserAdmin(ModelView, model=User):
    column_list = [User.id, User.name]


class PostAdmin(ModelViewWithInlines, model=Post):
    column_list = [Post.id, Post.title]
    inlines = [TagInline, CommentInline]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def engine():
    """Single async engine shared across the session (in-memory SQLite)."""
    return create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)


@pytest.fixture(scope="session")
def session_maker(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def create_tables(engine):
    """Create schema once per test session."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture()
def app(engine, session_maker):
    """Full FastAPI + sqladmin app with inline routes registered."""
    _app = FastAPI()
    admin = Admin(_app, engine=engine, session_maker=session_maker)
    setup_inline_routes(admin)
    # Explicitly inject the template directory from the installed package.
    # This ensures templates are found regardless of the project layout or
    # whether the user renamed the templates subfolder.
    import pathlib
    from jinja2 import ChoiceLoader, FileSystemLoader
    import sqladmin_inline.views as _views_module

    _pkg_tmpl_dir = str(
        pathlib.Path(_views_module.__file__).parent.parent / "templates"
    )
    existing = admin.templates.env.loader
    loaders = existing.loaders if hasattr(existing, "loaders") else [existing]
    if not any(
        isinstance(l, FileSystemLoader)
        and _pkg_tmpl_dir in getattr(l, "searchpath", [])
        for l in loaders
    ):
        admin.templates.env.loader = ChoiceLoader(
            [FileSystemLoader(_pkg_tmpl_dir)] + list(loaders)
        )
    admin.add_view(UserAdmin)
    admin.add_view(PostAdmin)
    return _app


@pytest.fixture()
def client(app):
    """Sync test client (no ASGI lifespan)."""
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest_asyncio.fixture()
async def db(session_maker) -> AsyncGenerator[AsyncSession, None]:
    """Async session for direct DB manipulation in tests."""
    async with session_maker() as session:
        yield session


@pytest_asyncio.fixture()
async def post(db) -> Post:
    """A fresh Post with no children."""
    p = Post(title="Test Post")
    db.add(p)
    await db.commit()
    await db.refresh(p)
    yield p
    # Cleanup
    await db.delete(p)
    await db.commit()


@pytest_asyncio.fixture()
async def user(db) -> User:
    """A User for FK-select tests."""
    u = User(name="Alice")
    db.add(u)
    await db.commit()
    await db.refresh(u)
    yield u
    await db.delete(u)
    await db.commit()


@pytest_asyncio.fixture()
async def post_with_tags(db, session_maker) -> Post:
    """Post with 5 tags (enough to test pagination with page_size=3)."""
    p = Post(title="Tagged Post")
    db.add(p)
    await db.commit()
    await db.refresh(p)

    for i in range(1, 6):
        db.add(Tag(name=f"tag{i}", post_id=p.id))
    await db.commit()
    yield p

    # Cleanup
    from sqlalchemy import select, delete

    await db.execute(delete(Tag).where(Tag.post_id == p.id))
    await db.delete(p)
    await db.commit()


@pytest_asyncio.fixture()
async def post_with_comments(db, user) -> Post:
    """Post with 4 comments linked to a User (FK-select)."""
    p = Post(title="Commented Post")
    db.add(p)
    await db.commit()
    await db.refresh(p)

    for i in range(1, 5):
        db.add(Comment(body=f"Comment body {i}", post_id=p.id, author_id=user.id))
    await db.commit()
    yield p

    from sqlalchemy import delete

    await db.execute(delete(Comment).where(Comment.post_id == p.id))
    await db.delete(p)
    await db.commit()
