"""Reordering sections and lessons, and moving a lesson to another place."""

from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests.courses.helpers import (
    add_lesson,
    add_section,
    create_course,
    lesson_positions,
    outline,
)
from tests.helpers import Teacher, make_teacher


async def _course(
    client: httpx.AsyncClient, db: AsyncSession, outline_spec: dict[str, list[str]]
) -> tuple[Teacher, dict[str, Any], dict[str, dict[str, Any]]]:
    """A course built from {"Section": ["Lesson", ...]}. Returns the teacher, the
    course, and every section and lesson by title."""
    teacher = await make_teacher(client, db)
    course = await create_course(client, teacher)
    by_title: dict[str, dict[str, Any]] = {}
    for section_title, lesson_titles in outline_spec.items():
        section = await add_section(client, teacher, course, section_title)
        by_title[section_title] = section
        for lesson_title in lesson_titles:
            by_title[lesson_title] = await add_lesson(
                client, teacher, section, lesson_title
            )
    return teacher, course, by_title


async def _move(
    client: httpx.AsyncClient, teacher: Teacher, lesson: dict[str, Any], **body: Any
) -> httpx.Response:
    return await client.post(
        f"/lessons/{lesson['id']}/move", json=body, headers=teacher.headers
    )


# --- Sections ----------------------------------------------------------------------------


async def test_reordering_sections(client: httpx.AsyncClient, db: AsyncSession) -> None:
    teacher, course, s = await _course(client, db, {"A": [], "B": [], "C": []})

    response = await client.put(
        f"/courses/{course['id']}/sections/order",
        json={"section_ids": [s["C"]["id"], s["A"]["id"], s["B"]["id"]]},
        headers=teacher.headers,
    )

    assert response.status_code == 200
    assert [(x["title"], x["position"]) for x in response.json()] == [
        ("C", 0),
        ("A", 1),
        ("B", 2),
    ]
    assert await outline(client, course, teacher.headers) == [
        ("C", []),
        ("A", []),
        ("B", []),
    ]


