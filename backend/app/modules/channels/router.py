"""HTTP endpoints under /channels. Thin on purpose: the rules live in service.py."""

from fastapi import APIRouter, status

from app.core.db import DbSession
from app.core.pagination import DEFAULT_LIMIT, Cursor, Limit, Page, SearchQuery
from app.modules.auth.deps import CurrentUser
from app.modules.channels import service
from app.modules.channels.schemas import ChannelCreate, ChannelOut, ChannelUpdate

router = APIRouter(prefix="/channels", tags=["channels"])


@router.get("")
async def find_channels(
    db: DbSession,
    query: SearchQuery = None,
    cursor: Cursor = None,
    limit: Limit = DEFAULT_LIMIT,
) -> Page[ChannelOut]:
    """Browse channels, newest first, or find them by name with ?query=."""
    channels, next_cursor = await service.find_channels(db, query, cursor, limit)
    return Page[ChannelOut](
        items=[ChannelOut.model_validate(channel) for channel in channels],
        next_cursor=next_cursor,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_channel(
    data: ChannelCreate, user: CurrentUser, db: DbSession
) -> ChannelOut:
    """Create your channel; you become a TEACHER. Needs a verified email."""
    channel = await service.create_channel(db, user, data)
    return ChannelOut.model_validate(channel)


# The /me routes are declared before /{slug}, so "me" is never read as a slug. "me" is
# also a reserved slug, so no channel can be called that.
@router.get("/me")
async def read_my_channel(user: CurrentUser, db: DbSession) -> ChannelOut:
    return ChannelOut.model_validate(await service.get_my_channel(db, user))


@router.patch("/me")
async def update_my_channel(
    data: ChannelUpdate, user: CurrentUser, db: DbSession
) -> ChannelOut:
    """Change your channel. Only the fields you send are changed."""
    channel = await service.update_my_channel(db, user, data)
    return ChannelOut.model_validate(channel)


@router.get("/{slug}")
async def read_channel(slug: str, db: DbSession) -> ChannelOut:
    """A channel's public page. No sign-in needed."""
    return ChannelOut.model_validate(await service.get_public_channel(db, slug))
