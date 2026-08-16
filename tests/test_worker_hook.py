import importlib.util
import json
import subprocess
import sys
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Load the worker-hook script as a module
script_path = Path("scripts/worker-hook.py")
spec = importlib.util.spec_from_file_location("worker_hook", str(script_path))
if spec and spec.loader:
    worker_hook = importlib.util.module_from_spec(spec)
    sys.modules["worker_hook"] = worker_hook
    spec.loader.exec_module(worker_hook)
else:
    raise ImportError("Failed to load worker-hook.py")


def test_load_env_valid(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("FOO=bar\nBAZ='qux'\n#comment\nEMPTY=\n")
    env = worker_hook.load_env(env_file)
    assert env == {"FOO": "bar", "BAZ": "qux", "EMPTY": ""}


def test_load_env_not_found(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env = worker_hook.load_env(env_file)
    assert env == {}


def test_load_env_exception(monkeypatch, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("FOO=bar")

    # Mock Path.open to raise an OSError
    mock_open = MagicMock(side_effect=OSError("Test error"))
    monkeypatch.setattr(Path, "open", mock_open)

    env = worker_hook.load_env(env_file)
    assert env == {}


def test_get_docker_info_success(monkeypatch) -> None:
    mock_run = MagicMock()
    mock_run.return_value.stdout = "test_node\n"
    monkeypatch.setattr("subprocess.run", mock_run)
    result = worker_hook.get_docker_info("{{.Name}}")
    assert result == "test_node"
    mock_run.assert_called_once_with(
        ["docker", "info", "--format", "{{.Name}}"],
        capture_output=True,
        text=True,
        check=True,
    )


def test_get_docker_info_failure(monkeypatch) -> None:
    mock_run = MagicMock()
    mock_run.side_effect = subprocess.CalledProcessError(1, "docker", stderr="error")
    monkeypatch.setattr("subprocess.run", mock_run)
    with pytest.raises(RuntimeError, match="Failed to get docker info"):
        worker_hook.get_docker_info("{{.Name}}")


def test_is_manager(monkeypatch) -> None:
    mock_get = MagicMock(return_value="true")
    monkeypatch.setattr(worker_hook, "get_docker_info", mock_get)
    assert worker_hook.is_manager() is True

    mock_get.return_value = "false"
    assert worker_hook.is_manager() is False


def test_get_node_name(monkeypatch) -> None:
    mock_get = MagicMock(return_value="my_node")
    monkeypatch.setattr(worker_hook, "get_docker_info", mock_get)
    assert worker_hook.get_node_name() == "my_node"


def test_update_node_local_success(monkeypatch) -> None:
    mock_run = MagicMock()
    monkeypatch.setattr("subprocess.run", mock_run)
    worker_hook.update_node_local("my_node", "drain")
    mock_run.assert_called_once_with(["docker", "node", "update", "--availability", "drain", "my_node"], check=True)


def test_update_node_local_failure(monkeypatch) -> None:
    mock_run = MagicMock()
    mock_run.side_effect = subprocess.CalledProcessError(1, "docker")
    monkeypatch.setattr("subprocess.run", mock_run)
    with pytest.raises(RuntimeError, match="Failed to update node locally"):
        worker_hook.update_node_local("my_node", "drain")


def test_send_discord_notification_success(monkeypatch) -> None:
    mock_urlopen = MagicMock()
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.__enter__.return_value = mock_response
    mock_urlopen.return_value = mock_response
    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

    worker_hook.send_discord_notification("http://discord.com", "test message")

    mock_urlopen.assert_called_once()
    request = mock_urlopen.call_args[0][0]
    assert request.full_url == "http://discord.com"
    assert json.loads(request.data) == {"content": "test message"}


def test_send_discord_notification_exception(monkeypatch) -> None:
    mock_urlopen = MagicMock(side_effect=urllib.error.URLError("Test error"))
    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

    worker_hook.send_discord_notification("http://discord.com", "test message")
    mock_urlopen.assert_called_once()


def test_notify_manager_api_success(monkeypatch) -> None:
    mock_urlopen = MagicMock()
    mock_response = MagicMock()
    mock_response.read.return_value = b'{"status": "success"}'
    mock_response.__enter__.return_value = mock_response
    mock_urlopen.return_value = mock_response
    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

    worker_hook.notify_manager_api("http://manager", "secretpsk", "my_node", "drain")

    mock_urlopen.assert_called_once()
    request = mock_urlopen.call_args[0][0]
    assert request.full_url == "http://manager/api/v1/nodes/drain"
    payload = json.loads(request.data)
    assert payload["node_name"] == "my_node"
    assert "hmac_signature" in payload
    assert "timestamp" in payload
    assert "nonce" in payload


def test_notify_manager_api_failure_retry_httperror(monkeypatch) -> None:
    mock_urlopen = MagicMock()
    mock_sleep = MagicMock()
    monkeypatch.setattr("time.sleep", mock_sleep)
    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

    # Fail 2 times, succeed on 3rd
    mock_error = urllib.error.HTTPError("http://manager", 500, "Internal Server Error", {}, None)
    mock_error.read = MagicMock(return_value=b"error")

    mock_success = MagicMock()
    mock_success.read.return_value = b'{"status": "success"}'
    mock_success.__enter__.return_value = mock_success

    mock_urlopen.side_effect = [mock_error, mock_error, mock_success]

    worker_hook.notify_manager_api("http://manager", "secretpsk", "my_node", "drain")

    assert mock_urlopen.call_count == 3
    assert mock_sleep.call_count == 2


def test_notify_manager_api_failure_fatal_httperror(monkeypatch) -> None:
    mock_urlopen = MagicMock()
    mock_sleep = MagicMock()
    monkeypatch.setattr("time.sleep", mock_sleep)
    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

    mock_error = urllib.error.HTTPError("http://manager", 400, "Bad Request", {}, None)
    mock_error.read = MagicMock(return_value=b"error")
    mock_urlopen.side_effect = [mock_error]

    with pytest.raises(RuntimeError, match="API request failed"):
        worker_hook.notify_manager_api("http://manager", "secretpsk", "my_node", "drain")
    assert mock_urlopen.call_count == 1
    assert mock_sleep.call_count == 0


def test_notify_manager_api_failure_fatal_urlerror(monkeypatch) -> None:
    mock_urlopen = MagicMock()
    mock_sleep = MagicMock()
    monkeypatch.setattr("time.sleep", mock_sleep)
    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

    mock_error = urllib.error.URLError("Connection refused")
    mock_urlopen.side_effect = [mock_error, mock_error, mock_error]

    with pytest.raises(RuntimeError, match="API request failed"):
        worker_hook.notify_manager_api("http://manager", "secretpsk", "my_node", "drain")
    assert mock_urlopen.call_count == 3
    assert mock_sleep.call_count == 2


def test_main_manager_success(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["worker-hook.py", "drain"])

    mock_load_env = MagicMock(return_value={})
    monkeypatch.setattr(worker_hook, "load_env", mock_load_env)

    mock_get_node = MagicMock(return_value="manager_node")
    monkeypatch.setattr(worker_hook, "get_node_name", mock_get_node)

    mock_is_manager = MagicMock(return_value=True)
    monkeypatch.setattr(worker_hook, "is_manager", mock_is_manager)

    mock_update = MagicMock()
    monkeypatch.setattr(worker_hook, "update_node_local", mock_update)

    worker_hook.main()

    mock_update.assert_called_once_with("manager_node", "drain")


def test_main_manager_success_active(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["worker-hook.py", "active"])

    env = {
        "DISCORD_WEBHOOK_URL": "http://discord",
        "DISCORD_STARTUP": "Node starting up",
    }
    mock_load_env = MagicMock(return_value=env)
    monkeypatch.setattr(worker_hook, "load_env", mock_load_env)

    mock_get_node = MagicMock(return_value="manager_node")
    monkeypatch.setattr(worker_hook, "get_node_name", mock_get_node)

    mock_is_manager = MagicMock(return_value=True)
    monkeypatch.setattr(worker_hook, "is_manager", mock_is_manager)

    mock_update = MagicMock()
    monkeypatch.setattr(worker_hook, "update_node_local", mock_update)

    mock_discord = MagicMock()
    monkeypatch.setattr(worker_hook, "send_discord_notification", mock_discord)

    worker_hook.main()

    mock_update.assert_called_once_with("manager_node", "active")
    mock_discord.assert_called_once_with("http://discord", "Node starting up")


def test_main_worker_success(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["worker-hook.py", "active"])

    env = {
        "API_PSK": "secret",
        "MANAGER_API_URL": "http://manager",
        "DISCORD_WEBHOOK_URL": "http://discord",
        "DISCORD_STARTUP": "Node starting up",
    }
    mock_load_env = MagicMock(return_value=env)
    monkeypatch.setattr(worker_hook, "load_env", mock_load_env)

    mock_get_node = MagicMock(return_value="worker_node")
    monkeypatch.setattr(worker_hook, "get_node_name", mock_get_node)

    mock_is_manager = MagicMock(return_value=False)
    monkeypatch.setattr(worker_hook, "is_manager", mock_is_manager)

    mock_notify = MagicMock()
    monkeypatch.setattr(worker_hook, "notify_manager_api", mock_notify)

    mock_discord = MagicMock()
    monkeypatch.setattr(worker_hook, "send_discord_notification", mock_discord)

    worker_hook.main()

    mock_notify.assert_called_once_with("http://manager", "secret", "worker_node", "active")
    mock_discord.assert_not_called()


def test_main_worker_active_failure_fallback_notification(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["worker-hook.py", "active"])

    env = {
        "API_PSK": "secret",
        "MANAGER_API_URL": "http://manager",
        "DISCORD_WEBHOOK_URL": "http://discord",
        "DISCORD_STARTUP": "Node starting up",
    }
    mock_load_env = MagicMock(return_value=env)
    monkeypatch.setattr(worker_hook, "load_env", mock_load_env)

    mock_get_node = MagicMock(return_value="worker_node")
    monkeypatch.setattr(worker_hook, "get_node_name", mock_get_node)

    mock_is_manager = MagicMock(return_value=False)
    monkeypatch.setattr(worker_hook, "is_manager", mock_is_manager)

    mock_notify = MagicMock(side_effect=RuntimeError("Error"))
    monkeypatch.setattr(worker_hook, "notify_manager_api", mock_notify)

    mock_discord = MagicMock()
    monkeypatch.setattr(worker_hook, "send_discord_notification", mock_discord)

    mock_exit = MagicMock(side_effect=SystemExit)
    monkeypatch.setattr("sys.exit", mock_exit)

    with pytest.raises(SystemExit):
        worker_hook.main()

    mock_notify.assert_called_once_with("http://manager", "secret", "worker_node", "active")
    mock_discord.assert_called_once_with("http://discord", "Node starting up")
    mock_exit.assert_called_once_with(1)


def test_main_worker_missing_env(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["worker-hook.py", "active"])
    mock_load_env = MagicMock(return_value={})
    monkeypatch.setattr(worker_hook, "load_env", mock_load_env)

    mock_get_node = MagicMock(return_value="worker_node")
    monkeypatch.setattr(worker_hook, "get_node_name", mock_get_node)

    mock_is_manager = MagicMock(return_value=False)
    monkeypatch.setattr(worker_hook, "is_manager", mock_is_manager)

    mock_exit = MagicMock(side_effect=SystemExit)
    monkeypatch.setattr("sys.exit", mock_exit)

    with pytest.raises(SystemExit):
        worker_hook.main()
    mock_exit.assert_called_once_with(1)


def test_main_get_node_error(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["worker-hook.py", "active"])
    mock_load_env = MagicMock(return_value={})
    monkeypatch.setattr(worker_hook, "load_env", mock_load_env)

    mock_get_node = MagicMock(side_effect=RuntimeError("Error"))
    monkeypatch.setattr(worker_hook, "get_node_name", mock_get_node)

    mock_exit = MagicMock(side_effect=SystemExit)
    monkeypatch.setattr("sys.exit", mock_exit)

    with pytest.raises(SystemExit):
        worker_hook.main()
    mock_exit.assert_called_once_with(1)


def test_main_is_manager_error(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["worker-hook.py", "active"])
    mock_load_env = MagicMock(return_value={})
    monkeypatch.setattr(worker_hook, "load_env", mock_load_env)

    mock_get_node = MagicMock(return_value="node")
    monkeypatch.setattr(worker_hook, "get_node_name", mock_get_node)

    mock_is_manager = MagicMock(side_effect=RuntimeError("Error"))
    monkeypatch.setattr(worker_hook, "is_manager", mock_is_manager)

    mock_exit = MagicMock(side_effect=SystemExit)
    monkeypatch.setattr("sys.exit", mock_exit)

    with pytest.raises(SystemExit):
        worker_hook.main()
    mock_exit.assert_called_once_with(1)


def test_main_update_node_local_error(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["worker-hook.py", "active"])
    mock_load_env = MagicMock(return_value={})
    monkeypatch.setattr(worker_hook, "load_env", mock_load_env)

    mock_get_node = MagicMock(return_value="manager_node")
    monkeypatch.setattr(worker_hook, "get_node_name", mock_get_node)

    mock_is_manager = MagicMock(return_value=True)
    monkeypatch.setattr(worker_hook, "is_manager", mock_is_manager)

    mock_update = MagicMock(side_effect=RuntimeError("Error"))
    monkeypatch.setattr(worker_hook, "update_node_local", mock_update)

    mock_exit = MagicMock(side_effect=SystemExit)
    monkeypatch.setattr("sys.exit", mock_exit)

    with pytest.raises(SystemExit):
        worker_hook.main()
    mock_exit.assert_called_once_with(1)


def test_main_notify_manager_error(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["worker-hook.py", "drain"])

    env = {
        "API_PSK": "secret",
        "MANAGER_API_URL": "http://manager",
        "DISCORD_WEBHOOK_URL": "http://discord",
        "DISCORD_SHUTDOWN": "Node shutting down",
    }
    mock_load_env = MagicMock(return_value=env)
    monkeypatch.setattr(worker_hook, "load_env", mock_load_env)

    mock_get_node = MagicMock(return_value="worker_node")
    monkeypatch.setattr(worker_hook, "get_node_name", mock_get_node)

    mock_is_manager = MagicMock(return_value=False)
    monkeypatch.setattr(worker_hook, "is_manager", mock_is_manager)

    mock_notify = MagicMock(side_effect=RuntimeError("Error"))
    monkeypatch.setattr(worker_hook, "notify_manager_api", mock_notify)

    mock_discord = MagicMock()
    monkeypatch.setattr(worker_hook, "send_discord_notification", mock_discord)

    mock_exit = MagicMock(side_effect=SystemExit)
    monkeypatch.setattr("sys.exit", mock_exit)

    with pytest.raises(SystemExit):
        worker_hook.main()

    mock_notify.assert_called_once_with("http://manager", "secret", "worker_node", "drain")
    mock_discord.assert_called_once_with("http://discord", "Node shutting down")
    mock_exit.assert_called_once_with(1)
