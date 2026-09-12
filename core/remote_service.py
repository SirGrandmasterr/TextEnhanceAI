"""OpenAI-compatible remote backend (TextEnhanceAI relay or any vLLM-style server).

The desktop app talks plain HTTPS to the relay; the relay forwards the request
over an outbound WebSocket to the GPU agent. Only the standard library is used
so the client keeps its single optional ``ollama`` dependency. Cancellation is
implemented by closing the socket from a watcher thread, which aborts the
blocking read immediately and lets the relay tell the GPU to stop generating.
"""

import http.client
import json
import socket
import ssl
import threading
from urllib.parse import urlsplit

from .backend import (
    DEFAULT_MAX_TOKENS,
    TEMPERATURE,
    TOP_P,
    BackendUnavailable,
    EditCancelled,
    OutputTruncated,
    build_messages,
    strip_thinking,
    truncated_message,
)

USER_AGENT = "TextEnhanceAI/0.13"
DEFAULT_TIMEOUT = 120  # seconds per blocking socket operation; relay keeps alive every 15s


class RemoteUnavailable(BackendUnavailable):
    """Raised when the relay cannot be reached, rejects us, or fails a request."""


class RemoteService:
    """Provide model discovery and cancellable editing through an HTTP relay."""

    display_name = "remote model"
    backend_id = "remote"

    def __init__(
        self,
        base_url,
        api_key="",
        max_tokens=DEFAULT_MAX_TOKENS,
        enable_thinking=False,
        timeout=DEFAULT_TIMEOUT,
    ):
        self.base_url = (base_url or "").strip()
        self.api_key = (api_key or "").strip()
        self.max_tokens = max_tokens
        self.enable_thinking = enable_thinking
        self.timeout = timeout
        self.last_status = None
        self._parsed = None
        if self.base_url:
            self._parsed = self._parse_url(self.base_url)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _parse_url(url):
        if "://" not in url:
            url = "https://" + url
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise RemoteUnavailable(
                "The relay URL must look like https://relay.example.com"
            )
        prefix = parts.path.rstrip("/")
        if prefix.endswith("/v1"):
            prefix = prefix[:-3]
        return {
            "scheme": parts.scheme,
            "host": parts.hostname,
            "port": parts.port or (443 if parts.scheme == "https" else 80),
            "prefix": prefix,
        }

    @property
    def configured(self):
        """Return whether a relay URL has been provided."""
        return self._parsed is not None

    def _require_config(self):
        if not self.configured:
            raise RemoteUnavailable(
                "No relay URL is configured. Open Connection settings and enter "
                "the address of your TextEnhanceAI relay."
            )

    def _connect(self):
        parsed = self._parsed
        if parsed["scheme"] == "https":
            return http.client.HTTPSConnection(
                parsed["host"],
                parsed["port"],
                timeout=self.timeout,
                context=ssl.create_default_context(),
            )
        return http.client.HTTPConnection(
            parsed["host"], parsed["port"], timeout=self.timeout
        )

    def _headers(self, json_body=False):
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _path(self, path):
        return self._parsed["prefix"] + path

    def _describe_transport_error(self, exc):
        target = "{0}://{1}:{2}".format(
            self._parsed["scheme"], self._parsed["host"], self._parsed["port"]
        )
        if isinstance(exc, ssl.SSLError):
            return (
                "Secure connection to {0} failed ({1}). Check the relay "
                "certificate or use the correct https address."
            ).format(target, exc.__class__.__name__)
        if isinstance(exc, socket.timeout):
            return "The relay at {0} did not respond in time.".format(target)
        if isinstance(exc, socket.gaierror):
            return "The relay host name {0} could not be resolved.".format(
                self._parsed["host"]
            )
        return "Cannot reach the relay at {0} ({1}).".format(target, exc)

    @staticmethod
    def _error_message(status, body):
        message = None
        try:
            payload = json.loads(body.decode("utf-8", "replace")) if body else None
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            error = payload.get("error") or payload.get("detail") or payload.get("message")
            if isinstance(error, dict):
                message = error.get("message")
            elif isinstance(error, str):
                message = error
        if not message:
            text = body.decode("utf-8", "replace").strip() if body else ""
            message = text[:300] if text else http.client.responses.get(status, "")
        if status in (401, 403):
            return "The relay rejected the API key ({0}).".format(message or status)
        if status == 404:
            return (
                "The relay answered 404 for this endpoint. Check that the URL "
                "points at the relay root, not a sub-page."
            )
        return "Relay error {0}: {1}".format(status, message)

    def _request_json(self, method, path, payload=None):
        """Perform a non-streaming JSON request and return the decoded body."""
        self._require_config()
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        conn = self._connect()
        try:
            conn.request(
                method, self._path(path), body=body, headers=self._headers(body is not None)
            )
            response = conn.getresponse()
            data = response.read()
        except (OSError, http.client.HTTPException) as exc:
            raise RemoteUnavailable(self._describe_transport_error(exc)) from exc
        finally:
            conn.close()
        if response.status != 200:
            raise RemoteUnavailable(self._error_message(response.status, data))
        try:
            return json.loads(data.decode("utf-8"))
        except ValueError as exc:
            raise RemoteUnavailable("The relay returned an unreadable response.") from exc

    # -------------------------------------------------------------- discovery
    def list_models(self):
        """Return sorted model ids exposed by the relay (``GET /v1/models``)."""
        payload = self._request_json("GET", "/v1/models")
        items = payload.get("data", []) if isinstance(payload, dict) else []
        names = sorted({str(item.get("id")) for item in items if item.get("id")})
        self.last_status = self._fetch_status()
        return names

    def _fetch_status(self):
        """Best-effort ``GET /status``; plain OpenAI-style servers lack it."""
        try:
            status = self._request_json("GET", "/status")
        except RemoteUnavailable:
            return None
        return status if isinstance(status, dict) else None

    def fetch_status(self):
        """Return the relay status document (agents, models, counters)."""
        self._require_config()
        self.last_status = self._request_json("GET", "/status")
        return self.last_status

    def connection_summary(self):
        """Return a short connection label derived from the last status call."""
        status = self.last_status
        if not status:
            return "Connected"
        agents = status.get("agents") or []
        if not agents:
            return "Connected · no GPU agent online"
        ready = [agent for agent in agents if agent.get("state") == "ready"]
        if not ready:
            return "Connected · {0} loading".format(
                ", ".join(agent.get("name", "agent") for agent in agents)
            )
        names = ", ".join(agent.get("name", "agent") for agent in ready)
        busy = sum(int(agent.get("in_flight", 0)) for agent in ready)
        label = "Connected · {0}".format(names)
        if busy:
            label += " · {0} busy".format(busy)
        return label

    def no_models_hint(self):
        """Return guidance shown when the model list is empty."""
        status = self.last_status
        agents = (status or {}).get("agents") or []
        if not agents:
            return (
                "No GPU agent is connected to the relay. Start the GPU server "
                "stack, then refresh the list."
            )
        loading = [agent.get("name", "agent") for agent in agents if agent.get("state") != "ready"]
        if loading:
            return (
                "GPU agent {0} is still loading its model. Refresh the list in a "
                "few minutes."
            ).format(", ".join(loading))
        return "The connected GPU agent reports no models. Check the vLLM logs."

    # ------------------------------------------------------------- generation
    def _build_request(self, model, instruction, text):
        return {
            "model": model,
            "messages": build_messages(instruction, text),
            "stream": True,
            "max_tokens": self.max_tokens,
            "temperature": TEMPERATURE,
            "top_p": TOP_P,
            # Honoured by vLLM/SGLang/llama.cpp; ignored by servers without templates.
            "chat_template_kwargs": {"enable_thinking": bool(self.enable_thinking)},
        }

    @staticmethod
    def _start_cancel_watcher(cancel_event, done_event, conn, sock_holder):
        """Close the socket as soon as the user cancels, unblocking the read.

        ``sock_holder`` is a one-item list the worker fills with the connected
        socket after the request is sent: http.client drops its own reference
        once it knows the response will close the connection.
        """

        def close_connection():
            sockets = [getattr(conn, "sock", None)] + list(sock_holder)
            for sock in sockets:
                if sock is None:
                    continue
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            try:
                conn.close()
            except Exception:  # pragma: no cover - best effort
                pass

        def watch():
            while not done_event.is_set():
                if cancel_event.wait(0.2):
                    close_connection()
                    # http.client re-opens a closed socket on the next request,
                    # so keep closing until the worker acknowledges the cancel.
                    done_event.wait(0.2)

        thread = threading.Thread(target=watch, daemon=True)
        thread.start()
        return thread

    @staticmethod
    def _iter_sse_events(response):
        """Yield the ``data`` payload of each server-sent event."""
        data_lines = []
        for raw in response:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if not line:
                if data_lines:
                    yield "\n".join(data_lines)
                    data_lines = []
                continue
            if line.startswith(":"):
                continue  # keepalive comment from the relay
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if data_lines:
            yield "\n".join(data_lines)

    def stream_edit(self, model, instruction, text, cancel_event, on_progress=None):
        """Return the edited document, honoring cancellation mid-stream."""
        self._require_config()
        if cancel_event.is_set():
            raise EditCancelled("Editing was cancelled.")

        body = json.dumps(self._build_request(model, instruction, text)).encode("utf-8")
        conn = self._connect()
        done_event = threading.Event()
        sock_holder = []
        self._start_cancel_watcher(cancel_event, done_event, conn, sock_holder)
        chunks = []
        received = 0
        finish_reason = None
        try:
            try:
                headers = self._headers(json_body=True)
                headers["Accept"] = "text/event-stream"
                conn.request("POST", self._path("/v1/chat/completions"), body=body, headers=headers)
                if conn.sock is not None:
                    sock_holder.append(conn.sock)
                response = conn.getresponse()
                if response.status != 200:
                    raise RemoteUnavailable(
                        self._error_message(response.status, response.read())
                    )
                for payload in self._iter_sse_events(response):
                    if cancel_event.is_set():
                        raise EditCancelled("Editing was cancelled.")
                    if payload.strip() == "[DONE]":
                        break
                    try:
                        event = json.loads(payload)
                    except ValueError:
                        continue
                    if "error" in event and not event.get("choices"):
                        error = event["error"]
                        message = error.get("message") if isinstance(error, dict) else str(error)
                        raise RemoteUnavailable("Relay error: {0}".format(message))
                    for choice in event.get("choices") or []:
                        delta = choice.get("delta") or {}
                        content = delta.get("content")
                        if content:
                            chunks.append(content)
                            received += len(content)
                            if on_progress:
                                on_progress(received)
                        if choice.get("finish_reason"):
                            finish_reason = choice["finish_reason"]
            except (OSError, http.client.HTTPException) as exc:
                if cancel_event.is_set():
                    raise EditCancelled("Editing was cancelled.")
                raise RemoteUnavailable(self._describe_transport_error(exc)) from exc
        finally:
            done_event.set()
            try:
                conn.close()
            except Exception:  # pragma: no cover - best effort
                pass

        if cancel_event.is_set():
            raise EditCancelled("Editing was cancelled.")
        if finish_reason == "length":
            raise OutputTruncated(truncated_message(model))
        result = strip_thinking("".join(chunks))
        if not result.strip():
            raise RemoteUnavailable("The remote model returned an empty response.")
        return result
