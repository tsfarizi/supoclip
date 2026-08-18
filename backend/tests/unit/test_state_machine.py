"""
Falsification tests for T1: task status state machine (contract P2).

Pinned contracts (from the test plan):
1. `TaskRepository.update_task_status(db, task_id, status, *, expected_statuses, ...)`
   is a CAS write guarded by `WHERE id = :id AND status IN (:expected...)`.
   A False result means the guard did not match (concurrent change or wrong
   expected list); the caller must never overwrite a terminal status.
2. Transition matrix (call-site guards): queued->processing|error|cancelled|queued;
   processing->completed|error|cancelled; error->queued|processing; cancelled->queued;
   completed is terminal.
3. The DB CHECK constraint `chk_tasks_status` rejects any status outside
   (pending, queued, processing, completed, error, cancelled, deleted).
4. Every production caller of `update_task_status` passes `expected_statuses`
   (keyword-only in the signature, so omitting it is a TypeError at runtime).

Every DB test follows the suite convention: `asyncio(loop_scope="session")`
(session-scoped `db_session` fixture runs in the session loop), seeds unique
user/source/task rows, and deletes them at the end.
"""

import ast
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from src.repositories.task_repository import TaskRepository
from src.services.task_service import TaskService
from tests.fixtures.factories import create_source, create_user

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = Path(__file__).resolve().parents[2]

ALLOWED_STATUSES = {
    "pending",
    "queued",
    "processing",
    "completed",
    "error",
    "cancelled",
    "deleted",
}


def _uid(prefix: str) -> str:
    # id columns are VARCHAR(36); keep generated ids within that bound.
    return f"{prefix}-{uuid4().hex}"[:36]


async def _seed_user(db) -> str:
    user_id = _uid("usr")
    await create_user(db, user_id=user_id, email=f"{user_id}@example.com")
    return user_id


async def _seed_source(db) -> str:
    source = await create_source(db, source_id=_uid("src"), title="State machine source")
    return source["id"]


async def _seed_task(db, user_id: str, source_id: str, status: str) -> str:
    return await TaskRepository.create_task(
        db, user_id=user_id, source_id=source_id, status=status
    )


async def _cleanup(db, *, user_ids=(), source_ids=()):
    for source_id in source_ids:
        await db.execute(
            text("DELETE FROM sources WHERE id = :sid"), {"sid": source_id}
        )
    for user_id in user_ids:
        await db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
    await db.commit()


# ---------------------------------------------------------------------------
# 1. CAS terminal protection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_completed_is_terminal_and_cas_rejects_second_completion(db_session):
    """P2 clause 1: after completed is reached, a second update_task_status
    call guarded by expected=['processing'] must return False and leave the
    row in completed (no overwrite)."""
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session)
    task_id = await _seed_task(db_session, user_id, source_id, status="processing")
    try:
        assert (
            await TaskRepository.update_task_status(
                db_session,
                task_id,
                "completed",
                expected_statuses=["processing"],
                progress=100,
                progress_message="Complete!",
            )
            is True
        )

        second = await TaskRepository.update_task_status(
            db_session, task_id, "completed", expected_statuses=["processing"]
        )
        assert second is False
        task = await TaskRepository.get_task_by_id(db_session, task_id)
        assert task["status"] == "completed"
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


@pytest.mark.asyncio(loop_scope="session")
async def test_cas_rejects_cancel_after_completed(db_session):
    """P2 clause 1: once completed, a cancel attempt guarded by
    expected=['processing'] must return False and the row stays completed."""
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session)
    task_id = await _seed_task(db_session, user_id, source_id, status="processing")
    try:
        assert await TaskRepository.update_task_status(
            db_session,
            task_id,
            "completed",
            expected_statuses=["processing"],
            progress=100,
            progress_message="Complete!",
        )

        cancelled = await TaskRepository.update_task_status(
            db_session,
            task_id,
            "cancelled",
            expected_statuses=["processing"],
            progress=0,
            progress_message="Cancelled by user",
        )
        assert cancelled is False
        task = await TaskRepository.get_task_by_id(db_session, task_id)
        assert task["status"] == "completed"
        assert task["progress_message"] == "Complete!"
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


