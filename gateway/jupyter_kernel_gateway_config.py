"""Development-only, authenticated loopback Gateway; no notebook server launch."""
import os
import sys
from pathlib import Path

c = get_config()  # noqa: F821 - supplied by Jupyter's config loader
token = os.environ.get("KG_AUTH_TOKEN", "")
if not token:
    raise RuntimeError("KG_AUTH_TOKEN must be configured")

c.KernelGatewayApp.api = "kernel_gateway.jupyter_websocket"
c.KernelGatewayApp.ip = "127.0.0.1"
c.KernelGatewayApp.port = int(os.environ.get("KG_PORT", "8888"))
c.KernelGatewayApp.port_retries = 0
c.KernelGatewayApp.auth_token = token
c.KernelGatewayApp.max_kernels = 4
c.KernelGatewayApp.prespawn_count = 0
c.KernelGatewayApp.env_process_whitelist = ["HOME", "TMPDIR", "LANG", "LC_ALL"]
c.JupyterWebsocketPersonality.list_kernels = True
c.JupyterWebsocketPersonality.env_whitelist = []
c.KernelSpecManager.allowed_kernelspecs = {"python3"}
c.KernelSpecManager.kernel_dirs = [str(Path(sys.prefix) / "share/jupyter/kernels")]
