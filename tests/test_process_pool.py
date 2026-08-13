import asyncio
import unittest
from concurrent.futures.process import BrokenProcessPool
from unittest.mock import AsyncMock, MagicMock, patch

from modules.parser.v1.exceptions import ProcessPoolUnavailable
from modules.parser.v1.process_pool import ProcessPoolHolder
from modules.parser.v1.utils import run_in_process


def _fake_pool_factory():
    """Фабрика пулов-заглушек: настоящие процессы в тестах не нужны."""
    return MagicMock(name="ProcessPoolExecutor")


class FakeLoop:
    """Минимальный event loop с подменённым run_in_executor."""

    def __init__(self, side_effect):
        self.run_in_executor = AsyncMock(side_effect=side_effect)


def _payload():
    return "результат"


class ProcessPoolHolderTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        pool_class = patch(
            "modules.parser.v1.process_pool.ProcessPoolExecutor",
            side_effect=lambda max_workers: _fake_pool_factory(),
        )
        pool_class.start()
        self.addCleanup(pool_class.stop)

    async def test_rebuild_replaces_executor_and_bumps_generation(self):
        holder = ProcessPoolHolder(max_workers=2)
        old = await holder.get()

        new = await holder.rebuild(old)

        self.assertIsNot(new, old)
        self.assertIs(new, await holder.get())
        self.assertEqual(holder.generation, 1)
        old.shutdown.assert_called_once_with(wait=False, cancel_futures=True)

    async def test_rebuild_is_idempotent_for_stale_executor(self):
        holder = ProcessPoolHolder(max_workers=2)
        old = await holder.get()

        first, second = await asyncio.gather(
            holder.rebuild(old),
            holder.rebuild(old),
        )

        self.assertIs(first, second)
        self.assertEqual(holder.generation, 1)

    async def test_get_after_shutdown_raises_process_pool_unavailable(self):
        holder = ProcessPoolHolder(max_workers=2)
        await holder.shutdown()
        await holder.shutdown()  # идемпотентность

        with self.assertRaises(ProcessPoolUnavailable):
            await holder.get()


class RunInProcessTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        pool_class = patch(
            "modules.parser.v1.process_pool.ProcessPoolExecutor",
            side_effect=lambda max_workers: _fake_pool_factory(),
        )
        pool_class.start()
        self.addCleanup(pool_class.stop)

    @staticmethod
    def _patch_loop(side_effect):
        loop = FakeLoop(side_effect)
        return loop, patch(
            "modules.parser.v1.utils.asyncio.get_running_loop",
            return_value=loop,
        )

    async def test_run_in_process_retries_once_after_broken_pool(self):
        holder = ProcessPoolHolder(max_workers=2)
        real_rebuild = holder.rebuild
        holder.rebuild = AsyncMock(side_effect=real_rebuild)

        loop, loop_patch = self._patch_loop(
            [BrokenProcessPool("worker died"), "результат"]
        )
        with loop_patch:
            result = await run_in_process(_payload, holder)

        self.assertEqual(result, "результат")
        self.assertEqual(loop.run_in_executor.await_count, 2)
        holder.rebuild.assert_awaited_once()

    async def test_run_in_process_raises_when_pool_stays_broken(self):
        holder = ProcessPoolHolder(max_workers=2)
        real_rebuild = holder.rebuild
        holder.rebuild = AsyncMock(side_effect=real_rebuild)

        loop, loop_patch = self._patch_loop(BrokenProcessPool("worker died"))
        with loop_patch:
            with self.assertRaises(ProcessPoolUnavailable):
                await run_in_process(_payload, holder)

        self.assertEqual(loop.run_in_executor.await_count, 2)
        holder.rebuild.assert_awaited_once()

    async def test_run_in_process_releases_semaphore_after_failure(self):
        holder = ProcessPoolHolder(max_workers=2)
        semaphore = asyncio.Semaphore(2)

        loop, loop_patch = self._patch_loop(BrokenProcessPool("worker died"))
        with loop_patch:
            with self.assertRaises(ProcessPoolUnavailable):
                await run_in_process(_payload, holder, semaphore=semaphore)

        self.assertEqual(semaphore._value, 2)

    async def test_run_in_process_accepts_plain_executor(self):
        executor = MagicMock(name="PlainExecutor")

        loop, loop_patch = self._patch_loop(BrokenProcessPool("worker died"))
        with loop_patch:
            with self.assertRaises(ProcessPoolUnavailable):
                await run_in_process(_payload, executor)

        # Голый executor пересобрать нельзя — попытка ровно одна.
        self.assertEqual(loop.run_in_executor.await_count, 1)
        executor.shutdown.assert_not_called()


if __name__ == "__main__":
    unittest.main()
