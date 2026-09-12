"""Wire protocol constants shared with the relay (see remote/PROTOCOL.md)."""

PROTOCOL_VERSION = 1

# Agent -> relay
HELLO = "hello"
MODELS = "models"
STATUS = "status"
CHUNK = "chunk"
DONE = "done"
RESPONSE = "response"
ERROR = "error"
CANCELLED = "cancelled"

# Relay -> agent
WELCOME = "welcome"
REQUEST = "request"
CANCEL = "cancel"

STATE_LOADING = "loading"
STATE_READY = "ready"
STATE_UNAVAILABLE = "unavailable"
AGENT_STATES = (STATE_LOADING, STATE_READY, STATE_UNAVAILABLE)

# Paths a client may ask the relay to forward to vLLM.
FORWARDABLE_PATHS = ("/v1/chat/completions", "/v1/completions")
