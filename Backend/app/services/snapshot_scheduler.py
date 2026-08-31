import logging
from datetime import datetime, timedelta, timezone
from typing import List
from app.queue import task_queue

logger = logging.getLogger(__name__)

SNAPSHOT_INTERVALS = {
    "1m": 1,
    "5m": 5,
    "10m": 10,
    "30m": 30,
    "1h": 60,
    "6h": 360,
    "24h": 1440,
}

class SnapshotScheduler:
    """Schedules historical snapshots for a token after it is discovered.

    Since we don't have a full-fledged distributed scheduler (like Celery Beat),
    we push 'scheduled' tasks to Redis with a timestamp.
    The worker will check the timestamp before executing.
    """
    @staticmethod
    async def schedule_snapshots(token_address: str, detected_at: datetime):
        """Schedule a series of snapshots based on the detection timestamp."""
        for interval, minutes in SNAPSHOT_INTERVALS.items():
            scheduled_time = detected_at + timedelta(minutes=minutes)
            # We push to a separate 'snapshots' queue or the general 'analysis' queue
            # with a 'scheduled_at' field.
            await task_queue.push(
                "snapshots",
                "take_snapshot",
                {
                    "token_address": token_address,
                    "interval": interval,
                    "scheduled_at": scheduled_time.isoformat(),
                },
            )
        logger.info("Scheduled %d snapshots for token %s", len(SNAPSHOT_INTERVALS), token_address)
