"""Request and response bodies for /auth, matching frontend/src/api.ts."""

from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    EmailStr,
    SecretStr,
    StringConstraints,
    field_validator,
)
from pydantic_core import PydanticCustomError

from app.modules.users.schemas import UserOut

# --- Passwords ---------------------------------------------------------------------------
#
# Passwords are SecretStr: if a request model is ever logged or shows up in an error, the
# password appears as '**********'. The checks are written out by hand because pydantic's
# built-in length errors for SecretStr read badly ("at least 8 items").

MIN_PASSWORD_LENGTH = 8  # same as the frontend's MIN_PASSWORD in Register.tsx
# Long enough for any passphrase or password manager, short enough that nobody can make
# the server hash megabytes.
MAX_PASSWORD_LENGTH = 128


def _within_max_length(password: SecretStr) -> SecretStr:
    if len(password.get_secret_value()) > MAX_PASSWORD_LENGTH:
        raise PydanticCustomError(
            "password_too_long",
            "Password must be at most {max} characters.",
            {"max": MAX_PASSWORD_LENGTH},
        )
    return password


def _long_enough(password: SecretStr) -> SecretStr:
    # Length is the only rule. Composition rules ("one digit, one symbol") push people
    # towards predictable passwords; NIST's guidance (SP 800-63B) is to drop them.
    if len(password.get_secret_value()) < MIN_PASSWORD_LENGTH:
        raise PydanticCustomError(
            "password_too_short",
            "Password must be at least {min} characters.",
            {"min": MIN_PASSWORD_LENGTH},
        )
    return password


# A password being set: checked against the current rules.
NewPassword = Annotated[
    SecretStr, AfterValidator(_within_max_length), AfterValidator(_long_enough)
]
# A password being checked at login. No minimum: if the rules change, passwords set
# under the old rules must still work.
Password = Annotated[SecretStr, AfterValidator(_within_max_length)]

# A random token from an email link (43 characters); the limit just rejects junk early.
EmailLinkToken = Annotated[str, StringConstraints(min_length=1, max_length=100)]


# --- Requests ----------------------------------------------------------------------------


class RegisterIn(BaseModel):
    # EmailStr checks the format, trims spaces and lowercases the domain. Comparisons
    # ignore case anyway, because the column is CITEXT.
    email: EmailStr
    password: NewPassword
    full_name: (
        Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] | None
    ) = None

    @field_validator("full_name")
    @classmethod
    def _blank_name_means_none(cls, full_name: str | None) -> str | None:
        return full_name or None


class LoginIn(BaseModel):
    email: EmailStr
    password: Password


class ChangePasswordIn(BaseModel):
    # Required when the account already has a password. Google-only accounts leave it
    # out to set their first password.
    current_password: Password | None = None
    new_password: NewPassword


class PasswordResetRequestIn(BaseModel):
    email: EmailStr


class PasswordResetConfirmIn(BaseModel):
    token: EmailLinkToken
    new_password: NewPassword


class EmailVerificationConfirmIn(BaseModel):
    token: EmailLinkToken


# --- Responses ---------------------------------------------------------------------------


class ProvidersOut(BaseModel):
    """Which sign-in methods the login page should offer."""

    password: bool
    google: bool


class TokenOut(BaseModel):
    """What /auth/token returns: the OAuth2 shape Swagger's "Authorize" button expects."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"


class AuthResponse(TokenOut):
    """Returned by register, login and refresh. The refresh token travels separately,
    as an httpOnly cookie, so it never appears in a response body."""

    expires_in: int  # seconds until access_token expires
    user: UserOut
