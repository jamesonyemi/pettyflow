"""Application factory for REST deployment, OpenAPI generation, and PWA serving."""

from pathlib import Path
from typing import Optional
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.api.rest.approval_router import create_approval_router
from src.api.rest.float_router import create_float_router
from src.api.rest.receipt_router import create_receipt_router
from src.api.rest.reconciliation_router import create_reconciliation_router
from src.api.rest.reports_router import create_reports_router
from src.domain.funds.service import FundService
from src.domain.workflow.service import ApprovalWorkflowService


def create_app(
    service: Optional[FundService] = None,
    approval_service: Optional[ApprovalWorkflowService] = None,
) -> FastAPI:
    app = FastAPI(title="PettyFlow API", version="1.0.0")

    # API Routers
    app.include_router(create_float_router(service or FundService()))
    app.include_router(create_receipt_router())
    app.include_router(create_reconciliation_router())
    app.include_router(create_reports_router())
    app.include_router(create_approval_router(approval_service or ApprovalWorkflowService()))

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
