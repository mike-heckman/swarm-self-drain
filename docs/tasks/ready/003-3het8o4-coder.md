# 003-3het8o4-coder: Create Worker Hook Script and Systemd Unit

## Context
- **Objective:** Implement the client-side logic to execute on worker nodes during shutdown and startup, computing the HMAC and notifying the API.
- **Reference:** `docs/software-design-document.md`

## Implementation Plan
### Proposed Changes
#### [NEW] [scripts/worker-hook.py](file:///home/mike/Projects/mike-heckman/swarm-self-drain/scripts/worker-hook.py)
- Python 3 standard library script.
- Uses `subprocess.run(['docker', 'info', '--format', '{{.Swarm.ControlAvailable}}'])` to detect if the local node is a manager.
- **If Manager:** Executes `docker node update --availability <drain|active> <node_name>` locally via subprocess and exits (bypassing the HTTP API).
- **If Worker:** 
  - Reads the `API_PSK` and `MANAGER_API_URL` from `/opt/swarm-self-drain/.env`.
  - Generates payload (`node_name|timestamp|nonce`), signs it with HMAC using the retrieved PSK.
  - Executes HTTP POST request to the `MANAGER_API_URL` using `urllib.request`. Implements a `timeout=60` and up to 3 retries.
- Also reads optional `DISCORD_WEBHOOK_URL`, `DISCORD_SHUTDOWN`, and `DISCORD_STARTUP` from the `.env` file. If present, sends the configured message to Discord using `urllib.request`.

#### [NEW] [scripts/swarm-self-drain.service](file:///home/mike/Projects/mike-heckman/swarm-self-drain/scripts/swarm-self-drain.service)
- Systemd unit: `Type=oneshot`, `RemainAfterExit=yes`.
- Executes script with `drain` on `ExecStop` and `active` on `ExecStart`.
