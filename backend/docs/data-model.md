# LearnerHub data model

This is the design for the **whole** system. Tables are built in the phase that needs them, each with its own Alembic migration. Tables not built yet are a design, and may change when their phase starts. Update this file whenever a migration changes the schema.

| Table | Module | Status |
|---|---|---|
| `users`, `user_roles` | users | ✅ Built (Phase 1) |
| `oauth_accounts`, `refresh_tokens`, `email_tokens` | auth | ✅ Built (Phase 1) |
| `channels` | channels | ✅ Built (Phase 1) |
| `courses`, `sections`, `lessons` | courses | ✅ Built (Phase 1) |
| `video_assets`, `watch_progress` | media | Designed (Phase 3) |
| `materials` | materials | Designed (Phase 4) |
| `subscriptions` | subscriptions | Designed (Phase 4) |
| `payments`, `webhook_events` | payments | Designed (Phase 4) |
| `comments`, `course_bans` | comments | Designed (Phase 5) |
| `live_sessions` | live | Designed (Phase 6) |
| `notifications`, `push_subscriptions`, `lesson_view_counts` | notifications | Designed (Phase 7) |
| `reports` | admin | Designed (Phase 8) |

## Diagram

Every table has `id` (BIGINT identity) and `created_at`. Most also have `updated_at`. These columns are left out below to keep the diagram readable. Tables from Phase 7–8 that only add counters or reports are also left out.

```mermaid
erDiagram
    users ||--o{ user_roles : "holds"
    users ||--o{ oauth_accounts : "links"
    users ||--o{ refresh_tokens : "logs in with"
    users ||--o{ email_tokens : "receives"
    refresh_tokens |o--o| refresh_tokens : "replaced by"

    users ||--o| channels : "teaches (max one)"
    channels ||--o{ courses : "offers"
    courses ||--o{ sections : "contains"
    sections ||--o{ lessons : "contains"

    lessons ||--o| video_assets : "VIDEO file"
    lessons ||--o| live_sessions : "LIVE session"
    live_sessions |o--o| video_assets : "recording"
    users ||--o{ watch_progress : "watches"
    lessons ||--o{ watch_progress : "tracked in"

    courses ||--o{ materials : "has"
    lessons |o--o{ materials : "optionally attached to"

    users ||--o{ subscriptions : "subscribes"
    channels |o--o{ subscriptions : "CHANNEL scope"
    courses |o--o{ subscriptions : "COURSE scope"
    subscriptions ||--o{ payments : "paid by"

    lessons ||--o{ comments : "has"
    users ||--o{ comments : "writes"
    comments |o--o{ comments : "replies"
    courses ||--o{ course_bans : "moderates"
    users ||--o{ course_bans : "muted or banned"

    users ||--o{ notifications : "receives"

    users {
        citext email UK "case-insensitive"
        varchar full_name
        varchar avatar_url
        varchar password_hash "NULL for Google-only"
        timestamptz email_verified_at
        bool is_active
    }
    user_roles {
        bigint user_id PK, FK
        varchar role PK "LEARNER, TEACHER, ADMIN"
    }
    oauth_accounts {
        bigint user_id FK "UK with provider"
        varchar provider "GOOGLE"
        varchar provider_sub "UK with provider"
    }
    refresh_tokens {
        bigint user_id FK
        uuid family_id "one login chain"
        varchar token_hash UK "SHA-256"
        timestamptz expires_at
        timestamptz revoked_at
        bigint replaced_by_id FK
    }
    email_tokens {
        bigint user_id FK
        varchar purpose "VERIFY_EMAIL, RESET_PASSWORD"
        varchar token_hash UK
        timestamptz expires_at
        timestamptz used_at
    }
    channels {
        bigint teacher_id FK, UK "one per teacher"
        varchar name
        varchar slug UK
        text bio
        varchar banner_key "S3 key"
        int price_monthly_paise "0 = free"
    }
    courses {
        bigint channel_id FK
        varchar title
        varchar slug UK
        text description
        varchar thumbnail_key
        int price_paise
        varchar status "DRAFT, PUBLISHED, UNLISTED"
        timestamptz published_at
        tsvector search_vector "generated, GIN index"
    }
    sections {
        bigint course_id FK
        varchar title
        int position "UK with course_id, deferred"
    }
    lessons {
        bigint section_id FK
        varchar type "VIDEO, LIVE"
        varchar title
        int position "UK with section_id, deferred"
        varchar status "DRAFT, PUBLISHED"
        timestamptz published_at "future = scheduled"
    }
    video_assets {
        bigint lesson_id FK, UK
        varchar raw_s3_key
        varchar hls_prefix
        int duration_sec
        varchar status "UPLOADING, PROCESSING, READY, FAILED"
        varchar transcript_key "AI phase"
    }
    watch_progress {
        bigint user_id PK, FK
        bigint lesson_id PK, FK
        int position_sec
        bool completed
    }
    live_sessions {
        bigint lesson_id FK, UK
        varchar livekit_room_name
        timestamptz scheduled_at
        timestamptz started_at
        timestamptz ended_at
        varchar status
        bigint recording_asset_id FK
    }
    materials {
        bigint course_id FK
        bigint lesson_id FK "optional"
        varchar file_name
        varchar s3_key UK
        bigint size_bytes
        varchar mime_type
        text extracted_text "AI phase"
    }
    subscriptions {
        bigint learner_id FK
        varchar scope "CHANNEL, COURSE"
        bigint channel_id FK "set when scope = CHANNEL"
        bigint course_id FK "set when scope = COURSE"
        varchar status "PENDING, ACTIVE, PAST_DUE, CANCELLED, EXPIRED"
        timestamptz current_period_end
        varchar razorpay_sub_id UK
    }
    payments {
        bigint subscription_id FK
        int amount_paise
        varchar status
        varchar razorpay_payment_id UK
    }
    webhook_events {
        varchar provider
        varchar event_id UK "makes webhooks idempotent"
        jsonb payload
        timestamptz processed_at
    }
    comments {
        bigint lesson_id FK
        bigint user_id FK
        bigint parent_id FK "one level of replies"
        text body
        int video_timestamp_sec "at 12:30, why..."
        bool is_live
        bool is_pinned "max one per lesson"
        timestamptz deleted_at "soft delete"
    }
    course_bans {
        bigint course_id FK
        bigint user_id FK
        varchar kind "MUTE, BAN"
        timestamptz until
    }
    notifications {
        bigint user_id FK
        varchar type
        jsonb payload
        timestamptz read_at
    }
```

