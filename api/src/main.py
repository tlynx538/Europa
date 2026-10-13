import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from internal.middleware import debug_middleware
from internal.router import router
from kernel.gateway_client import GatewayClient, GatewayError


def create_app(*, gateway_url: str | None = None, gateway_token: str | None = None):
    @asynccontextmanager
    async def lifespan(app):
        url = gateway_url or os.environ.get("EUROPA_GATEWAY_URL", "http://127.0.0.1:8888")
        token = gateway_token or os.environ.get("EUROPA_GATEWAY_TOKEN", "")
        async with GatewayClient(url, token) as gateway:
            await gateway.health()
            app.state.gateway = gateway
            yield

    app = FastAPI(lifespan=lifespan)

    @app.exception_handler(GatewayError)
    async def gateway_error(_: Request, exc: GatewayError):
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    @app.get("/")
    async def home():
        return {"message": "Europa API", "backend": "jupyter-kernel-gateway"}

    app.middleware("http")(debug_middleware)
    app.include_router(router, prefix="/api", tags=["kernel"])
    return app


app = create_app()
