import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.db import engine
from app.core.errors import AppError, app_error_handler, validation_error_handler
from app.core.health import router as health_router
from app.modules.auth.router import router as auth_router
from app.modules.channels.router import router as channels_router
from app.modules.courses.router import router as courses_router
from app.modules.users.router import router as users_router

# Show the app's own INFO logs alongside uvicorn's.
logging.basicConfig(
    level=logging.INFO, format="%(levelname)s:     %(name)s: %(message)s"
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield  # the app serves requests while we're paused here
    # On shutdown: close the pooled database connections cleanly.
    await engine.dispose()


app = FastAPI(title="LearnHub Api", lifespan=lifespan)

# The React app runs on another origin (port 5173), and browsers block its calls here
# unless CORS allows them. allow_credentials lets the refresh cookie travel with those
# calls. Browsers refuse credentials with a wildcard origin, so the origin is exact.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_exception_handler(RequestValidationError, validation_error_handler)
# Every module's domain errors (AuthError, NotFound, Conflict, ...) become
# {"detail": ...} responses with their own status code.
app.add_exception_handler(AppError, app_error_handler)

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(channels_router)
app.include_router(courses_router)
