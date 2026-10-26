"""REST API adapters."""

from src.api.rest.approval_router import create_approval_router
from src.api.rest.float_router import create_float_router
from src.api.rest.receipt_router import create_receipt_router
from src.api.rest.reconciliation_router import create_reconciliation_router
from src.api.rest.reports_router import create_reports_router

__all__ = [
    "create_approval_router",
    "create_float_router",
    "create_receipt_router",
    "create_reconciliation_router",
    "create_reports_router",
]
