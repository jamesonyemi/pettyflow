"""Multi-Currency Conversion & Historical Revaluation Engine.

Implements micro-precision fixed-point currency conversion with triangular arbitrage prevention:
- Rate Scale Factor: 1,000,000 (10^6) for sub-pip exchange rate precision.
- Amount Scale Factor: 10,000 (10^4) for standard monetary units.
- Supports historical spot rates, triangular cross-rate calculation (A -> USD -> B),
  and multi-currency ledger balance revaluation.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Constants & Scale Factors
# ---------------------------------------------------------------------------

RATE_SCALE = 1_000_000        # 10^6 for micro-precision exchange rates
AMOUNT_SCALE = 10_000         # 10^4 for standard 64-bit integer money units


def _normalize_currency_code(currency: str, field_name: str) -> str:
    if not isinstance(currency, str):
        raise TypeError(f"{field_name} must be a string ISO-4217 code.")
    normalized = currency.strip().upper()
    if len(normalized) != 3 or not normalized.isalpha():
        raise ValueError(f"{field_name} must be a three-letter ISO-4217 code.")
    return normalized


def _validate_positive_rate(rate: float, field_name: str = "rate") -> float:
    if isinstance(rate, bool) or not isinstance(rate, (int, float)):
        raise TypeError(f"{field_name} must be a numeric rate.")
    if rate <= 0:
        raise ValueError(f"{field_name} must be greater than zero.")
    return float(rate)


@dataclass(frozen=True)
class ExchangeRateRecord:
    """Historical spot rate record."""
    base_currency: str        # e.g., "EUR"
    quote_currency: str       # e.g., "USD"
    rate_scaled: int          # Rate * 1,000,000 (e.g. 1.0850 USD/EUR -> 1,085,000)
    effective_date: str       # YYYY-MM-DD
    source: str = "ECB"       # European Central Bank / Fixer

    @property
    def rate_float(self) -> float:
        return self.rate_scaled / RATE_SCALE


class CurrencyConversionError(Exception):
    """Raised when currency conversion fails due to missing rates."""
    pass


# ---------------------------------------------------------------------------
# Currency Converter & Revaluation Engine
# ---------------------------------------------------------------------------

class CurrencyConverter:
    """Fixed-point currency converter with cross-rate calculation and historical lookup."""

    def __init__(self):
        # (base_currency, quote_currency, effective_date) -> rate_scaled
        self._rates: Dict[Tuple[str, str, str], int] = {}

    def set_rate(
        self,
        base_currency: str,
        quote_currency: str,
        rate: float,
        effective_date: Optional[str] = None,
        source: str = "ECB",
    ) -> None:
        """Register a spot exchange rate."""
        base = _normalize_currency_code(base_currency, "base_currency")
        quote = _normalize_currency_code(quote_currency, "quote_currency")
        rate = _validate_positive_rate(rate, "rate")
        date_str = effective_date or datetime.date.today().isoformat()
        scaled_rate = int(round(rate * RATE_SCALE))

        self._rates[(base, quote, date_str)] = scaled_rate
        # Store inverse rate using integer arithmetic
        if scaled_rate > 0:
            inv_rate = (RATE_SCALE * RATE_SCALE + (scaled_rate // 2)) // scaled_rate
            self._rates[(quote, base, date_str)] = inv_rate

    def get_rate_scaled(
        self,
        from_currency: str,
        to_currency: str,
        date_str: Optional[str] = None,
    ) -> int:
        """Retrieve rate scaled x10^6. Computes triangular cross-rate via USD if direct rate missing."""
        from_curr = _normalize_currency_code(from_currency, "from_currency")
        to_curr = _normalize_currency_code(to_currency, "to_currency")
        dt = date_str or datetime.date.today().isoformat()

        if from_curr == to_curr:
            return RATE_SCALE

        # Direct lookup for the exact date
        if (from_curr, to_curr, dt) in self._rates:
            rate = self._rates[(from_curr, to_curr, dt)]
            if rate <= 0:
                raise CurrencyConversionError(f"Stored exchange rate for {from_curr}/{to_curr} is invalid.")
            return rate

        # Fallback to latest available date on or prior to dt
        matching = [
            (d, rate) for (b, q, d), rate in self._rates.items()
            if b == from_curr and q == to_curr
        ]
        if matching:
            prior_rates = [item for item in matching if item[0] <= dt]
            if prior_rates:
                selected = max(prior_rates, key=lambda x: x[0])
                if selected[1] <= 0:
                    raise CurrencyConversionError(f"Stored exchange rate for {from_curr}/{to_curr} is invalid.")
                return selected[1]
            selected = min(matching, key=lambda x: x[0])
            if selected[1] <= 0:
                raise CurrencyConversionError(f"Stored exchange rate for {from_curr}/{to_curr} is invalid.")
            return selected[1]

        # Triangular cross-rate via USD
        if from_curr != "USD" and to_curr != "USD":
            rate_from_usd = self.get_rate_scaled(from_curr, "USD", dt)
            rate_usd_to = self.get_rate_scaled("USD", to_curr, dt)
            if rate_from_usd <= 0 or rate_usd_to <= 0:
                raise CurrencyConversionError(f"Invalid cross-rate for {from_curr}/{to_curr} on date {dt}.")
            # Cross rate = (rate_from_usd * rate_usd_to + (RATE_SCALE // 2)) // RATE_SCALE
            return (rate_from_usd * rate_usd_to + (RATE_SCALE // 2)) // RATE_SCALE

        raise CurrencyConversionError(
            f"No exchange rate found for pair {from_curr}/{to_curr} on date {dt}"
        )

    def convert(
        self,
        amount_scaled: int,
        from_currency: str,
        to_currency: str,
        date_str: Optional[str] = None,
    ) -> int:
        """Convert monetary amount (scaled x10^4) from one currency to another using fixed-point math.

        Formula:
            converted_scaled = (amount_scaled * rate_scaled + (RATE_SCALE // 2)) // RATE_SCALE
        """
        if from_currency.upper() == to_currency.upper():
            return amount_scaled

        rate_scaled = self.get_rate_scaled(from_currency, to_currency, date_str)
        # Fixed point integer multiplication and round-half-up division
        return (amount_scaled * rate_scaled + (RATE_SCALE // 2)) // RATE_SCALE

    def revalue_amount(
        self,
        amount_scaled: int,
        from_currency: str,
        base_currency: str,
        historical_date: str,
        current_date: Optional[str] = None,
    ) -> Tuple[int, int, int]:
        """Perform historical currency revaluation.

        Returns:
            (historical_base_amount_scaled, current_base_amount_scaled, fx_gain_loss_scaled)
        """
        curr_dt = current_date or datetime.date.today().isoformat()
        hist_val = self.convert(amount_scaled, from_currency, base_currency, historical_date)
        curr_val = self.convert(amount_scaled, from_currency, base_currency, curr_dt)
        gain_loss = curr_val - hist_val
        return hist_val, curr_val, gain_loss
