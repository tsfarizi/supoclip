"""T5 (P4): billing reservation falsification.

Contracts under test (src.services.billing_service + TaskService):
  1. With monetization enabled and a plan limit of 1, two PARALLEL
     ``create_task_with_source`` calls for the same user resolve to exactly one
     success and one ``BillingLimitExceeded``. The FOR UPDATE row lock
     serializes the creators; the re-count after the first commit sees the
     consumed slot.
  2. Counting semantics: a FAILED task (status 'error') still counts toward the
     monthly window quota, so a user at the limit cannot farm quota by
     submitting failing tasks.

Isolation: every test seeds its own uuid user and cleans up in teardown.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import Config
from src.services.billing_service import BillingLimitExceeded, BillingService
from src.services.task_service import TaskService
from src.repositories.task_repository import TaskRepository
from tests.fixtures.factories import create_user


def _uid(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"[:36]


def _unique_url() -> str:
    # get_youtube_video_id validates the ID length is exactly 11 chars.
    return f"https://www.youtube.com/watch?v={uuid4().hex[:11]}"


def _paid_config(limit: int) -> Config:
    config = Config()
    # monetization_enabled is computed at __init__ as `not self_host`; flip it
    # explicitly like the existing billing unit tests do.
    config.self_host = False
    config.monetization_enabled = True
    config.pro_plan_task_limit = limit
    return config


async def _cleanup_user(db, *, user_id: str, source_ids: tuple[str, ...] = ()) -> None:
    await db.execute(text("DELETE FROM tasks WHERE user_id = :uid"), {"uid": user_id})
    for source_id in source_ids:
        await db.execute(
            text("DELETE FROM sources WHERE id = :sid"), {"sid": source_id}
        )
    await db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
    await db.commit()


# ---------------------------------------------------------------------------
# 1. Parallel creation with limit 1 -> exactly one winner, one limit breach
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_parallel_billing_reservation_allows_exactly_one_when_limit_reached(
    initialized_database,
):
    session_maker = async_sessionmaker(
        initialized_database, class_=AsyncSession, expire_on_commit=False
    )
    user_id = _uid("usr")
    source_ids: list[str] = []
    try:
        async with session_maker() as seed_session:
            await create_user(
                seed_session,
                user_id=user_id,
                plan="pro",
                subscription_status="active",
            )

        config = _paid_config(limit=1)
        # Distinct URLs: the partial unique index must NOT be the differentiator;
        # only the billing row lock + re-count may reject the second creator.
        url_a = _unique_url()
        url_b = _unique_url()

        async def _attempt(session: AsyncSession, url: str):
            service = TaskService(session, config=config)
            return await service.create_task_with_source(
                user_id=user_id, url=url, title="Billing race"
            )

        session_a = session_maker()
        session_b = session_maker()
        try:
            results = await asyncio.gather(
                _attempt(session_a, url_a),
                _attempt(session_b, url_b),
                return_exceptions=True,
            )
        finally:
            await session_a.close()
            await session_b.close()

        successes = [r for r in results if isinstance(r, str)]
        exceeded = [r for r in results if isinstance(r, BillingLimitExceeded)]
        others = [
            r for r in results
            if not isinstance(r, str) and not isinstance(r, BillingLimitExceeded)
        ]
        assert not others, f"unexpected outcome from billing race: {others}"
        assert len(successes) == 1, f"expected exactly one winner, got {successes}"
        assert len(exceeded) == 1, f"expected exactly one BillingLimitExceeded"

        async with session_maker() as check_session:
            winner_task = await TaskService(check_session).task_repo.get_task_by_id(
                check_session, successes[0]
            )
            assert winner_task is not None
            source_ids.append(winner_task["source_id"])
            # Only one task may exist for the user.
            count = (
                await check_session.execute(
                    text("SELECT count(*) FROM tasks WHERE user_id = :uid"),
                    {"uid": user_id},
                )
            ).scalar()
            assert count == 1
    finally:
        async with session_maker() as cleanup_session:
            await _cleanup_user(
                cleanup_session, user_id=user_id, source_ids=tuple(source_ids)
            )


# ---------------------------------------------------------------------------
# 2. Failed tasks count toward the monthly window quota
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_failed_task_counts_toward_monthly_window_limit(initialized_database):
    session_maker = async_sessionmaker(
        initialized_database, class_=AsyncSession, expire_on_commit=False
    )
    user_id = _uid("usr")
    source_id = None
    try:
        async with session_maker() as seed_session:
            await create_user(
                seed_session,
                user_id=user_id,
                plan="pro",
                subscription_status="active",
            )

        config = _paid_config(limit=1)
        async with session_maker() as setup_session:
            # First submission is created and then fails (status 'error').
            task_id = await TaskService(setup_session, config=config).create_task_with_source(
                user_id=user_id,
                url=_unique_url(),
                title="Will fail",
            )
            transitioned = await TaskRepository.update_task_status(
                setup_session,
                task_id,
                "error",
                expected_statuses=["queued"],
                progress=0,
                progress_message="simulated failure",
            )
            assert transitioned is True
            task = await TaskService(setup_session).task_repo.get_task_by_id(
                setup_session, task_id
            )
            source_id = task["source_id"]

        # The failed task is inside the current billing window and counts.
        async with session_maker() as check_session:
            summary = await BillingService(check_session, config).get_usage_summary(
                user_id
            )
            assert summary["usage_count"] == 1
            assert summary["can_create_task"] is False

        # A second creation attempt must be rejected by the quota.
        async with session_maker() as attempt_session:
            with pytest.raises(BillingLimitExceeded):
                await TaskService(
                    attempt_session, config=config
                ).create_task_with_source(
                    user_id=user_id,
                    url=_unique_url(),
                    title="Over quota",
                )

        # The quota did not grow: still exactly one task for the user.
        async with session_maker() as final_session:
            count = (
                await final_session.execute(
                    text("SELECT count(*) FROM tasks WHERE user_id = :uid"),
                    {"uid": user_id},
                )
            ).scalar()
            assert count == 1
    finally:
        async with session_maker() as cleanup_session:
            await _cleanup_user(
                cleanup_session,
                user_id=user_id,
                source_ids=(source_id,) if source_id else (),
            )