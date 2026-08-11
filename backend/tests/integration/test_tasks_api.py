"""
Gap-fill integration tests for the /tasks API routes (contract falsification).

Each test targets exactly one clause of the tasks API contract:

- GET /tasks/               : 401 without signed auth (1b)
- GET /tasks/{task_id}      : 200 owned / 403 other user / 404 missing (2a-2c)
- GET /tasks/{task_id}/clips: 200 {task_id, clips, total_clips} (3a)
- GET clip file             : 404 unknown clip / 404 missing file / 200 existing file (4a-4c)
- POST /tasks/{id}/share    : 409 when task is not completed (5b)
- GET /tasks/shared/{token} : 404 unknown token / 200 repo-enabled completed / 404 enabled-but-incomplete (6a-6c)
- POST /tasks/{id}/cancel   : 200 for queued task, marks cancelled (7)
- POST /tasks/              : 400 when source.url is missing (10)

State is seeded through repositories/factories, never through POST /tasks/
(which depends on the queue adapter), keeping each test deterministic and
independent of any single queue adapter implementation.
"""

from uuid import uuid4

import pytest

from src.repositories.clip_repository import ClipRepository
from src.repositories.task_repository import TaskRepository
from tests.fixtures.factories import create_source, create_task, create_user


@pytest.fixture(autouse=True)
def _isolate_cached_redis_client():
    """Reset the module-level cached Redis clients before and after each test.

    src/infra/redis_client.get_redis_client() caches a client for the whole
    process; its connection pool binds to the loop active on first use. Under
    pytest-asyncio's per-test loops a client created in one test becomes a
    poisoned shared state for later tests ("Event loop is closed" on reuse).
    Resetting per test keeps every test loop-bound and independent.
    """
    from src.infra import redis_client as _redis_client_module

    _redis_client_module._async_client = None
    _redis_client_module._sync_client = None
    yield
    _redis_client_module._async_client = None
    _redis_client_module._sync_client = None


async def _seed_clip(db, task_id, file_path, *, text="Seeded clip", clip_order=1):
    """Create a clip record with an explicit on-disk file path.

    ClipRepository.create_clip does not commit, so the seeded row must be
    committed explicitly to be visible to the app's request sessions.
    """
    clip_id = await ClipRepository.create_clip(
        db,
        task_id=task_id,
        filename=f"clip-{clip_order}.mp4",
        file_path=str(file_path),
        start_time="00:00",
        end_time="00:10",
        duration=10.0,
        text=text,
        relevance_score=0.9,
        reasoning="seeded",
        clip_order=clip_order,
    )
    await db.commit()
    return clip_id


