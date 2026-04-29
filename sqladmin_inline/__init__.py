"""
sqladmin-inlines — Django-style inline editing for sqladmin.

Usage::

    from sqladmin_inline import InlineModelAdmin, ModelViewWithInlines

    class TagInline(InlineModelAdmin, model=Tag):
        column_list = [Tag.name]
        inline_label = "Tags"

    class PostAdmin(ModelViewWithInlines, model=Post):
        inlines = [TagInline]
"""

from .inline import InlineModelAdmin
from .views import ModelViewWithInlines, register_inline_globals, setup_inline_routes

__all__ = [
    "InlineModelAdmin",
    "ModelViewWithInlines",
    "setup_inline_routes",
    "register_inline_globals",
]
__version__ = "0.0.1"
