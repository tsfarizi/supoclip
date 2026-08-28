import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch

from tests.fixtures.factories import create_user, create_source, create_task, create_clip


@pytest.mark.asyncio
async def test_get_composition_lazy_backfill(client, db_session, auth_headers):
    # Test GET /tasks/{task_id}/clips/{clip_id}/composition lazy creation
    user = await create_user(db_session, user_id="user-1", email="comp1@example.com")
    source = await create_source(db_session, title="Comp Source")
    task = await create_task(db_session, user_id=user["id"], source_id=source["id"], status="completed")
    clip = await create_clip(db_session, task_id=task["id"], text_value="Test clip composition text")

    resp = await client.get(f"/tasks/{task['id']}/clips/{clip['id']}/composition", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "composition" in data
    assert "composition_version" in data
    assert data["composition_version"] == 1
    assert len(data["composition"]["segments"]) >= 1


@pytest.mark.asyncio
async def test_patch_composition_optimistic_locking(client, db_session, auth_headers):
    user = await create_user(db_session, user_id="user-1", email="comp2@example.com")
    source = await create_source(db_session, title="Comp Source 2")
    task = await create_task(db_session, user_id=user["id"], source_id=source["id"], status="completed")
    clip = await create_clip(db_session, task_id=task["id"], text_value="Test clip composition text 2")

    # 1. Get initial composition
    get_resp = await client.get(f"/tasks/{task['id']}/clips/{clip['id']}/composition", headers=auth_headers)
    assert get_resp.status_code == 200
    comp_data = get_resp.json()["composition"]
    comp_version = get_resp.json()["composition_version"]

    # Modify speed
    comp_data["segments"][0]["speed"]["rate"] = 1.25

    # 2. Patch with wrong base_version -> 409
    bad_patch = await client.patch(
        f"/tasks/{task['id']}/clips/{clip['id']}/composition",
        headers=auth_headers,
        json={"composition": comp_data, "base_version": 999},
    )
    assert bad_patch.status_code == 409
    assert "Composition was modified" in bad_patch.json()["detail"]

    # 3. Patch with correct base_version -> 200
    good_patch = await client.patch(
        f"/tasks/{task['id']}/clips/{clip['id']}/composition",
        headers=auth_headers,
        json={"composition": comp_data, "base_version": comp_version},
    )
    assert good_patch.status_code == 200
    patch_res = good_patch.json()
    assert patch_res["composition_version"] == comp_version + 1
    assert patch_res["composition"]["segments"][0]["speed"]["rate"] == 1.25


@pytest.mark.asyncio
async def test_render_composition_preview_and_export(client, db_session, auth_headers):
    from pathlib import Path

    user = await create_user(db_session, user_id="user-1", email="comp3@example.com")
    source = await create_source(db_session, title="Comp Source 3")
    task = await create_task(db_session, user_id=user["id"], source_id=source["id"], status="completed")
    clip = await create_clip(db_session, task_id=task["id"], text_value="Test clip composition text 3")

    with patch("src.domain.media.concurrency_governor.acquire_render_slot", new_callable=AsyncMock) as mock_acquire, \
         patch("src.domain.media.concurrency_governor.release_render_slot", new_callable=AsyncMock) as mock_release, \
         patch("src.domain.media.resolver.SourceAssetResolver.resolve", new_callable=AsyncMock) as mock_resolve, \
         patch("src.domain.media.render_engine.RenderEngine.render") as mock_render, \
         patch("src.video_utils.ffprobe_duration", return_value=12.5):

        mock_acquire.return_value = True
        mock_resolve.return_value = Path("/tmp/src.mp4")
        mock_render.return_value = Path("/tmp/clips/preview_123.mp4")

        # Intent = preview
        resp = await client.post(
            f"/tasks/{task['id']}/clips/{clip['id']}/composition/render",
            headers=auth_headers,
            json={"intent": "preview"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ready"
        assert data["preview_url"] == "/api/media/clips/preview_123.mp4"
        assert data["duration"] == 12.5

        # Intent = export
        export_resp = await client.post(
            f"/tasks/{task['id']}/clips/{clip['id']}/composition/render",
            headers=auth_headers,
            json={"intent": "export"},
        )
        assert export_resp.status_code == 200
        export_data = export_resp.json()
        assert export_data["status"] == "processing"
        assert "job_id" in export_data


@pytest.mark.asyncio
async def test_render_composition_invalid_intent(client, db_session, auth_headers):
    user = await create_user(db_session, user_id="user-1", email="comp4@example.com")
    source = await create_source(db_session, title="Comp Source 4")
    task = await create_task(db_session, user_id=user["id"], source_id=source["id"], status="completed")
    clip = await create_clip(db_session, task_id=task["id"], text_value="Test clip composition text 4")

    resp = await client.post(
        f"/tasks/{task['id']}/clips/{clip['id']}/composition/render",
        headers=auth_headers,
        json={"intent": "invalid_intent"},
    )
    assert resp.status_code == 400
    assert "intent must be either 'preview' or 'export'" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_render_composition_queued_when_slots_full(client, db_session, auth_headers):
    user = await create_user(db_session, user_id="user-1", email="comp5@example.com")
    source = await create_source(db_session, title="Comp Source 5")
    task = await create_task(db_session, user_id=user["id"], source_id=source["id"], status="completed")
    clip = await create_clip(db_session, task_id=task["id"], text_value="Test clip composition text 5")

    with patch("src.domain.media.concurrency_governor.acquire_render_slot", new_callable=AsyncMock) as mock_acquire:
        mock_acquire.return_value = False

        resp = await client.post(
            f"/tasks/{task['id']}/clips/{clip['id']}/composition/render",
            headers=auth_headers,
            json={"intent": "preview"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "queued"

