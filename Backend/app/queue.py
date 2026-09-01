import json
import logging
from typing import Any, Callable, Awaitable
import redis.asyncio as redis
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

class TaskQueue:
    """Simple Redis-backed task queue for asynchronous analysis.

    Uses Redis lists (LPUSH/BRPOP) for basic FIFO queuing.
    """
    def __init__(self):
        self._redis = redis.from_url(settings.redis_url, decode_responses=True)

    async def push(self, queue_name: str, task_type: str, data: Any) -> None:
        """Push a task to the specified queue."""
        try:
            payload = json.dumps({"type": task_type, "data": data})
            await self._redis.lpush(queue_name, payload)
            logger.debug("Pushed task %s to queue %s", task_type, queue_name)
        except Exception:
            logger.exception("Failed to push task %s to Redis queue %s", task_type, queue_name)


    async def pop(self, queue_name: str, timeout: int = 0) -> tuple[str, dict] | None:
        """Pop a task from the specified queue.

        Returns (queue_name, task_payload) or None if timeout reached.
        """
        result = await self._redis.brpop(queue_name, timeout=timeout)
        if result:
            _, payload_str = result
            return queue_name, json.loads(payload_str)
        return None

    async def close(self) -> None:
        await self._redis.close()

def wrap_as_queued_task(task_type: str, queue_name: str = "analysis"):
    """Wraps a task type into a callback that pushes to the queue.

    Returns a callback that takes a 'block' dict and pushes it.
    """
    async def callback(block: dict) -> None:
        await task_queue.push(queue_name, task_type, block)
    return callback

# Singleton instance
task_queue = TaskQueue()
