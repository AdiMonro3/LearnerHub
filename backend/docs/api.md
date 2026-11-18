# LearnerHub API: channels and courses

How to use the content API: channels, courses, sections and lessons, plus browsing and search. The interactive docs at `http://localhost:8000/docs` list every field. This page explains the rules behind them.

Auth (register, login, refresh, Google, email links) follows the contract in `frontend/src/api.ts`, and is not repeated here.

## Conventions

**Signing in.** Send the access token as `Authorization: Bearer <token>`. A missing, bad or expired token gives **401**. Answer it by calling `POST /auth/refresh` and retrying.

**Errors** always look like `{"detail": "..."}`. For a 422, `detail` is a list of `{loc, msg, type}`, one entry per invalid field.

| Status | Meaning |
|---|---|
| 400 | The request can't be done as asked, e.g. moving a lesson to another course, or a broken page cursor |
| 401 | Not signed in, or the access token expired |
| 403 | Signed in, but not allowed: e.g. creating a channel before verifying your email |
| 404 | Doesn't exist, **or you may not see it** (someone else's draft, someone else's course) |
| 409 | Clashes with current data: a URL that's taken, or a reorder list that's out of date (reload, then retry) |
| 422 | A field is invalid |

**Ids and slugs.** Public pages use the slug, the URL people share: `GET /channels/{slug}`, `GET /courses/{slug}`. Teacher actions use the numeric id, which never changes: `PATCH /courses/{id}`.

**Slugs** are lowercase letters, digits and single hyphens (`python-from-zero`). Leave `slug` out when creating, and one is made from the name or title, with `-2`, `-3` added if needed. Renaming never changes the slug; send `slug` to change the URL on purpose.

**Money** is whole paise in integers: ₹499 is `49900`. 0 means free. The maximum is ₹1,00,000 (`10000000`).

**Times** are ISO 8601 with a timezone, e.g. `2026-11-24T10:00:00Z` or `2026-11-24T15:30:00+05:30`. Times without a timezone are refused.

**PATCH** changes only the fields you send.
- `null` clears an optional field (`bio`, `description`).
- `null` for a required field (`name`, `title`, `slug`, `status`, prices) is a 422.
- Unknown fields are a 422, so a typo can't pass as a successful change.

**Lists that can grow** (`GET /courses`, `GET /channels`) return one page:

```json
{ "items": [ ... ], "next_cursor": "WyJuZXciLCIyMDI2LTExLTE3VDEwOjE1OjMwWiIsMTJd" }
```

Pass `next_cursor` back as `?cursor=` for the next page; `null` means this is the last page. `?limit=` sets the page size: 1–50, default 20. Cursors stay correct when new items appear while someone is paging through.

## Who sees what

| Course status | Course page | Channel page and search |
|---|---|---|
| `DRAFT` | its teacher only | never |
| `UNLISTED` | anyone with the link | never |
| `PUBLISHED` | everyone | listed |

- A course page includes its outline (sections and lessons).
  - Its teacher sees everything.
  - Everyone else sees only lessons that are `PUBLISHED` and whose `published_at` has passed, and no sections left empty by that.
  - Lesson titles are public, like a course catalogue. Watching a video will need a subscription (Phases 3–4).
- A disabled teacher's channel and courses disappear for everyone (404).

## Channels

One channel per teacher. Creating it makes you a `TEACHER`.

| Method and path | Who | Notes |
|---|---|---|
| `POST /channels` | signed in, verified email | `{name, slug?, bio?, price_monthly_paise?}`. 403 if the email is unverified, 409 if you already have a channel or the slug is taken |
| `GET /channels/me` | signed in | 404 if you have no channel yet |
| `PATCH /channels/me` | signed in | `{name?, slug?, bio?, price_monthly_paise?}` |
| `GET /channels/{slug}` | anyone | the public channel page |
| `GET /channels/{slug}/courses` | anyone | its `PUBLISHED` courses, newest first (not paginated) |
| `GET /channels/me/courses` | teacher | all your courses, any status, newest first (not paginated) |
| `GET /channels?query=&cursor=&limit=` | anyone | newest first; `query` matches part of the name, ignoring case |

A channel looks like this (the teacher's email is never included):

```json
{
  "id": 3, "name": "Python With Adi", "slug": "python-with-adi", "bio": "Python, simply.",
  "price_monthly_paise": 0,
  "teacher": { "id": 7, "full_name": "Adi", "avatar_url": null },
  "created_at": "2026-11-17T10:15:30.638189Z"
}
```

## Courses

| Method and path | Who | Notes |
|---|---|---|
| `POST /courses` | teacher | `{title, slug?, description?, price_paise?}`. Always starts as `DRAFT`. 404 if you have no channel |
| `GET /courses/{slug}` | anyone; signing in is optional | the course plus `sections[].lessons[]` (see "Who sees what") |
| `PATCH /courses/{id}` | its teacher | `{title?, slug?, description?, price_paise?, status?}` |
| `DELETE /courses/{id}` | its teacher | only if it was **never** published; otherwise 409, so set it back to `DRAFT` to hide it |
| `GET /courses?query=&cursor=&limit=` | anyone | `PUBLISHED` courses only. See below |

About `status` and `published_at`:
- The first time a course becomes `PUBLISHED` or `UNLISTED`, `published_at` records when.
- Going back to `DRAFT` keeps that date, and so does publishing again.

**Search** (`?query=`) is full-text, best match first:
- Title matches rank above description matches.
- Word forms match: "loops" finds "Looping in Python".
- It understands search-box syntax: `"exact phrase"`, `-excluded`, `or`.
- Any input is safe to send. Without `query`, you get the newest courses first.

## Sections and lessons

Only the course's teacher can change these. Anyone else gets 404.

| Method and path | Body | Notes |
|---|---|---|
| `POST /courses/{id}/sections` | `{title}` | added at the end |
| `PATCH /sections/{id}` | `{title?}` | |
| `DELETE /sections/{id}` | | deletes its lessons too |
| `POST /sections/{id}/lessons` | `{type, title, description?}` | `type` is `VIDEO` or `LIVE` and can't change later. Added at the end, as `DRAFT` |
| `PATCH /lessons/{id}` | `{title?, description?, status?, published_at?}` | see "Publishing a lesson" below |
| `DELETE /lessons/{id}` | | |

**Publishing a lesson:**
- `{"status": "PUBLISHED"}` publishes it now.
- `{"status": "PUBLISHED", "published_at": "<future time>"}` schedules it. Learners see it from that moment on, with no further action.
- `{"status": "DRAFT"}` hides it again.

## Reordering

Only the course's teacher can reorder or move.

| Method and path | Body | Notes |
|---|---|---|
| `PUT /courses/{id}/sections/order` | `{"section_ids": [...]}` | every section of the course, each once, in the new order |
| `PUT /sections/{id}/lessons/order` | `{"lesson_ids": [...]}` | every lesson of the section, each once |
| `POST /lessons/{id}/move` | `{"section_id": 9, "position": 0}` | any section of the **same** course (400 otherwise). `position` 0 is first; leave it out (or go past the end) for last |

If a reorder list doesn't match what's there now (missing, repeated or unknown ids), the API returns 409 and changes nothing. Reload, then send the list again.

## Not built yet

- **Banner and thumbnail images:** need uploads (Phase 3). `banner_url` and `thumbnail_url` will be added to responses then.
- **Locking lessons to subscribers:** Phases 3–4.
- **Rate limiting:** Redis now runs locally (Phase 0); the limits themselves are not built yet.
