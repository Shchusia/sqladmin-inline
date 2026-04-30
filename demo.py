"""
demo.py — sqladmin-inline demo.

Demonstrates all inline features:
  - icon, layout (sidebar / center)
  - can_create, can_edit, can_delete
  - column_default_sort  — sorting by field
  - order_field          — drag-and-drop row reordering
  - Load More            — next page loading
  - FK-select            — related object selection

Run:
    rm -f demo.db && python demo.py
    → http://localhost:8000/admin
"""

import pathlib
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI
from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from sqladmin import Admin, ModelView
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
    comments: Mapped[List["Comment"]] = relationship("Comment", back_populates="author")

    def __str__(self) -> str:
        return self.name


class Author(Base):
    __tablename__ = "authors"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    bio: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    posts: Mapped[List["Post"]] = relationship("Post", back_populates="author")

    def __str__(self) -> str:
        return self.name


class Post(Base):
    __tablename__ = "posts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    author_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("authors.id"), nullable=True
    )

    author: Mapped[Optional["Author"]] = relationship("Author", back_populates="posts")
    tags: Mapped[List["Tag"]] = relationship(
        "Tag", back_populates="post", cascade="all, delete-orphan"
    )
    comments: Mapped[List["Comment"]] = relationship(
        "Comment", back_populates="post", cascade="all, delete-orphan"
    )

    def __str__(self) -> str:
        return self.title


class Tag(Base):
    """
    Tag with position field for drag-and-drop sorting.
    """

    __tablename__ = "tags"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"))
    post: Mapped["Post"] = relationship("Post", back_populates="tags")

    def __str__(self) -> str:
        return self.name


class Comment(Base):
    """Comment with FK to User (author) — demonstrates FK-select in form."""

    __tablename__ = "comments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    body: Mapped[str] = mapped_column(Text)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"))
    author_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )

    post: Mapped["Post"] = relationship("Post", back_populates="comments")
    author: Mapped[Optional["User"]] = relationship(
        "User", back_populates="comments", lazy="selectin"
    )

    def __str__(self) -> str:
        return self.body[:40]


# ---------------------------------------------------------------------------
# Inline definitions
# ---------------------------------------------------------------------------


class TagInline(InlineModelAdmin, model=Tag):
    """
    Tags — sidebar, drag-and-drop via position field.

    Features:
    - layout = "sidebar"  → displayed on the right side
    - order_field = "position" → rows are draggable,
      position updates automatically via /reorder
    - column_default_sort not needed: order_field takes priority
    - can_create = True → Add button enabled
    """

    inline_label = "Tags"
    icon = "fas fa-tag"
    # layout        = "sidebar"

    # Drag-and-drop: integer order field
    order_field = "position"

    column_list = [Tag.name]  # position is shown as # automatically
    column_labels = {Tag.name: "Tag name"}
    column_searchable_list = [Tag.name]
    page_size = 5
    can_delete = True
    can_create = True
    can_edit = True
    form_excluded_columns = ["post"]


class CommentInline(InlineModelAdmin, model=Comment):
    """
    Comments — center, sorted by ID desc, FK-select for User.

    Features:
    - column_default_sort = ("id", True)  → new ones on top
    - page_size = 3 → "Load more" button appears with 4+ comments
    - can_delete = True → deletion enabled
    - can_edit = False   → no edit button
    - form_columns includes Comment.author → FK-select
    """

    inline_label = "Comments"
    icon = "fa fa-comments"
    layout = "center"

    # Sorting: new ones on top
    column_default_sort = ("id", True)

    column_list = [Comment.body, Comment.author]
    column_labels = {Comment.body: "Text", "author": "Author"}
    column_searchable_list = [Comment.body]
    page_size = 3  # intentionally small to demonstrate Load More
    can_delete = True
    can_create = True
    can_edit = True
    form_columns = [Comment.body, Comment.author]


class UserCommentInline(InlineModelAdmin, model=Comment):
    """
    User comments — FK by author, no drag-and-drop by ID,
    but sorting by body and Load More are available.
    """

    inline_label = "My Comments"
    icon = "fas fa-comments"
    layout = "center"
    column_default_sort = ("id", False)  # old ones on top

    column_list = [Comment.body, Comment.post]
    column_searchable_list = [Comment.body]
    page_size = 2  # small to demonstrate Load More
    can_delete = True
    can_create = True
    can_edit = True
    fk_attr = "author"
    form_columns = [Comment.body, Comment.post]


# ---------------------------------------------------------------------------
# Admin views
# ---------------------------------------------------------------------------


