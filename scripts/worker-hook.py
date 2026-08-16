#!/usr/bin/env python3
"""
Worker hook script to automatically drain or activate a Docker Swarm node.

Deployment Requirements:
- Place this script at `/opt/swarm-self-drain/worker-hook.py` and make it executable.
- Read environment variables from `/opt/swarm-self-drain/.env`.

Environment Variables required for Workers:
- MANAGER_API_URL: Base URL of the manager API.
- API_PSK: Pre-shared key to sign HMAC requests.

Optional Discord Environment Variables:
- DISCORD_WEBHOOK_URL: Webhook URL.
- DISCORD_NODE_SHUTDOWN: Message sent on node shutdown.
- DISCORD_NODE_STARTUP: Message sent on node startup.
- DISCORD_SWARM_DRAIN: Message sent after manager local update drain.
- DISCORD_SWARM_ACTIVE: Message sent after manager local update active.
- DISCORD_API_ERROR: Message sent if script encounters an error.
"""

import argparse
import hashlib
import hmac
import json
import logging
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Final

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

ENV_PATH: Final = Path("/opt/swarm-self-drain/.env")


def load_env(path: Path) -> dict[str, str]:
    """
    Loads environment variables from a .env file.

    Args:
        path: Path to the .env file.

    Returns:
        A dictionary of environment variables.
    """
    env_vars: dict[str, str] = {}
    if not path.exists():
        logger.warning(f"Environment file {path} not found.")
        return env_vars

    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, value = line.split("=", 1)
                    env_vars[key.strip()] = value.strip().strip("'\"")
    except OSError as e:
        logger.error(f"Failed to read env file: {e}")

    return env_vars


def get_docker_info(format_str: str) -> str:
    """
    Gets information from Docker using the format string.

    Args:
        format_str: The Go template format string.

    Returns:
        The output string.

    Raises:
        RuntimeError: If the docker command fails.
    """
    try:
        result = subprocess.run(["docker", "info", "--format", format_str], capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        logger.error(f"Docker info command failed: {e.stderr}")
        raise RuntimeError("Failed to get docker info") from e


def is_manager() -> bool:
    """
    Checks if the current node is a Swarm manager.

    Returns:
        True if the node is a manager, False otherwise.
    """
    control_available = get_docker_info("{{.Swarm.ControlAvailable}}")
    return control_available.lower() == "true"


def get_node_name() -> str:
    """
    Gets the Docker Swarm node name.

    Returns:
        The node name.
    """
    return get_docker_info("{{.Name}}")


def update_node_local(node_name: str, action: str) -> None:
    """
    Updates the node availability locally using Docker CLI.

    Args:
        node_name: The name of the node.
        action: The availability action ('active' or 'drain').
    """
    logger.info(f"Updating node {node_name} to {action} locally.")
    try:
        subprocess.run(["docker", "node", "update", "--availability", action, node_name], check=True)
        logger.info("Local node update successful.")
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to update node locally: {e}")
        raise RuntimeError("Failed to update node locally") from e


def send_discord_notification(webhook_url: str, message: str) -> None:
    """
    Sends a notification to Discord.

    Args:
        webhook_url: The Discord webhook URL.
        message: The message to send.
    """
    payload = {"content": message}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url, data=data, headers={"Content-Type": "application/json", "User-Agent": "SwarmSelfDrain/1.0"}
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status in (200, 204):
                logger.info("Discord notification sent successfully.")
            else:
                logger.warning(f"Discord API returned status {response.status}")
    except urllib.error.URLError as e:
        logger.error(f"Failed to send Discord notification: {e}")


def notify_manager_api(manager_url: str, psk: str, node_name: str, action: str) -> None:
    """
    Notifies the manager API to update the node state.

    Args:
        manager_url: The base URL of the manager API.
        psk: The pre-shared key for HMAC authentication.
        node_name: The name of the node.
        action: The availability action ('active' or 'drain').
    """
    timestamp = int(time.time())
    nonce = str(uuid.uuid4())

    message_str = f"{node_name}|{timestamp}|{nonce}"
    hmac_signature = hmac.new(psk.encode("utf-8"), message_str.encode("utf-8"), hashlib.sha256).hexdigest()

    payload = {"node_name": node_name, "timestamp": timestamp, "nonce": nonce, "hmac_signature": hmac_signature}

    data = json.dumps(payload).encode("utf-8")

    endpoint = f"{manager_url.rstrip('/')}/api/v1/nodes/{action}"
    logger.info(f"Sending API request to {endpoint}")

    req = urllib.request.Request(
        endpoint, data=data, headers={"Content-Type": "application/json", "User-Agent": "SwarmSelfDrain/1.0"}
    )

    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=25) as response:
                response_data = json.loads(response.read().decode("utf-8"))
                logger.info(f"API Request successful: {response_data}")
                return
        except urllib.error.HTTPError as e:
            logger.error(f"API Request failed with status {e.code}: {e.read().decode('utf-8')}")
            if e.code >= 500 and attempt < max_retries:
                logger.info(f"Retrying ({attempt}/{max_retries})...")
                time.sleep(2)
                continue
            raise RuntimeError(f"API request failed: {e}") from e
        except urllib.error.URLError as e:
            logger.error(f"API Request failed: {e.reason}")
            if attempt < max_retries:
                logger.info(f"Retrying ({attempt}/{max_retries})...")
                time.sleep(2)
                continue
            raise RuntimeError(f"API request failed: {e}") from e


