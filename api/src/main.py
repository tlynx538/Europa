import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from internal.middleware import debug_middleware
from internal.router import router
from kernel.gateway_client import GatewayError
from runtime.contracts import GatewayEndpoint, RuntimeErrorBase
from runtime.local import LocalRuntimeProvider
from runtime.supervisor import RuntimeSupervisor


def create_app(*, gateway_url: str | None = None, gateway_token: str | None = None,
               runtime_provider=None):
    @asynccontextmanager
    async def lifespan(app):
        url = gateway_url or os.environ.get("EUROPA_GATEWAY_URL")
        token = gateway_token or os.environ.get("EUROPA_GATEWAY_TOKEN", "")
        provider = runtime_provider or LocalRuntimeProvider()
        supervisor = RuntimeSupervisor({"local": provider})
        app.state.supervisor = supervisor
        try:
            if url:
                await supervisor.attach("default", GatewayEndpoint(url, token))
            app.state.gateway = await supervisor.client_for()
            yield
        finally:
            await supervisor.close()

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
