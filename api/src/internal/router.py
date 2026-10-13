from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from internal.models import CodeRequest
from kernel.gateway_client import GatewayClient

router = APIRouter()


def get_gateway(request: Request) -> GatewayClient:
    return request.app.state.gateway


Gateway = Annotated[GatewayClient, Depends(get_gateway)]
KernelID = Annotated[str, Query(pattern=r"^[a-zA-Z0-9-]{1,128}$")]


@router.post("/kernel/start", status_code=201)
async def start(gateway: Gateway, kernel_name: str = "python3"):
    return await gateway.create_kernel(kernel_name)


@router.get("/kernel/info")
async def info(kernel_id: KernelID, gateway: Gateway):
    return await gateway.inspect_kernel(kernel_id)


@router.post("/kernel/execute")
async def execute(request: CodeRequest, kernel_id: KernelID, gateway: Gateway,
                  timeout: Annotated[float, Query(gt=0, le=300)] = 30):
    return await gateway.execute(kernel_id, request.code, timeout=timeout)


@router.post("/kernel/interrupt", status_code=204)
async def interrupt(kernel_id: KernelID, gateway: Gateway):
    await gateway.interrupt_kernel(kernel_id)
    return Response(status_code=204)


@router.post("/kernel/restart")
async def restart(kernel_id: KernelID, gateway: Gateway):
    return await gateway.restart_kernel(kernel_id)


@router.post("/kernel/shutdown", status_code=204)
async def shutdown(kernel_id: KernelID, gateway: Gateway):
    await gateway.terminate_kernel(kernel_id)
    return Response(status_code=204)


@router.post("/kernel/install-packages")
async def install_packages():
    raise HTTPException(501, "Package installation is outside this Gateway milestone")


@router.get("/health")
async def health(gateway: Gateway):
    await gateway.health()
    return {"status": "ready", "backend": "jupyter-kernel-gateway",
            "executions": gateway.tracker.snapshot()}
