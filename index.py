"""FastAPI entrypoint for the complete JOKERS IA Vercel deployment.

The existing APIHandler remains the single source of application behavior;
this thin ASGI adapter lets Vercel route the UI and same-origin API through
one supported FastAPI entrypoint.
"""
from __future__ import annotations

import asyncio
import io
import json
import threading
from email.message import Message
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import Response

from api import APIHandler
from database import init_db

_db_initialized = False
_db_init_lock = threading.Lock()
_MAX_REQUEST_BODY_BYTES = 2_000_000

app = FastAPI(
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


class _CapturedRequest(APIHandler):
    """Minimal BaseHTTPRequestHandler surface backed by an ASGI request."""

    def __init__(self, method: str, path: str, headers: Message, body: bytes):
        self.command = method
        self.path = path
        self.headers = headers
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.status_code = 200
        self.response_headers = []

    def send_response(self, code, message=None):
        self.status_code = int(code)

    def send_header(self, keyword, value):
        self.response_headers.append((str(keyword), str(value)))

    def end_headers(self):
        return None


def _ensure_database():
    global _db_initialized
    if _db_initialized:
        return
    with _db_init_lock:
        if not _db_initialized:
            init_db()
            _db_initialized = True


def _execute_handler(handler: _CapturedRequest, method: str, request_path: str) -> None:
    parsed_path = urlparse(request_path).path
    if parsed_path.startswith("/api/") and parsed_path != "/api/health":
        _ensure_database()
    handler._dispatch(method)


def _request_too_large() -> Response:
    payload = json.dumps(
        {"error": "A requisição excede o limite permitido."},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return Response(
        content=payload,
        status_code=413,
        media_type="application/json",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "strict-origin-when-cross-origin",
        },
    )


async def _dispatch_request(request: Request) -> Response:
    path = request.scope.get("path", "/")
    body = b""
    if path.startswith("/api/") and request.method in ("POST", "PUT", "PATCH"):
        declared_length = request.headers.get("content-length")
        if declared_length:
            try:
                if int(declared_length) > _MAX_REQUEST_BODY_BYTES:
                    return _request_too_large()
            except ValueError:
                # Leave malformed Content-Length handling to APIHandler._body().
                pass
        chunks = bytearray()
        async for chunk in request.stream():
            if len(chunks) + len(chunk) > _MAX_REQUEST_BODY_BYTES:
                return _request_too_large()
            chunks.extend(chunk)
        body = bytes(chunks)

    headers = Message()
    for name, value in request.scope.get("headers", ()):
        headers[name.decode("latin-1")] = value.decode("latin-1")
    if body and not headers.get("Content-Length"):
        headers["Content-Length"] = str(len(body))

    query = request.scope.get("query_string", b"").decode("latin-1")
    request_path = path + (f"?{query}" if query else "")
    handler = _CapturedRequest(request.method, request_path, headers, body)

    try:
        await asyncio.to_thread(_execute_handler, handler, request.method, request_path)
    except Exception as error:
        print("Vercel FastAPI adapter error:", repr(error))
        payload = json.dumps({
            "error": "Banco indisponível ou não configurado. Verifique DATABASE_URL e os logs da função."
        }).encode("utf-8")
        handler.status_code = 503
        handler.response_headers = [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(payload))),
            ("Cache-Control", "no-store"),
        ]
        handler.wfile = io.BytesIO(payload)

    response_headers = {}
    for name, value in handler.response_headers:
        response_headers[name] = value
    return Response(
        content=handler.wfile.getvalue(),
        status_code=handler.status_code,
        headers=response_headers,
    )


_ALLOWED_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
app.add_api_route("/", _dispatch_request, methods=_ALLOWED_METHODS, include_in_schema=False)
app.add_api_route("/{path:path}", _dispatch_request, methods=_ALLOWED_METHODS, include_in_schema=False)