def main() -> None:
    """Main execution function."""
    parser = argparse.ArgumentParser(description="Docker Swarm Self-Drain Hook")
    parser.add_argument("action", choices=["drain", "active"], help="Action to perform on the node")
    args = parser.parse_args()

    env_vars = load_env(ENV_PATH)
    discord_url = env_vars.get("DISCORD_WEBHOOK_URL")

    try:
        node_name = get_node_name()
        manager = is_manager()

        if discord_url:
            if args.action == "drain" and "DISCORD_NODE_SHUTDOWN" in env_vars:
                send_discord_notification(discord_url, env_vars["DISCORD_NODE_SHUTDOWN"].format(node_name=node_name))
            elif args.action == "active" and "DISCORD_NODE_STARTUP" in env_vars:
                send_discord_notification(discord_url, env_vars["DISCORD_NODE_STARTUP"].format(node_name=node_name))

        if manager:
            logger.info(f"Node {node_name} is a manager. Performing local update.")
            update_node_local(node_name, args.action)
            if discord_url:
                if args.action == "drain" and "DISCORD_SWARM_DRAIN" in env_vars:
                    logger.info("Manager local drain complete. Sending Discord notification.")
                    send_discord_notification(discord_url, env_vars["DISCORD_SWARM_DRAIN"].format(node_name=node_name))
                elif args.action == "active" and "DISCORD_SWARM_ACTIVE" in env_vars:
                    logger.info("Manager local active complete. Sending Discord notification.")
                    send_discord_notification(discord_url, env_vars["DISCORD_SWARM_ACTIVE"].format(node_name=node_name))
        else:
            logger.info(f"Node {node_name} is a worker. Notifying manager API.")
            api_psk = env_vars.get("API_PSK")
            manager_url = env_vars.get("MANAGER_API_URL")

            if not api_psk or not manager_url:
                raise RuntimeError("API_PSK or MANAGER_API_URL is missing in .env")

            notify_manager_api(manager_url, api_psk, node_name, args.action)

    except RuntimeError as e:
        logger.error(f"Execution failed: {e}")
        if discord_url and "DISCORD_API_ERROR" in env_vars:
            logger.info("Sending API Error notification.")
            # If node_name wasn't successfully retrieved, fallback to 'unknown'
            safe_node_name = node_name if "node_name" in locals() else "unknown"
            send_discord_notification(
                discord_url, env_vars["DISCORD_API_ERROR"].format(node_name=safe_node_name, error=str(e))
            )
        sys.exit(1)


if __name__ == "__main__":
    main()
