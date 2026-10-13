import asyncio
from contextlib import asynccontextmanager
import time
import uuid

import aiohttp
import httpx
import psutil
import pytest

from kernel.gateway_client import GatewayError, GatewayTimeout
from main import create_app

pytestmark = pytest.mark.integration


def stdout(result):
    return "".join(m["content"]["text"] for m in result["messages"]
                   if m["header"]["msg_type"] == "stream" and m["content"]["name"] == "stdout")


def completed(result):
    messages = result["messages"]
    assert messages
    assert all(m["parent_header"]["msg_id"] == result["execution_id"] for m in messages)
    assert any(m["channel"] == "shell" and m["header"]["msg_type"] == "execute_reply" for m in messages)
    assert any(m["channel"] == "iopub" and m["header"]["msg_type"] == "status"
               and m["content"]["execution_state"] == "idle" for m in messages)


async def kernel_pid(client, kernel_id):
    return int(stdout(await client.execute(kernel_id, "import os; print(os.getpid())")).strip())


async def gone(process):
    for _ in range(50):
        if not process.is_running():
            return
        await asyncio.sleep(0.05)
    pytest.fail(f"Kernel process {process.pid} remains after termination")


@asynccontextmanager
async def running_execution(client, kernel_id, code):
    # REST execution_state is asynchronously observed metadata, not a start barrier.
    # Subscribe first and wait for the standard execute_input message instead.
    session = uuid.uuid4().hex
    async with client._session_ready().ws_connect(client._websocket_url(kernel_id, session)) as observer:
        await client._handshake(observer, session)
        execution = asyncio.create_task(client.execute(kernel_id, code, timeout=15))
        try:
            async with asyncio.timeout(5):
                while True:
                    msg = await client._receive(observer)
                    if (msg.get("channel") == "iopub"
                            and msg["header"].get("msg_type") == "execute_input"
                            and msg["content"].get("code") == code):
                        break
            yield execution
        finally:
            if not execution.done():
                execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)


async def test_startup_authentication_and_readiness(local_gateway, gateway_client):
    assert local_gateway.startup_seconds < 30
    async with aiohttp.ClientSession() as http:
        for headers in [{}, {"Authorization": "token incorrect"}]:
            async with http.get(f"{local_gateway.url}/api/kernelspecs", headers=headers) as response:
                assert response.status in {401, 403}
    assert "python3" in (await gateway_client.health())["kernelspecs"]
    started = time.monotonic()
    model = await gateway_client.create_kernel()
    assert (await gateway_client.wait_ready(model["id"]))["language_info"]["name"] == "python"
    assert (await gateway_client.inspect_kernel(model["id"]))["id"] == model["id"]
    print(f"kernel creation and readiness: {time.monotonic() - started:.3f}s")
    async with aiohttp.ClientSession(headers={"Authorization": "token incorrect"}) as http:
        with pytest.raises(aiohttp.WSServerHandshakeError) as exc:
            await http.ws_connect(gateway_client._websocket_url(model["id"], uuid.uuid4().hex))
        assert exc.value.status in {401, 403}
    result = await gateway_client.execute(model["id"],
        "import os, sys\nprint('KG_AUTH_TOKEN' in os.environ)\nprint(sys.executable)")
    assert stdout(result).splitlines()[0] == "False"
    assert stdout(result).splitlines()[1].endswith("/.venv/bin/python")


async def test_stdout_persistence_sequential_and_completion(gateway_client):
    kernel = (await gateway_client.create_kernel())["id"]
    for code, expected in [("value = 40; print('hello')", "hello\n"),
                           ("value += 2; print(value)", "42\n"),
                           ("print(value + 1)", "43\n")]:
        result = await gateway_client.execute(kernel, code)
        assert result["status"] == "ok"
        assert stdout(result) == expected
        completed(result)


async def test_python_error_reporting(gateway_client):
    kernel = (await gateway_client.create_kernel())["id"]
    result = await gateway_client.execute(kernel, "raise ValueError('trusted test error')")
    assert result["status"] == "error"
    assert result["reply"]["ename"] == "ValueError"
    assert any(m["header"]["msg_type"] == "error" for m in result["messages"])
    completed(result)
    assert stdout(await gateway_client.execute(kernel, "print('recovered')")) == "recovered\n"


async def test_rich_mime_outputs(gateway_client):
    kernel = (await gateway_client.create_kernel())["id"]
    result = await gateway_client.execute(kernel,
        "from IPython.display import display\n"
        "display({'text/plain': 'plain', 'text/html': '<b>rich</b>', 'application/json': {'n': 7}}, raw=True)\n"
        "6 * 7")
    display = next(m["content"]["data"] for m in result["messages"] if m["header"]["msg_type"] == "display_data")
    assert display == {"text/plain": "plain", "text/html": "<b>rich</b>", "application/json": {"n": 7}}
    assert any(m["header"]["msg_type"] == "execute_result" for m in result["messages"])
    completed(result)


async def test_interrupt_via_rest(gateway_client):
    kernel = (await gateway_client.create_kernel())["id"]
    async with running_execution(gateway_client, kernel, "import time; time.sleep(8)") as execution:
        await gateway_client.interrupt_kernel(kernel)
        result = await asyncio.wait_for(execution, 5)
        assert result["status"] == "error"
        assert result["reply"]["ename"] == "KeyboardInterrupt"
        completed(result)
    assert stdout(await gateway_client.execute(kernel, "print('usable')")) == "usable\n"


