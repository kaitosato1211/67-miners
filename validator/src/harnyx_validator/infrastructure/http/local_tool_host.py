"""Short-lived local tool-host server for sandboxed miner execution."""

from __future__ import annotations

import asyncio
import contextlib
import socket
from dataclasses import dataclass

from fastapi import FastAPI
from uvicorn import Config, Server

from harnyx_commons.tools.executor import ToolExecutor
from harnyx_commons.tools.token_semaphore import ToolConcurrencyLimiter
from harnyx_validator.infrastructure.http.routes import ToolRouteDeps, add_tool_routes

_DEFAULT_HOST = "0.0.0.0"  # noqa: S104 - sandbox gateway must reach the host callback
_DEFAULT_CONTAINER_HOST = "host.docker.internal"
_STARTUP_TIMEOUT_SECONDS = 10.0


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass(slots=True)
class LocalToolHostHandle:
    server: Server
    serve_task: asyncio.Task[None]
    port: int
    host_container_url: str

    async def aclose(self) -> None:
        self.server.should_exit = True
        try:
            await asyncio.wait_for(self.serve_task, timeout=_STARTUP_TIMEOUT_SECONDS)
        except TimeoutError as err:
            raise RuntimeError("local tool host did not stop cleanly") from err
        except asyncio.CancelledError:  # pragma: no cover - defensive
            raise RuntimeError("local tool host was cancelled during shutdown") from None


async def start_local_tool_host(
    *,
    tool_executor: ToolExecutor,
    tool_concurrency_limiter: ToolConcurrencyLimiter,
    host: str = _DEFAULT_HOST,
    container_host: str = _DEFAULT_CONTAINER_HOST,
) -> LocalToolHostHandle:
    port = _find_free_port()
    deps = ToolRouteDeps(
        tool_executor=tool_executor,
        tool_concurrency_limiter=tool_concurrency_limiter,
    )
    app = FastAPI(title="Harnyx Local Tool Host", version="0.1.0")
    add_tool_routes(app, lambda: deps)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    config = Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        log_config=None,
        timeout_graceful_shutdown=1,
    )
    server = Server(config)
    serve_task = asyncio.create_task(server.serve(), name="harnyx-local-tool-host")

    deadline = asyncio.get_running_loop().time() + _STARTUP_TIMEOUT_SECONDS
    try:
        while not server.started:
            if serve_task.done():
                try:
                    await serve_task
                except Exception as exc:  # pragma: no cover - startup failure
                    raise RuntimeError("local tool host exited before startup completed") from exc
                raise RuntimeError("local tool host exited before startup completed")
            if asyncio.get_running_loop().time() >= deadline:
                server.should_exit = True
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(serve_task, timeout=1.0)
                raise RuntimeError("local tool host did not start in time")
            await asyncio.sleep(0.05)
    except asyncio.CancelledError:
        server.should_exit = True
        serve_task.cancel()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(serve_task, timeout=1.0)
        raise

    return LocalToolHostHandle(
        server=server,
        serve_task=serve_task,
        port=port,
        host_container_url=f"http://{container_host}:{port}",
    )


__all__ = ["LocalToolHostHandle", "start_local_tool_host"]