@pytest.mark.asyncio
async def test_list_tasks_requires_signed_auth(client):
    """Contract 1b: GET /tasks/ without auth headers must be rejected with 401."""
    response = await client.get("/tasks/")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_get_task_returns_owned_task_with_id_status_and_clips_count(
    client, db_session, auth_headers
):
    """Contract 2a: owner receives 200 with id, status and clips_count."""
    owner = await create_user(db_session, user_id="user-1", email="owner@example.com")
    source = await create_source(db_session, title="Detail source")
    task = await create_task(
        db_session, user_id=owner["id"], source_id=source["id"], status="completed"
    )
    await _seed_clip(db_session, task["id"], "/tmp/seeded.mp4")

    response = await client.get(f"/tasks/{task['id']}", headers=auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == task["id"]
    assert payload["status"] == "completed"
    assert payload["clips_count"] == 1
    assert payload["source_title"] == "Detail source"


@pytest.mark.asyncio
async def test_get_task_returns_403_for_other_users_task(client, db_session, auth_headers):
    """Contract 2b: a task owned by another user must be forbidden with 403."""
    await create_user(db_session, user_id="user-1", email="owner@example.com")
    other = await create_user(db_session, user_id="user-2", email="other@example.com")
    source = await create_source(db_session, title="Private source")
    task = await create_task(
        db_session, user_id=other["id"], source_id=source["id"], status="completed"
    )

    response = await client.get(f"/tasks/{task['id']}", headers=auth_headers)

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_get_task_returns_404_for_unknown_task(client, db_session, auth_headers):
    """Contract 2c: an unknown task id must be answered with 404."""
    await create_user(db_session, user_id="user-1", email="owner@example.com")

    response = await client.get(f"/tasks/{uuid4()}", headers=auth_headers)

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_task_clips_returns_shape_with_total_count(
    client, db_session, auth_headers
):
    """Contract 3a: clips listing returns task_id, clips array and total_clips."""
    owner = await create_user(db_session, user_id="user-1", email="owner@example.com")
    source = await create_source(db_session, title="Clips source")
    task = await create_task(
        db_session, user_id=owner["id"], source_id=source["id"], status="completed"
    )
    clip_one = await _seed_clip(db_session, task["id"], "/tmp/one.mp4", clip_order=1)
    clip_two = await _seed_clip(db_session, task["id"], "/tmp/two.mp4", clip_order=2)

    response = await client.get(f"/tasks/{task['id']}/clips", headers=auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"] == task["id"]
    assert payload["total_clips"] == 2
    assert [clip["id"] for clip in payload["clips"]] == [clip_one, clip_two]
    assert "file_path" not in payload["clips"][0]


@pytest.mark.asyncio
async def test_get_clip_file_returns_404_for_unknown_clip(client, db_session, auth_headers):
    """Contract 4a: requesting a clip that does not exist must be answered with 404."""
    owner = await create_user(db_session, user_id="user-1", email="owner@example.com")
    source = await create_source(db_session, title="File source")
    task = await create_task(
        db_session, user_id=owner["id"], source_id=source["id"], status="completed"
    )

    response = await client.get(
        f"/tasks/{task['id']}/clips/{uuid4()}/file", headers=auth_headers
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_clip_file_returns_404_when_file_missing_on_disk(
    client, db_session, auth_headers, tmp_path
):
    """Contract 4b: a clip whose file_path does not exist on disk must be answered with 404."""
    owner = await create_user(db_session, user_id="user-1", email="owner@example.com")
    source = await create_source(db_session, title="Missing file source")
    task = await create_task(
        db_session, user_id=owner["id"], source_id=source["id"], status="completed"
    )
    missing_path = tmp_path / "does-not-exist.mp4"
    clip = await _seed_clip(db_session, task["id"], missing_path)

    response = await client.get(
        f"/tasks/{task['id']}/clips/{clip}/file", headers=auth_headers
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_clip_file_streams_existing_file_as_video(
    client, db_session, auth_headers, tmp_path
):
    """Contract 4c: an existing clip file is served as a video FileResponse (200)."""
    owner = await create_user(db_session, user_id="user-1", email="owner@example.com")
    source = await create_source(db_session, title="Existing file source")
    task = await create_task(
        db_session, user_id=owner["id"], source_id=source["id"], status="completed"
    )
    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(b"video-bytes")
    clip = await _seed_clip(db_session, task["id"], clip_path)

    response = await client.get(
        f"/tasks/{task['id']}/clips/{clip}/file", headers=auth_headers
    )

    assert response.status_code == 200
    assert response.content == b"video-bytes"
    assert response.headers["content-type"] == "video/mp4"
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_share_task_returns_409_for_non_completed_task(
    client, db_session, auth_headers
):
    """Contract 5b: sharing a task that is not completed must conflict with 409."""
    owner = await create_user(db_session, user_id="user-1", email="owner@example.com")
    source = await create_source(db_session, title="Queued source")
    task = await create_task(
        db_session, user_id=owner["id"], source_id=source["id"], status="queued"
    )

    response = await client.post(f"/tasks/{task['id']}/share", headers=auth_headers)

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_shared_task_returns_404_for_unknown_token(client):
    """Contract 6a: an unknown share token must be answered with 404."""
    response = await client.get("/tasks/shared/does-not-exist")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_shared_task_returns_200_when_share_enabled_via_repo(
    client, db_session
):
    """Contract 6b: a completed task whose share state is set via the repository
    is served publicly through its share token."""
    owner = await create_user(db_session, user_id="user-1", email="owner@example.com")
    source = await create_source(db_session, title="Repo-shared source")
    task = await create_task(
        db_session, user_id=owner["id"], source_id=source["id"], status="completed"
    )
    clip = await _seed_clip(db_session, task["id"], "/tmp/repo-shared.mp4", text="Repo transcript")
    token = "repo-enabled-share-token"
    stored_token = await TaskRepository.enable_sharing(db_session, task["id"], token)
    assert stored_token == token

    response = await client.get(f"/tasks/shared/{token}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["source_title"] == "Repo-shared source"
    assert payload["clips"][0]["id"] == clip
    assert payload["clips"][0]["text"] == "Repo transcript"
    assert "user_id" not in payload


@pytest.mark.asyncio
async def test_shared_task_hides_enabled_but_incomplete_generation(
    client, db_session
):
    """Contract 6c (negative boundary): share_enabled with a non-completed task
    must still be answered with 404  the public URL only exists for completed
    generations."""
    owner = await create_user(db_session, user_id="user-1", email="owner@example.com")
    source = await create_source(db_session, title="Incomplete source")
    task = await create_task(
        db_session, user_id=owner["id"], source_id=source["id"], status="queued"
    )
    token = "enabled-but-incomplete-token"
    await TaskRepository.enable_sharing(db_session, task["id"], token)

    response = await client.get(f"/tasks/shared/{token}")

    assert response.status_code == 404


@pytest.mark.asyncio(loop_scope="session")
async def test_cancel_queued_task_marks_task_cancelled(client, db_session, auth_headers):
    """Contract 7: cancelling a queued task returns 200 and marks it cancelled.

    Runs on the session-scoped fixture loop (the project sets
    asyncio_default_fixture_loop_scope = "session"): this route writes through
    the cached Redis client and performs DB writes; running it on the same loop
    as the async fixtures avoids cross-loop connection races on Windows'
    ProactorEventLoop. The autouse _isolate_cached_redis_client fixture keeps
    the module-level Redis cache loop-bound for this test.
    """
    owner = await create_user(db_session, user_id="user-1", email="owner@example.com")
    source = await create_source(db_session, title="Cancellable source")
    task = await create_task(
        db_session, user_id=owner["id"], source_id=source["id"], status="queued"
    )

    response = await client.post(f"/tasks/{task['id']}/cancel", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["message"] == "Task cancellation requested"

    updated = await TaskRepository.get_task_by_id(db_session, task["id"])
    assert updated["status"] == "cancelled"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"source": {}},
        {"source": {"url": ""}},
        {"source": {"url": None}},
    ],
)
@pytest.mark.asyncio
async def test_create_task_without_source_url_returns_400(client, db_session, auth_headers, payload):
    """Contract 10: POST /tasks/ with no source.url must be rejected with 400,
    regardless of whether source is absent, empty, or carries a blank URL."""
    await create_user(db_session, user_id="user-1", email="owner@example.com")

    response = await client.post("/tasks/", headers=auth_headers, json=payload)

    assert response.status_code == 400
    assert response.json()["detail"] == "Source URL is required"
