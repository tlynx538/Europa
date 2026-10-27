import os
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from internal.middleware import debug_middleware
from internal.router import router
from notebooks.router import router as notebook_router
from notebooks.store import NotebookStore, NotebookError
from notebooks.service import NotebookService
from application.store import ApplicationStore
from application.router import router as application_router
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
        notebook_store = notebook_service = application_store = None
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
            notebook_root = Path(os.environ.get('EUROPA_NOTEBOOK_ROOT',
                str(Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))) / 'europa/notebooks')))
            notebook_store = NotebookStore(notebook_root)
            app.state.notebook_store = notebook_store
            application_store = ApplicationStore(os.environ.get('EUROPA_APPLICATION_ROOT',
                str(notebook_root / 'application')), notebook_store)
            app.state.application_store = application_store
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
            notebook_service = NotebookService(notebook_store, supervisor)
            app.state.notebook_service = notebook_service
            yield
        finally:
            try:
                try:
                    if notebook_service:
                        await notebook_service.close(supervisor.lifecycle.policy.grace if getattr(supervisor, 'lifecycle', None) else 30)
                finally:
                    await supervisor.close()
            finally:
                if application_store:
                    application_store.close()
                if notebook_store:
                    notebook_store.close()
                if registry:
                    registry.close()
                if guard:
                    guard.close()

    app = FastAPI(lifespan=lifespan)

    @app.middleware('http')
    async def browser_boundary(request, call_next):
        # JSON is not a cross-origin ingress API. Keep existing non-browser API
        # clients working, but reject cross-site browser mutations / DNS rebinding.
        host = request.url.hostname
        origin = request.headers.get('origin')
        if host not in {'127.0.0.1', 'localhost', '::1', 'testserver', 'europa'}:
            return JSONResponse(status_code=403, content={'detail': 'Loopback host required'})
        if request.method not in {'GET', 'HEAD', 'OPTIONS'} and (
                request.headers.get('sec-fetch-site') == 'cross-site' or
                (origin and origin != str(request.base_url).rstrip('/'))):
            return JSONResponse(status_code=403, content={'detail': 'Same-origin request required'})
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = (
            "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; connect-src 'self'; frame-src 'self'; "
            "font-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; form-action 'self'")
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(NotebookError)
    async def notebook_error(_: Request, exc: NotebookError):
        return JSONResponse(status_code=exc.status_code, content={'detail': str(exc)})

    @app.exception_handler(GatewayError)
    async def gateway_error(_: Request, exc: GatewayError):
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    @app.exception_handler(RuntimeErrorBase)
    async def runtime_error(_: Request, exc: RuntimeErrorBase):
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.get("/")
    async def home():
        index = Path(__file__).resolve().parents[2] / 'web' / 'dist' / 'index.html'
        if index.is_file():
            return FileResponse(index, headers={'Cache-Control': 'no-cache'})
        return {"message": "Europa API", "backend": "jupyter-kernel-gateway"}

    app.middleware("http")(debug_middleware)
    app.include_router(router, prefix="/api", tags=["kernel"])
    app.include_router(notebook_router, prefix="/api", tags=["notebooks"])
    app.include_router(application_router, prefix="/api", tags=["application"])
    assets = Path(__file__).resolve().parents[2] / 'web' / 'dist' / 'assets'
    if assets.is_dir():
        app.mount('/assets', StaticFiles(directory=assets), name='web-assets')
    return app


app = create_app()
