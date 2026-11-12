"""Access this app's Supervisor settings through its local socket or legacy token."""
from __future__ import annotations

import http.client
import json
import os
import socket

MAX_RESPONSE_BYTES = 4 * 1024 * 1024
SELF_PATHS = {"info", "options", "restart"}


class SupervisorError(RuntimeError):
    """Safe diagnostics: never include request/response bodies or credentials."""

    def __init__(self, code, http_status=None):
        super().__init__(code)
        self.code = code
        self.http_status = http_status


def supervisor_available():
    return supervisor_transport() != "unavailable"


def supervisor_transport():
    if os.environ.get("SUPERVISOR_SOCKET"):
        return "unix"
    return "tcp" if os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HASSIO_TOKEN") else "unavailable"


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("supervisor", timeout=5)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        try:
            self.sock.connect(self.path)
        except OSError:
            self.sock.close()
            self.sock = None
            raise


def request_self(path, payload=None, *, token=None):
    """Use only this app's settings/restart API; Unix peers need no bearer token."""
    if path not in SELF_PATHS or path == "info" and payload is not None or path == 'restart' and payload != {}:
        raise ValueError("unsupported_supervisor_path")
    socket_path = os.environ.get("SUPERVISOR_SOCKET")
    token = token or os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HASSIO_TOKEN")
    if socket_path:
        if not os.path.isabs(socket_path):
            raise SupervisorError("supervisor_socket_invalid")
        connection = _UnixHTTPConnection(socket_path)
    elif token:
        # Fixed local host: credentials and camera settings never use a proxy.
        connection = http.client.HTTPConnection("supervisor", timeout=5)
    else:
        raise SupervisorError("supervisor_unavailable")
    headers = {"Content-Type": "application/json"}
    if not socket_path:
        headers["Authorization"] = "Bearer " + token
    body = json.dumps(payload).encode() if payload is not None else None
    try:
        connection.request("POST" if payload is not None else "GET",
                           "/addons/self/" + path, body=body, headers=headers)
        response = connection.getresponse()
        if response.status != 200:
            raise SupervisorError("supervisor_http_error", response.status)
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise SupervisorError("supervisor_response_too_large")
        result = json.loads(raw)
        if not isinstance(result, dict) or result.get("result") != "ok":
            raise SupervisorError("supervisor_rejected")
        data = result.get("data") or {}
        if not isinstance(data, dict):
            raise SupervisorError("supervisor_response_invalid")
        return data
    except (OSError, http.client.HTTPException):
        raise SupervisorError("supervisor_connection_failed") from None
    except (ValueError, UnicodeError):
        raise SupervisorError("supervisor_response_invalid") from None
    finally:
        connection.close()
