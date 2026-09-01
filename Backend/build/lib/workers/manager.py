import asyncio
import logging
from app.blockchain.provider import BlockchainProvider
from app.queue import task_queue
from app.workers.tasks import TASK_MAP

logger = logging.getLogger(__name__)

class WorkerManager:
    """Background manager that consumes tasks from Redis and executes them.

    One manager instance can run multiple worker loops concurrently.
    """
    def __init__(self, provider: BlockchainProvider, num_workers: int = 4):
        self._provider = provider
        self._num_workers = num_workers
        self._stop_event = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        self._stop_event.clear()
        for i in range(self._num_workers):
            t = asyncio.create_task(self._worker_loop(i), name=f"worker-{i}")
            self._tasks.append(t)
        logger.info("WorkerManager started with %d workers", self._num_workers)

    async def stop(self) -> None:
        self._stop_event.set()
        # Wake up workers from BRPOP if they are blocking
        # Redis BRPOP will return None after timeout, so they will check stop_event.
        # But we can just cancel them for immediate stop.
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        logger.info("WorkerManager stopped")

    async def _worker_loop(self, worker_id: int) -> None:
        logger.debug("Worker-%d loop started", worker_id)
        while not self._stop_event.is_set():
            try:
                # Poll from both 'analysis' and 'snapshots' queues
                # We prioritize analysis for latency reasons.
                result = await task_queue.pop("analysis", timeout=1)
                if not result:
                    result = await task_queue.pop("snapshots", timeout=1)

                if not result:
                    continue

                queue_name, payload = result
                task_type = payload.get("type")
                data = payload.get("data")

                if task_type in TASK_MAP:
                    handler = TASK_MAP[task_type]
                    logger.debug("Worker-%d executing task %s from %s", worker_id, task_type, queue_name)
                    await handler(self._provider, data)
                else:
                    logger.warning("Unknown task type %s received by worker-%d", task_type, worker_id)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Worker-%d encountered error processing task", worker_id)
                # Small backoff on error to avoid tight loop of failures
                await asyncio.sleep(1)