async def test_restart_resets_state_and_reaps_old_process(gateway_client, local_gateway):
    kernel = (await gateway_client.create_kernel())["id"]
    await gateway_client.execute(kernel, "saved = 99")
    old = psutil.Process(await kernel_pid(gateway_client, kernel))
    local_gateway.capture_children()
    model = await gateway_client.restart_kernel(kernel)
    assert model["id"] == kernel
    await gone(old)
    assert await kernel_pid(gateway_client, kernel) != old.pid
    assert stdout(await gateway_client.execute(kernel, "print('saved' in globals())")) == "False\n"


async def test_termination_reaps_kernel_and_removes_model(gateway_client, local_gateway):
    kernel = (await gateway_client.create_kernel())["id"]
    process = psutil.Process(await kernel_pid(gateway_client, kernel))
    local_gateway.capture_children()
    await gateway_client.terminate_kernel(kernel)
    await gone(process)
    with pytest.raises(GatewayError) as exc:
        await gateway_client._request("GET", f"/api/kernels/{kernel}")
    assert exc.value.status_code == 404


async def test_timeout_terminates_kernel(gateway_client, local_gateway):
    kernel = (await gateway_client.create_kernel())["id"]
    process = psutil.Process(await kernel_pid(gateway_client, kernel))
    local_gateway.capture_children()
    with pytest.raises(GatewayTimeout):
        await gateway_client.execute(kernel, "import time; time.sleep(3)", timeout=0.4)
    await gone(process)
    assert gateway_client.tracker.snapshot()[-1]["status"] == "timeout"


async def test_cancel_terminates_kernel(gateway_client, local_gateway):
    kernel = (await gateway_client.create_kernel())["id"]
    process = psutil.Process(await kernel_pid(gateway_client, kernel))
    local_gateway.capture_children()
    async with running_execution(gateway_client, kernel, "import time; time.sleep(3)") as execution:
        execution.cancel()
        with pytest.raises(asyncio.CancelledError):
            await execution
        await gone(process)
        assert gateway_client.tracker.snapshot()[-1]["status"] == "cancelled"


async def test_busy_conflict_and_bounded_validation(gateway_client):
    kernel = (await gateway_client.create_kernel())["id"]
    with pytest.raises(ValueError):
        await gateway_client.execute(kernel, "print(1)", timeout=-1)
    async with running_execution(gateway_client, kernel, "import time; time.sleep(0.5)") as execution:
        for operation in [lambda: gateway_client.execute(kernel, "print(2)"),
                          lambda: gateway_client.restart_kernel(kernel)]:
            with pytest.raises(GatewayError) as exc:
                await operation()
            assert exc.value.status_code == 409
        completed(await execution)


async def test_foreign_client_messages_are_not_returned(gateway_client):
    kernel = (await gateway_client.create_kernel())["id"]
    session = uuid.uuid4().hex
    async with gateway_client._session_ready().ws_connect(gateway_client._websocket_url(kernel, session)) as peer:
        await gateway_client._handshake(peer, session)
        foreign = gateway_client._message(session, "execute_request", {
            "code": "import time; time.sleep(0.2); print('foreign')", "silent": False,
            "store_history": False, "allow_stdin": False, "user_expressions": {}, "stop_on_error": True,
        })
        await peer.send_json(foreign)
        result = await gateway_client.execute(kernel, "print('ours')")
        assert stdout(result) == "ours\n"
        completed(result)
        assert all(m["parent_header"]["msg_id"] != foreign["header"]["msg_id"] for m in result["messages"])


async def test_unknown_kernel_and_startup_failure_are_controlled(gateway_client):
    with pytest.raises(GatewayError) as exc:
        await gateway_client.inspect_kernel("unknown")
    assert exc.value.status_code == 404
    with pytest.raises(GatewayError):
        await gateway_client.create_kernel("nonexistent-trusted-test-spec")
    assert await gateway_client._request("GET", "/api/kernels") == []


async def test_fastapi_end_to_end_and_lifespan_cleanup(local_gateway):
    app = create_app(gateway_url=local_gateway.url, gateway_token=local_gateway.token)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://europa") as http:
            assert (await http.get("/api/health")).status_code == 200
            response = await http.post("/api/kernel/start")
            assert response.status_code == 201
            kernel = response.json()["id"]
            params = {"kernel_id": kernel}
            assert (await http.get("/api/kernel/info", params=params)).status_code == 200
            response = await http.post("/api/kernel/execute", params=params, json={"code": "value = 21; print(value * 2)"})
            assert response.status_code == 200 and stdout(response.json()) == "42\n"
            completed(response.json())
            assert (await http.post("/api/kernel/execute", params={**params, "timeout": 0}, json={"code": "print(1)"})).status_code == 422
            assert (await http.post("/api/kernel/restart", params=params)).status_code == 200
            result = (await http.post("/api/kernel/execute", params=params, json={"code": "print('value' in globals())"})).json()
            assert stdout(result) == "False\n"
            assert (await http.post("/api/kernel/shutdown", params=params)).status_code == 204
            # A remaining owned kernel must also be deleted by application lifespan.
            kernel = (await http.post("/api/kernel/start")).json()["id"]
            process = psutil.Process(await kernel_pid(app.state.gateway, kernel))
            local_gateway.capture_children()
    await gone(process)


async def test_gateway_process_loss_is_controlled(gateway_client, local_gateway):
    kernel = (await gateway_client.create_kernel())["id"]
    process = psutil.Process(await kernel_pid(gateway_client, kernel))
    local_gateway.capture_children()
    # The test fixture owns this subprocess. Kernel termination remains Gateway's job.
    local_gateway.process.terminate()
    await asyncio.to_thread(local_gateway.process.wait, timeout=10)
    with pytest.raises(GatewayError):
        await gateway_client.execute(kernel, "print('trusted')", timeout=2)
    await gone(process)
