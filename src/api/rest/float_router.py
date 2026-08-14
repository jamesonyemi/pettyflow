"""FastAPI endpoints for tenant-scoped petty-cash float operations."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from src.domain.funds.service import FundNotFoundError, FundService, InsufficientFloatError


class CreateFundRequest(BaseModel):
    tenant_id: UUID
    name: str = Field(min_length=1, max_length=255)
    currency: str = Field(pattern=r"^[A-Za-z]{3}$")
    custodian_id: UUID
    initial_amount_scaled: int = Field(ge=0)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()


class FloatMutationRequest(BaseModel):
    tenant_id: UUID
    custodian_id: UUID
    amount_scaled: int = Field(gt=0)


class FundResponse(BaseModel):
    fund_id: UUID
    tenant_id: UUID
    name: str
    currency: str
    custodian_id: UUID
    available_amount_scaled: int


class CustodianBalanceResponse(BaseModel):
    fund_id: UUID
    tenant_id: UUID
    custodian_id: UUID
    currency: str
    amount_scaled: int


def create_float_router(service: FundService) -> APIRouter:
    router = APIRouter(prefix="/v1/funds", tags=["float-management"])

    @router.post("", response_model=FundResponse, status_code=status.HTTP_201_CREATED)
    def create_fund(request: CreateFundRequest) -> FundResponse:
        return FundResponse.model_validate(
            service.create_fund(**request.model_dump()), from_attributes=True
        )

    @router.post("/{fund_id}/allocations", response_model=CustodianBalanceResponse)
    def allocate_float(fund_id: UUID, request: FloatMutationRequest) -> CustodianBalanceResponse:
        return _balance_response(service.allocate_float, fund_id, request)

    @router.post("/{fund_id}/disbursements", response_model=CustodianBalanceResponse)
    def issue_disbursement(fund_id: UUID, request: FloatMutationRequest) -> CustodianBalanceResponse:
        return _balance_response(service.issue_disbursement, fund_id, request)

    @router.get("/{fund_id}/custodians/{custodian_id}/balance", response_model=CustodianBalanceResponse)
    def get_custodian_balance(
        fund_id: UUID, custodian_id: UUID, tenant_id: UUID
    ) -> CustodianBalanceResponse:
        try:
            result = service.get_custodian_balance(tenant_id, fund_id, custodian_id)
        except FundNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        return CustodianBalanceResponse.model_validate(result, from_attributes=True)

    return router


def _balance_response(operation, fund_id: UUID, request: FloatMutationRequest) -> CustodianBalanceResponse:
    try:
        result = operation(fund_id=fund_id, **request.model_dump())
    except FundNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except InsufficientFloatError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return CustodianBalanceResponse.model_validate(result, from_attributes=True)
