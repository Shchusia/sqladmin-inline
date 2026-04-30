"""
demo.py — sqladmin-inline demo.

Демонстрирует все возможности инлайнов:
  - icon, layout (sidebar / center)
  - can_create, can_edit, can_delete
  - column_default_sort  — сортировка по полю
  - order_field          — drag-and-drop перетаскивание строк
  - Load More            — подгрузка следующей страницы
  - FK-select            — выбор связанного объекта

Запуск:
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
    Тег с полем position для drag-and-drop сортировки.
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
    """Comment с FK на User (author) — демонстрирует FK-select в форме."""

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
    Теги — sidebar, drag-and-drop по полю position.

    Особенности:
    - layout = "sidebar"  → отображается справа
    - order_field = "position" → строки перетаскиваются мышью,
      position обновляется автоматически через /reorder
    - column_default_sort не нужен: order_field имеет приоритет
    - can_create = False → нет кнопки Add (только drag)
    """

    inline_label = "Tags"
    icon = "fas fa-tag"
    # layout        = "sidebar"

    # Drag-and-drop: integer поле порядка
    order_field = "position"

    column_list = [Tag.name]  # position показывается как # автоматически
    column_labels = {Tag.name: "Tag name"}
    column_searchable_list = [Tag.name]
    page_size = 5
    can_delete = True
    can_create = True
    can_edit = False
    form_excluded_columns = ["post"]


class CommentInline(InlineModelAdmin, model=Comment):
    """
    Комментарии — center, сортировка по ID desc, FK-select на User.

    Особенности:
    - column_default_sort = ("id", True)  → новые сверху
    - page_size = 3 → при 4+ комментариях появляется кнопка "Load more"
    - can_delete = False → нет чекбоксов удаления
    - can_edit = False   → нет кнопки карандаша
    - form_columns включает Comment.author → FK-select
    """

    inline_label = "Comments"
    icon = "fa fa-comments"
    layout = "center"

    # Сортировка: новые сверху
    column_default_sort = ("id", True)

    column_list = [Comment.body, Comment.author]
    column_labels = {Comment.body: "Text", "author": "Author"}
    column_searchable_list = [Comment.body]
    page_size = 3  # намеренно мало, чтобы показать Load More
    can_delete = True
    can_create = True
    can_edit = False
    form_columns = [Comment.body, Comment.author]


class UserCommentInline(InlineModelAdmin, model=Comment):
    """
    Комментарии пользователя — FK по author, drag-and-drop по ID нет,
    зато есть сортировка по body и Load More.
    """

    inline_label = "My Comments"
    icon = "fas fa-comments"
    layout = "center"
    column_default_sort = ("id", False)  # старые сверху

    column_list = [Comment.body, Comment.post]
    column_searchable_list = [Comment.body]
    page_size = 2  # мало для демонстрации Load More
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

        # Теги с position — попробуйте перетащить в admin!
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

        # Комментариев больше page_size=3 → покажется Load More
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
    print("  Posts → открой любой пост:")
    print()
    print("  Tags (sidebar, справа):")
    print("    • order_field='position' → перетаскивай строки мышью")
    print("    • иконка ⠿ слева — drag handle")
    print("    • порядок сохраняется в БД автоматически")
    print()
    print("  Comments (center, под формой):")
    print("    • column_default_sort=('id', True) → новые сверху")
    print("    • page_size=3, комментариев 4 → кнопка 'Load more'")
    print("    • Author — FK-select на таблицу Users")
    print("    • can_edit=False → нет карандаша")
    print("    • can_delete=False → нет чекбоксов")
    print()
    print("  Users (inlines) → открой пользователя:")
    print("    • UserCommentInline: page_size=2 → Load More активен")
    print("=" * 60)
    print()
    uvicorn.run(app, host="0.0.0.0", port=8001, reload=False)