class AuthorAdmin(ModelView, model=Author):
    name = "Author"
    name_plural = "Authors"
    icon = "fa-solid fa-user"
    column_list = [Author.id, Author.name]


class UserAdmin(ModelView, model=User):
    name = "User"
    name_plural = "Users"
    icon = "fa-solid fa-users"
    column_list = [User.id, User.name]


class UserAdminWithInlines(ModelViewWithInlines, model=User):
    name = "User (inlines)"
    name_plural = "Users (inlines)"
    icon = "fa-solid fa-user-gear"
    column_list = [User.id, User.name]
    inlines = [UserCommentInline]


class PostAdmin(ModelViewWithInlines, model=Post):
    name = "Post"
    name_plural = "Posts"
    icon = "fa-solid fa-newspaper"
    column_list = [Post.id, Post.title, Post.author]

    form_columns = [Post.title, Post.body, Post.author]
    column_labels = {Post.author: "Author", Post.title: "Title", Post.body: "Content"}

    # Tags → sidebar + drag-and-drop
    # Comments → center + sort desc + load more
    inlines = [TagInline, CommentInline]


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

DB_PATH = pathlib.Path(__file__).parent / "demo.db"
engine = create_async_engine(f"sqlite+aiosqlite:///{DB_PATH}", echo=False)
session_mk = async_sessionmaker(engine, expire_on_commit=False)


async def seed_db() -> None:
    from sqlalchemy import select

    async with session_mk() as s:
        if (await s.execute(select(Author).limit(1))).scalars().first():
            return

        alice_u = User(name="Alice")
        bob_u = User(name="Bob")
        carol_u = User(name="Carol")
        s.add_all([alice_u, bob_u, carol_u])
        await s.flush()

        alice = Author(name="Alice", bio="Python enthusiast.")
        bob = Author(name="Bob", bio="Backend dev.")
        s.add_all([alice, bob])
        await s.flush()

        p1 = Post(
            title="Getting started with FastAPI",
            body="FastAPI is a modern web framework…",
            author=alice,
        )
        p2 = Post(
            title="SQLAdmin deep dive",
            body="sqladmin is a beautiful admin panel…",
            author=bob,
        )
        p3 = Post(
            title="Async SQLAlchemy tips",
            body="Working with async sessions…",
            author=alice,
        )
        s.add_all([p1, p2, p3])
        await s.flush()

        # Tags with position — try dragging in admin!
        s.add_all(
            [
                Tag(name="python", position=1, post=p1),
                Tag(name="fastapi", position=2, post=p1),
                Tag(name="tutorial", position=3, post=p1),
                Tag(name="sqlalchemy", position=1, post=p2),
                Tag(name="admin", position=2, post=p2),
                Tag(name="async", position=1, post=p3),
            ]
        )

        # Comments count exceeds page_size=3 → Load More will be shown
        s.add_all(
            [
                Comment(body="Great post!", author=alice_u, post=p1),
                Comment(body="Very helpful!", author=bob_u, post=p1),
                Comment(body="Loved it.", author=carol_u, post=p1),
                Comment(body="Thanks for this!", author=alice_u, post=p1),
                Comment(body="Loved the examples.", author=carol_u, post=p2),
                Comment(body="Can you write more?", author=alice_u, post=p3),
            ]
        )
        await s.commit()
        print("✅ Sample data seeded.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await seed_db()
    yield


app = FastAPI(lifespan=lifespan)
admin = Admin(
    app, engine=engine, session_maker=session_mk, title="sqladmin-inline demo"
)

setup_inline_routes(admin)

# admin.add_view(UserAdmin)
admin.add_view(UserAdminWithInlines)
admin.add_view(AuthorAdmin)
admin.add_view(PostAdmin)


if __name__ == "__main__":
    import uvicorn

    print()
    print("=" * 60)
    print("  sqladmin-inline demo")
    print("  → http://localhost:8000/admin")
    print()
    print("  Posts → open any post:")
    print()
    print("  Tags (sidebar, on the right):")
    print("    • order_field='position' → drag rows with mouse")
    print("    • ⠿ icon on the left — drag handle")
    print("    • order is saved to DB automatically")
    print()
    print("  Comments (center, below the form):")
    print("    • column_default_sort=('id', True) → new ones on top")
    print("    • page_size=3, total comments 4 → 'Load more' button")
    print("    • Author — FK-select to Users table")
    print("    • can_edit=False → no edit pencil")
    print("    • can_delete=True → deletion enabled")
    print()
    print("  Users (inlines) → open a user:")
    print("    • UserCommentInline: page_size=2 → Load More active")
    print("=" * 60)
    print()
    uvicorn.run(app, host="0.0.0.0", port=8001, reload=False)
