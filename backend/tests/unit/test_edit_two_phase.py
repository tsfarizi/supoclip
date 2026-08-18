"""T5 (P5): two-phase clip edit falsification.

Contracts under test (src/services/clip_render_service.py +
src/clip_cleanup.py):
  1. Edit phase order: render -> DB commit -> unlink input. The superseded
     input file is removed only AFTER the ``generated_clips`` row has
     committed pointing at the re-rendered file.
  2. When the post-render DB commit FAILS, the row keeps pointing at the OLD
     file (still present), and the newly rendered file is left on disk as an
     unreferenced orphan - it must never become the referenced clip.
  3. ``reconcile_orphaned_clip_files`` removes orphans past the TTL (mp4 plus
     .sfx.json / .source_map.json sidecars), keeps referenced files, and keeps
     orphans younger than the TTL.

The tests exercise the real service against the real database; the caption
renderer is the only mock (``overlay_custom_captions``) so no ffmpeg runs.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.clip_cleanup import reconcile_orphaned_clip_files
from src.config import Config
from src.repositories.clip_repository import ClipRepository
from src.services import task_service as task_service_module
from src.services.clip_render_service import ClipRenderService
from src.services.task_service import TaskService
from tests.fixtures.factories import create_source, create_task, create_user


def _uid(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"[:36]


async def _seed_edit_context(
    db: AsyncSession, temp_dir: Path
) -> tuple[str, str, str, str]:
    """Seed user + source + task + clip row pointing at a real mp4 file.

    Returns (task_id, clip_id, user_id, source_id). The clip file lives under
    ``<temp_dir>/clips/old.mp4`` and exists on disk.
    """
    user_id = _uid("usr")
    await create_user(db, user_id=user_id)
    source = await create_source(
        db,
        source_id=_uid("src"),
        title="Edit source",
        url=f"https://www.youtube.com/watch?v={uuid4().hex[:11]}",
    )
    task = await create_task(
        db, task_id=_uid("task"), user_id=user_id, source_id=source["id"],
        status="processing",
    )
    clips_dir = Path(temp_dir) / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    old_path = clips_dir / "old.mp4"
    old_path.write_bytes(b"old-video-bytes")

    clip_id = _uid("clip")
    await db.execute(
        text(
            """
            INSERT INTO generated_clips (
                id, task_id, filename, file_path, start_time, end_time, duration,
                text, relevance_score, reasoning, clip_order, created_at, updated_at
            ) VALUES (
                :id, :task_id, 'old.mp4', :file_path, '00:00', '00:10', 10.0,
                'Original caption', 0.9, 'seeded', 1, NOW(), NOW()
            )
            """
        ),
        {
            "id": clip_id,
            "task_id": task["id"],
            "file_path": str(old_path),
        },
    )
    await db.commit()
    return task["id"], clip_id, user_id, source["id"]


async def _cleanup_edit_context(db: AsyncSession, *, user_id: str, source_id: str) -> None:
    """Remove the rows seeded for one edit test (task cascade drops clips)."""
    await db.execute(
        text("DELETE FROM tasks WHERE user_id = :uid"), {"uid": user_id}
    )
    await db.execute(
        text("DELETE FROM sources WHERE id = :sid"), {"sid": source_id}
    )
    await db.execute(
        text("DELETE FROM users WHERE id = :uid"), {"uid": user_id}
    )
    await db.commit()


def _make_edit_service(db: AsyncSession, temp_dir: Path) -> TaskService:
    config = Config()
    config.temp_dir = str(temp_dir)
    return TaskService(db=db, config=config)


# ---------------------------------------------------------------------------
# 1. Commit failure after render: row keeps old file; new file is orphaned
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_caption_edit_commit_failure_keeps_old_file_and_orphans_new(
    db_session, tmp_path, monkeypatch
):
    task_id, clip_id, user_id, source_id = await _seed_edit_context(db_session, tmp_path)
    service = _make_edit_service(db_session, tmp_path)

    clips_dir = Path(tmp_path) / "clips"
    new_path = clips_dir / "rendered.mp4"

    def fake_overlay(*args, **kwargs):
        new_path.write_bytes(b"new-video-bytes")
        return new_path

    monkeypatch.setattr(task_service_module, "overlay_custom_captions", fake_overlay)

    async def failing_update(*args, **kwargs):
        raise RuntimeError("simulated commit failure")

    monkeypatch.setattr(ClipRepository, "update_clip", staticmethod(failing_update))

    with pytest.raises(RuntimeError, match="simulated commit failure"):
        await service.update_clip_captions(
            task_id, clip_id, "Edited caption", "middle", ["edited"]
        )

    old_path = clips_dir / "old.mp4"
    # The row still points at the OLD file and the old file still exists.
    row = await ClipRepository.get_clip_by_id(db_session, clip_id)
    assert row is not None
    assert row["file_path"] == str(old_path)
    assert old_path.exists(), "old input file must survive a failed commit"
    assert row["text"] == "Original caption", "row text must not be updated"

    # The freshly rendered file exists on disk but is referenced by NO row.
    assert new_path.exists(), "render output must exist after render"
    refs = await db_session.execute(text("SELECT file_path FROM generated_clips"))
    referenced_paths = {r[0] for r in refs.fetchall()}
    assert str(new_path) not in referenced_paths, (
        "failed-commit render output must not be referenced by any row"
    )

    await _cleanup_edit_context(db_session, user_id=user_id, source_id=source_id)


# ---------------------------------------------------------------------------
# 2. Orphan sweep: TTL behaviour for orphan vs referenced vs fresh files
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_reconcile_orphaned_clip_files_ttl_semantics(db_session, tmp_path):
    clips_dir = Path(tmp_path) / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    old_orphan = clips_dir / "orphan-old.mp4"
    old_orphan.write_bytes(b"o")
    old_sfx = clips_dir / "orphan-old.sfx.json"
    old_sfx.write_bytes(b"{}")
    old_smap = clips_dir / "orphan-old.source_map.json"
    old_smap.write_bytes(b"{}")

    fresh_orphan = clips_dir / "orphan-fresh.mp4"
    fresh_orphan.write_bytes(b"f")

    referenced = clips_dir / "referenced.mp4"
    referenced.write_bytes(b"r")
    referenced_sfx = clips_dir / "referenced.sfx.json"
    referenced_sfx.write_bytes(b"{}")

    past = time.time() - 7200  # older than the 3600s TTL
    os.utime(old_orphan, (past, past))
    os.utime(old_sfx, (past, past))
    os.utime(old_smap, (past, past))

    # Seed a user/task and one clip row referencing `referenced` so the sweep
    # keeps that file (no extra seed-context mp4 in the clips dir).
    user_id = _uid("usr")
    await create_user(db_session, user_id=user_id)
    source = await create_source(
        db_session,
        source_id=_uid("src"),
        title="Sweep source",
        url=f"https://www.youtube.com/watch?v={uuid4().hex[:11]}",
    )
    task = await create_task(
        db_session,
        task_id=_uid("task"),
        user_id=user_id,
        source_id=source["id"],
        status="processing",
    )
    await db_session.execute(
        text(
            """
            INSERT INTO generated_clips (
                id, task_id, filename, file_path, start_time, end_time, duration,
                text, relevance_score, reasoning, clip_order, created_at, updated_at
            ) VALUES (
                :id, :task_id, 'referenced.mp4', :file_path, '00:00', '00:10', 10.0,
                'Kept', 0.9, 'seeded', 1, NOW(), NOW()
            )
            """
        ),
        {
            "id": _uid("clip"),
            "task_id": task["id"],
            "file_path": str(referenced),
        },
    )
    await db_session.commit()

    summary = await reconcile_orphaned_clip_files(
        db_session, temp_dir=tmp_path, ttl_seconds=3600
    )

    # Old orphan (mp4 + both sidecars) removed.
    assert not old_orphan.exists(), "orphan past TTL must be removed"
    assert not old_sfx.exists(), "orphan .sfx.json sidecar must be removed"
    assert not old_smap.exists(), "orphan .source_map.json sidecar must be removed"
    # Fresh orphan below the TTL is kept (mid-render protection).
    assert fresh_orphan.exists(), "orphan below TTL must be kept"
    # Referenced file and its sidecar are kept.
    assert referenced.exists(), "referenced file must be kept"
    assert referenced_sfx.exists(), "referenced sidecar must be kept"
    # Summary reflects one removal among three scanned mp4s. The referenced
    # count covers every generated_clips row in the shared dev DB, so only the
    # lower bound (my seeded row) is pinned.
    assert summary["scanned"] == 3
    assert summary["referenced"] >= 1
    assert summary["removed"] == 1
    assert summary["failed"] == 0

    await _cleanup_edit_context(db_session, user_id=user_id, source_id=source["id"])


# ---------------------------------------------------------------------------
# 3. Two-phase ordering: unlink happens only AFTER the row commit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_caption_edit_unlinks_input_only_after_commit(
    db_session, tmp_path, monkeypatch
):
    task_id, clip_id, user_id, source_id = await _seed_edit_context(db_session, tmp_path)
    service = _make_edit_service(db_session, tmp_path)

    clips_dir = Path(tmp_path) / "clips"
    old_path = clips_dir / "old.mp4"
    new_path = clips_dir / "rendered.mp4"

    events: list[tuple[str, bool]] = []

    def fake_overlay(*args, **kwargs):
        new_path.write_bytes(b"new-video-bytes")
        return new_path

    monkeypatch.setattr(task_service_module, "overlay_custom_captions", fake_overlay)

    real_update = ClipRepository.update_clip

    async def recording_update(*args, **kwargs):
        # Called as staticmethod(db, clip_id, filename, file_path, ...).
        events.append(("update_before", old_path.exists()))
        await real_update(*args, **kwargs)
        # After the real update_clip's internal commit, the unlink must not
        # have run yet: the old input is still on disk.
        events.append(("update_after", old_path.exists()))

    monkeypatch.setattr(ClipRepository, "update_clip", staticmethod(recording_update))

    real_remove = ClipRenderService._remove_superseded_clip

    def recording_remove(old: Path, new: Path):
        events.append(("remove_start", old.exists()))
        real_remove(old, new)
        events.append(("remove_end", old.exists()))

    monkeypatch.setattr(
        ClipRenderService, "_remove_superseded_clip", staticmethod(recording_remove)
    )

    result = await service.update_clip_captions(
        task_id, clip_id, "Edited caption", "middle", ["edited"]
    )

    # Phase 1 finished (row committed) while the old file still existed.
    assert ("update_before", True) in events
    assert ("update_after", True) in events, (
        "old input must still exist immediately after the row commit"
    )
    # Phase 2 (unlink) started only after phase 1's commit.
    update_idx = events.index(("update_after", True))
    remove_idx = events.index(("remove_start", True))
    assert update_idx < remove_idx, "unlink must run strictly after the row commit"

    # End state: row points at the new file; old file gone; new file exists.
    assert result["file_path"] == str(new_path)
    assert not old_path.exists(), "superseded input must be unlinked after commit"
    assert new_path.exists(), "re-rendered file must survive"

    row = await ClipRepository.get_clip_by_id(db_session, clip_id)
    assert row is not None
    assert row["file_path"] == str(new_path)
    assert row["text"] == "Edited caption"

    await _cleanup_edit_context(db_session, user_id=user_id, source_id=source_id)