# 001-3het8o4-coder: Implement FastAPI Manager App

## Context
- **Objective:** Implement the FastAPI application that will receive worker node requests and drain/activate them via the Docker socket. Security via HMAC and Tailscale networking.
- **Reference:** `docs/software-design-document.md`

## Implementation Plan
### Proposed Changes
#### [NEW] [src/api.py](file:///home/mike/Projects/mike-heckman/swarm-self-drain/src/api.py)
- Create FastAPI application.
- Create endpoints `POST /api/v1/nodes/drain` and `POST /api/v1/nodes/active`.
- Accept JSON payload: `node_name` (str), `timestamp` (int), `nonce` (str), `hmac_signature` (str).
- Read `API_PSK_FILE` path from environment, read file and `.strip()` whitespace.
- Compute SHA256 HMAC of `node_name|timestamp|nonce` using PSK. Compare safely with `hmac.compare_digest`.
- Validate timestamp window (+/- 60 seconds).
- Call local Docker socket using `httpx` or Docker SDK to update node availability.
- If `DISCORD_WEBHOOK_URL` and `DISCORD_SWARM_ACTIVE` / `DISCORD_SWARM_DRAIN` are present in the environment, asynchronously send the specified message to Discord after a successful Swarm update.
- Return appropriate HTTP errors (401/403 for invalid HMAC, 400 for timestamp).
