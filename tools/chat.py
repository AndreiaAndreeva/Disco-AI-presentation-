"""Диалог с агентом: DeepSeek API + MCP-инструменты воронки.

Пользователь пишет в диалоге путь к CSV — агент сам читает файл через
`funnel_inspect`, подставляет периоды и считает. Ответ-записку скрипт
сохраняет в `memos/`.

    python3 -m funnel_tools.chat                 # диалог
    python3 -m funnel_tools.chat -q "..."        # один вопрос
    python3 -m funnel_tools.chat --selftest      # проверка связки без API
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .mcp_client import McpClient

ROOT = Path(__file__).resolve().parent.parent
PROMPT_PATH = ROOT / "AGENT_PROMPT_MCP.md"
MEMO_DIR = ROOT / "memos"
PREFIX = "mcp__funnel__"
MAX_STEPS = 8


# ---------------------------------------------------------------- API
def read_key(explicit: Optional[str]) -> str:
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key
    if explicit:
        for line in Path(explicit).read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            if name.strip() == "DEEPSEEK_API_KEY":
                return value.strip().strip('"').strip("'")
    raise SystemExit("нет DEEPSEEK_API_KEY: задайте переменную окружения или --key-file")


def openai_tools(mcp_tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{
        "type": "function",
        "function": {"name": t["name"], "description": t["description"],
                     "parameters": t["inputSchema"]},
    } for t in mcp_tools]


def complete(messages: List[Dict[str, Any]], tools: List[Dict[str, Any]], key: str,
             model: str, base_url: str) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"model": model, "messages": messages, "temperature": 0.2}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:400]}") from exc


def tool_text(res: Dict[str, Any]) -> str:
    if res.get("isError"):
        return "ОШИБКА: " + ((res.get("content") or [{}])[0].get("text") or "нет текста")
    payload = res.get("structuredContent")
    if payload is not None:
        return json.dumps(payload, ensure_ascii=False)
    return (res.get("content") or [{}])[0].get("text") or "{}"


# ---------------------------------------------------------------- данные
def detect_csv(text: str) -> Optional[Path]:
    """Ищет в сообщении путь к .csv и проверяет, что файл существует."""
    for raw in re.split(r"[\s`\"'()\[\],;]+", text):
        token = raw.strip(".:;!?")
        if not token.lower().endswith(".csv"):
            continue
        path = Path(token)
        if not path.is_absolute():
            path = ROOT / path
        if path.exists():
            return path.resolve()
    return None


def build_context(state: Dict[str, Any]) -> str:
    return (
        "КОНТЕКСТ ДАННЫХ\n"
        f"- csv_path: {state['csv']}\n"
        f"- change_log_path: {state['changes']}\n"
        f"- база: {state['base_start']}…{state['base_end']}\n"
        f"- текущий: {state['current_start']}…{state['current_end']}\n"
        "Если пользователь не назвал другие пути или даты — используй эти значения дословно.\n"
    )


def apply_csv(client: McpClient, state: Dict[str, Any], path: Path) -> Dict[str, Any]:
    """Читает файл через MCP и обновляет путь и периоды в состоянии."""
    res = client.call("funnel_inspect", {"csv_path": str(path)})
    if res.get("isError"):
        raise RuntimeError(tool_text(res))
    payload = res.get("structuredContent") or {}
    state["csv"] = str(path)
    suggested = payload.get("suggested_periods") or {}
    for key in ("base_start", "base_end", "current_start", "current_end"):
        if suggested.get(key):
            state[key] = suggested[key]
    sibling = path.parent / "change_log.csv"
    if sibling.exists():
        state["changes"] = str(sibling)
    return payload


def save_memo(text: str) -> Optional[Path]:
    if "# Записка" not in text:
        return None
    MEMO_DIR.mkdir(exist_ok=True)
    path = MEMO_DIR / f"memo_{datetime.now():%Y-%m-%d_%H%M%S}.md"
    path.write_text(text.rstrip() + "\n")
    return path


# ---------------------------------------------------------------- диалог
def turn(messages: List[Dict[str, Any]], client: McpClient, tools: List[Dict[str, Any]],
         ask: Callable[[List[Dict[str, Any]]], Dict[str, Any]],
         stats: Optional[Dict[str, int]] = None) -> str:
    """Один ход: модель может несколько раз позвать MCP-инструменты."""
    for _ in range(MAX_STEPS):
        message = ask(messages)["choices"][0]["message"]
        calls = message.get("tool_calls") or []
        if not calls:
            text = (message.get("content") or "").strip()
            messages.append({"role": "assistant", "content": text})
            return text
        messages.append({"role": "assistant", "content": message.get("content"),
                         "tool_calls": calls})
        for call in calls:
            name = call["function"]["name"]
            raw_name = name[len(PREFIX):] if name.startswith(PREFIX) else name
            try:
                arguments = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}
            print(f"  ↳ MCP tools/call {name}  {json.dumps(arguments, ensure_ascii=False)[:110]}")
            if raw_name == "funnel_save_memo" and stats is not None:
                stats["saved"] = 1
            result = tool_text(client.call(raw_name, arguments))
            print(f"    ← {result[:130]}{'…' if len(result) > 130 else ''}")
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
    return "не удалось завершить за 8 шагов: модель продолжает вызывать инструменты"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="funnel_tools.chat", description="Диалог с агентом через MCP")
    ap.add_argument("-q", "--question", help="один вопрос и выход")
    ap.add_argument("--key-file", help="файл с DEEPSEEK_API_KEY=...")
    ap.add_argument("--model", default=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"))
    ap.add_argument("--base-url", default=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
    ap.add_argument("--prompt", default=str(PROMPT_PATH))
    ap.add_argument("--csv", default=str(ROOT / "data" / "funnel_weekly_raw.csv"))
    ap.add_argument("--changes", default=str(ROOT / "data" / "change_log.csv"))
    ap.add_argument("--base-start", default="2026-09-07")
    ap.add_argument("--base-end", default="2026-10-11")
    ap.add_argument("--current-start", default="2026-10-12")
    ap.add_argument("--current-end", default="2026-10-25")
    ap.add_argument("--selftest", action="store_true", help="проверить MCP и сохранение записки без API")
    args = ap.parse_args(argv)

    prompt = Path(args.prompt).read_text()
    state: Dict[str, Any] = {
        "csv": args.csv, "changes": args.changes,
        "base_start": args.base_start, "base_end": args.base_end,
        "current_start": args.current_start, "current_end": args.current_end,
    }

    client = McpClient(quiet=True)
    try:
        client.initialize()
        tools = openai_tools(client.list_tools())
    except Exception as exc:
        print(f"ОШИБКА MCP: {exc}", file=sys.stderr)
        return 2

    messages: List[Dict[str, Any]] = [{"role": "system", "content": prompt + "\n\n" + build_context(state)}]

    def refresh_system() -> None:
        messages[0] = {"role": "system", "content": prompt + "\n\n" + build_context(state)}

    def show_data(payload: Dict[str, Any], path: Path) -> None:
        periods = payload.get("suggested_periods") or {}
        print(f"  данные: {path}")
        print(f"  недели: {payload.get('first_week')}…{payload.get('last_week')} "
              f"({payload.get('weeks_count')}), строк {payload.get('rows_unique')}, "
              f"дублей {payload.get('duplicates')}")
        if periods:
            print(f"  предложены периоды: база {periods['base_start']}…{periods['base_end']}, "
                  f"текущий {periods['current_start']}…{periods['current_end']}")

    try:
        if args.selftest:
            payload = apply_csv(client, state, Path(args.csv).resolve())
            show_data(payload, Path(args.csv))
            calls = {"n": 0}

            def fake_ask(msgs: List[Dict[str, Any]]) -> Dict[str, Any]:
                calls["n"] += 1
                if calls["n"] == 1:
                    return {"choices": [{"message": {
                        "role": "assistant", "content": None,
                        "tool_calls": [{"id": "call_1", "type": "function", "function": {
                            "name": PREFIX + "funnel_check",
                            "arguments": json.dumps({"csv_path": state["csv"]}),
                        }}],
                    }}]}
                return {"choices": [{"message": {
                    "role": "assistant",
                    "content": "# Записка: selftest\nПроверка связки: MCP и сохранение работают.",
                }}]}

            stats = {"saved": 0}
            text = turn(messages, client, tools, fake_ask, stats)
            saved = None if stats["saved"] else save_memo(text)
            print(f"агент> {text}")
            ok = calls["n"] == 2 and saved is not None and saved.exists()
            print(f"selftest: {'ok' if ok else 'FAIL'}; записка: {saved.relative_to(ROOT) if saved else '—'}")
            return 0 if ok else 3

        key = read_key(args.key_file)

        def ask(msgs: List[Dict[str, Any]]) -> Dict[str, Any]:
            return complete(msgs, tools, key, args.model, args.base_url)

        print(f"агент готов · модель {args.model} · MCP-инструментов {len(tools)}")
        print("подайте путь к CSV (например, data/funnel_weekly_raw.csv) и попросите записку")
        print("команды: /file <путь>, /tools, /reset, /exit\n")

        if args.question:
            path = detect_csv(args.question)
            if path:
                show_data(apply_csv(client, state, path), path)
                refresh_system()
            messages.append({"role": "user", "content": args.question})
            stats = {"saved": 0}
            text = turn(messages, client, tools, ask, stats)
            print("агент> " + text)
            saved = None if stats["saved"] else save_memo(text)
            if saved:
                print(f"записка сохранена: {saved.relative_to(ROOT)}")
            return 0

        while True:
            try:
                line = input("вы> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                continue
            if line in ("/exit", "/quit"):
                break
            if line == "/tools":
                print("  " + ", ".join(t["function"]["name"] for t in tools))
                continue
            if line == "/reset":
                messages = [{"role": "system", "content": prompt + "\n\n" + build_context(state)}]
                print("  история очищена")
                continue

            if line.startswith("/file "):
                try:
                    payload = apply_csv(client, state, Path(line[6:].strip()).resolve())
                    show_data(payload, Path(state["csv"]))
                    refresh_system()
                except Exception as exc:
                    print(f"  ОШИБКА: {exc}")
                continue

            path = detect_csv(line)
            if path:
                try:
                    payload = apply_csv(client, state, path)
                    show_data(payload, path)
                    refresh_system()
                except Exception as exc:
                    print(f"  ОШИБКА данных: {exc}")

            messages.append({"role": "user", "content": line})
            try:
                stats = {"saved": 0}
                text = turn(messages, client, tools, ask, stats)
                print("агент> " + text + "\n")
                saved = None if stats["saved"] else save_memo(text)
                if saved:
                    print(f"  записка сохранена: {saved.relative_to(ROOT)}\n")
            except Exception as exc:
                print(f"ОШИБКА: {exc}\n")
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
