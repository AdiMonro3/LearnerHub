# LearnerHub

A teacher-led learning platform. A teacher runs a channel; the channel has courses, and courses have sections of video or live lessons. Learners subscribe to a channel or a single course.

FastAPI backend in `backend/`, React frontend in `frontend/`.

**Built so far:** accounts and sign-in (password, Google, email links), channels, courses, sections and lessons, search, and the local infrastructure. Video, subscriptions, comments, live classes and notifications come next.

## What you need

- Docker (OrbStack, Docker Desktop or Colima)
- [uv](https://docs.astral.sh/uv/), which installs Python and the backend's packages
- Node.js, for the frontend

## Run it

```bash
# 1. Start the services the backend needs (from the repo root)
docker compose up -d --wait

# 2. The API: http://localhost:8000, interactive docs at http://localhost:8000/docs
cd backend
uv sync                        # first time, and after dependencies change
uv run alembic upgrade head    # first time, and after pulling new migrations
uv run uvicorn app.main:app --reload

# 3. The background worker (sends emails), in a second terminal
cd backend
uv run celery -A app.worker worker --loglevel=info

# 4. The frontend: http://localhost:5173, in a third terminal
cd frontend
npm install                    # first time only
npm run dev
```

Open **http://localhost:5173**. Use `localhost`, not `127.0.0.1`, or cookies and CORS won't match.

`docker compose down` stops the services and keeps their data. `docker compose down -v` also deletes the data.

## Local services

| Service | Address | What it's for | Login |
|---|---|---|---|
| Postgres | `localhost:5433` | The database | `learnhub` / `learnhub` |
| Redis | `localhost:6379` | Job results; later rate limits and caching | |
| RabbitMQ | http://localhost:15672 | The job queue between the API and the worker | `learnhub` / `learnhub` |
| RustFS | http://localhost:9001/rustfs/console/ | File storage with the S3 API (bucket `learnhub`) | `learnhub` / `learnhub-secret` |
| Mailpit | http://localhost:8025 | Catches every email the app sends; nothing leaves your machine | |

Two health checks:
- `GET /health` asks "is the API running?"
- `GET /health/ready` asks "can it reach the database, Redis and RabbitMQ?" It returns 503 and names whatever is down.

## Configuration

Every setting and its default is in [`backend/app/core/config.py`](backend/app/core/config.py). The defaults match `compose.yaml`, so local development needs no configuration.

To change a setting, put it in `backend/.env`, for example `JWT_SECRET=...`. Never commit that file.

## Tests and checks

From `backend/`. Docker must be running, because the tests start their own throwaway Postgres.

```bash
uv run pytest            # all tests
uv run ruff check        # lint
uv run ruff format       # format the code
uv run mypy app tests    # type checks
uv run alembic check     # the models and the migrations agree
```

## Run everything in Docker

```bash
docker compose --profile app up -d --wait --build
```

This command also builds the backend image (`backend/Dockerfile`) and runs three things from it:
1. the database migrations;
2. the API, on port 8000;
3. the worker.

Stop your own uvicorn first, or choose another port with `API_PORT=8001 docker compose ...`.

## Turn on Google sign-in

1. Go to [Google Cloud Console → APIs & Services → Credentials](https://console.cloud.google.com/apis/credentials).
2. If asked, configure the **OAuth consent screen** (External; add your Gmail as a test user).
3. **Create credentials → OAuth client ID → Web application.**
4. Under **Authorized redirect URIs**, add exactly: `http://localhost:8000/auth/google/callback`
5. Copy the client ID and secret into `backend/.env`:
   ```
   GOOGLE_CLIENT_ID=....apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=GOCSPX-...
   ```
6. Restart the backend. The Google button on the login page becomes active.

## More documentation

- [`backend/docs/api.md`](backend/docs/api.md): how to use the channels and courses API
- [`backend/docs/data-model.md`](backend/docs/data-model.md): the database design (ER diagram)

## Before deploying for real

- **Secrets:** set `ENV=production` and a random `JWT_SECRET` of 32+ bytes. The app refuses to start without one.
- **HTTPS:** serve over HTTPS. Set `COOKIE_SECURE=true`, `FRONTEND_URL` and `GOOGLE_REDIRECT_URI`, and update the redirect URI in Google Console.
- **Real services:** point `DATABASE_URL`, `REDIS_URL`, `RABBITMQ_URL`, `SMTP_HOST`/`SMTP_PORT` and the `S3_*` settings at them.
- **Not built yet:**
  - Logging in to a real mail provider (SMTP username, password and TLS).
  - Rate limiting on `/auth/login` and `/auth/register`.
