"""MCP-сервер (stdio) с инструментами анализа воронки.

Протокол: JSON-RPC 2.0, по одному сообщению на строку (stdio transport).
Поддерживает: initialize, notifications/initialized, tools/list, tools/call, ping.

Запуск:
    python3 -m funnel_tools.mcp_server

Логи идут в stderr, протокол — только в stdout, без примесей.
"""
from __future__ import annotations

import json
import sys
import traceback
from typing import Any, Dict, Optional

from . import core, specs

SERVER_NAME = "funnel-tools"
SERVER_VERSION = "1.0.0"
SUPPORTED_VERSIONS = ["2024-11-05", "2025-03-26", "2025-06-18"]
DEFAULT_VERSION = "2025-06-18"

PROTOCOL_ERROR = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def log(msg: str) -> None:
    print(f"[funnel-mcp] {msg}", file=sys.stderr, flush=True)


def send(payload: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def result(req_id: Any, value: Dict[str, Any]) -> None:
    send({"jsonrpc": "2.0", "id": req_id, "result": value})


def error(req_id: Any, code: int, message: str) -> None:
    send({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


def tool_specs() -> list:
    out = []
    for schema in specs.SCHEMAS:
        params = dict(schema["parameters"])
        params.setdefault("type", "object")
        params.setdefault("additionalProperties", False)
        out.append({
            "name": schema["name"],
            "description": schema["description"],
            "inputSchema": params,
        })
    return out


def handle_initialize(req_id: Any, params: Dict[str, Any]) -> None:
    requested = params.get("protocolVersion")
    version = requested if requested in SUPPORTED_VERSIONS else DEFAULT_VERSION
    result(req_id, {
        "protocolVersion": version,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        "instructions": (
            "Инструменты анализа воронки записи. Начни с funnel_check, затем "
            "funnel_periods, funnel_decompose, funnel_segments, funnel_weekly, "
            "funnel_events. analyze_funnel отдаёт всё сразу в исходной схеме."
        ),
    })


def handle_tools_list(req_id: Any) -> None:
    result(req_id, {"tools": tool_specs()})


def handle_tools_call(req_id: Any, params: Dict[str, Any]) -> None:
    name = params.get("name")
    arguments = params.get("arguments") or {}
    if not name:
        error(req_id, INVALID_PARAMS, "не передан name инструмента")
        return
    if not isinstance(arguments, dict):
        error(req_id, INVALID_PARAMS, "arguments должен быть объектом")
        return

    try:
        res = specs.call(str(name), arguments)
    except specs.ToolError as exc:
        log(f"tool {name} -> ToolError: {exc}")
        result(req_id, {
            "content": [{"type": "text", "text": f"ОШИБКА: {exc}"}],
            "isError": True,
        })
        return
    except Exception as exc:  # непредвиденная ошибка: не роняем сервер
        log(f"tool {name} -> {type(exc).__name__}: {exc}")
        traceback.print_exc(file=sys.stderr)
        result(req_id, {
            "content": [{"type": "text", "text": f"ВНУТРЕННЯЯ ОШИБКА: {type(exc).__name__}: {exc}"}],
            "isError": True,
        })
        return

    payload = core.json_clean(res.get("result", res))
    log(f"tool {name} -> ok")
    out: Dict[str, Any] = {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
        "isError": not res.get("ok", True),
    }
    if isinstance(payload, dict):
        out["structuredContent"] = payload
    result(req_id, out)


def handle(message: Dict[str, Any]) -> None:
    method = message.get("method")
    req_id = message.get("id")
    params = message.get("params") or {}

    if req_id is None:  # нотификации: initialize/initialized, cancelled
        if method == "notifications/initialized":
            log("client initialized")
        return

    if method == "initialize":
        handle_initialize(req_id, params)
    elif method == "tools/list":
        handle_tools_list(req_id)
    elif method == "tools/call":
        handle_tools_call(req_id, params)
    elif method == "ping":
        result(req_id, {})
    elif method == "logging/setLevel":
        result(req_id, {})
    else:
        error(req_id, METHOD_NOT_FOUND, f"метод {method!r} не поддерживается")


def main() -> int:
    log(f"{SERVER_NAME} {SERVER_VERSION} готов, ожидание JSON-RPC на stdin")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            send({"jsonrpc": "2.0", "id": None, "error": {"code": PROTOCOL_ERROR, "message": f"битый JSON: {exc}"}})
            continue
        try:
            handle(message)
        except Exception as exc:  # последняя линия защиты
            log(f"необработанная ошибка: {exc}")
            traceback.print_exc(file=sys.stderr)
            if message.get("id") is not None:
                error(message["id"], INTERNAL_ERROR, str(exc))
    log("stdin закрыт, выход")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
