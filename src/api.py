"""
FastAPI Manager application for Docker Swarm node draining/activation.
"""

import asyncio
import hashlib
import hmac
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Final

import docker
import httpx
from docker.errors import APIError, NotFound
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for FastAPI startup and shutdown events.

    Args:
        app: The FastAPI application instance.
    """
    webhook_url = get_discord_webhook_url()
    discord_message = os.environ.get(DISCORD_API_STARTUP_ENV)

    if webhook_url and discord_message:
        try:
            import socket

            hostname = socket.gethostname()
        except OSError:
            hostname = "unknown"

        formatted_message = discord_message.format(node_name=hostname)
        await send_discord_notification(webhook_url, formatted_message)

    yield
    # No shutdown logic required


app = FastAPI(title="Swarm Self-Drain Manager API", lifespan=lifespan)

API_PSK_FILE_ENV: Final = "API_PSK_FILE"
DISCORD_WEBHOOK_URL_FILE_ENV: Final = "DISCORD_WEBHOOK_URL_FILE"
DISCORD_SWARM_DRAIN_ENV: Final = "DISCORD_SWARM_DRAIN"
DISCORD_SWARM_ACTIVE_ENV: Final = "DISCORD_SWARM_ACTIVE"
DISCORD_API_STARTUP_ENV: Final = "DISCORD_API_STARTUP"


class NodeRequest(BaseModel):
    """
    Data Transfer Object (DTO) pattern.

    Represents the incoming JSON payload for node drain/active requests.
    """

    node_name: str
    timestamp: int
    nonce: str
    hmac_signature: str


def get_psk() -> bytes:
    """
    Reads the PSK from the file specified by the API_PSK_FILE environment variable.

    Returns:
        bytes: The pre-shared key as a byte string.

    Raises:
        RuntimeError: If the environment variable is not set or the file cannot be read.
    """
    psk_file_env = os.environ.get(API_PSK_FILE_ENV)
    if not psk_file_env:
        raise RuntimeError("API_PSK_FILE environment variable is not set")

    psk_path = Path(psk_file_env)
    try:
        content = psk_path.read_text(encoding="utf-8")
        return content.strip().encode("utf-8")
    except Exception as e:
        logger.error(f"Failed to read PSK file: {e}")
        raise RuntimeError("Failed to read PSK file") from e


def get_discord_webhook_url() -> str | None:
    """
    Reads the Discord Webhook URL from the file specified by the DISCORD_WEBHOOK_URL_FILE environment variable.

    Returns:
        str | None: The Discord webhook URL, or None if the environment variable is not set.
    """
    url_file_env = os.environ.get(DISCORD_WEBHOOK_URL_FILE_ENV)
    if not url_file_env:
        return None

    url_path = Path(url_file_env)
    try:
        content = url_path.read_text(encoding="utf-8")
        return content.strip()
    except OSError as e:
        logger.error(f"Failed to read Discord Webhook URL file: {e}")
        return None


def verify_hmac(payload: NodeRequest, psk: bytes) -> bool:
    """
    Verifies the HMAC signature of the request payload.

    Args:
        payload: The incoming request payload.
        psk: The pre-shared key.

    Returns:
        bool: True if the signature matches, False otherwise.
    """
    message_str = f"{payload.node_name}|{payload.timestamp}|{payload.nonce}"
    message = message_str.encode("utf-8")
    expected_hmac = hmac.new(psk, message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected_hmac, payload.hmac_signature)


def update_node_state(node_name: str, state: str) -> None:
    """
    Updates the Docker Swarm node state using the Docker SDK.

    Args:
        node_name: The name of the node to update.
        state: The new availability state ("active" or "drain").

    Raises:
        HTTPException: If the node is not found or a Docker API error occurs.
    """
    try:
        client = docker.from_env()
        node = client.nodes.get(node_name)
        # Type ignored for node.attrs because docker-py does not have strict types for it.
        spec = node.attrs["Spec"]  # type: ignore
        spec["Availability"] = state
        node.update(spec)
        logger.info(f"Successfully updated node {node_name} to {state}")
    except NotFound as e:
        logger.error(f"Node {node_name} not found in swarm")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found") from e
    except APIError as e:
        logger.error(f"Docker API error updating node {node_name}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Docker API error") from e
    except Exception as e:
        logger.error(f"Unexpected error updating node {node_name}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error") from e


async def send_discord_notification(webhook_url: str, message: str) -> None:
    """
    Asynchronously sends a message to Discord.

    Args:
        webhook_url: The Discord webhook URL.
        message: The message content to send.
    """
    payload = {"content": message}
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(webhook_url, json=payload)
            response.raise_for_status()
            logger.info("Successfully sent Discord notification")
    except httpx.HTTPError as e:
        logger.error(f"Failed to send Discord notification: {e}")


def validate_request(payload: NodeRequest) -> None:
    """
    Validates the request timestamp and HMAC signature.

    Args:
        payload: The incoming request payload.

    Raises:
        HTTPException: If the timestamp is out of bounds or the HMAC is invalid.
    """
    current_time = int(time.time())

    if abs(current_time - payload.timestamp) > 60:
        logger.warning(f"Request timestamp {payload.timestamp} is outside the 60s window (current: {current_time})")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Timestamp out of bounds")

    try:
        psk = get_psk()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server configuration error"
        ) from e

    if not verify_hmac(payload, psk):
        logger.warning(f"Invalid HMAC signature for node {payload.node_name}")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid HMAC signature")


@app.post("/api/v1/nodes/drain")
async def drain_node(payload: NodeRequest) -> dict[str, str]:
    """
    Endpoint to drain a Docker Swarm node.

    Args:
        payload: The NodeRequest payload.

    Returns:
        A dictionary with the success status and message.
    """
    validate_request(payload)

    # Put the node in a drained state so that no new containers are scheduled on it.
    # Then, wait for all existing containers to be gracefully shutdown before proceeding.
    await asyncio.to_thread(update_node_state, payload.node_name, "drain")

    webhook_url = get_discord_webhook_url()
    discord_message = os.environ.get(DISCORD_SWARM_DRAIN_ENV)
    if webhook_url and discord_message:
        formatted_message = discord_message.format(node_name=payload.node_name)
        await send_discord_notification(webhook_url, formatted_message)

    return {"status": "success", "message": f"Node {payload.node_name} drained successfully"}


@app.post("/api/v1/nodes/active")
async def active_node(payload: NodeRequest) -> dict[str, str]:
    """
    Endpoint to activate a Docker Swarm node.

    Args:
        payload: The NodeRequest payload.

    Returns:
        A dictionary with the success status and message.
    """
    validate_request(payload)

    await asyncio.to_thread(update_node_state, payload.node_name, "active")

    webhook_url = get_discord_webhook_url()
    discord_message = os.environ.get(DISCORD_SWARM_ACTIVE_ENV)
    if webhook_url and discord_message:
        formatted_message = discord_message.format(node_name=payload.node_name)
        await send_discord_notification(webhook_url, formatted_message)

    return {"status": "success", "message": f"Node {payload.node_name} activated successfully"}
