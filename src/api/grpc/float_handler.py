"""gRPC implementation of the tenant-scoped float management contract."""

from uuid import UUID

import grpc

from proto.pettyflow.v1 import float_service_pb2, float_service_pb2_grpc
from src.domain.funds.service import FundNotFoundError, FundService, InsufficientFloatError


class FloatServiceHandler(float_service_pb2_grpc.FloatServiceServicer):
    """Translates protobuf requests into exact fixed-point domain operations."""

    def __init__(self, service: FundService) -> None:
        self._service = service

    async def CreateFund(self, request, context):
        try:
            fund = self._service.create_fund(
                tenant_id=UUID(request.tenant_id),
                name=request.name,
                currency=request.currency,
                custodian_id=UUID(request.custodian_id),
                initial_amount_scaled=request.initial_amount_scaled,
            )
        except (TypeError, ValueError) as error:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(error))
        return float_service_pb2.Fund(
            fund_id=str(fund.fund_id),
            tenant_id=str(fund.tenant_id),
            name=fund.name,
            currency=fund.currency,
            custodian_id=str(fund.custodian_id),
            available_amount_scaled=fund.available_amount_scaled,
        )

    async def AllocateFloat(self, request, context):
        return await self._mutate(self._service.allocate_float, request, context)

    async def IssueDisbursement(self, request, context):
        return await self._mutate(self._service.issue_disbursement, request, context)

    async def GetCustodianBalance(self, request, context):
        try:
            balance = self._service.get_custodian_balance(
                tenant_id=UUID(request.tenant_id),
                fund_id=UUID(request.fund_id),
                custodian_id=UUID(request.custodian_id),
            )
        except ValueError as error:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(error))
        except FundNotFoundError as error:
            await context.abort(grpc.StatusCode.NOT_FOUND, str(error))
        return self._balance_message(balance)

    async def _mutate(self, operation, request, context):
        try:
            balance = operation(
                tenant_id=UUID(request.tenant_id),
                fund_id=UUID(request.fund_id),
                custodian_id=UUID(request.custodian_id),
                amount_scaled=request.amount_scaled,
            )
        except ValueError as error:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(error))
        except FundNotFoundError as error:
            await context.abort(grpc.StatusCode.NOT_FOUND, str(error))
        except InsufficientFloatError as error:
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, str(error))
        return self._balance_message(balance)

    @staticmethod
    def _balance_message(balance):
        return float_service_pb2.CustodianBalance(
            fund_id=str(balance.fund_id),
            tenant_id=str(balance.tenant_id),
            custodian_id=str(balance.custodian_id),
            currency=balance.currency,
            amount_scaled=balance.amount_scaled,
        )
