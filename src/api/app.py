"""Application factory for REST deployment, OpenAPI generation, and PWA serving."""

from pathlib import Path
from typing import Optional
from uuid import UUID

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.api.rest.approval_router import create_approval_router
from src.api.rest.float_router import create_float_router
from src.api.rest.receipt_router import create_receipt_router
from src.api.rest.reconciliation_router import create_reconciliation_router
from src.api.rest.reports_router import create_reports_router
from src.domain.funds.service import FundNotFoundError, FundService
from src.domain.workflow.service import ApprovalWorkflowService

# ── Well-known demo identifiers (must match frontend state constants) ─────────
_DEFAULT_TENANT_ID   = UUID("00000000-0000-0000-0000-000000000001")  # "TENANT_CORP_01"
_DEFAULT_FUND_ID     = UUID("00000000-0000-0000-0000-000000000002")  # "fund-default-001"
_DEFAULT_CUSTODIAN_ID = UUID("00000000-0000-0000-0000-000000000003")  # "CUSTODIAN_BRANCH_42"
_INITIAL_FLOAT_SCALED = 500_000_000  # $50,000.00  (× 10,000)


def _seed_default_fund(fund_svc: FundService) -> None:
    """Ensure the default demo fund and custodian float exist on startup.

    Idempotent: silently skips if the fund already exists.
    Uses a known fixed fund_id so the float_router GET endpoint stays stable.
    """
    from src.domain.funds.service import Fund
    with fund_svc._lock:
        key = (_DEFAULT_TENANT_ID, _DEFAULT_FUND_ID)
        if key in fund_svc._funds:
            # Already seeded (e.g. multiple create_app calls in tests)
            return
        # Directly insert with deterministic fund_id (bypasses uuid4())
        fund_svc._funds[key] = Fund(
            fund_id=_DEFAULT_FUND_ID,
            tenant_id=_DEFAULT_TENANT_ID,
            name="Branch 42 Petty Cash",
            currency="USD",
            custodian_id=_DEFAULT_CUSTODIAN_ID,
            available_amount_scaled=0,  # immediately fully allocated below
        )
        # Allocate the full initial amount to the custodian balance.
        balance_key = (_DEFAULT_TENANT_ID, _DEFAULT_FUND_ID, _DEFAULT_CUSTODIAN_ID)
        fund_svc._balances[balance_key] = _INITIAL_FLOAT_SCALED


def create_app(
    service: Optional[FundService] = None,
    approval_service: Optional[ApprovalWorkflowService] = None,
) -> FastAPI:
    app = FastAPI(title="PettyFlow API", version="1.0.0")

    # Shared singletons — both services reference the same FundService instance.
    fund_svc = service or FundService()
    _seed_default_fund(fund_svc)

    # Wire FundService into ApprovalWorkflowService so disbursements deduct float.
    workflow_svc = approval_service or ApprovalWorkflowService(
        fund_service=fund_svc,
        default_fund_id=_DEFAULT_FUND_ID,
    )

    # API Routers
    app.include_router(create_float_router(fund_svc))
    app.include_router(create_receipt_router())
    app.include_router(create_reconciliation_router())
    app.include_router(create_reports_router())
    app.include_router(create_approval_router(workflow_svc))

    # ── Custodian balance convenience endpoint ────────────────────────────────
    # GET /v1/custodian/balance?tenant_id=...&fund_id=...&custodian_id=...
    # Used by the PWA float widget to fetch a live balance without UUID routing.
    from fastapi import Query
    from fastapi.routing import APIRouter
    from src.api.rest.float_router import CustodianBalanceResponse

    balance_router = APIRouter(prefix="/v1/custodian", tags=["custodian-balance"])

    @balance_router.get("/balance", response_model=CustodianBalanceResponse)
    def get_custodian_balance_simple(
        tenant_id: str = Query(...),
        fund_id: str = Query(...),
        custodian_id: str = Query(...),
    ) -> CustodianBalanceResponse:
        """Live custodian float balance — called by PWA after each disbursement."""
        from fastapi import HTTPException, status as http_status
        try:
            result = fund_svc.get_custodian_balance(
                tenant_id=UUID(tenant_id) if tenant_id else _DEFAULT_TENANT_ID,
                fund_id=UUID(fund_id) if fund_id else _DEFAULT_FUND_ID,
                custodian_id=UUID(custodian_id) if custodian_id else _DEFAULT_CUSTODIAN_ID,
            )
        except (FundNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        return CustodianBalanceResponse.model_validate(result, from_attributes=True)

    app.include_router(balance_router)

    # Static Assets & PWA Serving
    static_dir = Path(__file__).resolve().parent.parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

        @app.get("/", include_in_schema=False)
        async def serve_index() -> FileResponse:
            return FileResponse(str(static_dir / "index.html"), media_type="text/html")

        @app.get("/manifest.json", include_in_schema=False)
        async def serve_manifest() -> FileResponse:
            return FileResponse(str(static_dir / "manifest.json"), media_type="application/manifest+json")

        @app.get("/sw.js", include_in_schema=False)
        async def serve_service_worker() -> FileResponse:
            return FileResponse(str(static_dir / "sw.js"), media_type="application/javascript")

    return app


app = create_app()
