"""
sqladmin-inline — Django-style inline editing for sqladmin.

Usage::

    from sqladmin_inline import InlineModelAdmin, ModelViewWithInlines, setup_inline_routes

    class TagInline(InlineModelAdmin, model=Tag):
        column_list = [Tag.name]
        inline_label = "Tags"

    class PostAdmin(ModelViewWithInlines, model=Post):
        inlines = [TagInline]

    admin = Admin(app, engine)
    setup_inline_routes(admin)
    admin.add_view(PostAdmin)

All JavaScript/CSS is bundled with the package; nothing is loaded from a CDN.
"""

from importlib.metadata import PackageNotFoundError, version

from .inline import InlineModelAdmin, InlinePage
from .views import ModelViewWithInlines, register_inline_globals, setup_inline_routes

__all__ = [
    "InlineModelAdmin",
    "InlinePage",
    "ModelViewWithInlines",
    "register_inline_globals",
    "setup_inline_routes",
]
try:
    __version__ = version("sqladmin-inline")
except PackageNotFoundError:  # pragma: no cover - running from a source checkout
    __version__ = "0.0.0"
