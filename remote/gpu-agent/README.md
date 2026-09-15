# TextEnhanceAI GPU server stack (vLLM + agent)

Two containers, started together with Docker Compose:

- **vllm** – the official `vllm/vllm-openai` image serving your model on the
  internal network (`http://vllm:8000`, also published on `127.0.0.1:8000`
  for local tests).
- **agent** – `teai_agent`, a small Python service that waits for vLLM,
  opens an outbound WebSocket to the relay with the agent key, announces the
  models vLLM serves, and forwards each request to vLLM while streaming the
  answer back. No inbound port is needed.

## Deploy

```bash
cp .env.example .env
nano .env               # RELAY_URL, RELAY_AGENT_KEY, VLLM_MODEL, GPU sizing
docker compose up -d --build
docker compose logs -f agent
```

Expected log sequence:

```
teai_agent: connecting to relay wss://relay.example.com/agent/ws as gpu-1
teai_agent: connected to relay (relay version 1.0.0); state=loading models=[]
teai_agent: waiting for vLLM at http://vllm:8000 to finish loading...
teai_agent: vLLM ready at http://vllm:8000 with models ['qwen-27b']
```

While vLLM loads, the relay (and the TextEnhanceAI dialog) already show the
agent as *loading*. Requests are only accepted once vLLM reports its models.

Requirements on the host: an NVIDIA driver, Docker with the
[NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html),
outbound HTTPS to the relay, and enough disk for the weights (Docker volume
`hf-cache` or `HF_CACHE_DIR`).

## Choosing the model and sizing vLLM

`VLLM_MODEL` is the Hugging Face repo id. A 27B model in bf16 needs roughly
55 GB of GPU memory for weights alone plus KV cache; use a quantized checkpoint
(FP8/AWQ/GPTQ variants of the same model) for a single 24–48 GB card, or set
`VLLM_TENSOR_PARALLEL_SIZE` to the number of GPUs. Relevant knobs in `.env`:

| Variable                        | Purpose                                                      |
|---------------------------------|--------------------------------------------------------------|
| `VLLM_SERVED_MODEL_NAME`        | Friendly id shown to clients (default `qwen-27b`)             |
| `VLLM_MAX_MODEL_LEN`            | Context window (input + output tokens)                        |
| `VLLM_GPU_MEMORY_UTILIZATION`   | Fraction of VRAM vLLM may use (lower it if other jobs share the GPU) |
| `VLLM_TENSOR_PARALLEL_SIZE`     | Number of GPUs to shard across                                |
| `NVIDIA_VISIBLE_DEVICES`        | Which GPUs the container sees (`all` or `0,1`)               |
| `VLLM_MAX_NUM_SEQS`             | How many sequences vLLM batches                               |
| `VLLM_EXTRA_ARGS`               | Any other vLLM flag; default enables `--reasoning-parser qwen3` (`--enable-prefix-caching` is always on, see below) |
| `VLLM_IMAGE_TAG`                | Pin a vLLM version                                            |
| `HUGGING_FACE_HUB_TOKEN`        | For gated repos                                               |

### Prefix caching

Keep `--enable-prefix-caching` in the vLLM command (`docker-compose.yml` passes
it explicitly; do not remove it via `VLLM_EXTRA_ARGS`). The automatic review
sends the three checks of one manuscript segment back to back with the segment
text *before* the instruction, so every request starts with the same tokens
and vLLM only has to prefill the segment once per segment instead of once per
check. Without the flag the requests still work, they just cost the full
prefill three times. Recent vLLM versions (V1 engine) enable prefix caching by
default; the explicit flag keeps older images and pinned tags consistent.

Qwen3-family models "think" by default. TextEnhanceAI asks for
`enable_thinking: false` (fast, deterministic edits) unless you enable thinking
in the Connection dialog; the reasoning parser keeps any reasoning out of the
edited text either way.

Test vLLM directly on the server:

```bash
curl -s http://127.0.0.1:8000/v1/models
```

## Agent configuration

| Variable                     | Default             | Meaning                                              |
|------------------------------|---------------------|------------------------------------------------------|
| `RELAY_URL`                  | —                   | Public relay URL (`https://…`); `/agent/ws` is added |
| `RELAY_AGENT_KEY`            | —                   | One of the relay's agent keys                        |
| `AGENT_NAME`                 | hostname            | Shown in status output; unique per GPU server        |
| `AGENT_MAX_CONCURRENCY`      | 4                   | Parallel requests the relay may send                 |
| `AGENT_REQUEST_TIMEOUT`      | 900 s               | Upper bound for one generation                       |
| `AGENT_RECONNECT_MAX_DELAY`  | 30 s                | Reconnect backoff ceiling                            |
| `AGENT_TLS_VERIFY`           | true                | Set `false` only for a self-signed relay certificate |
| `AGENT_LOG_LEVEL`            | INFO                | `DEBUG` for frame-level detail                       |
| `VLLM_BASE_URL`              | `http://vllm:8000`  | Set by compose                                       |
| `VLLM_API_KEY`               | —                   | Only if vLLM runs with `--api-key`                   |

`curl http://127.0.0.1:8090/health` returns the agent's view (relay connection,
vLLM state and models, in-flight requests). HTTP 200 means connected to the
relay, 503 means not; Docker's healthcheck uses the same endpoint.

## Run the agent without Docker

```bash
cd agent && pip install -r requirements.txt
RELAY_URL=https://relay.example.com RELAY_AGENT_KEY=teai_... \
VLLM_BASE_URL=http://127.0.0.1:8000 python -m teai_agent
```

## Tests

```bash
pip install pytest aiohttp
pytest -q tests
```