async def test_swapping_two_sections_works_in_one_step(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    """Halfway through the swap both sections hold the same position. That's allowed
    because the uniqueness check waits until COMMIT (DEFERRABLE INITIALLY DEFERRED)."""
    teacher, course, s = await _course(client, db, {"A": [], "B": []})

    response = await client.put(
        f"/courses/{course['id']}/sections/order",
        json={"section_ids": [s["B"]["id"], s["A"]["id"]]},
        headers=teacher.headers,
    )

    assert response.status_code == 200
    assert await outline(client, course, teacher.headers) == [("B", []), ("A", [])]


@pytest.mark.parametrize(
    "pick",
    ["missing-one", "duplicate", "unknown-id", "another-courses-section", "empty"],
)
async def test_a_list_that_doesnt_match_is_409_and_changes_nothing(
    client: httpx.AsyncClient, db: AsyncSession, pick: str
) -> None:
    teacher, course, s = await _course(client, db, {"A": [], "B": []})
    elsewhere = await add_section(
        client, teacher, await create_course(client, teacher, title="Other"), "X"
    )
    a, b = s["A"]["id"], s["B"]["id"]
    ids = {
        "missing-one": [b],
        "duplicate": [b, a, a],
        "unknown-id": [b, a, 999_999],
        "another-courses-section": [b, a, elsewhere["id"]],
        "empty": [],
    }[pick]

    response = await client.put(
        f"/courses/{course['id']}/sections/order",
        json={"section_ids": ids},
        headers=teacher.headers,
    )

    assert response.status_code == 409
    assert "Reload and try again" in response.json()["detail"]
    assert await outline(client, course, teacher.headers) == [("A", []), ("B", [])]


# --- Lessons -----------------------------------------------------------------------------


async def test_reordering_lessons(client: httpx.AsyncClient, db: AsyncSession) -> None:
    teacher, course, x = await _course(client, db, {"Basics": ["I", "II", "III"]})

    response = await client.put(
        f"/sections/{x['Basics']['id']}/lessons/order",
        json={"lesson_ids": [x["III"]["id"], x["I"]["id"], x["II"]["id"]]},
        headers=teacher.headers,
    )

    assert response.status_code == 200
    assert [(y["title"], y["position"]) for y in response.json()] == [
        ("III", 0),
        ("I", 1),
        ("II", 2),
    ]
    assert await outline(client, course, teacher.headers) == [
        ("Basics", ["III", "I", "II"])
    ]


async def test_reordering_cannot_pull_in_a_lesson_from_another_section(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    teacher, course, x = await _course(
        client, db, {"Basics": ["I", "II"], "Loops": ["For"]}
    )

    response = await client.put(
        f"/sections/{x['Basics']['id']}/lessons/order",
        json={"lesson_ids": [x["I"]["id"], x["II"]["id"], x["For"]["id"]]},
        headers=teacher.headers,
    )

    assert response.status_code == 409
    assert await outline(client, course, teacher.headers) == [
        ("Basics", ["I", "II"]),
        ("Loops", ["For"]),
    ]


# --- Moving ------------------------------------------------------------------------------


async def test_moving_a_lesson_to_another_section(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    teacher, course, x = await _course(
        client, db, {"Basics": ["Intro", "Setup", "Install"], "Loops": ["For", "While"]}
    )

    response = await _move(
        client, teacher, x["Setup"], section_id=x["Loops"]["id"], position=1
    )

    assert response.status_code == 200
    assert (response.json()["section_id"], response.json()["position"]) == (
        x["Loops"]["id"],
        1,
    )
    assert await outline(client, course, teacher.headers) == [
        ("Basics", ["Intro", "Install"]),
        ("Loops", ["For", "Setup", "While"]),
    ]
    # Both sections are numbered 0, 1, 2, ... again: no gap where Setup was.
    assert await lesson_positions(client, course, teacher) == [[0, 1], [0, 1, 2]]


async def test_moving_without_a_position_puts_it_last(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    teacher, course, x = await _course(
        client, db, {"Basics": ["Intro"], "Loops": ["For", "While"]}
    )

    await _move(client, teacher, x["Intro"], section_id=x["Loops"]["id"])

    assert await outline(client, course, teacher.headers) == [
        ("Basics", []),
        ("Loops", ["For", "While", "Intro"]),
    ]


async def test_a_position_past_the_end_means_last(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    teacher, course, x = await _course(
        client, db, {"Basics": ["Intro"], "Loops": ["For"]}
    )

    await _move(client, teacher, x["Intro"], section_id=x["Loops"]["id"], position=99)

    assert await outline(client, course, teacher.headers) == [
        ("Basics", []),
        ("Loops", ["For", "Intro"]),
    ]


async def test_moving_within_the_same_section(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    teacher, course, x = await _course(
        client, db, {"Basics": ["Intro", "Setup", "Install"]}
    )

    await _move(client, teacher, x["Install"], section_id=x["Basics"]["id"], position=0)

    assert await outline(client, course, teacher.headers) == [
        ("Basics", ["Install", "Intro", "Setup"])
    ]
    assert await lesson_positions(client, course, teacher) == [[0, 1, 2]]


async def test_a_lesson_cannot_move_to_another_course(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    teacher, course, x = await _course(client, db, {"Basics": ["Intro"]})
    other_course = await create_course(client, teacher, title="Other")
    elsewhere = await add_section(client, teacher, other_course, "Elsewhere")

    response = await _move(client, teacher, x["Intro"], section_id=elsewhere["id"])

    assert response.status_code == 400
    assert response.json() == {
        "detail": "A lesson can only move to a section of the same course."
    }
    assert await outline(client, course, teacher.headers) == [("Basics", ["Intro"])]


async def test_a_lesson_cannot_move_into_someone_elses_section(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    teacher, course, x = await _course(client, db, {"Basics": ["Intro"]})
    _, _, theirs = await _course(client, db, {"Theirs": []})

    response = await _move(
        client, teacher, x["Intro"], section_id=theirs["Theirs"]["id"]
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Section not found."}
    assert await outline(client, course, teacher.headers) == [("Basics", ["Intro"])]


@pytest.mark.parametrize(
    "body",
    [
        {"section_id": 0},
        {"section_id": 99999999999999999999},
        {"section_id": 1, "position": -1},
        {},
    ],
    ids=["zero-id", "huge-id", "negative-position", "no-section"],
)
async def test_invalid_moves_are_422(
    client: httpx.AsyncClient, db: AsyncSession, body: dict[str, Any]
) -> None:
    teacher, _, x = await _course(client, db, {"Basics": ["Intro"]})

    response = await _move(client, teacher, x["Intro"], **body)

    assert response.status_code == 422


# --- Ownership ---------------------------------------------------------------------------


async def test_only_the_courses_teacher_can_reorder_or_move(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    teacher, course, x = await _course(
        client, db, {"Basics": ["Intro", "Setup"], "Loops": []}
    )
    other = await make_teacher(client, db)
    attempts: list[tuple[str, str, dict[str, Any]]] = [
        (
            "PUT",
            f"/courses/{course['id']}/sections/order",
            {"section_ids": [x["Loops"]["id"], x["Basics"]["id"]]},
        ),
        (
            "PUT",
            f"/sections/{x['Basics']['id']}/lessons/order",
            {"lesson_ids": [x["Setup"]["id"], x["Intro"]["id"]]},
        ),
        (
            "POST",
            f"/lessons/{x['Intro']['id']}/move",
            {"section_id": x["Loops"]["id"]},
        ),
    ]

    for method, url, body in attempts:
        as_other = await client.request(method, url, json=body, headers=other.headers)
        assert as_other.status_code == 404, (method, url)
        assert (await client.request(method, url, json=body)).status_code == 401

    assert await outline(client, course, teacher.headers) == [
        ("Basics", ["Intro", "Setup"]),
        ("Loops", []),
    ]
