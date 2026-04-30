# sqladmin-inline

> SQLAdmin Inline is an extension for [sqladmin](https://github.com/smithyhq/sqladmin) that brings Django-style inline editing to your SQLAlchemy models. It allows you to manage related records (one-to-many) directly within the parent model's form.

[![Coverage Status](https://img.shields.io/badge/%20Python%20Versions-%3E%3D3.10-informational)](https://github.com/Shchusia/sqladmin_inline)
[![Coverage Status](https://coveralls.io/repos/github/Shchusia/sqladmin-inline/badge.svg?branch=feature/v0.0.1)](https://coveralls.io/github/Shchusia/sqladmin-inline?branch=feature/v0.0.1)
[![Coverage Status](https://img.shields.io/badge/Version-0.0.4-informational)](https://pypi.org/project/sqladmin_inline/)

## Features

+ Django-style Inlines: Add, edit, and remove related records without leaving the main form.
+ Drag-and-Drop Reordering: Support for manual row reordering using SortableJS by simply defining an order_field
+ AJAX-powered "Load More": Seamlessly append more records to the list without a full page reload.
+ Bulk Actions: Integrated checkbox system for bulk deleting multiple child records at once.
+ Async & Sync Support: Fully compatible with both asynchronous and synchronous SQLAlchemy sessions.
+ Real-time Search & Pagination: Manage large datasets with AJAX-based search and paginated views.
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
    layout        = "sidebar"

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

# 2. Use ModelViewWithInlines for the parent model
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

# 3. Initialize and register
app = FastAPI()
admin = Admin(app, engine, session_maker=session_mk)

# This step is CRITICAL to register AJAX routes and templates
setup_inline_routes(admin)

admin.add_view(PostAdmin)
```

## Configuration Reference

InlineModelAdmin Attributes

+ `model`: The SQLAlchemy model class (required).
+ `fk_attr`: Explicit foreign key attribute name (auto-detected if omitted).
+ `column_list`: Columns to display in the inline table.
+ `column_searchable_list`: Columns to include in the AJAX search.
+ `layout`: "center" (below form) or "sidebar" (right side).
+ `can_create`, `can_edit`, `can_delete`: Permissions for the inline records.
+ `order_field`: The name of the integer column on the model used for manual drag-and-drop ordering.
+ `column_default_sort`: A tuple (column_name, is_descending) to set the default list order.
+ `icon`: A FontAwesome or Tabler icon class string for the inline header (e.g., "fas fa-tag").
+ `page_size`: Number of rows displayed per page or loaded via "Load More" (default: 5).

## Requirements
+ SQLAdmin >= 0.16.0
+ SQLAlchemy >= 2.0.0
+ Starlette / FastAPI
