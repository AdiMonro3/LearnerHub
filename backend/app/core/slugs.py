"""URL slugs: the readable part of /channels/{slug} and /courses/{slug}.

A slug is made from a name or title ("Python for Beginners!" -> "python-for-beginners")
and must be unique in its table. When it's taken, a number is added: "-2", "-3", ...
"""

import re
import unicodedata
from collections.abc import Collection

from pydantic_core import PydanticCustomError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

# Lowercase letters and digits in words joined by single hyphens, e.g. "python-with-adi".
# The channels and courses tables enforce it with a CHECK constraint too.
SLUG_PATTERN = "^[a-z0-9]+(-[a-z0-9]+)*$"

# Room kept free at the end of a generated slug for a "-<number>" suffix.
_SUFFIX_ROOM = len("-9999")


def slugify(text: str, max_length: int) -> str:
    """Turn a name into a slug: "C++ & Go: The Basics" -> "c-go-the-basics".

    Accents are dropped ("Café" -> "cafe"). Returns "" when no letters or digits are
    left, e.g. for a title written only in Devanagari; the caller then uses a default.
    """
    # NFKD splits "é" into "e" plus an accent mark; encoding to ASCII drops the mark.
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    # Cutting at max_length can leave a hyphen at the end ("python-"), so strip again.
    return slug[:max_length].rstrip("-")


def validate_slug(slug: str) -> str:
    """For request models (AfterValidator): a slug a person chose must already be in
    slug form. Not quietly rewritten, so the URL they get is the one they typed."""
    if not re.fullmatch(SLUG_PATTERN, slug):
        raise PydanticCustomError(
            "slug_format",
            'Use lowercase letters, digits and single hyphens, like "python-with-adi".',
        )
    return slug


async def free_slug(
    db: AsyncSession,
    column: InstrumentedAttribute[str],
    text: str,
    *,
    max_length: int,
    fallback: str,
    reserved: Collection[str] = (),
) -> str:
    """A slug for `text` that no row in `column`'s table uses yet.

    `fallback` is used when `text` gives nothing (see slugify). `reserved` lists slugs
    that clash with fixed routes, such as "me" in /channels/me.

    Two requests creating the same title at the same moment can still both pick the
    same slug. The UNIQUE constraint then rejects the second insert, and the caller
    turns that into an error rather than a duplicate.
    """
    base = slugify(text, max_length - _SUFFIX_ROOM) or fallback
    # One query fetches the base and every numbered variant ("python", "python-2", ...)
    # but not other slugs that merely start the same way ("python-basics"). The base
    # holds only [a-z0-9-], none of which are special in a regex outside brackets.
    taken = set(
        await db.scalars(select(column).where(column.op("~")(f"^{base}(-[0-9]+)?$")))
    )
    taken.update(reserved)
    if base not in taken:
        return base
    number = 2
    while f"{base}-{number}" in taken:
        number += 1
    return f"{base}-{number}"
