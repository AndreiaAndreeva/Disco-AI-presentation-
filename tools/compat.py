"""Совместимый инструмент `analyze_funnel` с исходной схемой ответа.

Нужен, чтобы системный промпт из original/agent.py исполнялся дословно:
пункт 1 требует вызвать `analyze_funnel` и читать поля
meta / base_period / current_period / delta_pp / stage_contribution /
segments / weekly_dynamics / all_changes / plot_path / data_quality.

Считает по исправленной логике (дедупликация, проверка периодов, трафик по
визитам, onset-тест), но отдаёт ту же структуру плюс несколько полей,
которых не хватало: знаменатели сегментов, доля вклада этапа, onset.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from . import core
from .specs import ToolError

ROOT = Path(__file__).resolve().parent.parent
PLOT_PATH = ROOT / "outputs" / "funnel_plot_fixed.png"
PLATFORM_ORDER = ["desktop_web", "mobile_web", "ios", "android"]
COLORS = {"desktop_web": "#4C78A8", "mobile_web": "#E45756", "ios": "#54A24B", "android": "#B279A2"}


def _rates(f: Dict[str, Any], period: str) -> Dict[str, float]:
    p = f["periods"][period]
    out = {f"{a} -> {b}": round(p["rates"][label] * 100, 2) for a, b, label in core.TRANSITIONS}
    out["overall"] = round(p["overall"] * 100, 2)
    return out


def _plot(wk: Dict[str, Any], path: Path) -> None:
    import os

    os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".mplconfig"))
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"figure.dpi": 140, "font.size": 10, "axes.grid": True,
                         "grid.alpha": 0.25, "axes.spines.top": False, "axes.spines.right": False})
    weeks = wk["weeks"]
    xs = list(range(len(weeks)))
    fig, ax = plt.subplots(figsize=(9, 4.6))
    for p in PLATFORM_ORDER:
        if p in wk["by_platform"]:
            ax.plot(xs, [wk["by_platform"][p][w]["conv"] * 100 for w in weeks],
                    marker="o", lw=2, label=p, color=COLORS.get(p))
    ax.axvspan(4.5, len(weeks) - 0.5, color="orange", alpha=0.08)
    ax.axvline(4.5, color="grey", ls="--", lw=1)
    ax.text(4.56, 30.3, "12.10 — начало текущего периода", fontsize=7.5, color="dimgrey")
    x_ev = weeks.index("2026-10-12") + 5 / 7  # 17.10 внутри недели 12.10
    ax.axvline(x_ev, color="#E45756", ls=":", lw=1.4)
    ax.text(x_ev - 0.09, 22.2, "17.10 — флаг", fontsize=7.5,
            color="#E45756", rotation=90, va="center", ha="center")
    ax.set_xticks(xs)
    ax.set_xticklabels([f"{w[8:10]}.{w[5:7]}" for w in weeks])
    ax.set_ylim(15.5, 31)
    ax.set_ylabel("% визитов → запись")
    ax.set_xlabel("Неделя (начало)")
    ax.set_title("Конверсия в запись по платформам")
    ax.legend(ncol=4, frameon=False, loc="lower left")
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)


def analyze_funnel(args: Dict[str, Any]) -> Dict[str, Any]:
    csv_path = args.get("csv_path")
    if not csv_path:
        raise ToolError("не передан csv_path")
    periods = {k: str(args[k]) for k in core.PERIOD_KEYS if k in args}
    if len(periods) != 4:
        raise ToolError("нужны base_start, base_end, current_start, current_end")
    group_by = str(args.get("group_by") or "platform")

    df, report = core.load(str(csv_path))
    assign = core.assign_periods(df, **periods)
    if not assign["ok"]:
        bad = [c for c in assign["checks"] if not c["ok"]]
        raise ToolError("периоды не прошли проверку: " + "; ".join(f"{c['name']} ({c['detail']})" for c in bad))

    df = df.copy()
    df["period"] = df.week_start.dt.strftime("%Y-%m-%d").map(assign["week_owner"])

    f = core.funnel(df, assign, **periods)
    dec = core.decompose(df, assign, **periods)
    seg = core.segments(df, assign, [group_by], **periods)
    event_date = args.get("event_date") or "2026-10-17"
    wk = core.weekly(df, assign, event_date=event_date)

    # координаты события: первая неделя текущего периода
    worst = min(dec["stages"], key=lambda s: s["log_ratio"])
    worst_stage = worst["transition"]
    wa, wb_ = next((a, b) for a, b, label in core.TRANSITIONS if label == worst_stage)
    worst_all = {}
    for w in wk["weeks"]:
        wd = df[df.week_start == pd.Timestamp(w)]
        denom = int(wd[wa].sum())
        worst_all[w] = (float(wd[wb_].sum()) / denom) if denom else 0.0

    segments = {}
    for r in seg["rows"]:
        label = " / ".join(str(v) for v in r["segment"].values())
        segments[label] = {
            "base_overall": round(r["conv_base"] * 100, 2),
            "current_overall": round(r["conv_current"] * 100, 2),
            "delta": round(r["delta_pp"], 2),
            "visitors_base": r["visitors_base"],
            "visitors_current": r["visitors_current"],
            "contribution_pp": round(r["contribution_pp"], 2),
            "stage_delta_pp": {k: round(v, 2) for k, v in r["stage_delta_pp"].items()},
        }

    changes: List[Dict[str, str]] = []
    if args.get("change_log_path"):
        ch = pd.read_csv(str(args["change_log_path"]), parse_dates=["date"])
        changes = [{"date": str(pd.Timestamp(r.date).date()), "scope": str(r.scope),
                    "type": str(r.event_type), "description": str(r.description)}
                   for r in ch.itertuples()]

    traffic_shift = {}
    for dim in ("platform", "region_type", "category"):
        b = df[df.period == "base"].groupby(dim).visitors.sum()
        c = df[df.period == "current"].groupby(dim).visitors.sum()
        traffic_shift[dim] = {
            "base": {k: round(v, 4) for k, v in (b / b.sum()).items()},
            "current": {k: round(v, 4) for k, v in (c / c.sum()).items()},
        }
    traffic_shift["mix_effect_pp"] = round(seg["mix_effect_pp"], 3)

    anomalies = []
    for m in report["monotonicity"]:
        for ex in m["examples"]:
            anomalies.append(f"нарушение монотонности {m['transition']}: {ex}")

    plot = PLOT_PATH
    plot_note = None
    try:
        _plot(wk, plot)
    except Exception as exc:  # график не должен ломать расчёт
        plot_note = f"график не построен: {exc}"
        plot = None

    return core.json_clean({
        "meta": {
            "base_rows": int(f["periods"]["base"]["rows"]),
            "current_rows": int(f["periods"]["current"]["rows"]),
            "duplicate_keys_found": len(report["duplicates"]),
            "rows_removed": report["rows_raw"] - report["rows_unique"],
            "quality_checks": report["checks"],
            "period_checks": assign["checks"],
        },
        "periods": assign["periods"],
        "base_period": _rates(f, "base"),
        "current_period": _rates(f, "current"),
        "delta_pp": {**{k: round(v, 2) for k, v in f["delta_pp"].items()}, "overall": round(f["overall_delta_pp"], 2)},
        "stage_contribution": {s["transition"]: round(s["log_ratio"], 4) for s in dec["stages"]},
        "stage_contribution_share_pct": {s["transition"]: round(s["share_pct"], 1) for s in dec["stages"]},
        "stage_delta_pp": {s["transition"]: round(s["delta_pp"], 2) for s in dec["stages"]},
        "segments": segments,
        "weekly_dynamics": [
            {
                "week_start": w,
                "overall_conv": round(wk["overall"][w]["conv"] * 100, 2),
                "worst_stage_conv": round(worst_all[w] * 100, 2),
                "worst_stage_conv_mobile_web": round(
                    wk["stage_series"][f"mobile_web | {worst_stage}"][w] * 100, 2
                ) if f"mobile_web | {worst_stage}" in wk["stage_series"] else None,
                "by_platform": {p: round(wk["by_platform"][p][w]["conv"] * 100, 2)
                                for p in wk["by_platform"]},
            }
            for w in wk["weeks"]
        ],
        "breakpoints": wk["breakpoints"][:5],
        "onset": wk["onset"],
        "all_changes": changes,
        "plot_path": str(plot.relative_to(ROOT)) if plot else plot_note,
        "data_quality": {
            "funnel_anomalies": anomalies,
            "traffic_shift": traffic_shift,
            "absolute_metrics": {
                "base_total_bookings": int(f["periods"]["base"]["raw"]["bookings_completed"]),
                "current_total_bookings": int(f["periods"]["current"]["raw"]["bookings_completed"]),
                "base_total_visitors": int(f["periods"]["base"]["raw"]["visitors"]),
                "current_total_visitors": int(f["periods"]["current"]["raw"]["visitors"]),
            },
        },
    })
