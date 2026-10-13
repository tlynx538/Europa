import os

c = get_config()  # noqa: F821
token = os.environ.get("KG_AUTH_TOKEN")
if not token:
    raise RuntimeError("Gateway authentication token is required")
c.KernelGatewayApp.api = "kernel_gateway.jupyter_websocket"
c.KernelGatewayApp.ip = "0.0.0.0"  # Private container namespace, never host networking.
c.KernelGatewayApp.port = 8888
c.KernelGatewayApp.port_retries = 0
c.KernelGatewayApp.auth_token = token
c.KernelGatewayApp.max_kernels = 1
c.KernelGatewayApp.prespawn_count = 0
c.KernelGatewayApp.env_process_whitelist = ["HOME", "TMPDIR", "LANG", "LC_ALL"]
c.JupyterWebsocketPersonality.list_kernels = True
c.JupyterWebsocketPersonality.env_whitelist = []
c.KernelSpecManager.allowed_kernelspecs = {"python3"}
c.KernelSpecManager.kernel_dirs = ["/usr/local/share/jupyter/kernels"]
