"""app/core/pagination.py: cursors, and splitting rows into pages."""

import base64
import re
from datetime import UTC, datetime
from typing import Literal

import pytest
from pydantic import AwareDatetime, TypeAdapter

from app.core.pagination import (
    CursorId,
    InvalidCursor,
    decode_cursor,
    encode_cursor,
    paginate,
)

NEWEST = TypeAdapter(tuple[Literal["new"], AwareDatetime, CursorId])
BEST_MATCH = TypeAdapter(tuple[Literal["rank"], float, CursorId])


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode()


def test_a_cursor_comes_back_exactly() -> None:
    when = datetime(2026, 11, 17, 10, 30, 15, 123456, tzinfo=UTC)
    assert decode_cursor(encode_cursor(("new", when, 42)), NEWEST) == ("new", when, 42)

    # A search rank. Not a single bit may change, or the next page would compare
    # against a slightly different value and skip or repeat courses that tie.
    rank = 0.060792710632085800
    cursor = encode_cursor(("rank", rank, 7))
    assert decode_cursor(cursor, BEST_MATCH) == ("rank", rank, 7)


def test_cursors_are_safe_in_a_url() -> None:
    cursor = encode_cursor(("new", datetime.now(UTC), 42))

    assert re.fullmatch(r"[A-Za-z0-9_-]+", cursor)


@pytest.mark.parametrize(
    "cursor",
    [
        "not a cursor!",
        _b64(b"not json"),
        _b64(b'["new", "yesterday", 42]'),
        _b64(b'["new", "2026-11-17T10:00:00", 42]'),
        _b64(b'["new", "2026-11-17T10:00:00Z", 99999999999999999999]'),
        _b64(b'["new", "2026-11-17T10:00:00Z"]'),
        _b64(b'["rank", 0.5, 42]'),
    ],
    ids=[
        "garbage",
        "not-json",
        "not-a-time",
        "time-without-timezone",
        "id-too-big-for-the-database",
        "missing-id",
        "cursor-of-another-sort-order",
    ],
)
def test_anything_else_is_an_invalid_cursor(cursor: str) -> None:
    with pytest.raises(InvalidCursor):
        decode_cursor(cursor, NEWEST)


def test_paginate_uses_the_extra_row_only_to_detect_a_next_page() -> None:
    # Fetched with LIMIT 3 + 1. Three rows came back, so this is the last page.
    assert paginate([1, 2, 3], 3, lambda n: ("n", n)) == ([1, 2, 3], None)

    # Four came back: show three, and point the cursor at the third, not the fourth.
    page, cursor = paginate([1, 2, 3, 4], 3, lambda n: ("n", n))
    assert page == [1, 2, 3]
    assert cursor is not None
    assert decode_cursor(cursor, TypeAdapter(tuple[str, int])) == ("n", 3)
