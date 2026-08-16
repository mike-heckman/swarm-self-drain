# Swarm Self-Drain Worker Client

The scripts in this directory are responsible for intercepting a Docker Swarm node's shutdown or restart events to automatically update its availability in the cluster before it goes offline, and restore its availability when it boots back up.

## Requirements

The `worker-hook.py` script utilizes the Python 3 standard library and does not require third-party dependencies like `requests` or `python-dotenv`.
It interacts with the local Docker daemon via the `docker` CLI, which must be installed and running on the node.

## Deployment Files

Deploying the worker hook requires two primary files located in this directory:
1. `worker-hook.py`: The executable Python script containing the logic. It should be deployed to `/opt/swarm-self-drain/worker-hook.py` and must be executable (`chmod +x`).
2. `swarm-self-drain.service`: The Systemd service unit. It should be deployed to `/etc/systemd/system/swarm-self-drain.service` and enabled.

## Environment Variables Configuration

By default, the script reads environment variables from a `.env` file located at: `/opt/swarm-self-drain/.env`

If the node is a **Manager**, it will bypass the API and execute updates locally. The environment variables are not strictly required, but Discord notifications can still be configured.
If the node is a **Worker**, it *must* have the required variables configured to contact the Manager API successfully.

### Required Variables (Worker Nodes)
* **`MANAGER_API_URL`**: The base URL where the Manager API is hosted (e.g., `http://100.x.x.x:8000`).
* **`API_PSK`**: The secure pre-shared key used for HMAC request signing. This must match the PSK configured on the Manager API.

### Optional Variables (Discord Notifications)
* **`DISCORD_WEBHOOK_URL`**: The Discord webhook URL to send notifications to.
* **`DISCORD_SHUTDOWN`**: The exact string message to post to Discord when the node begins to shutdown. You can use `{node_name}` to inject the hostname (e.g., `⚠️ Node {node_name} is shutting down...`).
* **`DISCORD_STARTUP`**: The exact string message to post to Discord when the node boots. You can use `{node_name}` to inject the hostname (e.g., `✅ Node {node_name} is online!`).

## Example `.env` File
```ini
# /opt/swarm-self-drain/.env
MANAGER_API_URL=http://100.100.100.100:8000
API_PSK=super_secret_hex_string_here

DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
DISCORD_SHUTDOWN="⚠️ Node {node_name} shutting down"
DISCORD_STARTUP="✅ Node {node_name} running"
```

## Systemd Setup

After placing `worker-hook.py`, `swarm-self-drain.service`, and your `.env` file into their correct locations:
```bash
sudo chown -R root:root /opt/swarm-self-drain
sudo chmod 0700 /opt/swarm-self-drain/worker-hook.py
sudo chmod 0400 /opt/swarm-self-drain/.env
sudo systemctl daemon-reload
sudo systemctl enable --now swarm-self-drain.service
```