@pytest.mark.asyncio(loop_scope="session")
async def test_cas_rejects_write_when_current_status_not_in_expected(db_session):
    """P2 clause 1: a guard that does not contain the current status must not
    write. A queued row cannot be completed with expected=['processing']."""
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session)
    task_id = await _seed_task(db_session, user_id, source_id, status="queued")
    try:
        updated = await TaskRepository.update_task_status(
            db_session, task_id, "completed", expected_statuses=["processing"]
        )
        assert updated is False
        task = await TaskRepository.get_task_by_id(db_session, task_id)
        assert task["status"] == "queued"
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


@pytest.mark.asyncio(loop_scope="session")
async def test_update_task_status_rejects_empty_expected_statuses(db_session):
    """P2 contract: expected_statuses is mandatory; an empty list is a
    programming error and must raise before any DB write."""
    with pytest.raises(ValueError, match="expected_statuses must be non-empty"):
        await TaskRepository.update_task_status(
            db_session, _uid("task"), "processing", expected_statuses=[]
        )


# ---------------------------------------------------------------------------
# 2. Legal transition matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "start,new,expected,label",
    [
        ("queued", "processing", ["queued", "error"], "worker-start"),
        ("error", "processing", ["queued", "error"], "retry-after-error"),
        ("queued", "error", ["queued"], "stale-queue-sweep"),
        ("processing", "completed", ["processing"], "completion"),
        ("processing", "cancelled", ["queued", "processing"], "user-cancel"),
        ("cancelled", "queued", ["cancelled", "error", "queued"], "resume"),
        ("queued", "queued", ["cancelled", "error", "queued"], "resume-idempotent"),
    ],
)
@pytest.mark.asyncio(loop_scope="session")
async def test_legal_transition_applies(
    db_session, start: str, new: str, expected: list[str], label: str
):
    """P2 clause 2: each legal transition (with its call-site guard) wins the
    CAS and persists the new status."""
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session)
    task_id = await _seed_task(db_session, user_id, source_id, status=start)
    try:
        updated = await TaskRepository.update_task_status(
            db_session, task_id, new, expected_statuses=expected
        )
        assert updated is True, f"legal transition {label} ({start}->{new}) rejected"
        task = await TaskRepository.get_task_by_id(db_session, task_id)
        assert task["status"] == new
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


# ---------------------------------------------------------------------------
# 3. CHECK constraint at the storage layer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_check_constraint_chk_tasks_status_exists(db_session):
    """P2 clause 3 precondition: the migration applied the constraint, so the
    illegal-status tests below are meaningful."""
    row = (
        await db_session.execute(
            text(
                """
                SELECT conname FROM pg_constraint
                WHERE conname = 'chk_tasks_status'
                  AND conrelid = 'tasks'::regclass
                """
            )
        )
    ).fetchone()
    assert row is not None, "chk_tasks_status CHECK constraint is not applied"


@pytest.mark.asyncio(loop_scope="session")
async def test_check_constraint_rejects_invalid_status_on_insert(db_session):
    """P2 clause 3: INSERT with an out-of-set status must raise IntegrityError
    (a writer that bypasses the CAS cannot persist an unknown status)."""
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session)
    task_id = _uid("task")
    try:
        with pytest.raises(IntegrityError):
            await db_session.execute(
                text(
                    """
                    INSERT INTO tasks (id, user_id, source_id, status, created_at, updated_at)
                    VALUES (:id, :uid, :sid, 'bogus', NOW(), NOW())
                    """
                ),
                {"id": task_id, "uid": user_id, "sid": source_id},
            )
        # The failed statement aborted the transaction; reset before cleanup.
        await db_session.rollback()
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


@pytest.mark.asyncio(loop_scope="session")
async def test_check_constraint_rejects_invalid_status_on_update(db_session):
    """P2 clause 3: UPDATE that sets an out-of-set status must raise
    IntegrityError even when the row was validly created through the CAS path."""
    user_id = await _seed_user(db_session)
    source_id = await _seed_source(db_session)
    task_id = await _seed_task(db_session, user_id, source_id, status="processing")
    try:
        with pytest.raises(IntegrityError):
            await db_session.execute(
                text("UPDATE tasks SET status = 'bogus' WHERE id = :id"),
                {"id": task_id},
            )
        await db_session.rollback()
        task = await TaskRepository.get_task_by_id(db_session, task_id)
        assert task["status"] == "processing"
    finally:
        await _cleanup(db_session, user_ids=[user_id], source_ids=[source_id])


# ---------------------------------------------------------------------------
# 4. Adversarial enumeration: every production caller passes expected_statuses
# ---------------------------------------------------------------------------

