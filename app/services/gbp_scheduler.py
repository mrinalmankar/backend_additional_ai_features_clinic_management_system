import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.models.gbp import GBPAccount, GBPPost
from app.services.gbp_api import build_post_payload, create_local_post

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
POLL_INTERVAL_SECONDS = 60
CLAIM_BATCH_SIZE = 20

_CLAIM_SQL = text(
    """
    UPDATE gbp_posts
    SET status = 'PUBLISHING'
    WHERE id IN (
        SELECT id FROM gbp_posts
        WHERE status = 'SCHEDULED'
          AND scheduled_at <= :now
          AND retry_count < :max_retries
        ORDER BY scheduled_at
        FOR UPDATE SKIP LOCKED
        LIMIT :batch_size
    )
    RETURNING id
    """
)

async def _claim_due_posts() -> list:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            _CLAIM_SQL,
            {
                "now": datetime.now(timezone.utc),
                "max_retries": MAX_RETRIES,
                "batch_size": CLAIM_BATCH_SIZE,
            },
        )
        ids = [row[0] for row in result.fetchall()]
        await db.commit()
        return ids

_STUCK_PUBLISHING_THRESHOLD_SECONDS = 300

_RECLAIM_SQL = text(
    """
    UPDATE gbp_posts
    SET status = 'PUBLISHING'
    WHERE id IN (
        SELECT id FROM gbp_posts
        WHERE status = 'PUBLISHING'
          AND updated_at <= :cutoff
          AND retry_count < :max_retries
        ORDER BY updated_at
        FOR UPDATE SKIP LOCKED
        LIMIT :batch_size
    )
    RETURNING id
    """
)

async def _reclaim_stuck_publishing_posts() -> list:
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=_STUCK_PUBLISHING_THRESHOLD_SECONDS)
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            _RECLAIM_SQL,
            {
                "cutoff": cutoff,
                "max_retries": MAX_RETRIES,
                "batch_size": CLAIM_BATCH_SIZE,
            },
        )
        ids = [row[0] for row in result.fetchall()]
        await db.commit()
        return ids

async def _publish_due_posts() -> None:
    claimed_ids = await _claim_due_posts()

    claimed_ids += await _reclaim_stuck_publishing_posts()

    for post_id in claimed_ids:
        async with AsyncSessionLocal() as db:
            db_post = await db.get(GBPPost, post_id)
            if db_post is None:
                continue

            account = await db.get(GBPAccount, db_post.account_id)
            if account is None or not account.is_active:
                logger.warning("GBPPost %s: linked account not found or inactive", db_post.id)
                db_post.status = "FAILED"
                db_post.last_error = "Linked GBP account not found or inactive"
                await db.commit()
                continue

            title = db_post.final_title or db_post.ai_title
            body = db_post.final_body or db_post.ai_body
            cta = db_post.call_to_action
            payload = build_post_payload(db_post.post_type, title, body, cta)

            try:
                result_data = await create_local_post(account, payload)
                db_post.status = "PUBLISHED"
                db_post.published_at = datetime.now(timezone.utc)
                db_post.google_post_id = result_data.get("name")
                db_post.last_error = None
                logger.info(
                    "GBPPost %s published successfully. Google post ID: %s",
                    db_post.id,
                    db_post.google_post_id,
                )
            except Exception as exc:
                db_post.retry_count += 1
                db_post.last_error = str(exc)[:1000]
                if db_post.retry_count >= MAX_RETRIES:
                    db_post.status = "FAILED"
                    logger.error(
                        "GBPPost %s failed after %d retries: %s",
                        db_post.id, MAX_RETRIES, exc,
                    )
                else:

                    db_post.status = "SCHEDULED"
                    logger.warning(
                        "GBPPost %s publish attempt %d failed: %s",
                        db_post.id, db_post.retry_count, exc,
                    )

            db.add(account)
            await db.commit()

_scheduler: AsyncIOScheduler | None = None

def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone="UTC")
        _scheduler.add_job(
            _publish_due_posts,
            trigger="interval",
            seconds=POLL_INTERVAL_SECONDS,
            id="gbp_post_publisher",
            name="GBP Scheduled Post Publisher",
            replace_existing=True,
            max_instances=1,
        )
    return _scheduler

def start_scheduler() -> None:
    scheduler = get_scheduler()
    if not scheduler.running:
        scheduler.start()
        logger.info("GBP post scheduler started (interval: %ds)", POLL_INTERVAL_SECONDS)

def stop_scheduler() -> None:
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("GBP post scheduler stopped")
