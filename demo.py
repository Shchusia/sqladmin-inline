import pathlib
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI
from sqlalchemy import ForeignKey, Integer, String, Text, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


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
    __tablename__ = "tags"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"))
    post: Mapped["Post"] = relationship("Post", back_populates="tags")

    def __str__(self) -> str:
        return self.name


class Comment(Base):
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
# Admin views
# ---------------------------------------------------------------------------

from sqladmin import Admin, ModelView
from sqladmin_inline import InlineModelAdmin, ModelViewWithInlines, setup_inline_routes


class TagInline(InlineModelAdmin, model=Tag):
    inline_label = "Tags"
    icon = "fas fa-tag"
    # layout = "sidebar"
    column_list = [Tag.name]
    column_searchable_list = [Tag.name]
    page_size = 5
    can_delete = True
    can_create = False
    can_edit = True
    form_excluded_columns = ["post"]


class CommentInline(InlineModelAdmin, model=Comment):
    inline_label = "Comments"
    icon = "fa fa-comments"
    layout = "center"
    column_list = [Comment.body, Comment.author]
    column_searchable_list = [Comment.body]
    page_size = 3
    can_delete = False
    # can_create = False
    can_edit = False

    form_columns = [Comment.body, Comment.author]

    column_labels = {Comment.body: "Body", "author_id": "Author"}


class UserCommentInline(InlineModelAdmin, model=Comment):
    inline_label = "My Comments"
    icon = "fas fa-comments"
    layout = "center"
    column_list = [Comment.body, Comment.post]
    column_searchable_list = [Comment.body]
    page_size = 5
    can_delete = True
    # can_create = False
    fk_attr = "author"
    form_columns = [Comment.body, Comment.post]


class UserAdminWithInlines(ModelViewWithInlines, model=User):
    name = "User"
    name_plural = "Users"
    icon = "fa-solid fa-users"
    column_list = [User.id, User.name]
    column_searchable_list = [User.name]
    inlines = [UserCommentInline]


class PostAdmin(ModelViewWithInlines, model=Post):
    name = "Post"
    name_plural = "Posts"
    icon = "fa-solid fa-newspaper"
    column_list = [Post.id, Post.title, Post.author]
    column_searchable_list = [Post.title]

    form_columns = [Post.title, Post.body, Post.author]

    column_labels = {Post.author: "Author", Post.title: "Title", Post.body: "Content"}

    inlines = [TagInline, CommentInline]


class AuthorAdmin(ModelView, model=Author):
    name = "Author"
    name_plural = "Authors"
    icon = "fa-solid fa-user"
    column_list = [Author.id, Author.name]
    column_searchable_list = [Author.name]


class UserAdmin(ModelView, model=User):
    name = "User"
    name_plural = "Users"
    icon = "fa-solid fa-users"
    column_list = [User.id, User.name]


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

        # Authors
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

        s.add_all(
            [
                Tag(name="python", post=p1),
                Tag(name="fastapi", post=p1),
                Tag(name="tutorial", post=p1),
                Tag(name="sqlalchemy", post=p2),
                Tag(name="admin", post=p2),
                Tag(name="async", post=p3),
            ]
        )

        s.add_all(
            [
                Comment(body="Great post!", author=alice_u, post=p1),
                Comment(body="Very helpful!", author=bob_u, post=p1),
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


from importlib.resources import files

templates_path = files("sqladmin_inline") / "templates"
app = FastAPI(lifespan=lifespan)
admin = Admin(
    app,
    engine=engine,
    session_maker=session_mk,
    title="sqladmin-inlines demo",
    templates_dir=templates_path,
)

setup_inline_routes(admin)

admin.add_view(UserAdminWithInlines)
admin.add_view(AuthorAdmin)
admin.add_view(PostAdmin)

# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    print()
    print("=" * 60)
    print(" sqladmin-inlines demo")
    print(" → http://localhost:8000/admin")
    print()
    print(" Posts → any post:")
    print(" • Tags (sidebar, right, fa-tag icon)")
    print(" • Comments (center, below form, fa-comments icon)")
    print(" └── Author field — FK-select to Users table")
    print("=" * 60)
    print()
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)
