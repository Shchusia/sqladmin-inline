# sqladmin-inline

> SQLAdmin Inline is an extension for [sqladmin](https://github.com/smithyhq/sqladmin) that brings Django-style inline editing to your SQLAlchemy models. It allows you to manage related records (one-to-many) directly within the parent model's form.

[![Coverage Status](https://img.shields.io/badge/%20Python%20Versions-%3E%3D3.10-informational)](https://github.com/Shchusia/sqladmin_inline)
[![Coverage Status](https://coveralls.io/repos/github/Shchusia/sqladmin-inline/badge.svg)](https://coveralls.io/github/Shchusia/sqladmin-inline)
[![Coverage Status](https://img.shields.io/badge/Version-0.2.1-informational)](https://pypi.org/project/sqladmin_inline/)

**Works fully offline.** Every asset is either bundled with this package (SortableJS, the
inline JS/CSS) or already shipped by sqladmin (Bootstrap/Tabler, Font Awesome, jQuery,
select2, flatpickr). Nothing is loaded from a CDN, so the admin keeps working inside a VPN
or on an isolated network. This is enforced by the test-suite.

## Features

- Add / edit in a modal, bulk delete with confirmation
- AJAX search, pagination and "Load more"
- Drag-and-drop ordering (`order_field`)
- Layouts: `center` (under the form) or `sidebar` (right column)
- Async **and** sync SQLAlchemy session makers
- Same security as sqladmin's edit page: `authentication_backend`, `is_accessible`,
  `can_edit`, `check_can_edit` and `form_edit_query` all apply to inline endpoints
- Works with sqladmin's `base_url` and behind reverse proxies (`root_path`)

## Installation

```shell
pip install sqladmin-inline
```

## Quick start

```python
from fastapi import FastAPI
from sqladmin import Admin
from sqladmin_inline import InlineModelAdmin, ModelViewWithInlines, setup_inline_routes


class TagInline(InlineModelAdmin, model=Tag):
    inline_label = "Tags"
    icon = "fa-solid fa-tag"
    layout = "sidebar"
    order_field = "position"            # integer column -> drag-and-drop
    column_list = [Tag.name]
    column_searchable_list = [Tag.name]
    form_excluded_columns = [Tag.post, Tag.position]
    page_size = 5


class CommentInline(InlineModelAdmin, model=Comment):
    inline_label = "Comments"
    icon = "fa-solid fa-comments"
    column_default_sort = ("id", True)   # newest first
    column_list = [Comment.body, Comment.author]
    column_labels = {Comment.body: "Text"}
    form_columns = [Comment.body, Comment.author]   # FK select for author
    can_edit = False


class PostAdmin(ModelViewWithInlines, model=Post):
    column_list = [Post.id, Post.title]
    inlines = [TagInline, CommentInline]


app = FastAPI()
admin = Admin(app, engine)
setup_inline_routes(admin)   # registers endpoints, templates and static files
admin.add_view(PostAdmin)
```

## `InlineModelAdmin` options

| Option | Default | Description |
|---|---|---|
| `model` | — | Child SQLAlchemy model (class keyword) |
| `fk_attr` | auto | Relationship or column pointing to the parent |
| `identity` | `<model>_inline` | Set it when a parent has two inlines of the same model |
| `inline_label` | `<Model>s` | Section title |
| `icon` | `None` | Icon classes, e.g. `"fa-solid fa-tag"` (Font Awesome bundled with sqladmin) |
| `layout` | `"center"` | `"center"` or `"sidebar"` |
| `column_list` | all non-PK columns | Columns of the table |
| `column_labels` | `{}` | Column labels |
| `column_searchable_list` | `[]` | Columns used by the search box |
| `column_default_sort` | PK asc | `(column, descending)` |
| `order_field` | `None` | Integer column for drag-and-drop ordering |
| `page_size` | `5` | Rows per page / per "Load more" |
| `can_create`, `can_edit`, `can_delete` | `True` | Inline permissions |
| `form_columns`, `form_excluded_columns`, `form_args`, `form_widget_args` | — | As in `sqladmin.ModelView` |

If the form contains the relationship to the parent, it is pre-selected when adding a row, and
the parent passed in the URL always wins on create. Most projects simply exclude it
(`form_excluded_columns = [Tag.post]`).

## HTTP endpoints

All are relative to the admin mount point and protected like the parent's edit page:

```
GET    /{identity}/inline/{inline}/{parent_pk}/list      table fragment (?page=&search=)
GET    /{identity}/inline/{inline}/{parent_pk}/form      form fragment (?pk= to edit)
POST   /{identity}/inline/{inline}/{parent_pk}/save      create/update (422 + form on errors)
DELETE /{identity}/inline/{inline}/{parent_pk}/delete    {"pks": [...]}
POST   /{identity}/inline/{inline}/{parent_pk}/reorder   {"pks": [...]} in the new order
GET    /_inline/static/...                               bundled JS/CSS
```

## Compatibility

Python ≥ 3.10, SQLAlchemy ≥ 2.0, sqladmin ≥ 0.25 (tested with 0.25, 0.28, 0.30, 0.32).
On sqladmin ≥ 0.31 the official `edit_context` hook is used; older versions get a thin
wrapper around the edit route that keeps sqladmin's own handler.

## Development

```shell
task sync     # uv sync with demo/tests/lint groups
task test     # unit + HTTP + security + offline checks (async and sync sessions)
task e2e      # headless browser test (needs node), external network blocked
task lint     # pre-commit: ruff, black, mypy, bandit
task demo     # http://localhost:8000/admin
```

## Licenses

SortableJS (MIT) is bundled; its license is in `sqladmin_inline/statics/licenses/`.
