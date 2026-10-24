from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from internal.models import CodeRequest
from kernel.gateway_client import GatewayClient

router = APIRouter()


async def get_gateway(request: Request, notebook_id: Annotated[str, Query(min_length=1, max_length=128, pattern=r".*\S.*")] = "default") -> GatewayClient:
    return await request.app.state.supervisor.client_for(notebook_id)


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


@router.get("/runtime")
async def runtime_status(request: Request, notebook_id: str = "default"):
    return await request.app.state.supervisor.status_for(notebook_id)


@router.post("/runtime/provision", status_code=201)
async def provision_runtime(request: Request, notebook_id: Annotated[str, Query(min_length=1, max_length=128, pattern=r".*\S.*")],
                            resource_profile: Annotated[str | None, Query(min_length=1, max_length=64, pattern=r".*\S.*")] = None):
    await request.app.state.supervisor.client_for(notebook_id, resource_profile=resource_profile)
    return await request.app.state.supervisor.status_for(notebook_id)


@router.get("/runtime/admission")
async def admission_status(request: Request):
    return await request.app.state.supervisor.admission_status()


@router.get("/runtime/recovery")
async def recovery_status(request: Request):
    supervisor = request.app.state.supervisor
    if supervisor.registry is None:
        return {"enabled": False, "reason": "Local/external process adoption is unsupported"}
    return {"enabled": True, **await supervisor.recovery_status()}


@router.delete("/runtime", status_code=204)
async def destroy_runtime(request: Request, notebook_id: str = "default"):
    await request.app.state.supervisor.destroy_notebook(notebook_id)
    return Response(status_code=204)
