import hashlib
import hmac
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api import app

client = TestClient(app)


def generate_signature(node_name: str, timestamp: int, nonce: str, psk: bytes) -> str:
    """Helper to generate a valid HMAC signature."""
    message = f"{node_name}|{timestamp}|{nonce}".encode()
    return hmac.new(psk, message, hashlib.sha256).hexdigest()


@pytest.fixture
def mock_env(monkeypatch):
    """Mocks the environment variables."""
    monkeypatch.setenv("DISCORD_SWARM_DRAIN", "Node {node_name} is draining.")
    monkeypatch.setenv("DISCORD_SWARM_ACTIVE", "Node {node_name} is active.")


@pytest.fixture(autouse=True)
def mock_webhook_file(tmp_path, monkeypatch):
    """Creates a mock webhook URL file."""
    webhook_url = "http://mock-webhook"
    webhook_file = tmp_path / "mock_webhook"
    webhook_file.write_text(webhook_url, encoding="utf-8")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL_FILE", str(webhook_file))
    return webhook_url


@pytest.fixture
def mock_psk_file(tmp_path, monkeypatch):
    """Creates a mock PSK file."""
    psk = b"supersecretpsk"
    psk_file = tmp_path / "mock_psk"
    psk_file.write_text("supersecretpsk\n", encoding="utf-8")
    monkeypatch.setenv("API_PSK_FILE", str(psk_file))
    return psk


@patch("src.api.docker.from_env")
@patch("src.api.httpx.AsyncClient")
def test_drain_node_success(mock_async_client, mock_docker_env, mock_psk_file):
    """Test successful node draining."""
    mock_docker_client = MagicMock()
    mock_docker_env.return_value = mock_docker_client

    mock_node = MagicMock()
    mock_node.attrs = {"Spec": {"Availability": "active"}}
    mock_docker_client.nodes.get.return_value = mock_node

    # Setup mock httpx client response
    mock_response = MagicMock()
    mock_client_instance = mock_async_client.return_value.__aenter__.return_value
    mock_client_instance.post.return_value = mock_response

    node_name = "worker-1"
    timestamp = int(time.time())
    nonce = "random123"
    hmac_signature = generate_signature(node_name, timestamp, nonce, mock_psk_file)

    payload = {
        "node_name": node_name,
        "timestamp": timestamp,
        "nonce": nonce,
        "hmac_signature": hmac_signature,
    }

    response = client.post("/api/v1/nodes/drain", json=payload)

    assert response.status_code == 200
    assert response.json() == {"status": "success", "message": "Node worker-1 drained successfully"}

    mock_docker_client.nodes.get.assert_called_once_with(node_name)
    mock_node.update.assert_called_once_with({"Availability": "drain"})


@patch("src.api.docker.from_env")
@patch("src.api.httpx.AsyncClient")
def test_active_node_success(mock_async_client, mock_docker_env, mock_psk_file):
    """Test successful node activation."""
    mock_docker_client = MagicMock()
    mock_docker_env.return_value = mock_docker_client

    mock_node = MagicMock()
    mock_node.attrs = {"Spec": {"Availability": "drain"}}
    mock_docker_client.nodes.get.return_value = mock_node

    # Setup mock httpx client response
    mock_response = MagicMock()
    mock_client_instance = mock_async_client.return_value.__aenter__.return_value
    mock_client_instance.post.return_value = mock_response

    node_name = "worker-2"
    timestamp = int(time.time())
    nonce = "random456"
    hmac_signature = generate_signature(node_name, timestamp, nonce, mock_psk_file)

    payload = {
        "node_name": node_name,
        "timestamp": timestamp,
        "nonce": nonce,
        "hmac_signature": hmac_signature,
    }

    response = client.post("/api/v1/nodes/active", json=payload)

    assert response.status_code == 200
    assert response.json() == {"status": "success", "message": "Node worker-2 activated successfully"}

    mock_docker_client.nodes.get.assert_called_once_with(node_name)
    mock_node.update.assert_called_once_with({"Availability": "active"})


def test_invalid_hmac(mock_psk_file):
    """Test rejection with invalid HMAC."""
    payload = {
        "node_name": "worker-1",
        "timestamp": int(time.time()),
        "nonce": "random123",
        "hmac_signature": "invalid_signature",
    }
    response = client.post("/api/v1/nodes/drain", json=payload)
    assert response.status_code == 403


def test_expired_timestamp(mock_psk_file):
    """Test rejection with expired timestamp."""
    payload = {
        "node_name": "worker-1",
        "timestamp": int(time.time()) - 100,  # 100 seconds ago
        "nonce": "random123",
        "hmac_signature": "ignored",
    }
    response = client.post("/api/v1/nodes/drain", json=payload)
    assert response.status_code == 400


def test_missing_psk_env_var(monkeypatch):
    """Test behavior when API_PSK_FILE is not set."""
    monkeypatch.delenv("API_PSK_FILE", raising=False)
    payload = {
        "node_name": "worker-1",
        "timestamp": int(time.time()),
        "nonce": "random123",
        "hmac_signature": "ignored",
    }
    response = client.post("/api/v1/nodes/drain", json=payload)
    assert response.status_code == 500


