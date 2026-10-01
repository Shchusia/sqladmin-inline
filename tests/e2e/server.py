"""Demo/E2E server: python -m tests.e2e.server <db-path> <port>"""

from __future__ import annotations

import sys

from fastapi import FastAPI
from sqladmin import Admin
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import uvicorn

from sqladmin_inline import setup_inline_routes
from tests.conftest import Base, Post, PostAdmin, Tag, User, UserAdmin


def build(db_path: str) -> FastAPI:
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    maker = sessionmaker(engine, expire_on_commit=False)
    with maker() as s:
        post = Post(title="E2E post")
        s.add_all([post, User(name="Alice")])
        s.flush()
        s.add_all([Tag(name=f"tag{i}", post_id=post.id) for i in range(1, 6)])
        s.commit()
    app = FastAPI()
    admin = Admin(app, engine=engine, session_maker=maker)
    setup_inline_routes(admin)
    admin.add_view(UserAdmin)
    admin.add_view(PostAdmin)
    return app


if __name__ == "__main__":
    uvicorn.run(
        build(sys.argv[1]),
        host="127.0.0.1",
        port=int(sys.argv[2]),
        log_level="warning",
        timeout_keep_alive=300,
    )
