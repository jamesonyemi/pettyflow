"""Thread-safe fixed-point petty-cash fund operations."""

from dataclasses import dataclass
from threading import RLock
from typing import Dict, Tuple
from uuid import UUID, uuid4


class FundNotFoundError(Exception):
    """Raised when a requested fund is outside the tenant's scope."""


class InsufficientFloatError(Exception):
    """Raised when an allocation or disbursement exceeds available funds."""


@dataclass(frozen=True)
class Fund:
    fund_id: UUID
    tenant_id: UUID
    name: str
    currency: str
    custodian_id: UUID
    available_amount_scaled: int


@dataclass(frozen=True)
class CustodianBalance:
    fund_id: UUID
    tenant_id: UUID
    custodian_id: UUID
    currency: str
    amount_scaled: int


class FundService:
    """Maintains tenant-scoped funds using integer fixed-point amounts only."""

    def __init__(self) -> None:
        self._funds: Dict[Tuple[UUID, UUID], Fund] = {}
        self._balances: Dict[Tuple[UUID, UUID, UUID], int] = {}
        self._lock = RLock()

    def create_fund(
        self,
        tenant_id: UUID,
        name: str,
        currency: str,
        custodian_id: UUID,
        initial_amount_scaled: int,
    ) -> Fund:
        self._validate_amount(initial_amount_scaled, "initial_amount_scaled", allow_zero=True)
        normalized_currency = self._normalize_currency(currency)
        if not name.strip():
            raise ValueError("name must not be blank")

        fund = Fund(
            fund_id=uuid4(),
            tenant_id=tenant_id,
            name=name.strip(),
            currency=normalized_currency,
            custodian_id=custodian_id,
            available_amount_scaled=initial_amount_scaled,
        )
        with self._lock:
            self._funds[(tenant_id, fund.fund_id)] = fund
        return fund

    def allocate_float(
        self, tenant_id: UUID, fund_id: UUID, custodian_id: UUID, amount_scaled: int
    ) -> CustodianBalance:
        self._validate_amount(amount_scaled, "amount_scaled")
        with self._lock:
            fund = self._get_fund(tenant_id, fund_id)
            if fund.available_amount_scaled < amount_scaled:
                raise InsufficientFloatError("fund has insufficient available balance")
            updated = Fund(
                **{**fund.__dict__, "available_amount_scaled": fund.available_amount_scaled - amount_scaled}
            )
            self._funds[(tenant_id, fund_id)] = updated
            key = (tenant_id, fund_id, custodian_id)
            balance = self._balances.get(key, 0) + amount_scaled
            self._balances[key] = balance
            return CustodianBalance(fund_id, tenant_id, custodian_id, fund.currency, balance)

    def issue_disbursement(
        self, tenant_id: UUID, fund_id: UUID, custodian_id: UUID, amount_scaled: int
    ) -> CustodianBalance:
        self._validate_amount(amount_scaled, "amount_scaled")
        with self._lock:
            fund = self._get_fund(tenant_id, fund_id)
            key = (tenant_id, fund_id, custodian_id)
            balance = self._balances.get(key, 0)
            if balance < amount_scaled:
                raise InsufficientFloatError("custodian has insufficient allocated float")
            updated_balance = balance - amount_scaled
            self._balances[key] = updated_balance
            return CustodianBalance(fund_id, tenant_id, custodian_id, fund.currency, updated_balance)

    def get_custodian_balance(
        self, tenant_id: UUID, fund_id: UUID, custodian_id: UUID
    ) -> CustodianBalance:
        with self._lock:
            fund = self._get_fund(tenant_id, fund_id)
            amount = self._balances.get((tenant_id, fund_id, custodian_id), 0)
            return CustodianBalance(fund_id, tenant_id, custodian_id, fund.currency, amount)

    def _get_fund(self, tenant_id: UUID, fund_id: UUID) -> Fund:
        try:
            return self._funds[(tenant_id, fund_id)]
        except KeyError as error:
            raise FundNotFoundError("fund was not found for tenant") from error

    @staticmethod
    def _normalize_currency(currency: str) -> str:
        normalized = currency.upper()
        if len(normalized) != 3:
            raise ValueError("currency must be a three-letter ISO-4217 code")
        if not all(c.isascii() and c.isalpha() for c in normalized):
            raise ValueError("currency must be a three-letter ISO-4217 code")
        return normalized

    @staticmethod
    def _validate_amount(amount_scaled: int, name: str, allow_zero: bool = False) -> None:
        if not isinstance(amount_scaled, int) or isinstance(amount_scaled, bool):
            raise TypeError(f"{name} must be an integer")
        if amount_scaled < 0 or (amount_scaled == 0 and not allow_zero):
            raise ValueError(f"{name} must be {'non-negative' if allow_zero else 'positive'}")
        max_int64 = 2**63 - 1
        if amount_scaled > max_int64:
            raise ValueError(f"{name} must not exceed protobuf int64 maximum ({max_int64})")
