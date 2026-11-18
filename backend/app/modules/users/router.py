from fastapi import APIRouter

from app.core.db import DbSession
from app.modules.auth import service as auth_service
from app.modules.auth.deps import CurrentUser
from app.modules.users.schemas import UserOut

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me")
async def read_me(user: CurrentUser, db: DbSession) -> UserOut:
    """The signed-in user."""
    return await auth_service.user_out(db, user)
