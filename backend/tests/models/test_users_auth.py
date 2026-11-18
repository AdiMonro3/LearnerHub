"""Database rules for the users and auth tables."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EmailToken, OAuthAccount, RefreshToken, User, UserRole
from app.modules.auth.models import EmailTokenPurpose, OAuthProvider
from app.modules.users.models import Role
from tests.factories import make_user, violates


async def test_email_is_unique_ignoring_case(db: AsyncSession) -> None:
    await make_user(db, "Adi@Example.com")
    with violates("uq_users_email"):
        await make_user(db, "adi@example.com")


async def test_user_holds_each_role_once(db: AsyncSession) -> None:
    user = await make_user(db)
    await db.execute(insert(UserRole).values(user_id=user.id, role=Role.LEARNER))
    with violates("pk_user_roles"):
        await db.execute(insert(UserRole).values(user_id=user.id, role=Role.LEARNER))


async def test_database_rejects_unknown_role(db: AsyncSession) -> None:
    # Raw SQL skips the Python enum entirely: the CHECK constraint still refuses it.
    user = await make_user(db)
    with violates("ck_user_roles_role"):
        await db.execute(
            text("INSERT INTO user_roles (user_id, role) VALUES (:id, 'SUPERUSER')"),
            {"id": user.id},
        )


async def test_google_account_links_to_only_one_user(db: AsyncSession) -> None:
    first, second = await make_user(db), await make_user(db)
    row = {"provider": OAuthProvider.GOOGLE, "provider_sub": "google-sub-123"}
    await db.execute(insert(OAuthAccount).values(user_id=first.id, **row))
    with violates("uq_oauth_accounts_provider_provider_sub"):
        await db.execute(insert(OAuthAccount).values(user_id=second.id, **row))


async def test_user_links_only_one_google_account(db: AsyncSession) -> None:
    user = await make_user(db)
    google = OAuthProvider.GOOGLE
    await db.execute(
        insert(OAuthAccount).values(
            user_id=user.id, provider=google, provider_sub="sub-a"
        )
    )
    with violates("uq_oauth_accounts_user_id_provider"):
        await db.execute(
            insert(OAuthAccount).values(
                user_id=user.id, provider=google, provider_sub="sub-b"
            )
        )


async def test_deleting_user_deletes_their_roles_tokens_and_links(
    db: AsyncSession,
) -> None:
    user = await make_user(db)
    expires = datetime.now(UTC) + timedelta(days=7)
    db.add_all(
        [
            UserRole(user_id=user.id, role=Role.LEARNER),
            OAuthAccount(
                user_id=user.id, provider=OAuthProvider.GOOGLE, provider_sub="s"
            ),
            RefreshToken(
                user_id=user.id,
                family_id=uuid.uuid4(),
                token_hash="a" * 64,
                expires_at=expires,
            ),
            EmailToken(
                user_id=user.id,
                purpose=EmailTokenPurpose.VERIFY_EMAIL,
                token_hash="b" * 64,
                expires_at=expires,
            ),
        ]
    )
    await db.flush()

    await db.execute(delete(User).where(User.id == user.id))

    for model in (UserRole, OAuthAccount, RefreshToken, EmailToken):
        remaining = await db.scalar(select(func.count()).select_from(model))
        assert remaining == 0, model.__name__


async def test_updated_at_can_be_read_right_after_an_update(db: AsyncSession) -> None:
    # Without eager_defaults on TimestampMixin, updated_at would be "expired" after the
    # UPDATE, and reading it would try a lazy load and raise MissingGreenlet under async.
    user = await make_user(db)
    user.full_name = "Adi"
    await db.flush()
    assert isinstance(user.updated_at, datetime)
