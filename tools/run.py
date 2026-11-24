"""CLI: пошаговый запуск инструментов с печатью расчёта.

    python3 -m funnel_tools.run --all
    python3 -m funnel_tools.run --step periods
    python3 -m funnel_tools.run --all --json
    python3 -m funnel_tools.run --export-tools funnel_tools/tools.json
    python3 -m funnel_tools.run --step periods --base-end 2026-10-12   # ловушка с периодами
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

from . import core, specs

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "analysis" / "fixed"

STEP_ORDER = ["check", "periods", "decompose", "segments", "weekly", "events"]
STEP_TITLES = {
    "check": ("funnel_check", "проверка данных"),
    "periods": ("funnel_periods", "периоды и воронка"),
    "decompose": ("funnel_decompose", "разложение по этапам"),
    "segments": ("funnel_segments", "сегменты"),
    "weekly": ("funnel_weekly", "недели и дата перелома"),
    "events": ("funnel_events", "журнал изменений"),
}


def defaults() -> Dict[str, Any]:
    return {
        "csv_path": str(ROOT / "data" / "funnel_weekly_raw.csv"),
        "change_log_path": str(ROOT / "data" / "change_log.csv"),
        "base_start": "2026-09-07",
        "base_end": "2026-10-11",
        "current_start": "2026-10-12",
        "current_end": "2026-10-25",
        "by": ["platform"],
        "platform": None,
        "event_date": "2026-10-17",
    }


def spec_for(step: str) -> Dict[str, Any]:
    return specs.TOOLS[STEP_TITLES[step][0]]["spec"]


def args_for(step: str, base: Dict[str, Any]) -> Dict[str, Any]:
    name = STEP_TITLES[step][0]
    allowed = spec_for(step)["parameters"]["properties"].keys()
    return {k: base[k] for k in allowed if k in base and base[k] is not None}


def fmt_date(s: str) -> str:
    return f"{s[8:10]}.{s[5:7]}"


def sgn(x: float) -> str:
    return "0.00" if abs(x) < 0.005 else f"{x:+.2f}"


def ok(x: bool) -> str:
    return "✔" if x else "✘"


def print_checks(checks: List[Dict[str, Any]], indent: str = "  ") -> None:
    for c in checks:
        print(f"{indent}{ok(c['ok'])} {c['name']}: {c['detail']}")


# ---------------------------------------------------------------- шаги
def show_check(res: Dict[str, Any]) -> None:
    r = res["result"]
    print(f"  вход      : {r['rows_raw']} строк, {len(r['columns'])} колонок, {len(r['weeks'])} недель")
    rpw = sorted(set(r["rows_per_week"].values()))
    def rows_word(n):
        return "строка" if n % 10 == 1 and n % 100 != 11 else "строки" if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else "строк"
    per_week = f"по {rpw[0]} {rows_word(rpw[0])}" if len(rpw) == 1 else f"по {rpw[0]}–{rpw[-1]} строк"
    print(f"  недели    : {fmt_date(r['weeks'][0])} … {fmt_date(r['weeks'][-1])}, {per_week}")
    print(f"  сетка     : {len(r['weeks'])}×{len(r['platforms'])}×{len(r['categories'])}×{len(r['regions'])} = {r['rows_expected']} строк")
    if r["duplicates"]:
        for d in r["duplicates"]:
            key = d["key"]
            print(f"  дубль     : {fmt_date(key['week_start'])} / {key['platform']} / {key['category']} / {key['region_type']} — {d['rows']} строки, значения этапов {'совпадают' if d['identical_stage_values'] else 'РАЗЛИЧАЮТСЯ'}")
        print(f"  действие  : удалено {r['rows_raw'] - r['rows_unique']} строка, осталось {r['rows_unique']}")
    else:
        print("  дубль     : нет")
    print("  этапы     : " + ", ".join(f"{m['transition']} — нарушений {m['violations']}" for m in r["monotonicity"]))
    print("\n  проверки:")
    print_checks(r["checks"], "    ")


def table(rows: List[List[str]], header: List[str], align: str, indent: str = "  ") -> None:
    widths = [max(len(str(h)), *(len(str(r[i])) for r in rows)) if rows else len(str(h))
              for i, h in enumerate(header)]
    line = lambda cells: indent + "  ".join(
        str(c).rjust(w) if a == "r" else str(c).ljust(w) for c, w, a in zip(cells, widths, align))
    print(line(header))
    print(indent + "  ".join("-" * w for w in widths))
    for r in rows:
        print(line(r))


def show_periods(res: Dict[str, Any]) -> None:
    r = res["result"]
    a, f = r["assign"], r["funnel"]
    print(f"  недели    : база {fmt_date(a['periods']['base'][0])}…{fmt_date(a['periods']['base'][-1])} ({len(a['periods']['base'])}), "
          f"текущий {fmt_date(a['periods']['current'][0])}…{fmt_date(a['periods']['current'][-1])} ({len(a['periods']['current'])})")
    print("\n  проверки границ:")
    print_checks(a["checks"], "    ")
    b, c = f["periods"]["base"], f["periods"]["current"]
    rows = [[core.STAGE_RU[s], str(b["raw"][s]), str(c["raw"][s]),
             f"{b['share_of_visitors'][s]*100:.1f}%", f"{c['share_of_visitors'][s]*100:.1f}%"] for s in core.STAGES]
    print("\n  воронка:")
    table(rows, ["этап", "база", "текущий", "доля базы", "доля тек"], "lrrrr")
    print("\n  CVR       : " + " · ".join(
        f"{core.STAGE_RU[a_].split(' ')[0]}→{core.STAGE_RU[b_].split(' ')[0]} {b['rates'][l]*100:.2f}→{c['rates'][l]*100:.2f}"
        for a_, b_, l in core.TRANSITIONS))
    print(f"  конверсия : {b['overall']*100:.2f}% → {c['overall']*100:.2f}%  "
          f"Δ {f['overall_delta_pp']:+.2f} п.п. ({f['overall_delta_rel_pct']:+.1f}%)")
    print(f"  CI 95%    : [{b['overall_ci'][0]*100:.2f}; {b['overall_ci'][1]*100:.2f}] → "
          f"[{c['overall_ci'][0]*100:.2f}; {c['overall_ci'][1]*100:.2f}]")


def show_decompose(res: Dict[str, Any]) -> None:
    r = res["result"]
    print(f"  общая     : {r['overall']['base']*100:.2f}% → {r['overall']['current']*100:.2f}%  Δ {r['overall_delta_pp']:+.2f} п.п.")
    rows = [[s["transition"], f"{s['base']*100:.2f}", f"{s['current']*100:.2f}",
             f"{s['delta_pp']:+.2f}", f"{s['log_ratio']:+.4f}", f"{s['share_pct']:.1f}%"] for s in r["stages"]]
    print("\n  разложение:")
    table(rows, ["этап", "база", "текущий", "Δ п.п.", "ln-вклад", "доля"], "lrrrrr")
    print("\n  проверки:")
    print_checks(r["checks"], "    ")


def show_segments(res: Dict[str, Any]) -> None:
    r = res["result"]
    print(f"  по        : {', '.join(r['by'])}")
    rows = []
    for s in r["rows"]:
        label = " / ".join(str(v) for v in s["segment"].values())
        rows.append([label, str(s["visitors_base"]), str(s["visitors_current"]),
                     f"{s['conv_base']*100:.2f}", f"{s['conv_current']*100:.2f}",
                     f"{s['delta_pp']:+.2f}", f"{s['contribution_pp']:+.2f}"])
    print("\n  сегменты:")
    table(rows, ["сегмент", "визиты база", "визиты тек", "база %", "тек %", "Δ п.п.", "вклад"], "lrrrrrr")
    print(f"\n  rate/mix  : rate {sgn(r['rate_effect_pp'])} · mix {sgn(r['mix_effect_pp'])} · "
          f"interaction {sgn(r['interaction_pp'])} → сумма {sgn(r['rate_effect_pp']+r['mix_effect_pp']+r['interaction_pp'])} п.п.")
    print("\n  проверки:")
    print_checks(r["checks"], "    ")
    if r.get("drilldown"):
        d = r["drilldown"]
        print(f"\n  внутри худшего сегмента ({d['platform']}):")
        for dim, label in (("category", "категория"), ("region_type", "регион")):
            for s in d[dim]:
                name = " / ".join(str(v) for v in s["segment"].values())
                print(f"    {label:10s} {name:12s} {s['conv_base']*100:5.2f} → {s['conv_current']*100:5.2f} "
                      f"({s['delta_pp']:+.2f} п.п.), этап {min(s['stage_delta_pp'], key=s['stage_delta_pp'].get)} "
                      f"{s['stage_delta_pp'][min(s['stage_delta_pp'], key=s['stage_delta_pp'].get)]:+.2f} п.п.")


def show_weekly(res: Dict[str, Any]) -> None:
    r = res["result"]
    plats = list(r["by_platform"].keys())
    rows = [[fmt_date(w), f"{r['overall'][w]['conv']*100:.2f}"] +
            [f"{r['by_platform'][p][w]['conv']*100:.2f}" for p in plats] for w in r["weeks"]]
    print("  недельная конверсия, %:")
    table(rows, ["неделя", "итого", *plats], "l" + "r" * (len(plats) + 1))
    target = "mobile_web" if "mobile_web" in plats else plats[0]
    key = f"{target} | Слот→Форма"
    if key in r["stage_series"]:
        print(f"\n  {target} · слот→форма: " + " · ".join(
            f"{fmt_date(w)} {r['stage_series'][key][w]*100:.1f}" for w in r["weeks"]))
    print("\n  максимальные перепады неделя-к-неделе:")
    for b in r["breakpoints"][:4]:
        print(f"    {b['series']:34s} {fmt_date(b['from'])}→{fmt_date(b['to'])}  {b['delta_pp']:+.2f} п.п.")
    o = r["onset"]
    if o and o.get("ok"):
        print(f"\n  onset-тест ({o['platform']}, {o['stage']}, событие {fmt_date(o['event_date'])}):")
        print(f"    уровень до {o['p_before']*100:.2f}% · в неделе события {o['observed']*100:.2f}% · "
              f"полный эффект {o['p_full']*100:.2f}%")
        print(f"    ожидание при старте ровно в дату: {o['expected_if_onset_on_event']*100:.2f}% "
              f"(эффект захватил {o['affected_fraction_of_week']*100:.0f}% недели)")
        p_txt = "< 1e-16" if o["p_value"] < 1e-16 else f"{o['p_value']:.2e}"
        print(f"    z = {o['z']:.1f} · p {p_txt} · подразумеваемая доля недели под эффектом = "
              f"{o['implied_affected_share_of_week']*100:.0f}%")
        print(f"    вывод: {o['verdict']}")
    print("\n  проверки:")
    print_checks(r["checks"], "    ")


def show_events(res: Dict[str, Any]) -> None:
    r = res["result"]
    rows = []
    for e in r["rows"]:
        scope_delta = "—" if e["delta_scope_pp"] is None else f"{e['delta_scope_pp']:+.2f}"
        ctrl = "—" if e.get("delta_control_pp") is None else f"{e['delta_control_pp']:+.2f}"
        stage = e.get("worst_stage") or "—"
        stage_delta = "—" if e.get("worst_stage_delta_pp") is None else f"{e['worst_stage_delta_pp']:+.2f}"
        rows.append([fmt_date(e["date"]), e["scope"], f"{stage} {stage_delta}", scope_delta, ctrl, e["verdict"]])
    print("  события:")
    table(rows, ["дата", "scope", "худший этап", "Δ scope", "Δ прочие", "вердикт"], "llrrrl")
    print("  Δ scope — спад в сегменте события; Δ прочие — остальные платформы (в них входит mobile_web).")
    print("  Для событий scope=all Δ scope — это спад за период целиком, а не эффект события.")
    print(f"\n  примечание: {r['note']}")


def show_compat(res: Dict[str, Any]) -> None:
    r = res["result"]
    m = r["meta"]
    print(f"  meta       : base_rows={m['base_rows']}, current_rows={m['current_rows']}, "
          f"duplicate_keys_found={m['duplicate_keys_found']}, rows_removed={m['rows_removed']}")
    print(f"  воронка    : {r['base_period']['overall']:.2f}% → {r['current_period']['overall']:.2f}% "
          f"(Δ {r['delta_pp']['overall']:+.2f} п.п.)")
    worst = min(r["stage_delta_pp"], key=r["stage_delta_pp"].get)
    print(f"  этапы      : худший — {worst} {r['stage_delta_pp'][worst]:+.2f} п.п. "
          f"(ln {r['stage_contribution'][worst]:+.4f}, {r['stage_contribution_share_pct'][worst]:.1f}% вклада)")
    print("  сегменты   : " + " · ".join(
        f"{k} {v['base_overall']:.2f}→{v['current_overall']:.2f} ({v['delta']:+.2f})"
        for k, v in r["segments"].items()))
    ts = r["data_quality"]["traffic_shift"]
    print(f"  сдвиг      : mobile_web {ts['platform']['base'].get('mobile_web', 0)*100:.2f}% → "
          f"{ts['platform']['current'].get('mobile_web', 0)*100:.2f}% визитов; mix {sgn(ts['mix_effect_pp'])} п.п.")
    w12 = next((w for w in r["weekly_dynamics"] if w["week_start"] == "2026-10-12"), None)
    if w12:
        print(f"  перелом    : {w12['week_start']}: overall {w12['overall_conv']:.2f}%, "
              f"mobile_web {w12['by_platform'].get('mobile_web', 0):.2f}%, этап {w12['worst_stage_conv']:.2f}%")
    o = r.get("onset") or {}
    if o.get("ok"):
        print(f"  onset      : событие {o['event_date']} → ожидание {o['expected_if_onset_on_event']*100:.2f}%, "
              f"факт {o['observed']*100:.2f}%, z {o['z']:.1f}, доля недели {o['implied_affected_share_of_week']*100:.0f}%")
    print(f"  события    : {len(r['all_changes'])} записей в журнале")
    print(f"  plot_path  : {r['plot_path']}")


SHOW = {"check": show_check, "periods": show_periods, "decompose": show_decompose,
        "segments": show_segments, "weekly": show_weekly, "events": show_events}


def run_step(step: str, base: Dict[str, Any], save: bool = True, verbose: bool = True) -> Dict[str, Any]:
    name, title = STEP_TITLES[step]
    a = args_for(step, base)
    if verbose:
        shown = {k: v for k, v in a.items() if k not in ("csv_path", "change_log_path")}
        tail = f"  ({', '.join(f'{k}={v!r}' for k, v in shown.items())})" if shown else ""
        print(f"\n════ STEP {STEP_ORDER.index(step)+1}/{len(STEP_ORDER)} · {name} — {title} ════")
        print(f"$ python3 -m funnel_tools.run --step {step}{tail}")
    res = specs.call(name, a)
    if save:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / f"step{STEP_ORDER.index(step)+1}_{step}.json").write_text(
            json.dumps(res, ensure_ascii=False, indent=2))
    if verbose:
        SHOW[step](res)
    return res


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="funnel_tools.run", description="Пошаговый анализ воронки")
    ap.add_argument("--step", choices=STEP_ORDER)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--json", action="store_true", help="печатать сырой JSON результата")
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--csv", default=defaults()["csv_path"])
    ap.add_argument("--changes", default=defaults()["change_log_path"])
    for k, v in defaults().items():
        if k in core.PERIOD_KEYS:
            ap.add_argument("--" + k.replace("_", "-"), default=v)
    ap.add_argument("--by", default="platform", help="platform / category / region_type через запятую")
    ap.add_argument("--platform", default=None)
    ap.add_argument("--event-date", default="2026-10-17")
    ap.add_argument("--compat", action="store_true", help="вызвать analyze_funnel (исходная схема ответа)")
    ap.add_argument("--export-tools", metavar="PATH", help="сохранить JSON-схемы инструментов")
    args = ap.parse_args(argv)

    if args.export_tools:
        Path(args.export_tools).write_text(json.dumps(specs.SCHEMAS, ensure_ascii=False, indent=2))
        print(f"схемы инструментов: {args.export_tools} ({len(specs.SCHEMAS)} шт.)")
        return 0

    base = defaults()
    base.update({
        "csv_path": args.csv, "change_log_path": args.changes,
        "by": [p.strip() for p in args.by.split(",") if p.strip()],
        "platform": args.platform, "event_date": args.event_date or None,
    })
    for k in core.PERIOD_KEYS:
        base[k] = getattr(args, k)

    if args.compat:
        cargs = {"csv_path": base["csv_path"], "change_log_path": base["change_log_path"],
                 "group_by": "platform", "event_date": "2026-10-17",
                 **{k: base[k] for k in core.PERIOD_KEYS}}
        print("════ analyze_funnel — исходная схема ответа ════")
        print("$ python3 -m funnel_tools.run --compat")
        res = specs.call("analyze_funnel", cargs)
        payload = res["result"]  # сохраняем без обёртки: схема как у исходного инструмента
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "analyze_funnel.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            show_compat(res)
            print(f"\n  полный JSON: {(OUT / 'analyze_funnel.json').relative_to(ROOT)}")
        return 0

    steps = STEP_ORDER if args.all else ([args.step] if args.step else ["check"])
    results: Dict[str, Any] = {}
    try:
        for step in steps:
            results[step] = run_step(step, base, save=not args.no_save, verbose=not args.json)
            if args.json:
                print(json.dumps(results[step], ensure_ascii=False, indent=2))
    except specs.ToolError as exc:
        print(f"\nОШИБКА: {exc}", file=sys.stderr)
        return 2

    if args.all and not args.json:
        f = results["periods"]["result"]["funnel"]
        seg = results["segments"]["result"]
        o = results["weekly"]["result"]["onset"]
        worst = seg["rows"][0]
        name = " / ".join(str(v) for v in worst["segment"].values())
        print("\n════ ИТОГ ════")
        print(f"  конверсия {f['periods']['base']['overall']*100:.2f}% → {f['periods']['current']['overall']*100:.2f}% "
              f"({f['overall_delta_pp']:+.2f} п.п.); худший сегмент {name} ({worst['delta_pp']:+.2f} п.п.)")
        print(f"  перелом: неделя {results['weekly']['result']['breakpoints'][0]['to'][8:10]}."
              f"{results['weekly']['result']['breakpoints'][0]['to'][5:7]}; "
              f"onset: {o['verdict'] if o else 'нет'}")
        print(f"  шаги сохранены в {OUT.relative_to(ROOT)}/step1..step{len(steps)}.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
