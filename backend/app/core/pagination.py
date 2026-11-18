"""Cursor ("keyset") pagination, shared by every list that can grow long.

A page is {"items": [...], "next_cursor": "..."}. For the next page, send the cursor
back as ?cursor=. It holds the sort values of the last item shown, and the next page
is simply "the rows that sort after it":

    WHERE (published_at, id) < (:last_published_at, :last_id)
    ORDER BY published_at DESC, id DESC
    LIMIT 21     -- one row more than the page, only to learn if another page exists

Why not ?page=3 (OFFSET 40)?
  * Rows added while someone pages through shift every OFFSET page: they see an item
    twice, or never. A cursor marks a place in the order, so new rows don't move it.
  * OFFSET 10000 makes the database read and throw away 10,000 rows first. A cursor
    starts right where the last page ended.
Every sort key ends with `id`, so no two rows tie and none can fall between pages.
"""

import base64
from collections.abc import Callable, Sequence
from typing import Annotated, Any

from fastapi import Query
from pydantic import BaseModel, Field, TypeAdapter
from pydantic_core import to_json

from app.core.db import BIGINT_MAX
from app.core.errors import AppError

DEFAULT_LIMIT = 20
MAX_LIMIT = 50

# Query parameters for list endpoints:
#     query: SearchQuery = None, cursor: Cursor = None, limit: Limit = DEFAULT_LIMIT
SearchQuery = Annotated[str | None, Query(max_length=200)]
Cursor = Annotated[
    str | None, Query(max_length=500, description="next_cursor from the previous page")
]
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]

# The id inside a cursor goes back into SQL, so it gets the same bound as ids in URLs.
CursorId = Annotated[int, Field(gt=0, le=BIGINT_MAX)]


class Page[Item](BaseModel):
    items: list[Item]
    # Send it back as ?cursor= for the next page. null: this is the last page.
    next_cursor: str | None


class InvalidCursor(AppError):
    detail = "This page link is invalid. Start again from the first page."


def encode_cursor(key: tuple[Any, ...]) -> str:
    """Base64 of the JSON sort key, e.g. ["new", "2026-11-17T10:00:00Z", 42].

    Opaque to clients (they pass it back unchanged), so the key's format can change
    without breaking them. Not secret, and it needn't be: editing a cursor only
    changes where a page starts, never what the reader is allowed to see.
    """
    return base64.urlsafe_b64encode(to_json(key)).rstrip(b"=").decode()


def decode_cursor[Key](cursor: str, key: TypeAdapter[Key]) -> Key:
    """The sort key inside `cursor`, checked against `key`'s type. InvalidCursor (400)
    for anything else: bad base64, bad JSON, or a cursor from a different list."""
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        return key.validate_json(raw)
    except ValueError:  # covers binascii.Error and pydantic's ValidationError
        raise InvalidCursor from None


def paginate[Row](
    rows: Sequence[Row], limit: int, sort_key: Callable[[Row], tuple[Any, ...]]
) -> tuple[list[Row], str | None]:
    """Split rows fetched with LIMIT limit + 1 into this page and the next cursor."""
    if len(rows) <= limit:
        return list(rows), None
    page = list(rows[:limit])
    return page, encode_cursor(sort_key(page[-1]))
