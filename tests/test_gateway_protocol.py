"""Protocol regressions without processes, network access, or Python execution."""
import asyncio
import json
from collections import deque
from types import SimpleNamespace

import aiohttp
import pytest

from kernel.gateway_client import GatewayClient, GatewayError, GatewayTimeout


def message(parent, channel, kind, content):
    return {"parent_header": {"msg_id": parent}, "channel": channel,
            "header": {"msg_type": kind}, "content": content, "metadata": {}}


class FakeWebSocket:
    def __init__(self, order):
        self.frames = deque()
        self.order = order
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.closed = True

    async def send_json(self, request):
        parent = request["header"]["msg_id"]
        if request["header"]["msg_type"] == "kernel_info_request":
            self.frames.append(message(parent, "shell", "kernel_info_reply", {"status": "ok"}))
        else:
            for kind in self.order:
                if kind == "foreign_idle":
                    self.frames.append(message("foreign", "iopub", "status", {"execution_state": "idle"}))
                elif kind == "foreign_output":
                    self.frames.append(message("foreign", "iopub", "stream", {"text": "foreign"}))
                elif kind == "reply":
                    self.frames.append(message(parent, "shell", "execute_reply", {"status": "ok"}))
                elif kind == "wrong_channel_reply":
                    self.frames.append(message(parent, "iopub", "execute_reply", {"status": "ok"}))
                elif kind == "idle":
                    self.frames.append(message(parent, "iopub", "status", {"execution_state": "idle"}))
                elif kind == "output":
                    self.frames.append(message(parent, "iopub", "stream", {"name": "stdout", "text": "ours"}))
                elif kind == "oversize":
                    self.frames.append(message(parent, "iopub", "stream", {"text": "x" * 5000}))

    async def receive(self):
        if not self.frames:
            await asyncio.Event().wait()
        return SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps(self.frames.popleft()))


def fake_client(order):
    client = GatewayClient("http://127.0.0.1:8888", "test-token", max_output_bytes=2000)
    websocket = FakeWebSocket(order)
    client._session = SimpleNamespace(ws_connect=lambda *args, **kwargs: websocket)
    client._owned.add("kernel")
    calls = []

    async def request(method, path, **kwargs):
        calls.append((method, path))
    client._request = request
    return client, websocket, calls


@pytest.mark.parametrize("order", [
    ["foreign_idle", "foreign_output", "output", "reply", "idle"],
    ["foreign_idle", "output", "idle", "reply"],
])
async def test_correlation_and_both_completion_orders(order):
    client, websocket, _ = fake_client(order)
    result = await client.execute("kernel", "trusted placeholder", timeout=1)
    assert result["status"] == "ok"
    assert len(result["messages"]) == 3
    assert all(m["parent_header"]["msg_id"] == result["execution_id"] for m in result["messages"])
    assert websocket.closed


@pytest.mark.parametrize("order", [["reply"], ["idle"], ["wrong_channel_reply", "idle"], ["foreign_idle"]])
async def test_partial_or_foreign_completion_times_out_and_deletes(order):
    client, websocket, calls = fake_client(order)
    with pytest.raises(GatewayTimeout):
        await client.execute("kernel", "trusted placeholder", timeout=0.02)
    assert calls == [("DELETE", "/api/kernels/kernel")]
    assert "kernel" not in client._owned
    assert websocket.closed
    assert client.tracker.snapshot()[-1]["status"] == "timeout"


async def test_output_budget_deletes_without_reporting_success():
    client, websocket, calls = fake_client(["oversize", "reply", "idle"])
    with pytest.raises(GatewayError) as exc:
        await client.execute("kernel", "trusted placeholder")
    assert exc.value.status_code == 413
    assert calls == [("DELETE", "/api/kernels/kernel")]
    assert websocket.closed


@pytest.mark.parametrize("url,token", [("http://localhost:8888", ""),
    ("file:///tmp/gateway", "token"), ("http://localhost:8888/?token=secret", "token")])
def test_invalid_or_unauthenticated_configuration_rejected(url, token):
    with pytest.raises(ValueError):
        GatewayClient(url, token)


@pytest.mark.parametrize('models', [
    [{'id': 'safe', 'name': 'python3', 'execution_state': 'busy'}],
    [{'id': '../foreign', 'name': 'python3', 'execution_state': 'idle'}],
    [{'id': 'safe', 'name': 'other', 'execution_state': 'idle'}],
    [{'id': 'same', 'name': 'python3', 'execution_state': 'idle'}] * 2,
    {'id': 'invalid'},
])
async def test_recovery_rejects_ambiguous_kernels_without_deleting(models):
    client = GatewayClient('http://127.0.0.1:8888', 'test-token')
    calls = []
    async def request(method, path, **kwargs):
        calls.append((method, path))
        return models
    client._request = request
    with pytest.raises(GatewayError): await client.recover_kernels()
    assert not client._owned and calls == [('GET', '/api/kernels')]


async def test_recovery_handshake_failure_does_not_delete_surviving_kernel():
    client = GatewayClient('http://127.0.0.1:8888', 'test-token')
    calls = []
    async def request(method, path, **kwargs):
        calls.append((method, path))
        return [{'id': 'safe', 'name': 'python3', 'execution_state': 'idle'}]
    async def fail(identity): raise GatewayError('handshake failed')
    client._request = request
    client.wait_ready = fail
    with pytest.raises(GatewayError): await client.recover_kernels()
    assert not client._owned and calls == [('GET', '/api/kernels')]


async def test_recovery_and_disconnect_preserve_surviving_kernel():
    client = GatewayClient('http://127.0.0.1:8888', 'test-token')
    ready = []; closed = []
    async def request(method, path, **kwargs):
        assert method == 'GET'
        return [{'id': 'safe', 'name': 'python3', 'execution_state': 'idle'}]
    async def handshake(identity): ready.append(identity)
    async def close(): closed.append(True)
    client._request = request
    client.wait_ready = handshake
    client._session = SimpleNamespace(close=close)
    await client.recover_kernels()
    assert client._owned == {'safe'} and ready == ['safe']
    await client.disconnect()
    assert client._closing and client._session is None and closed == [True]


async def test_initial_kernel_is_rechecked_and_delivered_once():
    client = GatewayClient('http://127.0.0.1:8888', 'test-token')
    client._owned.add('initial')
    consumed = []
    checked = []
    model = {'id':'initial','name':'python3'}
    async def ready(identity): checked.append(identity)
    async def request(method,path,**kwargs):
        assert method == 'GET'
        return model
    client.wait_ready = ready
    client._request = request
    client.offer_initial_kernel(model, lambda: consumed.append(True))
    assert (await client.create_kernel())['id'] == 'initial'
    assert checked == ['initial'] and consumed == [True] and client._initial_kernel is None


async def test_dead_initial_kernel_is_never_reported_ready():
    client = GatewayClient('http://127.0.0.1:8888', 'test-token')
    client._owned.add('initial')
    consumed = []; deleted = []
    async def ready(identity): raise GatewayError('dead kernel')
    async def request(method,path,**kwargs): deleted.append((method,path))
    client.wait_ready = ready
    client._request = request
    client.offer_initial_kernel({'id':'initial','name':'python3'}, lambda: consumed.append(True))
    with pytest.raises(GatewayError): await client.create_kernel()
    assert consumed == [True] and deleted == [('DELETE','/api/kernels/initial')]
    assert not client._owned and client._initial_kernel is None
