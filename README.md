# Auth: Google OAuth 2.0 + email/password

FastAPI backend (`backend/`) + React frontend (`frontend/`).

## Run it

```bash
# Terminal 1 — backend (http://localhost:8000, API docs at /docs)
cd backend
../.venv/bin/pip install -r requirements.txt   # first time only
../.venv/bin/fastapi dev src/main.py

# Terminal 2 — frontend (http://localhost:5173)
cd frontend
npm install        # first time only
npm run dev
```

Open **http://localhost:5173** (use `localhost`, not `127.0.0.1`, or cookies and CORS won't match).

Tests: `cd backend && ../.venv/bin/python -m pytest`

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

## How it works

| Endpoint | What it does |
|---|---|
| `POST /auth/register` | Create account (email, password ≥ 8 chars, optional name) and sign in |
| `POST /auth/login` | Email + password sign-in |
| `POST /auth/token` | Standard OAuth 2.0 password grant (form: `username`=email, `password`). Used by the **Authorize** button in `/docs` |
| `GET /auth/google/login` → `/auth/google/callback` | Google sign-in (Authorization Code flow + PKCE, `state`, `nonce`, signature-checked ID token) |
| `POST /auth/refresh` | Get a new access token using the refresh cookie |
| `POST /auth/logout` | End this session |
| `POST /auth/password` | Change password, or set one on a Google-only account. Signs out other devices |
| `GET /users/me` | Example protected route (`Authorization: Bearer <token>`) |

- **Access token:** a JWT valid for 15 minutes. The frontend keeps it in memory only.
- **Refresh token:** random, valid 7 days, stored only as a SHA-256 hash, sent as an `httpOnly` cookie scoped to `/auth`. It **rotates on every use**; replaying an old one revokes that whole login chain (stolen-token detection).
- **Passwords:** hashed with Argon2.
- **Same email, both methods:** signing in with Google using the email of an existing password account links them into one account.

## Before deploying for real

- Set `COOKIE_SECURE=true`, serve over HTTPS, and update `BACKEND_URL` / `FRONTEND_URL` (and the redirect URI in Google Console).
- Add email verification for password sign-ups. Without it, someone could register with another person's email first, and that account would later be linked when the real owner uses Google.
- Add rate limiting on `/auth/login` and `/auth/register`.
- Use Postgres + Alembic migrations instead of SQLite + `create_all`.
