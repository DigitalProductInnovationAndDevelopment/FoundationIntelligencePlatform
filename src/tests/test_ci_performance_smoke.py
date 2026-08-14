import asyncio
from dataclasses import replace
import os
from time import perf_counter
import unittest

from bff.database import DatabaseManager, DatabaseSettings


@unittest.skipUnless(
    os.getenv("RUN_POSTGRES_INTEGRATION") == "1" or os.getenv("TEST_DATABASE_URL"),
    "requires an explicitly enabled PostgreSQL integration environment",
)
class TestCiPerformanceSmoke(unittest.IsolatedAsyncioTestCase):
    async def test_health_pool_is_bounded_concurrent_and_isolated(self):
        manager = DatabaseManager(
            replace(
                DatabaseSettings.from_env(),
                pool_size=1,
                max_overflow=0,
                pool_timeout_seconds=1,
            )
        )
        held = await manager.engine().connect()
        try:
            started = perf_counter()
            results = await asyncio.gather(*(manager.check() for _ in range(5)))
            elapsed_ms = (perf_counter() - started) * 1000
            self.assertEqual(results, [True] * 5)
            self.assertLess(elapsed_ms, 3000)
            self.assertEqual(manager.pool_status()["checked_out"], 1.0)
        finally:
            await held.close()
            await manager.close()


if __name__ == "__main__":
    unittest.main()
