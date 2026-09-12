# TextEnhanceAI relay protocol (version 1)

Three parties take part in a remote edit:

```
┌──────────────────┐  HTTPS (OpenAI-style)  ┌──────────────────┐  WSS (outbound only)  ┌──────────────────┐
│ TextEnhanceAI    │ ─────────────────────▶ │ Relay            │ ◀──────────────────── │ GPU agent        │
│ desktop client   │ ◀───── SSE stream ──── │ (Strato server)  │ ── request frames ──▶ │ (next to vLLM)   │
└──────────────────┘                        └──────────────────┘                       └───────┬──────────┘
                                                                                               │ HTTP (docker network)
                                                                                       ┌───────▼──────────┐
                                                                                       │ vLLM             │
                                                                                       └──────────────────┘
```

The GPU server can only open outbound connections, so the agent dials the
relay and keeps a WebSocket open. Clients never see the GPU server; they talk
to the relay with the standard OpenAI chat-completions API, which means any
OpenAI-compatible tool can use the relay, not just TextEnhanceAI.

## Client side (HTTP)

All client endpoints require `Authorization: Bearer <client key>`.

| Method | Path                   | Purpose                                                        |
|--------|------------------------|----------------------------------------------------------------|
| GET    | `/health`              | No auth. `{"ok": true, "agents": n, "ready_agents": m}`.        |
| GET    | `/v1/models`           | OpenAI list of models served by *ready* agents (`owned_by` = agent). |
| POST   | `/v1/chat/completions` | Forwarded verbatim to vLLM; streaming (SSE) or JSON.            |
| POST   | `/v1/completions`      | Same, legacy completion endpoint.                              |
| GET    | `/status`              | Agents, their state/models/load, relay counters.               |

Errors use the OpenAI shape: `{"error": {"message": "...", "type": "...", "code": 503}}`.
Relevant statuses: `401` bad key, `400` invalid body, `413` body too large,
`503` no ready agent serves the model / queue timeout, `504` agent idle timeout,
`502` agent failed or disconnected. The relay adds an `X-Request-ID` header.

While a streaming request waits for the GPU, the relay writes SSE comments
(`: keepalive`, `: queued`) every `RELAY_KEEPALIVE_INTERVAL` seconds so
proxies and clients keep the connection open. Comments are ignored by every
SSE parser. When an error occurs *after* streaming started, the relay emits
`data: {"error": {...}}` followed by `data: [DONE]`.

Closing the HTTP connection cancels the request: the relay sends a `cancel`
frame to the agent, which drops its HTTP connection to vLLM, and vLLM aborts
the generation.

## Agent side (WebSocket)

Endpoint: `GET /agent/ws` with `Authorization: Bearer <agent key>`. Every frame
is one JSON text message with a `type` field. The relay never inspects prompt
content beyond the fields it needs for routing (`model`, `stream`).

### Agent → relay

| type        | fields                                                                                   |
|-------------|------------------------------------------------------------------------------------------|
| `hello`     | `protocol` (1), `agent` (name), `agent_version`, `state`, `models`, `max_concurrency`, `meta` |
| `models`    | `state` (`loading`\|`ready`\|`unavailable`), `models` (list of `{id, max_model_len?}`)   |
| `status`    | optional periodic `{state, in_flight}`                                                    |
| `chunk`     | `request_id`, `data` — the raw JSON payload of one SSE `data:` line from vLLM              |
| `done`      | `request_id` — streaming finished                                                         |
| `response`  | `request_id`, `status`, `body` — full JSON answer for non-streaming requests               |
| `error`     | `request_id`, `status` (HTTP-like), `message`                                             |
| `cancelled` | `request_id` — acknowledgement of a cancel                                                |

The first frame must be `hello` (sent within 30 s of connecting). A second
connection with the same agent name replaces the first.

### Relay → agent

| type      | fields                                                        |
|-----------|---------------------------------------------------------------|
| `welcome` | `protocol`, `relay_version`, `server_time`, `keepalive_interval` |
| `request` | `request_id`, `path` (`/v1/chat/completions` or `/v1/completions`), `body`, `client` (key name) |
| `cancel`  | `request_id`                                                  |

Keepalive uses WebSocket ping/pong (`RELAY_WS_HEARTBEAT`, default 20 s). If the
socket drops, every in-flight request on that agent fails with `502` and the
agent reconnects with exponential backoff (1 s → 30 s, with jitter).

## Concurrency

Each agent announces `max_concurrency`. The relay picks the ready agent with
the fewest in-flight requests that serves the requested model, waits up to
`RELAY_QUEUE_TIMEOUT` for a free slot (sending `: queued` keepalives), then
dispatches. vLLM batches concurrent requests, so 4–8 is a good default on a
single GPU.
