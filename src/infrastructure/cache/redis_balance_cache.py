"""
PettyFlow Redis Balance Cache Layer
Implements cache-aside balance aggregation, optimistic locking, and sub-100 microsecond
atomic balance mutation via Lua scripting.
"""

from typing import Optional, Tuple, Any

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

    def _get_keys(self, tenant_id: str, account_id: str) -> Tuple[str, str]:
        balance_key = f"pettyflow:{tenant_id}:{account_id}:balance"
        version_key = f"pettyflow:{tenant_id}:{account_id}:version"
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

        balance = int(raw_balance)
        version = int(raw_version) if raw_version is not None else 1
        return balance, version

    def set_balance(self, tenant_id: str, account_id: str, amount_scaled: int, version_id: int = 1) -> bool:
        """
        Initialize or populate cache-aside balance with optimistic version.
        """
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

        status_code = result[0]
        payload = result[1]

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

        return int(status_code), int(payload)

    def invalidate_cache(self, tenant_id: str, account_id: str) -> bool:
        """Evict cached balance and version keys."""
        b_key, v_key = self._get_keys(tenant_id, account_id)
        self.client.delete(b_key, v_key)
        return True
