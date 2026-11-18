"""app/core/slugs.py: making slugs from names, and finding one that isn't taken."""

import re

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.slugs import SLUG_PATTERN, free_slug, slugify
from app.models import Channel
from tests.factories import make_channel

# --- slugify -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "slug"),
    [
        ("Python for Beginners!", "python-for-beginners"),
        ("  C++ & Go: The Basics  ", "c-go-the-basics"),
        ("Café Crème", "cafe-creme"),
        ("DSA -- Part 2", "dsa-part-2"),
        ("हिंदी में पाइथन", ""),  # no Latin letters or digits left
        ("!!!", ""),
    ],
)
def test_slugify(text: str, slug: str) -> None:
    assert slugify(text, max_length=100) == slug


def test_slugify_cuts_long_names_without_a_trailing_hyphen() -> None:
    assert slugify("python basics", max_length=7) == "python"  # not "python-"


@pytest.mark.parametrize(
    "text", ["Hello, World", "  --a--b--  ", "Ünïcödé 123", "x y " * 100]
)
def test_slugify_output_passes_the_database_check(text: str) -> None:
    slug = slugify(text, max_length=100)

    assert re.match(SLUG_PATTERN, slug)
    assert len(slug) <= 100


# --- free_slug ---------------------------------------------------------------------------


async def _free(db: AsyncSession, text: str, reserved: set[str] | None = None) -> str:
    return await free_slug(
        db,
        Channel.slug,
        text,
        max_length=100,
        fallback="untitled",
        reserved=reserved or set(),
    )


async def test_unused_slug_is_returned_as_is(db: AsyncSession) -> None:
    assert await _free(db, "Python With Adi") == "python-with-adi"


async def test_taken_slug_gets_the_next_free_number(db: AsyncSession) -> None:
    await make_channel(db, slug="python")
    assert await _free(db, "Python") == "python-2"

    await make_channel(db, slug="python-2")
    assert await _free(db, "Python") == "python-3"


async def test_a_gap_in_the_numbers_is_reused(db: AsyncSession) -> None:
    await make_channel(db, slug="python")
    await make_channel(db, slug="python-3")

    assert await _free(db, "Python") == "python-2"


async def test_slugs_that_only_start_the_same_are_not_clashes(
    db: AsyncSession,
) -> None:
    await make_channel(db, slug="python-basics")
    await make_channel(db, slug="pythonista")

    assert await _free(db, "Python") == "python"


async def test_reserved_slugs_are_skipped(db: AsyncSession) -> None:
    # "me" would collide with the /channels/me route.
    assert await _free(db, "Me", reserved={"me"}) == "me-2"


async def test_fallback_when_the_name_gives_no_slug(db: AsyncSession) -> None:
    assert await _free(db, "हिंदी") == "untitled"

    await make_channel(db, slug="untitled")
    assert await _free(db, "हिंदी") == "untitled-2"


async def test_long_names_leave_room_for_a_number(db: AsyncSession) -> None:
    first = await _free(db, "a" * 150)
    await make_channel(db, slug=first)

    second = await _free(db, "a" * 150)

    assert second == f"{first}-2"
    assert len(second) <= 100  # still fits the column
