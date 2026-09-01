"""
PettyFlow Redis Balance Cache Layer
Implements cache-aside balance aggregation, optimistic locking, and sub-100 microsecond
atomic balance mutation via Lua scripting.
"""

from typing import Any, Optional, Tuple

# Atomic Balance Mutation Lua Script
# KEYS[1]: Balance key (pettyflow:<tenant_id>:<account_id>:balance)
# KEYS[2]: Version key (pettyflow:<tenant_id>:<account_id>:version)
# ARGV[1]: Delta scaled amount (integer)
# ARGV[2]: Expected version for optimistic concurrency (-1 to ignore version check)
ATOMIC_BALANCE_MUTATION_LUA = """
local balance_key = KEYS[1]
local version_key = KEYS[2]
local delta = tonumber(ARGV[1])
local expected_version = tonumber(ARGV[2])

local current_version = tonumber(redis.call('GET', version_key) or '0')

if expected_version ~= -1 and current_version ~= expected_version then
    return {-1, current_version} -- Version conflict
end

local current_balance = tonumber(redis.call('GET', balance_key) or '0')
local new_balance = current_balance + delta

if new_balance < 0 then
    return {-2, current_balance} -- Insufficient balance / overdraft violation
end

local new_version = current_version + 1
redis.call('SET', balance_key, tostring(new_balance))
redis.call('SET', version_key, tostring(new_version))

return {new_balance, new_version}
"""

class OptimisticLockException(Exception):
    """Raised when version_id does not match expected_version during atomic mutation."""
    pass

class InsufficientBalanceException(Exception):
    """Raised when balance mutation results in negative funds."""
    pass

class RedisBalanceCache:
    """
    High-performance balance cache aggregator with atomic Lua scripts.
    """
    def __init__(self, redis_client: Any):
        self.client = redis_client
        self._script_sha = None

    @staticmethod
    def _coerce_int(raw_value: Any, field_name: str) -> int:
        if raw_value is None:
            raise ValueError(f"{field_name} cannot be None.")
        if isinstance(raw_value, (bytes, bytearray)):
            raw_value = raw_value.decode("utf-8")
        if isinstance(raw_value, str):
            raw_value = raw_value.strip()
            if not raw_value:
                raise ValueError(f"{field_name} cannot be empty.")
        try:
            return int(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name} is not an integer-typed Redis value: {raw_value!r}") from exc

    def _get_keys(self, tenant_id: str, account_id: str) -> Tuple[str, str]:
        """Return co-located Redis Cluster keys for an account balance."""
        if not tenant_id or not account_id:
            raise ValueError("tenant_id and account_id must be non-empty")

        # Redis Cluster requires every key passed to EVAL to be in one hash slot.
        # The shared hash tag keeps the balance and optimistic-lock version together.
        key_prefix = f"pettyflow:{{{tenant_id}:{account_id}}}"
        balance_key = f"{key_prefix}:balance"
        version_key = f"{key_prefix}:version"
        return balance_key, version_key

    def get_balance(self, tenant_id: str, account_id: str) -> Optional[Tuple[int, int]]:
        """
        Fetch cached balance and version for an account.
        Returns (amount_scaled, version_id) or None if cache miss.
        """
        b_key, v_key = self._get_keys(tenant_id, account_id)
        pipe = self.client.pipeline()
        pipe.get(b_key)
        pipe.get(v_key)
        results = pipe.execute()

        raw_balance, raw_version = results[0], results[1]
        if raw_balance is None:
            return None

        balance = self._coerce_int(raw_balance, "balance")
        version = self._coerce_int(raw_version, "version") if raw_version is not None else 1
        return balance, version

    def set_balance(self, tenant_id: str, account_id: str, amount_scaled: int, version_id: int = 1) -> bool:
        """
        Initialize or populate cache-aside balance with optimistic version.
        """
        if not isinstance(amount_scaled, int) or isinstance(amount_scaled, bool):
            raise TypeError("amount_scaled must be an integer")
        if not isinstance(version_id, int) or isinstance(version_id, bool) or version_id < 0:
            raise ValueError("version_id must be a non-negative integer")

        b_key, v_key = self._get_keys(tenant_id, account_id)
        pipe = self.client.pipeline()
        pipe.set(b_key, str(amount_scaled))
        pipe.set(v_key, str(version_id))
        pipe.execute()
        return True

    def atomic_increment_balance(
        self,
        tenant_id: str,
        account_id: str,
        delta_scaled: int,
        expected_version: int = -1
    ) -> Tuple[int, int]:
        """
        Execute atomic Lua mutation in Redis.
        Returns updated (new_balance, new_version).
        """
        if not isinstance(delta_scaled, int) or isinstance(delta_scaled, bool):
            raise TypeError("delta_scaled must be an integer")
        if not isinstance(expected_version, int) or isinstance(expected_version, bool):
            raise TypeError("expected_version must be an integer")

        b_key, v_key = self._get_keys(tenant_id, account_id)

        # Execute Lua script
        result = self.client.eval(
            ATOMIC_BALANCE_MUTATION_LUA,
            2,
            b_key,
            v_key,
            str(delta_scaled),
            str(expected_version)
        )

        if not isinstance(result, (list, tuple)) or len(result) < 2:
            raise ValueError(f"Unexpected Redis Lua response for account {account_id}: {result!r}")

        status_code = self._coerce_int(result[0], "status_code")
        payload = self._coerce_int(result[1], "payload")

        if status_code == -1:
            raise OptimisticLockException(
                f"Optimistic lock conflict for account {account_id}. "
                f"Expected version {expected_version}, current version is {payload}"
            )
        elif status_code == -2:
            raise InsufficientBalanceException(
                f"Insufficient funds for account {account_id}. "
                f"Current balance is {payload}, attempted delta {delta_scaled}"
            )

        return status_code, payload

    def invalidate_cache(self, tenant_id: str, account_id: str) -> bool:
        """Evict cached balance and version keys."""
        b_key, v_key = self._get_keys(tenant_id, account_id)
        self.client.delete(b_key, v_key)
        return True
