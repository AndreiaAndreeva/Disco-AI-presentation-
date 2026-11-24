"""MCP-клиент: ходит в funnel_tools.mcp_server по протоколу JSON-RPC (stdio).

Показывает ровно то, что происходит на проводе: initialize → tools/list →
tools/call. Полный лог пишется в analysis/fixed/mcp_session.jsonl.

    python3 -m funnel_tools.mcp_client --list
    python3 -m funnel_tools.mcp_client --call funnel_check --args '{"csv_path":"data/funnel_weekly_raw.csv"}'
    python3 -m funnel_tools.mcp_client --run-all
"""
from __future__ import annotations

import argparse
import json
import select
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = ROOT / "analysis" / "fixed" / "mcp_session.jsonl"
PROTOCOL_VERSION = "2025-06-18"


class McpClient:
    def __init__(self, raw: bool = False, timeout: float = 180.0, quiet: bool = False):
        self.raw = raw
        self.quiet = quiet
        self.timeout = timeout
        self.log: List[Dict[str, Any]] = []
        self._id = 0
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "funnel_tools.mcp_server"],
            cwd=str(ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            env={**__import__("os").environ, "MPLCONFIGDIR": str(ROOT / ".mplconfig")},
        )

    # ------------------------------------------------------------ транспорт
    def _send(self, payload: Dict[str, Any]) -> None:
        assert self.proc.stdin
        self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def _read(self) -> Dict[str, Any]:
        assert self.proc.stdout
        ready, _, _ = select.select([self.proc.stdout], [], [], self.timeout)
        if not ready:
            raise TimeoutError(f"MCP-сервер не ответил за {self.timeout} с")
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError("MCP-сервер закрыл stdout")
        return json.loads(line)

    def notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self._send(payload)
        if not self.raw and not self.quiet:
            print(f"→ {method} (нотификация)")
        self.log.append({"dir": "→", "message": payload})

    def request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            payload["params"] = params
        self._send(payload)
        response = self._read()
        self.log.append({"dir": "→", "message": payload})
        self.log.append({"dir": "←", "message": response})
        if self.raw:
            print(f"→ {method} (id={self._id})")
            print("← " + json.dumps(response, ensure_ascii=False)[:400])
        if "error" in response:
            raise RuntimeError(f"MCP error {response['error']['code']}: {response['error']['message']}")
        return response

    def close(self) -> None:
        if self.proc.stdin:
            self.proc.stdin.close()
        self.proc.terminate()
        self.proc.wait(timeout=5)

    # ------------------------------------------------------------ сценарий
    def initialize(self) -> Dict[str, Any]:
        resp = self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "clientInfo": {"name": "funnel-cli", "version": "1.0.0"},
        })
        info = resp["result"]
        if not self.raw and not self.quiet:
            print(f"← протокол {info['protocolVersion']} · сервер {info['serverInfo']['name']} "
                  f"{info['serverInfo']['version']} · capabilities {list(info['capabilities'])}")
        self.notify("notifications/initialized")
        return info

    def list_tools(self) -> List[Dict[str, Any]]:
        resp = self.request("tools/list")
        tools = resp["result"]["tools"]
        if not self.raw and not self.quiet:
            print(f"← инструментов: {len(tools)}")
            for t in tools:
                required = ", ".join(t["inputSchema"].get("required", []))
                print(f"    {t['name']:16s} обязательные: {required}")
        return tools

    def call(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if not self.raw and not self.quiet:
            print(f"\n→ tools/call {name}  аргументы: {json.dumps(arguments, ensure_ascii=False)[:110]}")
        resp = self.request("tools/call", {"name": name, "arguments": arguments})
        res = resp["result"]
        payload = res.get("structuredContent", {})
        if not self.raw and not self.quiet:
            if res.get("isError"):
                text = (res.get("content") or [{}])[0].get("text", "нет текста")
                print(f"← ОШИБКА · {text[:180]}")
            else:
                print(f"← ok · {summary(name, payload)}")
        return res


def summary(name: str, p: Dict[str, Any]) -> str:
    if not p:
        return "пусто"
    try:
        if name == "funnel_check":
            return (f"{p['rows_raw']}→{p['rows_unique']} строк, дублей {len(p['duplicates'])}, "
                    f"недель {len(p['weeks'])}")
        if name == "funnel_periods":
            f = p["funnel"]
            return (f"{f['periods']['base']['overall']*100:.2f}% → "
                    f"{f['periods']['current']['overall']*100:.2f}% "
                    f"({f['overall_delta_pp']:+.2f} п.п.)")
        if name == "funnel_decompose":
            worst = min(p["stages"], key=lambda s: s["log_ratio"])
            return f"худший этап {worst['transition']} {worst['delta_pp']:+.2f} п.п., доля {worst['share_pct']:.1f}%"
        if name == "funnel_segments":
            worst = p["rows"][0]
            label = " / ".join(str(v) for v in worst["segment"].values())
            return f"худший {label} {worst['delta_pp']:+.2f} п.п., вклад {worst['contribution_pp']:+.2f}"
        if name == "funnel_weekly":
            b = p["breakpoints"][0]
            return f"перелом {b['from']}→{b['to']} {b['delta_pp']:+.2f} п.п. ({b['series']})"
        if name == "funnel_events":
            return f"событий {len(p['rows'])}; совпало: " + ", ".join(
                r["date"] for r in p["rows"] if r["verdict"].startswith("совпадает")) or "нет"
        if name == "analyze_funnel":
            worst = min(p["segments"], key=lambda k: p["segments"][k]["delta"])
            return (f"{p['base_period']['overall']:.2f}% → {p['current_period']['overall']:.2f}% "
                    f"({p['delta_pp']['overall']:+.2f}); худший {worst} {p['segments'][worst]['delta']:+.2f}")
    except Exception:
        pass
    return json.dumps(p, ensure_ascii=False)[:120]


def defaults() -> Dict[str, Dict[str, Any]]:
    csv = str(ROOT / "data" / "funnel_weekly_raw.csv")
    ch = str(ROOT / "data" / "change_log.csv")
    periods = {"base_start": "2026-09-07", "base_end": "2026-10-11",
               "current_start": "2026-10-12", "current_end": "2026-10-25"}
    return {
        "funnel_check": {"csv_path": csv},
        "funnel_periods": {"csv_path": csv, **periods},
        "funnel_decompose": {"csv_path": csv, **periods},
        "funnel_segments": {"csv_path": csv, **periods, "by": ["platform"]},
        "funnel_weekly": {"csv_path": csv, **periods, "event_date": "2026-10-17"},
        "funnel_events": {"csv_path": csv, **periods, "change_log_path": ch},
        "analyze_funnel": {"csv_path": csv, "change_log_path": ch, **periods,
                           "group_by": "platform", "event_date": "2026-10-17"},
    }


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="funnel_tools.mcp_client")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--call")
    ap.add_argument("--args", default="{}")
    ap.add_argument("--run-all", action="store_true")
    ap.add_argument("--raw", action="store_true", help="печатать сырые JSON-RPC сообщения")
    args = ap.parse_args(argv)

    client = McpClient(raw=args.raw)
    try:
        print("MCP stdio · " + " ".join([sys.executable, "-m", "funnel_tools.mcp_server"]))
        client.initialize()
        client.list_tools()

        if args.call:
            client.call(args.call, json.loads(args.args))
        elif args.run_all:
            for name, payload in defaults().items():
                client.call(name, payload)
        print("\nсессия MCP: ok")
    except Exception as exc:
        print(f"\nОШИБКА MCP: {exc}", file=sys.stderr)
        return 2
    finally:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOG_PATH.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in client.log) + "\n")
        client.close()
        print(f"лог JSON-RPC: {LOG_PATH.relative_to(ROOT)} ({len(client.log)} сообщений)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
