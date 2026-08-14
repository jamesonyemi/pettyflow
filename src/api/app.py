"""Application factory for REST deployment and OpenAPI generation."""

from fastapi import FastAPI

from src.api.rest.float_router import create_float_router
from src.domain.funds.service import FundService


def create_app(service: FundService | None = None) -> FastAPI:
    app = FastAPI(title="PettyFlow API", version="1.0.0")
    app.include_router(create_float_router(service or FundService()))
    return app


app = create_app()
