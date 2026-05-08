import socket
import threading
import time

from fedora_nexus import cli


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_http_call_uses_configured_timeout(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout):
        captured["timeout"] = timeout
        return _FakeResponse(b'{"count":0,"repos":[]}')

    monkeypatch.setenv("FEDORA_NEXUS_HTTP_TIMEOUT_SECONDS", "1.25")
    monkeypatch.setattr(cli, "urlopen", fake_urlopen)

    result = cli._http_call("http://localhost:7832", "list_repos", {})

    assert result["count"] == 0
    assert captured["timeout"] == 1.25


def test_http_timeout_falls_back_to_default_for_invalid_value(monkeypatch):
    monkeypatch.setenv("FEDORA_NEXUS_HTTP_TIMEOUT_SECONDS", "not-a-number")

    assert cli._http_timeout_seconds() == 15.0


def test_http_call_fails_fast_when_server_stalls_headers(monkeypatch):
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.bind(("127.0.0.1", 0))
    server_sock.listen(1)
    port = server_sock.getsockname()[1]

    accepted = threading.Event()
    release = threading.Event()

    def stall_server():
        conn, _ = server_sock.accept()
        accepted.set()
        release.wait(timeout=1.0)
        conn.close()

    thread = threading.Thread(target=stall_server, daemon=True)
    thread.start()
    monkeypatch.setenv("FEDORA_NEXUS_HTTP_TIMEOUT_SECONDS", "0.05")

    start = time.monotonic()
    result = cli._http_call(f"http://127.0.0.1:{port}", "list_repos", {})
    elapsed = time.monotonic() - start

    release.set()
    thread.join(timeout=1.0)
    server_sock.close()

    assert accepted.is_set()
    assert elapsed < 0.5
    assert result["code"] in {"TOOL_ERROR", "SERVER_UNREACHABLE"}
    assert "error" in result

