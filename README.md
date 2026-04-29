# sqladmin-inline

> SQLAdmin Inline is an extension for [sqladmin](https://github.com/smithyhq/sqladmin) that brings Django-style inline editing to your SQLAlchemy models. It allows you to manage related records (one-to-many) directly within the parent model's form.

[![Coverage Status](https://img.shields.io/badge/%20Python%20Versions-%3E%3D3.10-informational)](https://github.com/Shchusia/sqladmin_inline)
[![Coverage Status](https://coveralls.io/repos/github/Shchusia/sqladmin-inline/badge.svg?branch=feature/v0.0.1)](https://coveralls.io/github/Shchusia/sqladmin-inline?branch=feature/v0.0.1)
[![Coverage Status](https://img.shields.io/badge/Version-0.0.3-informational)](https://pypi.org/project/sqladmin_inline/)

## Features

+ Django-style Inlines: Add, edit, and remove related records without leaving the main form.
+ Async & Sync Support: Fully compatible with both asynchronous and synchronous SQLAlchemy sessions.
+ Built-in Search & Pagination: Manage large numbers of child records with AJAX-powered search.
+ Flexible Layouts: Supports modal-based editing and different positions (center or sidebar).

## Installation

```shell
pip install sqladmin-inline
```

## Full Example

Based on the provided demo.py, here is how you can set up a Post with inline Tags and Comments:

```python

from sqladmin import Admin
from sqladmin_inline import InlineModelAdmin, ModelViewWithInlines, setup_inline_routes

# 1. Define the Inline configuration
class TagInline(InlineModelAdmin, model=Tag):
    inline_label = "Tags"
    icon = "fas fa-tag"
    column_list = [Tag.name]
    column_searchable_list = [Tag.name]
    page_size = 5

class CommentInline(InlineModelAdmin, model=Comment):
    inline_label = "Comments"
    icon = "fa fa-comments"
    layout = "center" # Displays below the main form
    column_list = [Comment.body, Comment.author]
    form_columns = [Comment.body, Comment.author]

# 2. Use ModelViewWithInlines for the parent model
class PostAdmin(ModelViewWithInlines, model=Post):
    name_plural = "Posts"
    icon = "fa-solid fa-newspaper"
    inlines = [TagInline, CommentInline]

# 3. Initialize and register
app = FastAPI()
admin = Admin(app, engine, session_maker=session_mk)

# This step is CRITICAL to register AJAX routes and templates
setup_inline_routes(admin)

admin.add_view(PostAdmin)
```

## Configuration Reference

InlineModelAdmin Attributes

+ model: The SQLAlchemy model class (required).
+ fk_attr: Explicit foreign key attribute name (auto-detected if omitted).
+ column_list: Columns to display in the inline table.
+ column_searchable_list: Columns to include in the AJAX search.
+ layout: "center" (below form) or "sidebar" (right side).
+ can_create, can_edit, can_delete: Permissions for the inline records.

## Requirements
+ SQLAdmin >= 0.16.0
+ SQLAlchemy >= 2.0.0
+ Starlette / FastAPI
