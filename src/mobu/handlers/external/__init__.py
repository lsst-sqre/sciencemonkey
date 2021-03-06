"""Externally-accessible endpoint handlers that serve relative to
``/<app-name>/``.
"""

__all__ = [
    "get_index",
    "post_user",
    "get_users",
    "get_user",
    "delete_user",
]

from mobu.handlers.external.index import get_index
from mobu.handlers.external.user import (
    delete_user,
    get_user,
    get_users,
    post_user,
)
