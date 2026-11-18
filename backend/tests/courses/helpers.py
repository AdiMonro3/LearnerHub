"""Small helpers for the courses API tests."""

from typing import Any

import httpx

from tests.helpers import SignedIn


async def create_course(
    client: httpx.AsyncClient, teacher: SignedIn, **body: Any
) -> dict[str, Any]:
    response = await client.post(
        "/courses", json={"title": "Python Basics", **body}, headers=teacher.headers
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def set_status(
    client: httpx.AsyncClient, teacher: SignedIn, course: dict[str, Any], status: str
) -> dict[str, Any]:
    response = await client.patch(
        f"/courses/{course['id']}", json={"status": status}, headers=teacher.headers
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


async def add_section(
    client: httpx.AsyncClient, teacher: SignedIn, course: dict[str, Any], title: str
) -> dict[str, Any]:
    response = await client.post(
        f"/courses/{course['id']}/sections",
        json={"title": title},
        headers=teacher.headers,
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def add_lesson(
    client: httpx.AsyncClient,
    teacher: SignedIn,
    section: dict[str, Any],
    title: str,
    **body: Any,
) -> dict[str, Any]:
    response = await client.post(
        f"/sections/{section['id']}/lessons",
        json={"type": "VIDEO", "title": title, **body},
        headers=teacher.headers,
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def patch_lesson(
    client: httpx.AsyncClient, teacher: SignedIn, lesson: dict[str, Any], **body: Any
) -> dict[str, Any]:
    response = await client.patch(
        f"/lessons/{lesson['id']}", json=body, headers=teacher.headers
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


async def outline(
    client: httpx.AsyncClient,
    course: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> list[tuple[str, list[str]]]:
    """The course page's outline as [(section title, [lesson titles])], in order."""
    response = await client.get(f"/courses/{course['slug']}", headers=headers)
    assert response.status_code == 200, response.text
    return [
        (section["title"], [lesson["title"] for lesson in section["lessons"]])
        for section in response.json()["sections"]
    ]


async def lesson_positions(
    client: httpx.AsyncClient, course: dict[str, Any], teacher: SignedIn
) -> list[list[int]]:
    """Each section's lesson positions, as the teacher sees them."""
    response = await client.get(f"/courses/{course['slug']}", headers=teacher.headers)
    return [
        [lesson["position"] for lesson in section["lessons"]]
        for section in response.json()["sections"]
    ]