_PROD_MODULES_WITH_CALLS = [
    "services/task_service.py",
    "workers/tasks.py",
    "api/routes/tasks.py",
]


def test_all_update_task_status_callers_pass_nonempty_expected_statuses():
    """P2 clause 4 (structural): parse every production module that calls
    update_task_status and assert each call supplies a non-empty literal
    expected_statuses keyword. The parameter is keyword-only in the signature,
    so a call that omits it is a TypeError at runtime; this AST check fails
    loudly at test time instead."""
    violations: list[str] = []
    for rel in _PROD_MODULES_WITH_CALLS:
        path = BACKEND_ROOT / "src" / rel
        assert path.is_file(), f"expected production module {path}"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (
                isinstance(func, ast.Attribute)
                and func.attr == "update_task_status"
            ):
                continue
            kw_by_name = {kw.arg: kw for kw in node.keywords if kw.arg is not None}
            if "expected_statuses" not in kw_by_name:
                violations.append(
                    f"{rel}:{node.lineno}: call without expected_statuses keyword"
                )
                continue
            value = kw_by_name["expected_statuses"].value
            if not isinstance(value, ast.List) or not value.elts:
                violations.append(
                    f"{rel}:{node.lineno}: expected_statuses is not a non-empty list"
                )
                continue
            for elt in value.elts:
                if not (isinstance(elt, ast.Constant) and elt.value in ALLOWED_STATUSES):
                    violations.append(
                        f"{rel}:{node.lineno}: expected_statuses contains "
                        f"non-status value {ast.unparse(elt) if hasattr(ast, 'unparse') else elt}"
                    )
    assert not violations, "update_task_status call-site violations:\n" + "\n".join(
        violations
    )


def _clip_info(idx: int) -> dict:
    return {
        "filename": f"clip-{idx}.mp4",
        "path": f"/tmp/clip-{idx}.mp4",
        "start_time": f"00:0{idx}",
        "end_time": "00:10",
        "duration": 10.0,
        "text": f"Clip {idx}",
        "relevance_score": 0.9,
        "reasoning": "reason",
        "virality_score": 1,
        "hook_score": 1,
        "engagement_score": 1,
        "value_score": 1,
        "shareability_score": 1,
        "hook_type": "hook",
        "hook_title": None,
    }


def _result_dict() -> dict:
    return {
        "clips": [_clip_info(1), _clip_info(2)],
        "segments_to_render": [
            {"start_time": "00:00", "end_time": "00:10"},
            {"start_time": "00:10", "end_time": "00:20"},
        ],
        "video_path": "/tmp/source.mp4",
        "segments": [],
        "summary": None,
        "key_topics": [],
        "transcript": "Transcript",
        "analysis_json": "{}",
    }


def _build_mocked_service():
    service = TaskService(db=AsyncMock())
    service.cache_repo.get_cache = AsyncMock(return_value=None)
    service.cache_repo.upsert_cache = AsyncMock()
    service.task_repo.update_task_runtime_metadata = AsyncMock()
    service.task_repo.get_task_notification_context = AsyncMock(return_value=None)
    service.clip_repo.create_clip = AsyncMock(side_effect=["clip-1", "clip-2"])
    service.video_service.create_single_clip = AsyncMock(
        side_effect=[_clip_info(1), _clip_info(2)]
    )
    service.video_service.process_video_complete = AsyncMock(return_value=_result_dict())
    return service


@pytest.mark.asyncio
async def test_process_task_cas_guards_pin_transition_matrix():
    """P2 clause 4 (behavioral): a successful 2-clip process_task run makes
    exactly four DB status writes - start, two per-clip checkpoints, completion
    - each guarded by the pinned expected_statuses from the matrix."""
    service = _build_mocked_service()
    status_calls = []

    async def record_status(*args, **kwargs):
        status = kwargs.get("status", args[2] if len(args) > 2 else None)
        status_calls.append(
            {"status": status, "expected": list(kwargs["expected_statuses"])}
        )
        return True

    service.task_repo.update_task_status = AsyncMock(side_effect=record_status)

    result = await service.process_task(
        task_id="task-1",
        url="https://www.youtube.com/watch?v=demo",
        source_type="youtube",
    )

    assert result["clips_count"] == 2
    assert status_calls == [
        {"status": "processing", "expected": ["queued", "error"]},
        {"status": "processing", "expected": ["processing"]},
        {"status": "processing", "expected": ["processing"]},
        {"status": "completed", "expected": ["processing"]},
    ]