## Rules the schema enforces

These are enforced by Postgres itself, so even a buggy service or a manual SQL edit can't break them.

| Rule | How |
|---|---|
| An email is registered once, ignoring letter case | `users.email` is `CITEXT UNIQUE` |
| A user holds each role at most once | primary key `(user_id, role)` |
| A Google account links to one user, and a user links one Google account | `UNIQUE(provider, provider_sub)` and `UNIQUE(user_id, provider)` |
| A teacher has at most one channel | `UNIQUE(channels.teacher_id)` |
| Slugs are URL-safe and unique | `CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$')` plus `UNIQUE` |
| Prices are never negative; money is exact | integer paise with `CHECK (>= 0)` |
| Positions within a course or section are unique, but can be swapped in one transaction | `UNIQUE(parent_id, position) DEFERRABLE INITIALLY DEFERRED` |
| A published course or lesson records when it was published | `CHECK (status = 'DRAFT' OR published_at IS NOT NULL)` |
| Enum columns only hold known values | a `CHECK (col IN (...))` per enum |
| *(Phase 4)* A subscription points at exactly the target its scope says | `CHECK` on `scope` + `channel_id` + `course_id` |
| *(Phase 4)* At most one live subscription per learner and target | partial `UNIQUE` index on PENDING, ACTIVE and PAST_DUE |
| *(Phase 5)* At most one pinned comment per lesson | partial `UNIQUE` index where `is_pinned` |

## What happens on delete

| Deleting a… | Effect |
|---|---|
| user | Their roles, linked accounts and tokens are deleted (`CASCADE`). **Blocked** if they own a channel (`RESTRICT`): disable the account with `is_active = false` instead. |
| channel | **Blocked** while it has courses (`RESTRICT`). |
| course | Its sections and lessons are deleted (`CASCADE`). |
| section | Its lessons are deleted (`CASCADE`). |

## The access rule

Everything a learner can open (video, materials, comments, live) goes through one check:

```
can_access(user, course) =
       user owns the course's channel
    OR user has an ACTIVE subscription with scope COURSE  and course_id  = course.id
    OR user has an ACTIVE subscription with scope CHANNEL and channel_id = course.channel_id
```

The spec stores the subscription target as a single `target_id`. Here it's two columns, `channel_id` and `course_id`, because Postgres can't enforce a foreign key on a column that sometimes means a channel and sometimes a course. With two real foreign keys, a subscription can never point at a deleted or nonexistent target, and both lookups in the rule above get an index.
