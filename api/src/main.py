import os
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from internal.middleware import debug_middleware
from internal.router import router
from kernel.gateway_client import GatewayError
from runtime.contracts import GatewayEndpoint, RuntimeErrorBase
from runtime.containerd import ContainerdConfig, ContainerdRuntimeProvider
from runtime.containerd_backend import SDKBridgeBackend
from runtime.local import LocalRuntimeProvider
from runtime.supervisor import RuntimeSupervisor
from runtime.admission_config import configured_admission
from runtime.registry import RuntimeRegistry
from runtime.prewarming import PrewarmingRuntimeSupervisor
from runtime.lifecycle import IdlePolicy


def create_app(*, gateway_url: str | None = None, gateway_token: str | None = None,
               runtime_provider=None):
    @asynccontextmanager
    async def lifespan(app):
        url = gateway_url or os.environ.get("EUROPA_GATEWAY_URL")
        token = gateway_token or os.environ.get("EUROPA_GATEWAY_TOKEN", "")
        selection = "local" if url else os.environ.get("EUROPA_RUNTIME_PROVIDER", "local")
        resources = None
        admission = guard = None
        registry = None
        if runtime_provider is not None:
            provider = runtime_provider
            selection = "local"
        elif selection == "local":
            provider = LocalRuntimeProvider()
        elif selection == "containerd":
            if os.environ.get("EUROPA_CONTAINERD_ALLOW_NETWORK") != "1":
                raise RuntimeErrorBase("Containerd networking requires explicit operator opt-in")
            backend = SDKBridgeBackend(Path(os.environ["EUROPA_CONTAINERD_BRIDGE"]),
                socket=os.environ.get("EUROPA_CONTAINERD_SOCKET", "/run/containerd/containerd.sock"),
                cni_config=os.environ["EUROPA_CONTAINERD_CNI_CONFIG"],
                cni_bin=os.environ.get("EUROPA_CONTAINERD_CNI_BIN", "/opt/cni/bin"))
            provider = ContainerdRuntimeProvider(backend, ContainerdConfig(
                os.environ["EUROPA_CONTAINERD_IMAGE"], os.environ.get("EUROPA_CONTAINERD_HANDLER", "runc")))
            admission, guard = configured_admission(backend.socket)
        else:
            raise RuntimeErrorBase("Unknown runtime provider selection")
        supervisor = RuntimeSupervisor({selection: provider}, default_provider=selection,
                                       default_resources=resources, admission=admission)
        app.state.supervisor = supervisor
        try:
            if guard:
                guard.acquire()
                registry_path = Path(os.environ.get('EUROPA_RUNTIME_REGISTRY',
                    str(Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state'))) / 'europa/runtime.sqlite3')))
                registry = RuntimeRegistry(registry_path, provider.recovery_scope())
                supervisor = PrewarmingRuntimeSupervisor(provider, admission, registry,
                    target=int(os.environ.get('EUROPA_WARM_TARGET', '1')),
                    profile=os.environ.get('EUROPA_WARM_PROFILE'))
                app.state.supervisor = supervisor
                await supervisor.reconcile()
                supervisor.enable_lifecycle(IdlePolicy.environment())
            if url:
                if registry:
                    raise RuntimeErrorBase('External Gateway attachment is not supported by the durable containerd deployment')
                await supervisor.attach("default", GatewayEndpoint(url, token))
            if registry and (supervisor.recovery_blockers or 'default' in supervisor.reconciliation_notes):
                app.state.gateway = None  # Serve reconciliation diagnostics while provisioning is denied.
            else:
                if registry:
                    supervisor.startup_gateway_only = True
                try:
                    app.state.gateway = await supervisor.client_for()
                finally:
                    if registry:
                        supervisor.startup_gateway_only = False
            if registry:
                supervisor.pool.schedule()
                supervisor.lifecycle.start()
            yield
        finally:
            try:
                await supervisor.close()
            finally:
                if registry:
                    registry.close()
                if guard:
                    guard.close()

    app = FastAPI(lifespan=lifespan)

    @app.exception_handler(GatewayError)
    async def gateway_error(_: Request, exc: GatewayError):
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    @app.exception_handler(RuntimeErrorBase)
    async def runtime_error(_: Request, exc: RuntimeErrorBase):
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.get("/")
    async def home():
        return {"message": "Europa API", "backend": "jupyter-kernel-gateway"}

    app.middleware("http")(debug_middleware)
    app.include_router(router, prefix="/api", tags=["kernel"])
    return app


app = create_app()
