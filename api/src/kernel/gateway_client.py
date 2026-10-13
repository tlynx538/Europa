"""Async adapter for official Kernel Gateway REST and WebSocket APIs.

No subprocesses, Jupyter managers, ZeroMQ sockets, or recovery threads live here.
One active operation per kernel is admitted; interrupt remains available while busy.
"""
import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import aiohttp

from kernel.execution_tracker import ExecutionTracker

logger = logging.getLogger(__name__)


class GatewayError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


class GatewayTimeout(GatewayError):
    def __init__(self):
        super().__init__("Gateway operation timed out; kernel termination requested", 504)


class GatewayClient:
    def __init__(self, base_url: str, token: str, *, request_timeout: float = 10,
                 readiness_timeout: float = 30, max_output_bytes: int = 4 * 1024 * 1024):
        parts = urlsplit(base_url)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.query or parts.fragment:
            raise ValueError("Gateway URL must be an HTTP(S) base URL without query or fragment")
        if parts.username or parts.password or not token:
            raise ValueError("A separate Gateway authentication token is required")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.request_timeout = request_timeout
        self.readiness_timeout = readiness_timeout
        self.max_output_bytes = max_output_bytes
        self.tracker = ExecutionTracker()
        self._session: aiohttp.ClientSession | None = None
        self._owned: set[str] = set()
        self._busy: set[str] = set()
        self._closing = False

    async def __aenter__(self):
        self._session = aiohttp.ClientSession(
            headers={"Authorization": f"token {self.token}"},
            timeout=aiohttp.ClientTimeout(total=self.request_timeout),
            trust_env=False,
        )
        return self

    async def __aexit__(self, *_):
        await self.close()

    async def close(self):
        self._closing = True
        try:
            for kernel_id in tuple(self._owned):
                try:
                    await self._delete(kernel_id)
                except GatewayError:
                    logger.error("Gateway could not terminate an owned kernel during teardown")
        finally:
            if self._session:
                await self._session.close()
                self._session = None

    def _session_ready(self) -> aiohttp.ClientSession:
        if self._session is None:
            raise GatewayError("Gateway client is not open", 503)
        return self._session

    async def _request(self, method: str, path: str, **kwargs):
        try:
            async with self._session_ready().request(
                method, f"{self.base_url}{path}", allow_redirects=False, **kwargs
            ) as response:
                if response.status >= 300:
                    status = response.status if response.status in {400, 404, 409} else 502
                    raise GatewayError(f"Gateway returned HTTP {response.status}", status)
                if response.status == 204:
                    return None
                return await response.json()
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            raise GatewayError("Gateway request failed") from exc

    async def health(self):
        return await self._request("GET", "/api/kernelspecs")

    def _known(self, kernel_id: str):
        if self._closing:
            raise GatewayError("Gateway client is closing", 503)
        if kernel_id not in self._owned:
            raise GatewayError("Kernel is not owned by this Europa instance", 404)

    def _admit(self, kernel_id: str):
        self._known(kernel_id)
        if kernel_id in self._busy:
            raise GatewayError("Kernel has an active operation", 409)
        self._busy.add(kernel_id)

    async def create_kernel(self, name: str = "python3"):
        if self._closing:
            raise GatewayError("Gateway client is closing", 503)
        # env={} selects KG environment filtering instead of implicit inheritance.
        model = await self._request("POST", "/api/kernels", json={"name": name, "env": {}})
        kernel_id = model["id"]
        self._owned.add(kernel_id)
        try:
            await self.wait_ready(kernel_id)
            return model
        except BaseException:
            await self._abort(kernel_id)
            raise

    async def inspect_kernel(self, kernel_id: str):
        self._known(kernel_id)
        return await self._request("GET", f"/api/kernels/{kernel_id}")

    async def interrupt_kernel(self, kernel_id: str):
        self._known(kernel_id)
        await self._request("POST", f"/api/kernels/{kernel_id}/interrupt")

    async def restart_kernel(self, kernel_id: str):
        self._admit(kernel_id)
        try:
            model = await self._request("POST", f"/api/kernels/{kernel_id}/restart")
            await self.wait_ready(kernel_id)
            return model
        except BaseException:
            await self._abort(kernel_id)
            raise
        finally:
            self._busy.discard(kernel_id)

    async def _delete(self, kernel_id: str):
        try:
            await self._request("DELETE", f"/api/kernels/{kernel_id}")
        except GatewayError as exc:
            if exc.status_code != 404:
                raise
        self._owned.discard(kernel_id)

    async def terminate_kernel(self, kernel_id: str):
        self._admit(kernel_id)
        try:
            await self._delete(kernel_id)
        finally:
            self._busy.discard(kernel_id)

    async def _abort(self, kernel_id: str):
        task = asyncio.create_task(self._delete(kernel_id))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
        except GatewayError:
            logger.error("Gateway termination failed; kernel ownership retained for teardown")

    def _websocket_url(self, kernel_id: str, session_id: str):
        parts = urlsplit(self.base_url)
        return urlunsplit(("wss" if parts.scheme == "https" else "ws", parts.netloc,
                           f"{parts.path}/api/kernels/{kernel_id}/channels",
                           f"session_id={session_id}", ""))

    @staticmethod
    def _message(session_id: str, msg_type: str, content: dict):
        return {
            "header": {"msg_id": uuid.uuid4().hex, "username": "europa",
                       "session": session_id, "date": datetime.now(timezone.utc).isoformat(),
                       "msg_type": msg_type, "version": "5.3"},
            "parent_header": {}, "metadata": {}, "content": content,
            "channel": "shell", "buffers": [],
        }

    async def _receive(self, websocket):
        frame = await websocket.receive()
        if frame.type != aiohttp.WSMsgType.TEXT:
            raise GatewayError("Gateway channel closed or unsupported binary message received")
        try:
            message = json.loads(frame.data)
            if (not isinstance(message, dict) or any(
                    not isinstance(message.get(key), dict)
                    for key in ("header", "parent_header", "content"))):
                raise ValueError()
            return message
        except (ValueError, TypeError) as exc:
            raise GatewayError("Invalid Gateway message") from exc

    async def _handshake(self, websocket, session_id: str):
        request = self._message(session_id, "kernel_info_request", {})
        await websocket.send_json(request)
        while True:
            message = await self._receive(websocket)
            if (message.get("parent_header", {}).get("msg_id") == request["header"]["msg_id"]
                    and message.get("channel") == "shell"
                    and message.get("header", {}).get("msg_type") == "kernel_info_reply"):
                return message["content"]

    async def wait_ready(self, kernel_id: str):
        self._known(kernel_id)
        session_id = uuid.uuid4().hex
        try:
            async with asyncio.timeout(self.readiness_timeout):
                async with self._session_ready().ws_connect(
                    self._websocket_url(kernel_id, session_id),
                    max_msg_size=self.max_output_bytes,
                ) as websocket:
                    return await self._handshake(websocket, session_id)
        except TimeoutError as exc:
            raise GatewayError("Kernel readiness timed out", 504) from exc
        except aiohttp.ClientError as exc:
            raise GatewayError("Gateway channel connection failed") from exc

    async def execute(self, kernel_id: str, code: str, *, timeout: float = 30):
        if not code.strip() or len(code) > 1_000_000 or not 0 < timeout <= 300:
            raise ValueError("Code and timeout are outside supported bounds")
        self._admit(kernel_id)
        session_id = uuid.uuid4().hex
        request = self._message(session_id, "execute_request", {
            "code": code, "silent": False, "store_history": True,
            "user_expressions": {}, "allow_stdin": False, "stop_on_error": True,
        })
        msg_id = request["header"]["msg_id"]
        status = "failed"
        messages, reply, idle, size = [], None, False, 0
        try:
            async with asyncio.timeout(timeout):
                async with self._session_ready().ws_connect(
                    self._websocket_url(kernel_id, session_id),
                    max_msg_size=self.max_output_bytes,
                ) as websocket:
                    await self._handshake(websocket, session_id)
                    await websocket.send_json(request)
                    while reply is None or not idle:
                        message = await self._receive(websocket)
                        if message.get("parent_header", {}).get("msg_id") != msg_id:
                            continue
                        size += len(json.dumps(message).encode())
                        if size > self.max_output_bytes:
                            raise GatewayError("Execution output limit exceeded", 413)
                        messages.append(message)
                        channel = message.get("channel")
                        kind = message.get("header", {}).get("msg_type")
                        if channel == "shell" and kind == "execute_reply":
                            reply = message["content"]
                        if channel == "iopub" and kind == "status":
                            idle = message.get("content", {}).get("execution_state") == "idle"
            status = reply["status"]
            return {"execution_id": msg_id, "kernel_id": kernel_id, "status": status,
                    "reply": reply, "messages": messages}
        except TimeoutError as exc:
            status = "timeout"
            await self._abort(kernel_id)
            raise GatewayTimeout() from exc
        except asyncio.CancelledError:
            status = "cancelled"
            await self._abort(kernel_id)
            raise
        except (aiohttp.ClientError, GatewayError, KeyError, TypeError) as exc:
            await self._abort(kernel_id)
            if isinstance(exc, GatewayError):
                raise
            raise GatewayError("Gateway execution failed") from exc
        finally:
            self.tracker.finish(msg_id, kernel_id, status)
            self._busy.discard(kernel_id)
