from __future__ import annotations

from datetime import datetime, timezone
import calendar
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Config, get_config

PAID_PLAN_STATUSES = {"active", "trialing"}
PAID_PLAN_LIMIT_CONFIG = {
    "pro": "pro_plan_task_limit",
    "scale": "scale_plan_task_limit",
}


class BillingLimitExceeded(Exception):
    def __init__(self, summary: dict[str, Any]):
        super().__init__("Billing limit reached")
        self.summary = summary


class BillingService:
    def __init__(self, db: AsyncSession, config: Config | None = None):
        self.db = db
        self.config = config or get_config()

    @staticmethod
    def _month_window(now: datetime) -> tuple[datetime, datetime]:
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        _, day_count = calendar.monthrange(start.year, start.month)
        end = start.replace(
            day=day_count, hour=23, minute=59, second=59, microsecond=999999
        )
        return start, end

    async def _load_user_billing_row(self, user_id: str) -> dict[str, Any]:
        # B3 fallback removed: ValueError (user not found) and DB errors flow to
        # the caller instead of silently degrading to a free-plan row.
        result = await self.db.execute(
            text(
                """
                SELECT
                    plan,
                    subscription_status,
                    subscription_provider,
                    billing_period_start,
                    billing_period_end,
                    trial_ends_at
                FROM users
                WHERE id = :user_id
                """
            ),
            {"user_id": user_id},
        )
        row = result.fetchone()
        if not row:
            raise ValueError("User not found")

        return {
            "plan": (row.plan or "free").lower(),
            "subscription_status": (row.subscription_status or "inactive").lower(),
            "subscription_provider": (
                row.subscription_provider.lower()
                if row.subscription_provider
                else None
            ),
            "billing_period_start": row.billing_period_start,
            "billing_period_end": row.billing_period_end,
            "trial_ends_at": row.trial_ends_at,
        }

    async def _lock_user_billing_row(self, user_id: str) -> dict[str, Any]:
        """Lock the user's billing row inside the caller's open transaction.

        The FOR UPDATE serializes concurrent task creation for the same user:
        the second writer blocks until the first commits, then re-counts in a
        transaction that already sees the first writer's committed task.
        """
        result = await self.db.execute(
            text(
                """
                SELECT
                    plan,
                    subscription_status,
                    subscription_provider,
                    billing_period_start,
                    billing_period_end,
                    trial_ends_at
                FROM users
                WHERE id = :user_id
                FOR UPDATE
                """
            ),
            {"user_id": user_id},
        )
        row = result.fetchone()
        if not row:
            raise ValueError("User not found")

        return {
            "plan": (row.plan or "free").lower(),
            "subscription_status": (row.subscription_status or "inactive").lower(),
            "subscription_provider": (
                row.subscription_provider.lower()
                if row.subscription_provider
                else None
            ),
            "billing_period_start": row.billing_period_start,
            "billing_period_end": row.billing_period_end,
            "trial_ends_at": row.trial_ends_at,
        }

    async def _count_tasks(
        self, user_id: str, period_start: datetime, period_end: datetime
    ) -> int:
        result = await self.db.execute(
            text(
                """
                SELECT COUNT(*) AS total
                FROM tasks
                WHERE user_id = :user_id
                  AND created_at >= :period_start
                  AND created_at <= :period_end
                """
            ),
            {
                "user_id": user_id,
                "period_start": period_start,
                "period_end": period_end,
            },
        )
        row = result.fetchone()
        return int(row.total) if row and row.total is not None else 0

    def _build_usage_summary(
        self,
        row: dict[str, Any],
        period_start: datetime,
        period_end: datetime,
        usage_count: int,
    ) -> dict[str, Any]:
        plan = row["plan"]
        status = row["subscription_status"]
        is_paid = plan in PAID_PLAN_LIMIT_CONFIG and status in PAID_PLAN_STATUSES

        if not is_paid:
            return {
                "monetization_enabled": True,
                "plan": plan,
                "subscription_status": status,
                "subscription_provider": row.get("subscription_provider"),
                "period_start": period_start,
                "period_end": period_end,
                "trial_ends_at": row.get("trial_ends_at"),
                "usage_count": usage_count,
                "usage_limit": 0,
                "remaining": 0,
                "can_create_task": False,
                "upgrade_required": True,
                "reason": "Choose a paid plan to process videos.",
            }

        usage_limit = int(
            getattr(self.config, PAID_PLAN_LIMIT_CONFIG[plan])
        )
        unlimited = usage_limit <= 0
        can_create = unlimited or usage_count < usage_limit
        remaining = None if unlimited else max(usage_limit - usage_count, 0)
        reason = None if can_create else "Plan usage limit reached"

        return {
            "monetization_enabled": True,
            "plan": plan,
            "subscription_status": status,
            "subscription_provider": row.get("subscription_provider"),
            "period_start": period_start,
            "period_end": period_end,
            "trial_ends_at": row.get("trial_ends_at"),
            "usage_count": usage_count,
            "usage_limit": None if unlimited else usage_limit,
            "remaining": remaining,
            "can_create_task": can_create,
            "upgrade_required": not can_create,
            "reason": reason,
        }

    async def get_usage_summary(self, user_id: str) -> dict[str, Any]:
        if not self.config.monetization_enabled:
            return {
                "monetization_enabled": False,
                "plan": "self_host",
                "subscription_status": "inactive",
                "subscription_provider": None,
                "period_start": None,
                "period_end": None,
                "usage_count": 0,
                "usage_limit": None,
                "remaining": None,
                "can_create_task": True,
                "upgrade_required": False,
                "reason": None,
            }

        row = await self._load_user_billing_row(user_id)
        now = datetime.now(timezone.utc)

        start = row.get("billing_period_start")
        end = row.get("billing_period_end")
        if not start or not end:
            start, end = self._month_window(now)

        usage_count = await self._count_tasks(user_id, start, end)
        return self._build_usage_summary(row, start, end, usage_count)

    async def assert_can_create_task(self, user_id: str) -> None:
        summary = await self.get_usage_summary(user_id)
        if summary.get("can_create_task"):
            return
        raise BillingLimitExceeded(summary)

    async def assert_can_create_task_locked(self, user_id: str) -> None:
        """Reserve one quota slot inside the caller's open transaction.

        Caller contract: this method assumes a transaction is already open on
        the session (task creation wraps billing + source + task in one atomic
        commit) and never commits. It locks the user's billing row
        (``SELECT ... FOR UPDATE``), re-counts usage, and raises
        :class:`BillingLimitExceeded` when the plan is full, leaving the
        decision to the caller's commit/rollback.

        Counting semantics (pinned in D3): every non-deleted task created in
        the billing window counts toward the limit, regardless of status
        (including failed/cancelled/errored tasks). Hard-deleted rows no
        longer exist to count. This prevents quota farming through repeated
        submissions that fail and are retried within the same window.
        """
        if not self.config.monetization_enabled:
            return

        row = await self._lock_user_billing_row(user_id)
        now = datetime.now(timezone.utc)

        start = row.get("billing_period_start")
        end = row.get("billing_period_end")
        if not start or not end:
            start, end = self._month_window(now)

        usage_count = await self._count_tasks(user_id, start, end)
        summary = self._build_usage_summary(row, start, end, usage_count)
        if summary.get("can_create_task"):
            return
        raise BillingLimitExceeded(summary)
