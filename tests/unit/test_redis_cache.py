"""
Unit Test Suite for PettyFlow Redis Balance Cache
Validates Lua atomic mutation, optimistic locking, balance fetch, and cache eviction.
Uses unittest.mock to simulate Redis client without requiring a live Redis instance.
"""

import unittest
from unittest.mock import MagicMock, patch, call

from src.infrastructure.cache.redis_balance_cache import (
    RedisBalanceCache,
    OptimisticLockException,
    InsufficientBalanceException,
    ATOMIC_BALANCE_MUTATION_LUA,
)


class MockRedisPipeline:
    def __init__(self, return_values):
        self._cmds = []
        self._return_values = return_values

    def get(self, key):
        self._cmds.append(('get', key))
        return self

    def set(self, key, value):
        self._cmds.append(('set', key, value))
        return self

    def execute(self):
        return self._return_values


class TestRedisBalanceCacheGetBalance(unittest.TestCase):

    def _make_cache(self, pipe_returns):
        client = MagicMock()
        client.pipeline.return_value = MockRedisPipeline(pipe_returns)
        return RedisBalanceCache(redis_client=client)

    def test_get_balance_cache_hit(self):
        """Returns (amount_scaled, version_id) for cached account."""
        cache = self._make_cache([b"500000", b"3"])
        result = cache.get_balance("tenant-1", "acc-1")
        self.assertEqual(result, (500000, 3))

    def test_get_balance_cache_miss(self):
        """Returns None when balance key is absent."""
        cache = self._make_cache([None, None])
        result = cache.get_balance("tenant-1", "acc-missing")
        self.assertIsNone(result)

    def test_get_balance_version_defaults_to_1(self):
        """When version key missing, defaults to 1."""
        cache = self._make_cache([b"100000", None])
        result = cache.get_balance("tenant-1", "acc-1")
        self.assertEqual(result, (100000, 1))


class TestRedisBalanceCacheSetBalance(unittest.TestCase):

    def test_set_balance_success(self):
        """Validates pipeline SET calls for balance and version keys."""
        client = MagicMock()
        pipe_mock = MagicMock()
        client.pipeline.return_value = pipe_mock

        cache = RedisBalanceCache(redis_client=client)
        result = cache.set_balance("tenant-1", "acc-1", 200000, version_id=5)

        pipe_mock.set.assert_any_call(
            "pettyflow:tenant-1:acc-1:balance", "200000"
        )
        pipe_mock.set.assert_any_call(
            "pettyflow:tenant-1:acc-1:version", "5"
        )
        pipe_mock.execute.assert_called_once()
        self.assertTrue(result)


class TestRedisBalanceCacheAtomicIncrement(unittest.TestCase):

    def _make_cache_with_eval(self, eval_return):
        client = MagicMock()
        client.eval.return_value = eval_return
        return RedisBalanceCache(redis_client=client)

    def test_atomic_increment_success(self):
        """Happy path: Lua returns (new_balance, new_version)."""
        cache = self._make_cache_with_eval([700000, 4])
        new_bal, new_ver = cache.atomic_increment_balance(
            "tenant-1", "acc-1", delta_scaled=200000, expected_version=3
        )
        self.assertEqual(new_bal, 700000)
        self.assertEqual(new_ver, 4)

    def test_atomic_increment_optimistic_lock_conflict(self):
        """Lua returns -1 code for version mismatch -> OptimisticLockException."""
        cache = self._make_cache_with_eval([-1, 5])
        with self.assertRaises(OptimisticLockException):
            cache.atomic_increment_balance(
                "tenant-1", "acc-1", delta_scaled=100000, expected_version=3
            )

    def test_atomic_increment_insufficient_balance(self):
        """Lua returns -2 code for overdraft -> InsufficientBalanceException."""
        cache = self._make_cache_with_eval([-2, 50000])
        with self.assertRaises(InsufficientBalanceException):
            cache.atomic_increment_balance(
                "tenant-1", "acc-1", delta_scaled=-100000, expected_version=-1
            )

    def test_atomic_increment_skips_version_check(self):
        """When expected_version=-1, Lua skips version check."""
        cache = self._make_cache_with_eval([300000, 2])
        new_bal, new_ver = cache.atomic_increment_balance(
            "tenant-1", "acc-1", delta_scaled=100000, expected_version=-1
        )
        self.assertEqual(new_bal, 300000)
        self.assertEqual(new_ver, 2)
        # Verify script was called with expected_version=-1 as str
        call_args = cache.client.eval.call_args
        self.assertEqual(call_args[0][5], "-1")


class TestRedisBalanceCacheInvalidate(unittest.TestCase):

    def test_invalidate_cache(self):
        """Calls DELETE on both balance and version keys."""
        client = MagicMock()
        cache = RedisBalanceCache(redis_client=client)
        result = cache.invalidate_cache("tenant-1", "acc-1")
        client.delete.assert_called_once_with(
            "pettyflow:tenant-1:acc-1:balance",
            "pettyflow:tenant-1:acc-1:version"
        )
        self.assertTrue(result)


class TestRedisBalanceCacheKeyNamespace(unittest.TestCase):

    def test_key_namespace_format(self):
        """Validates cache key namespace format."""
        client = MagicMock()
        cache = RedisBalanceCache(redis_client=client)
        b_key, v_key = cache._get_keys("acme-corp", "cash-001")
        self.assertEqual(b_key, "pettyflow:acme-corp:cash-001:balance")
        self.assertEqual(v_key, "pettyflow:acme-corp:cash-001:version")


class TestLuaScriptContract(unittest.TestCase):

    def test_lua_script_keys_and_argv_contract(self):
        """Validates Lua script has correct KEYS[]/ARGV[] access patterns."""
        self.assertIn("KEYS[1]", ATOMIC_BALANCE_MUTATION_LUA)
        self.assertIn("KEYS[2]", ATOMIC_BALANCE_MUTATION_LUA)
        self.assertIn("ARGV[1]", ATOMIC_BALANCE_MUTATION_LUA)
        self.assertIn("ARGV[2]", ATOMIC_BALANCE_MUTATION_LUA)
        self.assertIn("expected_version ~= -1", ATOMIC_BALANCE_MUTATION_LUA)
        self.assertIn("new_balance < 0", ATOMIC_BALANCE_MUTATION_LUA)


if __name__ == "__main__":
    unittest.main()
