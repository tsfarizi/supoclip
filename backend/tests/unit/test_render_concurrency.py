import pytest
from tests.fixtures.factories import create_user
from src.shared.config.runtime_settings import (
    DEFAULT_RENDER_CONCURRENCY,
    get_render_concurrency,
    set_render_concurrency,
)


@pytest.mark.asyncio
async def test_render_concurrency_get(client):
    response = await client.get("/settings/render-concurrency")
    assert response.status_code == 200
    data = response.json()
    assert "render_concurrency" in data
    assert data["render_concurrency"] >= 1


@pytest.mark.asyncio
async def test_render_concurrency_put_requires_admin(client, db_session, auth_headers):
    await create_user(
        db_session,
        user_id="user-1",
        email="owner@example.com",
        is_admin=False,
    )
    response = await client.put(
        "/settings/render-concurrency",
        headers=auth_headers,
        json={"render_concurrency": 4},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_render_concurrency_put_updates_and_validates(client, db_session, auth_headers):
    await create_user(
        db_session,
        user_id="user-1",
        email="owner@example.com",
        is_admin=True,
    )

    # Valid update
    response = await client.put(
        "/settings/render-concurrency",
        headers=auth_headers,
        json={"render_concurrency": 4},
    )
    assert response.status_code == 200
    assert response.json() == {"render_concurrency": 4}
    assert get_render_concurrency() == 4

    # Invalid range (< 1)
    bad_res_low = await client.put(
        "/settings/render-concurrency",
        headers=auth_headers,
        json={"render_concurrency": 0},
    )
    assert bad_res_low.status_code == 422

    # Invalid range (> 8)
    bad_res_high = await client.put(
        "/settings/render-concurrency",
        headers=auth_headers,
        json={"render_concurrency": 9},
    )
    assert bad_res_high.status_code == 422
