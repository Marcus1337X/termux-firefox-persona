"""A small asynchronous WebDriver BiDi client for Firefox.

Firefox exposes BiDi directly from its remote debugging port.  This module is
intentionally independent of geckodriver: it owns one WebSocket, correlates
commands by id, and leaves persona policy to callers.
"""

from __future__ import annotations

import asyncio
import inspect
import itertools
import json
import logging
from collections import defaultdict
from typing import Any, Callable, Mapping

from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)


class BiDiError(Exception):
    """Base class for BiDi client errors."""


class BiDiConnectionError(BiDiError, ConnectionError):
    """The WebSocket was not connected or was disconnected."""


class BiDiTimeoutError(BiDiError, TimeoutError):
    """A BiDi command did not receive a reply before its deadline."""


class BiDiProtocolError(BiDiError):
    """The browser sent malformed or otherwise invalid BiDi data."""


class BiDiCommandError(BiDiError):
    """The browser rejected a command."""

    def __init__(self, method: str, error: Any, message: str | None = None):
        self.method = method
        self.error = error
        self.message = message or str(error)
        super().__init__(f"BiDi command {method} failed: {self.message}")


class BiDiScriptError(BiDiError):
    """JavaScript evaluated by ``script.evaluate`` threw an exception."""

    def __init__(self, context: str, details: Any):
        self.context = context
        self.details = details
        message = details.get("text") if isinstance(details, Mapping) else None
        super().__init__(message or f"JavaScript evaluation failed in context {context}")


# Both names are useful to callers and keep the public error vocabulary clear.
BiDiJavaScriptError = BiDiScriptError
BiDiEvaluationError = BiDiScriptError


EventHandler = Callable[[Mapping[str, Any]], Any]


