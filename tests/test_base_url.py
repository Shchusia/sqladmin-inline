"""URLs follow the admin mount point and reverse-proxy root_path."""

from __future__ import annotations

from .conftest import DB, AppFactory, client_for


async def test_custom_base_url(db: DB, make_app: AppFactory) -> None:
    app, _ = make_app(base_url="/panel")
    post = db.post()
    async with client_for(app) as c:
        html = (await c.get(f"/panel/post/edit/{post.id}")).text
        assert f'data-base-url="/panel/post/inline/tag_inline/{post.id}"' in html
        assert "/admin/" not in html
        assert (
            await c.get(f"/panel/post/inline/tag_inline/{post.id}/list")
        ).status_code == 200
        assert (
            await c.get("/panel/_inline/static/js/sqladmin-inline.js")
        ).status_code == 200


async def test_behind_proxy_root_path(db: DB, make_app: AppFactory) -> None:
    app, _ = make_app()
    post = db.post()
    async with client_for(app, root_path="/proxy") as c:
        r = await c.get(f"/proxy/admin/post/edit/{post.id}")
        assert r.status_code == 200
        assert (
            f'data-base-url="/proxy/admin/post/inline/tag_inline/{post.id}"' in r.text
        )
        list_html = (
            await c.get(f"/proxy/admin/post/inline/tag_inline/{post.id}/list")
        ).text
        assert (
            f'data-current-url="/proxy/admin/post/inline/tag_inline/{post.id}/list?page=1&amp;search="'
            in list_html
        )
