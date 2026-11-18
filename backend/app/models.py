"""Imports every module's models, so they are all registered on `Base.metadata`.

Alembic (to compare models with the database) and the tests (to see every table)
import this file. When a module gets a new models.py, import it here.
"""

from app.core.db import Base
from app.modules.auth.models import EmailToken, OAuthAccount, RefreshToken
from app.modules.channels.models import Channel
from app.modules.courses.models import Course, Lesson, Section
from app.modules.users.models import User, UserRole

__all__ = [
    "Base",
    "Channel",
    "Course",
    "EmailToken",
    "Lesson",
    "OAuthAccount",
    "RefreshToken",
    "Section",
    "User",
    "UserRole",
]
