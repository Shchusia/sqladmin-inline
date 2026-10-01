"""Shared fixtures.

Every test gets its own SQLite file, so tests are fully isolated.  Fixtures
that touch the database are parametrized over an *async* and a *sync*
session maker: the library must behave identically with both.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator
import dataclasses
from typing import Any

from fastapi import FastAPI
import httpx
import pytest
from sqladmin import Admin, ModelView
from sqlalchemy import ForeignKey, Integer, String, Text, create_engine, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)

from sqladmin_inline import InlineModelAdmin, ModelViewWithInlines, setup_inline_routes

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    comments: Mapped[list[Comment]] = relationship(back_populates="author")

    def __str__(self) -> str:
        return self.name


class Post(Base):
    __tablename__ = "posts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    tags: Mapped[list[Tag]] = relationship(
        back_populates="post", cascade="all, delete-orphan"
    )
    comments: Mapped[list[Comment]] = relationship(
        back_populates="post", cascade="all, delete-orphan"
    )

    def __str__(self) -> str:
        return self.title


class Tag(Base):
    __tablename__ = "tags"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"))
    post: Mapped[Post] = relationship(back_populates="tags")

    def __str__(self) -> str:
        return self.name


class Comment(Base):
    __tablename__ = "comments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    body: Mapped[str] = mapped_column(Text)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"))
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    post: Mapped[Post] = relationship(back_populates="comments")
    author: Mapped[User | None] = relationship(back_populates="comments")

    def __str__(self) -> str:
        return self.body[:40]


class Attachment(Base):
    """FK column to Post without any relationship()."""

    __tablename__ = "attachments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filename: Mapped[str] = mapped_column(String(100))
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"))


# ---------------------------------------------------------------------------
# Admin configuration
# ---------------------------------------------------------------------------


class TagInline(InlineModelAdmin, model=Tag):
    inline_label = "Tags"
    icon = "fa-solid fa-tag"
    layout = "sidebar"
    order_field = "position"
    column_list = [Tag.name]
    column_searchable_list = [Tag.name]
    form_excluded_columns = [Tag.position]
    page_size = 3


class CommentInline(InlineModelAdmin, model=Comment):
    inline_label = "Comments"
    icon = "fa-solid fa-comments"
    layout = "center"
    column_list = [Comment.body, Comment.author]
    column_labels = {Comment.body: "Text"}
    column_searchable_list = [Comment.body]
    page_size = 3


class UserAdmin(ModelView, model=User):
    column_list = [User.id, User.name]


class PostAdmin(ModelViewWithInlines, model=Post):
    column_list = [Post.id, Post.title]
    inlines = [TagInline, CommentInline]


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class DB:
    mode: str
    engine: Any
    session_maker: Any
    sync: sessionmaker  # always-sync maker for seeding / assertions

    def add(self, *objs: Any) -> list[Any]:
        with self.sync() as s:
            s.add_all(objs)
            s.commit()
            for o in objs:
                s.refresh(o)
                s.expunge(o)
        return list(objs)

    def post(self, title: str = "Post") -> Post:
        return self.add(Post(title=title))[0]

    def user(self, name: str = "Alice") -> User:
        return self.add(User(name=name))[0]

    def tags(self, post: Post, n: int, prefix: str = "tag") -> list[Tag]:
        return self.add(
            *[Tag(name=f"{prefix}{i}", post_id=post.id) for i in range(1, n + 1)]
        )

    def comments(self, post: Post, n: int, author: User | None = None) -> list[Comment]:
        return self.add(
            *[
                Comment(
                    body=f"Comment {i}",
                    post_id=post.id,
                    author_id=author.id if author else None,
                )
                for i in range(1, n + 1)
            ]
        )

    def get(self, model: Any, pk: Any) -> Any:
        with self.sync() as s:
            obj = s.get(model, pk)
            if obj is not None:
                s.expunge(obj)
            return obj

    def all(self, model: Any, *where: Any, order_by: Any = None) -> list[Any]:
        with self.sync() as s:
            stmt = select(model).where(*where)
            if order_by is not None:
                stmt = stmt.order_by(order_by)
            rows = list(s.execute(stmt).scalars())
            s.expunge_all()
            return rows


@pytest.fixture(params=["async", "sync"])
async def db(request: pytest.FixtureRequest, tmp_path: Any) -> AsyncIterator[DB]:
    url = tmp_path / "test.db"
    sync_engine = create_engine(
        f"sqlite:///{url}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(sync_engine)
    seed = sessionmaker(sync_engine, expire_on_commit=False)
    if request.param == "async":
        engine = create_async_engine(f"sqlite+aiosqlite:///{url}")
        yield DB(
            "async", engine, async_sessionmaker(engine, expire_on_commit=False), seed
        )
        await engine.dispose()
    else:
        yield DB("sync", sync_engine, seed, seed)
    sync_engine.dispose()


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

AppFactory = Callable[..., tuple[FastAPI, Admin]]


@pytest.fixture
def make_app(db: DB) -> AppFactory:
    def _make(*views: Any, **admin_kwargs: Any) -> tuple[FastAPI, Admin]:
        app = FastAPI()
        admin = Admin(
            app, engine=db.engine, session_maker=db.session_maker, **admin_kwargs
        )
        setup_inline_routes(admin)
        for view in views or (UserAdmin, PostAdmin):
            admin.add_view(view)
        return app, admin

    return _make


def client_for(app: Any, **kwargs: Any) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, **kwargs)
    return httpx.AsyncClient(
        transport=transport, base_url="http://test", follow_redirects=False
    )


@pytest.fixture
async def client(make_app: AppFactory) -> AsyncIterator[httpx.AsyncClient]:
    app, _ = make_app()
    async with client_for(app) as c:
        yield c


@pytest.fixture
def admin_view(make_app: AppFactory) -> PostAdmin:
    _, admin = make_app()
    return admin._find_model_view("post")  # type: ignore[return-value]


def inline_url(post_id: Any, inline: str = "tag_inline", action: str = "list") -> str:
    return f"/admin/post/inline/{inline}/{post_id}/{action}"


def fake_request(
    path: str = "/admin/post/edit/1", query: bytes = b"", **path_params: Any
) -> Any:
    from starlette.requests import Request

    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "query_string": query,
            "headers": [],
            "path_params": path_params,
        }
    )


@pytest.fixture
def sync_session(db: DB) -> Iterator[Session]:
    with db.sync() as s:
        yield s
