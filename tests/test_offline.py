"""The admin must work without internet access (e.g. inside a VPN).

* no rendered page references an external host;
* every asset the edit page needs (including fonts referenced from CSS)
  is served by the application itself;
* every Font Awesome icon used by the package exists in the Font Awesome
  build bundled with sqladmin;
* the bundled JavaScript is syntactically valid.
"""

from __future__ import annotations

from importlib import resources
import pathlib
import re
import shutil
import subprocess
from urllib.parse import urljoin, urlparse

import httpx
import pytest
import sqladmin

from .conftest import DB, inline_url

PKG = resources.files("sqladmin_inline")
EXTERNAL = re.compile(r"""(?:src|href)\s*=\s*["']((?:https?:)?//[^"']+)["']""", re.I)
ASSET = re.compile(
    r"""<(?:script|link)\b[^>]*?(?:src|href)\s*=\s*["']([^"']+)["']""", re.I
)
CSS_URL = re.compile(r"""url\(\s*["']?([^"')]+)["']?\s*\)""")


def external_refs(html: str) -> list[str]:
    return [
        u
        for u in EXTERNAL.findall(html)
        if urlparse(urljoin("http://test/", u)).netloc != "test"
    ]


async def edit_page(db: DB, client: httpx.AsyncClient) -> str:
    post = db.post()
    db.tags(post, 2)
    r = await client.get(f"/admin/post/edit/{post.id}")
    assert r.status_code == 200
    return r.text


async def test_pages_have_no_external_references(
    db: DB, client: httpx.AsyncClient
) -> None:
    html = await edit_page(db, client)
    post_id = db.all(__import__("tests.conftest", fromlist=["Post"]).Post)[0].id
    fragments = [
        html,
        (await client.get("/admin/post/create")).text,
        (await client.get(inline_url(post_id))).text,
        (await client.get(inline_url(post_id, "comment_inline", "form"))).text,
    ]
    for fragment in fragments:
        assert external_refs(fragment) == []


async def test_every_asset_is_served_locally(db: DB, client: httpx.AsyncClient) -> None:
    html = await edit_page(db, client)
    assets = ASSET.findall(html)
    assert any("Sortable.min.js" in a for a in assets)
    assert any("sqladmin-inline.js" in a for a in assets)
    checked = 0
    for asset in assets:
        r = await client.get(asset)
        assert r.status_code == 200, asset
        checked += 1
        if asset.endswith(".css"):
            # @font-face lists several formats; the browser needs just one of them.
            variants: dict[str, list[int]] = {}
            for ref in CSS_URL.findall(r.text):
                if ref.startswith("data:"):
                    continue
                target = urljoin(asset, ref.split("#")[0].split("?")[0])
                assert urlparse(target).netloc in ("", "test"), f"{asset} -> {ref}"
                stem = target.rsplit(".", 1)[0]
                variants.setdefault(stem, []).append(
                    (await client.get(target)).status_code
                )
            for stem, statuses in variants.items():
                # sqladmin's Font Awesome CSS declares a v4-compat font it does not
                # ship; it is only used by legacy v4 icon names, which we never use.
                if stem.endswith("fa-v4compatibility"):
                    continue
                assert 200 in statuses, f"{asset}: no format of {stem} is served"
                checked += 1
    assert checked > 10


def test_package_files_have_no_cdn_urls() -> None:
    root = pathlib.Path(str(PKG))
    for path in (
        list(root.rglob("*.html"))
        + list(root.rglob("*.js"))
        + list(root.rglob("*.css"))
    ):
        text = path.read_text(encoding="utf-8")
        if path.name == "Sortable.min.js":
            text = text.split("\n", 1)[1]  # license banner mentions the git repo
        assert not re.search(r"https?://", text), path


def test_icons_exist_in_bundled_font_awesome() -> None:
    from .conftest import CommentInline, TagInline

    css_dir = pathlib.Path(sqladmin.__file__).parent / "statics" / "css"
    css = "".join(p.read_text() for p in css_dir.glob("fontawesome*.css"))
    assert css, "sqladmin no longer bundles Font Awesome"
    sources = [p.read_text() for p in pathlib.Path(str(PKG)).rglob("*.html")]
    sources.append(
        (pathlib.Path(str(PKG)) / "statics/js/sqladmin-inline.js").read_text()
    )
    sources += [TagInline.icon or "", CommentInline.icon or ""]
    modifiers = {"fa-solid", "fa-regular", "fa-brands", "fa-spin", "fa-2x", "fa-lg"}
    icons = set(re.findall(r"\bfa-[a-z0-9-]+", " ".join(sources))) - modifiers
    assert icons
    missing = [
        i for i in sorted(icons) if not re.search(rf"\.{re.escape(i)}[\s,:{{]", css)
    ]
    assert missing == []


def test_bundled_assets_present() -> None:
    statics = PKG / "statics"
    assert (
        (statics / "js" / "Sortable.min.js").read_text().startswith("/*! Sortable 1.15")
    )
    assert (
        "MIT License" in (statics / "licenses" / "SortableJS-LICENSE.txt").read_text()
    )
    for name in (
        "_inline_form.html",
        "_inline_scripts.html",
        "_inline_table.html",
        "create.html",
        "edit.html",
    ):
        assert (PKG / "templates" / "sqladmin_inline" / name).is_file()


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_javascript_syntax() -> None:
    path = pathlib.Path(str(PKG)) / "statics" / "js" / "sqladmin-inline.js"
    subprocess.run(["node", "--check", str(path)], check=True)


def test_ui_does_not_depend_on_bootstrap_javascript() -> None:
    """sqladmin 0.21-0.27 load Bootstrap 4.6 *and* Bootstrap 5 on the same page.

    Neither `window.bootstrap.Modal` nor the data-api attributes behave the same
    there, so the inline UI drives modals/collapse itself and only relies on the
    CSS classes shared by both versions.
    """
    root = pathlib.Path(str(PKG))
    js = (root / "statics" / "js" / "sqladmin-inline.js").read_text()
    for forbidden in (
        "bootstrap.Modal",
        "tabler.Modal",
        ".modal(",
        "getOrCreateInstance",
        "hidden.bs.",
    ):
        assert forbidden not in js, forbidden
    for path in root.rglob("*.html"):
        html = path.read_text()
        assert "data-bs-" not in html and 'data-toggle="' not in html, path
