"""Request and response bodies for /channels."""

from datetime import datetime
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    StringConstraints,
    field_validator,
)
from pydantic_core import PydanticCustomError

from app.core.schemas import NotNull, PricePaise
from app.core.slugs import validate_slug

# Slugs that would clash with fixed routes: /channels/me must never mean a channel
# called "me".
RESERVED_SLUGS = frozenset({"me"})

NAME_MAX_LENGTH = 100  # the channels.name column
SLUG_MAX_LENGTH = 100  # the channels.slug column
BIO_MAX_LENGTH = 5000


def _not_reserved(slug: str) -> str:
    if slug in RESERVED_SLUGS:
        raise PydanticCustomError("slug_reserved", "This URL is reserved.")
    return slug


ChannelName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=NAME_MAX_LENGTH),
]
# Lowercased for convenience ("Adi-Codes" -> "adi-codes"); anything else must already
# be a valid slug.
ChannelSlug = Annotated[
    str,
    StringConstraints(strip_whitespace=True, to_lower=True, max_length=SLUG_MAX_LENGTH),
    AfterValidator(validate_slug),
    AfterValidator(_not_reserved),
]
Bio = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=BIO_MAX_LENGTH)
]


# --- Requests ----------------------------------------------------------------------------


class ChannelCreate(BaseModel):
    name: ChannelName
    # Leave it out to have one made from the name ("Python With Adi" ->
    # "python-with-adi", or "python-with-adi-2" if that's taken).
    slug: ChannelSlug | None = None
    bio: Bio | None = None
    price_monthly_paise: PricePaise = 0

    @field_validator("bio")
    @classmethod
    def _blank_bio_means_none(cls, bio: str | None) -> str | None:
        return bio or None


class ChannelUpdate(BaseModel):
    """Only the fields sent are changed. `"bio": null` (or "") clears the bio.

    Renaming the channel keeps its URL, so links people shared keep working. Send
    `slug` to change the URL on purpose.
    """

    # Unknown fields are refused, so a typo can't pass as a successful change.
    model_config = ConfigDict(extra="forbid")

    name: Annotated[ChannelName | None, NotNull] = None
    slug: Annotated[ChannelSlug | None, NotNull] = None
    bio: Bio | None = None
    price_monthly_paise: Annotated[PricePaise | None, NotNull] = None

    @field_validator("bio")
    @classmethod
    def _blank_bio_means_none(cls, bio: str | None) -> str | None:
        return bio or None


# --- Responses ---------------------------------------------------------------------------


class TeacherOut(BaseModel):
    """Public facts about a channel's teacher. No email: anyone can see this."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str | None
    avatar_url: str | None


class ChannelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    bio: str | None
    price_monthly_paise: int
    teacher: TeacherOut
    created_at: datetime
