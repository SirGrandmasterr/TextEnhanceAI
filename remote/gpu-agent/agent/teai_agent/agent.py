"""The agent: keeps an outbound WebSocket to the relay and serves its requests."""

import asyncio
import json
import logging
import platform
import random
import socket
import time

import aiohttp
from aiohttp import WSMsgType, web

from . import __version__, protocol
from .vllm_client import VLLMClient, VLLMError

log = logging.getLogger("teai_agent")


class RelayGone(Exception):
    """The relay socket is closed; frames cannot be sent."""


class Agent:
    """Bridge between the relay (outbound WebSocket) and the local vLLM server."""

    def __init__(self, config, session=None):
        self.config = config
        self.session = session
        self.vllm = None
        self.state = protocol.STATE_LOADING
        self.models = []
        self.vllm_meta = {}
        self.ever_ready = False
        self.ws = None
        self.relay_connected = False
        self.relay_info = {}
        self.last_relay_error = ""
        self.reconnect_attempt = 0
        self.connected_since = None
        self.in_flight = {}
        self.requests_total = 0
        self.requests_failed = 0
        self.requests_cancelled = 0
        self.started_at = time.time()
        self._send_lock = None
        self._stop = None
        self._owns_session = session is None

    # ---------------------------------------------------------------- lifecycle
    async def run(self):
        """Run until ``request_stop`` is called (or the task is cancelled)."""
        self._send_lock = asyncio.Lock()
        self._stop = asyncio.Event()
        if self.session is None:
            self.session = aiohttp.ClientSession()
        self.vllm = VLLMClient(
            self.session,
            self.config.vllm_base_url,
            api_key=self.config.vllm_api_key,
            request_timeout=self.config.request_timeout,
        )
        health_runner = await self._start_health_server()
        monitor = asyncio.ensure_future(self._monitor_vllm())
        try:
            await self._connection_loop()
        finally:
            monitor.cancel()
            try:
                await monitor
            except (asyncio.CancelledError, Exception):
                pass
            if health_runner is not None:
                await health_runner.cleanup()
            if self._owns_session:
                await self.session.close()

    def request_stop(self):
        """Ask the agent to shut down gracefully (signal handler friendly)."""
        if self._stop is not None:
            self._stop.set()
        if self.ws is not None and not self.ws.closed:
            asyncio.ensure_future(self.ws.close(code=1001, message=b"agent shutting down"))

    async def _sleep_unless_stopped(self, seconds):
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    # ------------------------------------------------------------ vLLM monitor
    async def _monitor_vllm(self):
        announced_waiting = False
        while not self._stop.is_set():
            ok, models, meta = await self.vllm.probe()
            if ok and models:
                state = protocol.STATE_READY
                self.ever_ready = True
            elif self.ever_ready:
                state = protocol.STATE_UNAVAILABLE
            else:
                state = protocol.STATE_LOADING
            changed = (state, models) != (self.state, self.models)
            self.state, self.models = state, models
            self.vllm_meta.update(meta)
            if changed:
                if state == protocol.STATE_READY:
                    log.info("vLLM ready at %s with models %s", self.config.vllm_base_url, [m["id"] for m in models])
                else:
                    log.warning("vLLM at %s is %s: %s", self.config.vllm_base_url, state, meta.get("vllm_error", ""))
                announced_waiting = True
                if self.relay_connected:
                    try:
                        await self._send_models()
                    except RelayGone:
                        pass
            elif state != protocol.STATE_READY and not announced_waiting:
                log.info("waiting for vLLM at %s to finish loading...", self.config.vllm_base_url)
                announced_waiting = True
            interval = (
                self.config.poll_interval_ready
                if state == protocol.STATE_READY
                else self.config.poll_interval_loading
            )
            await self._sleep_unless_stopped(interval)

    # ---------------------------------------------------------- relay socket
    async def _connection_loop(self):
        while not self._stop.is_set():
            try:
                await self._serve_once()
                self.last_relay_error = ""
            except asyncio.CancelledError:
                raise
            except aiohttp.WSServerHandshakeError as exc:
                self.last_relay_error = "handshake rejected: HTTP {0} {1}".format(exc.status, exc.message)
                if exc.status in (401, 403):
                    log.error("relay rejected the agent key (HTTP %s). Check RELAY_AGENT_KEY.", exc.status)
                else:
                    log.error("relay handshake failed: HTTP %s %s", exc.status, exc.message)
            except (aiohttp.ClientError, OSError, asyncio.TimeoutError, RelayGone) as exc:
                self.last_relay_error = str(exc) or exc.__class__.__name__
                log.warning("relay connection lost: %s", self.last_relay_error)
            except Exception as exc:  # keep the loop alive no matter what
                self.last_relay_error = repr(exc)
                log.exception("unexpected relay error")
            if self._stop.is_set():
                break
            delay = min(self.config.reconnect_max_delay, 1.0 * (2 ** min(self.reconnect_attempt, 8)))
            delay += random.uniform(0, 1.0)
            self.reconnect_attempt += 1
            log.info("reconnecting to relay in %.0fs (attempt %d)", delay, self.reconnect_attempt)
            await self._sleep_unless_stopped(delay)

    def _hello_frame(self):
        return {
            "type": protocol.HELLO,
            "protocol": protocol.PROTOCOL_VERSION,
            "agent": self.config.agent_name,
            "agent_version": __version__,
            "state": self.state,
            "models": self.models,
            "max_concurrency": self.config.max_concurrency,
            "meta": self._meta(),
        }

    def _meta(self):
        meta = {
            "hostname": socket.gethostname(),
            "python": platform.python_version(),
            "vllm_base_url": self.config.vllm_base_url,
            "started_at": self.started_at,
        }
        meta.update({k: v for k, v in self.vllm_meta.items() if k != "vllm_error"})
        return meta

    async def _serve_once(self):
        headers = {"Authorization": "Bearer " + self.config.relay_key}
        options = {
            "headers": headers,
            "heartbeat": self.config.ws_heartbeat,
            "max_msg_size": self.config.max_ws_message_bytes,
        }
        if not self.config.tls_verify:
            options["ssl"] = False  # self-signed relay certificate (testing only)
        log.info("connecting to relay %s as %s", self.config.relay_ws_url, self.config.agent_name)
        async with self.session.ws_connect(self.config.relay_ws_url, **options) as ws:
            self.ws = ws
            try:
                await self._send(self._hello_frame())
                welcome = await ws.receive(timeout=30)
                frame = _parse_frame(welcome)
                if frame is None or frame.get("type") != protocol.WELCOME:
                    reason = _close_reason(welcome)
                    raise RelayGone("relay did not send a welcome ({0})".format(reason))
                self.relay_info = {
                    "relay_version": frame.get("relay_version"),
                    "protocol": frame.get("protocol"),
                }
                self.relay_connected = True
                self.connected_since = time.time()
                self.reconnect_attempt = 0
                log.info(
                    "connected to relay (relay version %s); state=%s models=%s",
                    frame.get("relay_version"), self.state, [m["id"] for m in self.models],
                )
                async for msg in ws:
                    frame = _parse_frame(msg)
                    if frame is None:
                        if msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED, WSMsgType.ERROR):
                            break
                        continue
                    await self._handle_frame(frame)
                reason = "closed by relay (code {0})".format(ws.close_code) if ws.close_code else "socket closed"
                if not self._stop.is_set():
                    raise RelayGone(reason)
            finally:
                self.relay_connected = False
                self.ws = None
                await self._abort_in_flight("relay connection lost")

    async def _abort_in_flight(self, reason):
        tasks = list(self.in_flight.values())
        if not tasks:
            return
        log.info("aborting %d in-flight request(s): %s", len(tasks), reason)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _send(self, frame):
        ws = self.ws
        if ws is None or ws.closed:
            raise RelayGone("relay socket is closed")
        try:
            async with self._send_lock:
                await ws.send_str(json.dumps(frame))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise RelayGone(str(exc) or exc.__class__.__name__) from exc

    async def _send_models(self):
        await self._send({"type": protocol.MODELS, "state": self.state, "models": self.models})

    async def _handle_frame(self, frame):
        kind = frame.get("type")
        if kind == protocol.REQUEST:
            request_id = frame.get("request_id")
            if not isinstance(request_id, str) or request_id in self.in_flight:
                return
            task = asyncio.ensure_future(self._handle_request(frame))
            self.in_flight[request_id] = task
        elif kind == protocol.CANCEL:
            task = self.in_flight.get(frame.get("request_id"))
            if task is not None:
                log.info("request %s cancelled by relay", frame.get("request_id"))
                task.cancel()
        elif kind == protocol.WELCOME:
            pass
        else:
            log.debug("ignoring frame type %r", kind)

    async def _handle_request(self, frame):
        request_id = frame["request_id"]
        path = frame.get("path", "")
        body = frame.get("body")
        client = frame.get("client", "?")
        started = time.time()
        self.requests_total += 1
        outcome = "ok"
        chunks = 0
        try:
            if not isinstance(body, dict):
                raise VLLMError(400, "request body must be a JSON object")
            if path not in protocol.FORWARDABLE_PATHS:
                raise VLLMError(404, "path {0} is not forwarded by this agent".format(path))
            if self.state != protocol.STATE_READY:
                raise VLLMError(503, "vLLM is not ready on this GPU server ({0})".format(self.state))
            log.info(
                "request %s client=%s model=%s stream=%s path=%s",
                request_id, client, body.get("model"), body.get("stream") is True, path,
            )
            streaming = body.get("stream") is True

            async def on_chunk(payload):
                nonlocal chunks
                chunks += 1
                await self._send({"type": protocol.CHUNK, "request_id": request_id, "data": payload})

            status, response_body = await self.vllm.forward(path, body, on_chunk if streaming else None)
            if streaming:
                await self._send({"type": protocol.DONE, "request_id": request_id})
            else:
                await self._send(
                    {"type": protocol.RESPONSE, "request_id": request_id, "status": status, "body": response_body}
                )
        except asyncio.CancelledError:
            outcome = "cancelled"
            self.requests_cancelled += 1
            try:
                await self._send({"type": protocol.CANCELLED, "request_id": request_id})
            except RelayGone:
                pass
        except VLLMError as exc:
            outcome = "error {0}".format(exc.status)
            self.requests_failed += 1
            log.warning("request %s failed: %s %s", request_id, exc.status, exc.message)
            await self._send_error(request_id, exc.status, exc.message)
        except RelayGone as exc:
            outcome = "relay gone"
            self.requests_failed += 1
            log.warning("request %s: relay went away while responding (%s)", request_id, exc)
        except Exception as exc:
            outcome = "error 500"
            self.requests_failed += 1
            log.exception("request %s crashed", request_id)
            await self._send_error(request_id, 500, "agent error: {0!r}".format(exc))
        finally:
            self.in_flight.pop(request_id, None)
            log.info(
                "request %s finished: %s in %.1fs (%d chunks)", request_id, outcome, time.time() - started, chunks
            )

    async def _send_error(self, request_id, status, message):
        try:
            await self._send(
                {"type": protocol.ERROR, "request_id": request_id, "status": int(status), "message": str(message)}
            )
        except RelayGone:
            pass

    # ------------------------------------------------------------ health API
    def snapshot(self):
        return {
            "agent": self.config.agent_name,
            "version": __version__,
            "uptime": round(time.time() - self.started_at),
            "relay": {
                "url": self.config.relay_ws_url,
                "connected": self.relay_connected,
                "connected_for": round(time.time() - self.connected_since) if self.connected_since and self.relay_connected else 0,
                "last_error": self.last_relay_error,
                "reconnect_attempt": self.reconnect_attempt,
                "info": self.relay_info,
            },
            "vllm": {
                "url": self.config.vllm_base_url,
                "state": self.state,
                "models": self.models,
                "error": self.vllm_meta.get("vllm_error", ""),
                "version": self.vllm_meta.get("vllm_version", ""),
            },
            "requests": {
                "in_flight": len(self.in_flight),
                "max_concurrency": self.config.max_concurrency,
                "total": self.requests_total,
                "failed": self.requests_failed,
                "cancelled": self.requests_cancelled,
            },
        }

    async def _start_health_server(self):
        if self.config.health_port <= 0:
            return None

        async def handler(request):
            status = 200 if self.relay_connected else 503
            return web.json_response(self.snapshot(), status=status)

        app = web.Application()
        app.router.add_get("/", handler)
        app.router.add_get("/health", handler)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, self.config.health_host, self.config.health_port)
        await site.start()
        log.info("health endpoint on http://%s:%d/health", self.config.health_host, self.config.health_port)
        return runner


def _parse_frame(msg):
    if msg.type != WSMsgType.TEXT:
        return None
    try:
        frame = json.loads(msg.data)
    except ValueError:
        return None
    return frame if isinstance(frame, dict) else None


def _close_reason(msg):
    if msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED):
        return "closed with code {0} {1}".format(msg.data, msg.extra or "")
    if msg.type == WSMsgType.ERROR:
        return "socket error {0}".format(msg.data)
    return "unexpected message type {0}".format(msg.type)
