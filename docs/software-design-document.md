# Software Design Document: Swarm Self-Drain

## 1. Introduction
This document outlines the architecture for the "Event-Driven Worker-Initiated Draining" system for Docker Swarm. This system allows worker nodes to safely drain themselves via an authenticated webhook prior to reboot/shutdown.

## 2. System Architecture
- **Language:** Python 3.12 (uv)
- **Framework:** FastAPI
- **Base Image:** `python:3.12-slim` (Lightweight and compatible with native Python extensions if ever needed).

### Components
1. **Manager API Container**
   - Binds to the secure Tailscale IP.
   - Mounts `/var/run/docker.sock` to execute Docker SDK commands.
   - Routes:
     - `POST /api/v1/nodes/drain`
     - `POST /api/v1/nodes/active`
2. **Worker Client Script (`scripts/worker-hook.py`)**
   - Minimal Python script using *only* standard libraries (`hmac`, `hashlib`, `urllib.request`, `subprocess`).
   - **Manager Autodetection:** Uses `docker info` to check if it's running on a manager node (`ControlAvailable == true`). If so, it executes the local `docker node update` command directly, completely bypassing the API.
   - **Worker Mode:** If it's a worker node, it generates the payload, signs it via HMAC, and executes the HTTP request.
3. **Systemd Service (`scripts/swarm-self-drain.service`)**
   - Triggers the Worker Client script on `ExecStop` (Drain) and `ExecStart` (Active).

## 3. Security Design
- **Network Level:** The API MUST be bound to a private mesh interface (e.g., Tailscale `100.x.x.x`). It should never be exposed to the public internet.
- **HMAC Authentication:** 
  - The client and server share a dedicated, randomly generated PSK (e.g., `openssl rand -hex 32`).
  - The client generates a `nonce` (UUID or random string) and a `timestamp`.
  - The client calculates the SHA256 HMAC of the payload (`node_name|timestamp|nonce`) using the PSK.
  - The server reconstructs the payload string and verifies the HMAC against its own PSK.
- **Replay Protection:** The server rejects requests where the `timestamp` is older than 60 seconds or in the future by more than 60 seconds.
- **Secret Management:** 
  - The PSK is supplied via Docker Secrets.
  - Environment Variable: `API_PSK_FILE` will point to `/run/secrets/api_psk`.
  
### Potential PSK Secret Problems & Mitigations
1. **Trailing Whitespace/Newlines:** Docker Secrets often contain a trailing newline (`\n`). If the client uses the exact string and the server reads the file without stripping, the HMACs will mismatch.
   *Mitigation:* The FastAPI server must `.strip()` the contents of the `API_PSK_FILE` when reading it into memory.
2. **Secret Rotation:** When a Docker secret is rotated, the file contents may update, or the container might need a restart depending on how Docker Swarm handles the secret injection. 
   *Mitigation:* The API should either read the secret file per-request, or require a container restart upon secret rotation. For MVP, reading the secret per-request is safer since the file is mounted in `tmpfs` and IO is cheap, avoiding the need for a service restart.

## 4. Discord Notifications (Optional)
To provide visibility into the cluster state, both the worker hook and the manager API support optional Discord notifications:
- **Worker Hook:** If `DISCORD_SHUTDOWN` or `DISCORD_STARTUP` environment variables are present (along with the webhook URL), the worker script will send these string messages to Discord immediately before executing its API request. This guarantees a notification that the node is going offline even if the manager is unreachable.
- **Manager API:** If `DISCORD_SWARM_ACTIVE` or `DISCORD_SWARM_DRAIN` environment variables are present, the Manager API will send a message to Discord *after* successfully updating the Docker Swarm node state. This confirms the action was successfully applied to the cluster.
## 4. Work Breakdown (Tasks)
To be implemented:
1. `0001-coder.md`: Implement the FastAPI Manager App (`src/api.py`).
2. `0002-coder.md`: Create the Dockerfile and `docker-compose.yml`.
3. `0003-coder.md`: Write the Worker Python script and systemd unit file (`scripts/worker-hook.py`, `scripts/swarm-self-drain.service`).
4. `0004-coder.md`: Create the Ansible playbook/role for worker node deployment (`ansible/deploy-worker.yml`).
