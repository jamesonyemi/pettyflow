"""gRPC end-to-end contract test for float management."""

import unittest
from uuid import uuid4

import grpc

from proto.pettyflow.v1 import float_service_pb2, float_service_pb2_grpc
from src.api.grpc.float_handler import FloatServiceHandler
from src.domain.funds.service import FundService


class TestFloatGrpcAPI(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.server = grpc.aio.server()
        float_service_pb2_grpc.add_FloatServiceServicer_to_server(
            FloatServiceHandler(FundService()), self.server
        )
        port = self.server.add_insecure_port("127.0.0.1:0")
        await self.server.start()
        self.channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
        self.client = float_service_pb2_grpc.FloatServiceStub(self.channel)
        self.tenant_id = str(uuid4())
        self.custodian_id = str(uuid4())

    async def asyncTearDown(self) -> None:
        await self.channel.close()
        await self.server.stop(grace=0)

    async def test_float_lifecycle_and_invalid_amount(self) -> None:
        fund = await self.client.CreateFund(
            float_service_pb2.CreateFundRequest(
                tenant_id=self.tenant_id,
                name="HQ Float",
                currency="GHS",
                custodian_id=self.custodian_id,
                initial_amount_scaled=500_000,
            )
        )
        allocation = await self.client.AllocateFloat(
            float_service_pb2.AllocateFloatRequest(
                tenant_id=self.tenant_id,
                fund_id=fund.fund_id,
                custodian_id=self.custodian_id,
                amount_scaled=200_000,
            )
        )
        self.assertEqual(allocation.amount_scaled, 200_000)

        balance = await self.client.IssueDisbursement(
            float_service_pb2.IssueDisbursementRequest(
                tenant_id=self.tenant_id,
                fund_id=fund.fund_id,
                custodian_id=self.custodian_id,
                amount_scaled=50_000,
            )
        )
        self.assertEqual(balance.amount_scaled, 150_000)

        with self.assertRaises(grpc.aio.AioRpcError) as error:
            await self.client.AllocateFloat(
                float_service_pb2.AllocateFloatRequest(
                    tenant_id=self.tenant_id,
                    fund_id=fund.fund_id,
                    custodian_id=self.custodian_id,
                    amount_scaled=0,
                )
            )
        self.assertEqual(error.exception.code(), grpc.StatusCode.INVALID_ARGUMENT)