class BiDiClient:
    """Low-level asynchronous WebDriver BiDi client.

    ``port`` may be an integer or a complete WebSocket URL.  The URL form is
    useful for tests and for Firefox installations configured with a different
    loopback address; the normal Firefox endpoint is
    ``ws://127.0.0.1:<port>/session``.
    """

    def __init__(
        self,
        port: int | str | None = None,
        *,
        ws_url: str | None = None,
        timeout: float = 30.0,
        capabilities: Mapping[str, Any] | None = None,
        max_size: int | None = 50 * 1024 * 1024,
    ):
        if ws_url is not None and port is not None:
            raise ValueError("pass either port or ws_url, not both")
        if ws_url is None and isinstance(port, str) and "://" in port:
            ws_url = port
            port = None
        if ws_url is None:
            if port is None:
                raise ValueError("a Firefox BiDi port or ws_url is required")
            ws_url = f"ws://127.0.0.1:{int(port)}/session"

        self.ws_url = ws_url
        self.timeout = float(timeout)
        self.capabilities = dict(capabilities or {})
        self.max_size = max_size
        self._ws: Any = None
        self._listener_task: asyncio.Task | None = None
        self._id_counter = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._event_handlers: dict[str, list[EventHandler]] = defaultdict(list)
        self._event_tasks: set[asyncio.Task] = set()
        self._send_lock = asyncio.Lock()
        self._closed = True
        self._last_error: BaseException | None = None
        self.session_id: str | None = None
        self.session: Mapping[str, Any] | None = None

    @property
    def connected(self) -> bool:
        """Whether a live WebSocket is currently available."""

        return bool(self._ws is not None and not self._closed)

    async def connect(self) -> "BiDiClient":
        """Open the socket and create a WebDriver BiDi session."""

        if self.connected:
            return self
        try:
            kwargs: dict[str, Any] = {}
            if self.max_size is not None:
                kwargs["max_size"] = self.max_size
            # websockets 15+ honors HTTP(S)_PROXY by default, which can make
            # a loopback Firefox endpoint fail or leak outside the device.
            # websockets 14 has no ``proxy`` keyword, hence the signature
            # check rather than unconditionally passing it.
            try:
                if "proxy" in inspect.signature(ws_connect).parameters:
                    kwargs["proxy"] = None
            except (TypeError, ValueError):
                pass
            self._ws = await ws_connect(self.ws_url, **kwargs)
        except Exception as exc:
            self._ws = None
            self._last_error = exc
            raise BiDiConnectionError(f"could not connect to {self.ws_url}: {exc}") from exc

        self._closed = False
        self._last_error = None
        self._listener_task = asyncio.create_task(self._listen(), name="bidi-listener")
        try:
            result = await self.send(
                "session.new", {"capabilities": self.capabilities}
            )
        except Exception:
            await self.close()
            raise
        self.session = result if isinstance(result, Mapping) else {"value": result}
        if isinstance(result, Mapping):
            session_id = result.get("sessionId")
            if isinstance(session_id, str):
                self.session_id = session_id
        return self

    async def _listen(self) -> None:
        """Route command responses and events until the socket closes."""

        try:
            async for raw in self._ws:
                try:
                    message = json.loads(raw)
                except (TypeError, ValueError) as exc:
                    logger.debug("Ignoring malformed BiDi message: %s", exc)
                    continue
                if not isinstance(message, Mapping):
                    continue
                msg_id = message.get("id")
                if msg_id is not None:
                    future = self._pending.pop(msg_id, None)
                    if future is not None and not future.done():
                        future.set_result(dict(message))
                    continue
                method = message.get("method")
                if method:
                    self._dispatch_event(str(method), message.get("params") or {})
        except asyncio.CancelledError:
            raise
        except ConnectionClosed as exc:
            self._last_error = exc
            logger.debug("BiDi WebSocket closed: %s", exc)
        except Exception as exc:
            self._last_error = exc
            logger.debug("BiDi listener stopped: %s", exc)
        finally:
            self._closed = True
            self._reject_pending(
                BiDiConnectionError(
                    "BiDi WebSocket disconnected"
                    + (f": {self._last_error}" if self._last_error else "")
                )
            )

    def _reject_pending(self, error: BaseException) -> None:
        pending, self._pending = self._pending, {}
        for future in pending.values():
            if not future.done():
                future.set_exception(error)

    def _dispatch_event(self, method: str, params: Mapping[str, Any]) -> None:
        for handler in tuple(self._event_handlers.get(method, ())):
            try:
                returned = handler(params)
                if inspect.isawaitable(returned):
                    task = asyncio.create_task(returned)
                    self._event_tasks.add(task)
                    task.add_done_callback(self._event_tasks.discard)
            except Exception:
                logger.exception("BiDi event handler failed for %s", method)

    async def send(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Send one command and return its unwrapped ``result`` object."""

        if not self.connected:
            detail = f": {self._last_error}" if self._last_error else ""
            raise BiDiConnectionError(f"BiDi WebSocket is not connected{detail}")

        msg_id = next(self._id_counter)
        payload: dict[str, Any] = {"id": msg_id, "method": method}
        if params is not None:
            payload["params"] = dict(params)
        future = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future
        try:
            async with self._send_lock:
                await self._ws.send(json.dumps(payload, separators=(",", ":")))
        except Exception as exc:
            self._pending.pop(msg_id, None)
            if isinstance(exc, BiDiError):
                raise
            raise BiDiConnectionError(f"failed to send {method}: {exc}") from exc

        deadline = self.timeout if timeout is None else float(timeout)
        try:
            response = await asyncio.wait_for(asyncio.shield(future), deadline)
        except asyncio.TimeoutError as exc:
            self._pending.pop(msg_id, None)
            raise BiDiTimeoutError(
                f"BiDi command {method} timed out after {deadline:g}s"
            ) from exc
        except asyncio.CancelledError:
            self._pending.pop(msg_id, None)
            raise

        if not isinstance(response, Mapping):
            raise BiDiProtocolError(f"invalid response for {method}: {response!r}")
        if response.get("type") == "error" or "error" in response:
            error = response.get("error", "unknown error")
            raise BiDiCommandError(method, error, response.get("message"))
        if "result" not in response:
            raise BiDiProtocolError(f"response for {method} has no result")
        return response["result"]

    def on(self, event: str, handler: EventHandler) -> EventHandler:
        """Register a callback for a BiDi event method."""

        if not callable(handler):
            raise TypeError("event handler must be callable")
        self._event_handlers[event].append(handler)
        return handler

    subscribe = on

    def off(self, event: str, handler: EventHandler) -> None:
        """Remove a previously registered event callback."""

        handlers = self._event_handlers.get(event)
        if not handlers:
            return
        try:
            handlers.remove(handler)
        except ValueError:
            pass
        if not handlers:
            self._event_handlers.pop(event, None)

    async def evaluate(self, context: str, expression: str, timeout: float | None = None) -> Any:
        """Evaluate JavaScript and decode the JSON.stringify result.

        Wrapping the expression in ``JSON.stringify`` means ordinary objects,
        arrays and primitive values arrive as one stable JSON value instead of
        requiring callers to understand BiDi's remote-value representation.
        """

        if not isinstance(expression, str):
            raise TypeError("expression must be a JavaScript string")
        # Awaiting inside the wrapper also makes evaluate useful for a Promise
        # expression while retaining one JSON string on the wire.
        wrapped = f"(async()=>JSON.stringify(await ({expression})))()"
        result = await self.send(
            "script.evaluate",
            {
                "expression": wrapped,
                "target": {"context": context},
                "awaitPromise": True,
                "resultOwnership": "root",
            },
            timeout=timeout,
        )
        value = result
        if isinstance(result, Mapping) and result.get("type") == "exception":
            raise BiDiScriptError(context, result.get("exceptionDetails", result))
        if isinstance(result, Mapping) and result.get("type") == "success":
            value = result.get("result")
        if isinstance(value, Mapping) and value.get("type") == "exception":
            raise BiDiScriptError(context, value.get("exceptionDetails", value))
        value = self._remote_value(value)
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (TypeError, ValueError):
                return value
        return value

    @staticmethod
    def _remote_value(value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        remote_type = value.get("type")
        if "value" in value and remote_type != "object":
            return value["value"]
        if remote_type in {"null", "undefined"}:
            return None
        return value

    async def get_tree(
        self,
        *,
        root: str | None = None,
        max_depth: int | None = None,
        timeout: float | None = None,
    ) -> Any:
        params: dict[str, Any] = {}
        if root is not None:
            params["root"] = root
        if max_depth is not None:
            params["maxDepth"] = max_depth
        return await self.send("browsingContext.getTree", params, timeout=timeout)

    async def create(
        self,
        type: str = "tab",
        *,
        reference_context: str | None = None,
        background: bool | None = None,
        user_context: str | None = None,
        timeout: float | None = None,
    ) -> Any:
        params: dict[str, Any] = {"type": type}
        if reference_context is not None:
            params["referenceContext"] = reference_context
        if background is not None:
            params["background"] = background
        if user_context is not None:
            params["userContext"] = user_context
        return await self.send("browsingContext.create", params, timeout=timeout)

    async def navigate(
        self,
        context: str,
        url: str,
        *,
        wait: str = "complete",
        timeout: float | None = None,
    ) -> Any:
        return await self.send(
            "browsingContext.navigate",
            {"context": context, "url": url, "wait": wait},
            timeout=timeout,
        )

    async def close(self) -> None:
        """Close the WebSocket and reject/cancel all outstanding work."""

        self._closed = True
        self._reject_pending(BiDiConnectionError("BiDi client closed"))
        listener = self._listener_task
        self._listener_task = None
        if listener is not None and listener is not asyncio.current_task():
            listener.cancel()
            try:
                await listener
            except asyncio.CancelledError:
                pass
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                logger.debug("error closing BiDi WebSocket", exc_info=True)
        tasks, self._event_tasks = self._event_tasks, set()
        for task in tasks:
            if not task.done():
                task.cancel()
        self._event_handlers.clear()
        self.session_id = None
        self.session = None

    async def __aenter__(self) -> "BiDiClient":
        return await self.connect()

    async def __aexit__(self, *_exc: Any) -> None:
        await self.close()