def test_psk_file_read_error(monkeypatch, tmp_path):
    """Test behavior when API_PSK_FILE cannot be read."""
    # Create a directory instead of a file so read_text fails
    psk_dir = tmp_path / "mock_psk_dir"
    psk_dir.mkdir()
    monkeypatch.setenv("API_PSK_FILE", str(psk_dir))

    payload = {
        "node_name": "worker-1",
        "timestamp": int(time.time()),
        "nonce": "random123",
        "hmac_signature": "ignored",
    }
    response = client.post("/api/v1/nodes/drain", json=payload)
    assert response.status_code == 500


@pytest.mark.asyncio
async def test_get_discord_webhook_url_read_error(monkeypatch, tmp_path):
    """Test behavior when DISCORD_WEBHOOK_URL_FILE cannot be read."""
    from src.api import get_discord_webhook_url

    url_dir = tmp_path / "mock_url_dir"
    url_dir.mkdir()
    monkeypatch.setenv("DISCORD_WEBHOOK_URL_FILE", str(url_dir))

    # Should return None on OSError
    assert get_discord_webhook_url() is None


@patch("src.api.docker.from_env")
def test_update_node_state_exceptions(mock_docker_env):
    """Test docker exceptions in update_node_state."""
    from docker.errors import APIError, NotFound
    from fastapi import HTTPException

    from src.api import update_node_state

    mock_docker_client = MagicMock()
    mock_docker_env.return_value = mock_docker_client

    # Test NotFound
    mock_docker_client.nodes.get.side_effect = NotFound("Node not found")
    with pytest.raises(HTTPException) as exc_info:
        update_node_state("worker-1", "drain")
    assert exc_info.value.status_code == 404

    # Test APIError
    mock_docker_client.nodes.get.side_effect = APIError("API error")
    with pytest.raises(HTTPException) as exc_info:
        update_node_state("worker-1", "drain")
    assert exc_info.value.status_code == 500

    # Test generic Exception
    mock_docker_client.nodes.get.side_effect = Exception("Generic error")
    with pytest.raises(HTTPException) as exc_info:
        update_node_state("worker-1", "drain")
    assert exc_info.value.status_code == 500


@pytest.mark.asyncio
@patch("src.api.httpx.AsyncClient")
async def test_send_discord_notification_httperror(mock_async_client):
    """Test HTTPError when sending Discord notification."""
    import httpx

    from src.api import send_discord_notification

    mock_client_instance = mock_async_client.return_value.__aenter__.return_value

    # Setup mock to raise HTTPError
    mock_client_instance.post.side_effect = httpx.HTTPError("Mocked HTTP Error")

    # Should handle exception and not raise
    await send_discord_notification("http://mock-webhook", "Test message")
    mock_client_instance.post.assert_called_once()


@pytest.mark.asyncio
@patch("src.api.httpx.AsyncClient")
async def test_send_discord_notification_success(mock_async_client):
    """Test successful Discord notification sending."""

    from src.api import send_discord_notification

    mock_client_instance = mock_async_client.return_value.__aenter__.return_value
    mock_response = MagicMock()
    mock_client_instance.post.return_value = mock_response

    await send_discord_notification("http://mock-webhook", "Test success message")
    mock_client_instance.post.assert_called_once()
    mock_response.raise_for_status.assert_called_once()


def test_get_discord_webhook_url_missing_env(monkeypatch):
    """Test behavior when DISCORD_WEBHOOK_URL_FILE is not set."""
    from src.api import get_discord_webhook_url

    monkeypatch.delenv("DISCORD_WEBHOOK_URL_FILE", raising=False)
    assert get_discord_webhook_url() is None


@pytest.mark.asyncio
@patch("src.api.httpx.AsyncClient")
async def test_lifespan_startup(mock_async_client, monkeypatch):
    """Test the lifespan context manager sends a startup notification."""
    from src.api import DISCORD_API_STARTUP_ENV, app, lifespan

    monkeypatch.setenv(DISCORD_API_STARTUP_ENV, "API Node {node_name} started.")

    # Mock socket.gethostname to ensure deterministic output
    import socket

    monkeypatch.setattr(socket, "gethostname", lambda: "mocked-host")

    mock_client_instance = mock_async_client.return_value.__aenter__.return_value
    mock_response = MagicMock()
    mock_client_instance.post.return_value = mock_response

    async with lifespan(app):
        # Startup phase should have triggered a notification
        mock_client_instance.post.assert_called_once()
        args, kwargs = mock_client_instance.post.call_args
        assert args[0] == "http://mock-webhook"
        assert kwargs["json"]["content"] == "API Node mocked-host started."


@pytest.mark.asyncio
@patch("src.api.httpx.AsyncClient")
async def test_lifespan_startup_hostname_exception(mock_async_client, monkeypatch):
    """Test the lifespan handles gethostname exceptions."""
    from src.api import DISCORD_API_STARTUP_ENV, app, lifespan

    monkeypatch.setenv(DISCORD_API_STARTUP_ENV, "API Node {node_name} started.")

    # Mock socket.gethostname to raise an OSError
    import socket

    monkeypatch.setattr(socket, "gethostname", MagicMock(side_effect=OSError("Failed")))

    mock_client_instance = mock_async_client.return_value.__aenter__.return_value
    mock_response = MagicMock()
    mock_client_instance.post.return_value = mock_response

    async with lifespan(app):
        mock_client_instance.post.assert_called_once()
        args, kwargs = mock_client_instance.post.call_args
        assert kwargs["json"]["content"] == "API Node unknown started."
