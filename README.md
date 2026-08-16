# Swarm Self-Drain

Event-Driven Worker-Initiated Draining for Docker Swarm.

## Overview
In a standard Docker Swarm cluster, worker nodes cannot execute management commands (like draining themselves) before they shut down for maintenance or reboots. This leads to dropped state and abrupt container termination.

`swarm-self-drain` solves this by introducing a lightweight Manager API that runs on Swarm Manager nodes, coupled with a simple `systemd` hook on worker nodes that notifies the Manager API of shutdowns and startups. 

## Architecture
1. **The Manager API:** A FastAPI container running on the Swarm Manager node. It mounts `/var/run/docker.sock` and is bound exclusively to a secure network interface (e.g., Tailscale/WireGuard). It exposes `POST /api/v1/nodes/drain` and `POST /api/v1/nodes/active`.
2. **The Worker Hook:** A minimal Python script executed via a `systemd` hook on worker nodes that fires off an API request to the Manager API during shutdown and startup.

## Security (Secret-less HMAC)
Because this API manipulates the Docker socket, it requires strict security:
- **Transport Security:** Traffic should run over an end-to-end encrypted mesh like Tailscale.
- **Application Authentication:** HMAC (Hash-based Message Authentication Code).
- **Pre-Shared Key (PSK):** A cryptographically secure random string (passed via Docker Secrets) used to generate and verify the HMAC signature.
- **Replay Protection:** The payload includes the `node_name`, a `timestamp` (validated to be within +/- 60 seconds), a `nonce`, and the `hmac_signature`.

## Discord Notifications (Optional)
To provide deep visibility into the cluster state, both the worker hook and the manager API support optional Discord notifications mapped to 6 distinct states.

**From the Worker Hook:**
- `DISCORD_NODE_SHUTDOWN`: Sent immediately when the node starts its shutdown procedure.
- `DISCORD_NODE_STARTUP`:  Sent immediately when the node starts its startup procedure.
- `DISCORD_API_ERROR`:     Sent if the worker fails to reach the Manager API or encounters an error.
- `DISCORD_SWARM_DRAIN`:   Sent *only* if the worker hook is running on a Manager node, after successfully self-draining locally.
- `DISCORD_SWARM_ACTIVE`:  Sent *only* if the worker hook is running on a Manager node, after successfully activating locally.

*(Note: The Worker Hook expects a `DISCORD_WEBHOOK_URL` environment variable).*

**From the Manager API:**
- `DISCORD_SWARM_DRAIN`:  Sent after the API successfully drains a standard worker node.
- `DISCORD_SWARM_ACTIVE`: Sent after the API successfully activates a standard worker node.
- `DISCORD_API_STARTUP`:  Sent when the Manager API container itself starts up.

*(Note: The Manager API expects a `DISCORD_WEBHOOK_URL_FILE` environment variable).*
## Getting Started

### 0. Generate a PSK

First, generate a secure random string to use as your `API_PSK` and save it as a Docker secret on the manager node:

```bash
openssl rand -hex 32 | docker secret create api_psk -
```

### 1. Manager API (docker-compose.yml)

Because managers detect their own status and drain themselves directly, you can safely deploy the Manager API either as a standard container or as a Swarm service!

Deploy this to your cluster:

```yaml
services:
  swarm-self-drain:
    image: ghcr.io/mike-heckman/swarm-self-drain:latest
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    secrets:
      - api_psk
    environment:
      - API_PSK_FILE=/run/secrets/api_psk
      # Ensure it binds only to your secure Tailscale interface
      - HOST=100.x.y.z
      - PORT=8000

secrets:
  api_psk:
    external: true
```

### 2. Worker Setup

Ensure the worker node has Python 3 installed. Install the systemd unit `swarm-self-drain.service` and the `worker-hook.py` script. The script relies purely on Python standard libraries.

#### Ansible Deployment
We provide a template Ansible playbook in the repository to automate the deployment of the worker hook across your Swarm worker nodes. It handles copying the script, setting up the systemd service, and distributing the PSK securely.
