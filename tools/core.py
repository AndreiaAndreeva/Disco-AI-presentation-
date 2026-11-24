"""Чистая логика анализа воронки: без печати и без глобального состояния.

Каждая функция принимает данные и параметры и возвращает словарь с результатом
и блоком проверок (`checks`), чтобы шаг можно было показать и проверить.
"""
from __future__ import annotations

import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import pandas as pd

STAGES: List[str] = [
    "visitors",
    "specialist_viewers",
    "slot_selectors",
    "booking_starters",
    "bookings_completed",
]
STAGE_RU = {
    "visitors": "Визит",
    "specialist_viewers": "Карточка",
    "slot_selectors": "Слот",
    "booking_starters": "Форма",
    "bookings_completed": "Запись",
}
TRANSITIONS: List[Tuple[str, str, str]] = [
    ("visitors", "specialist_viewers", "Визит→Карточка"),
    ("specialist_viewers", "slot_selectors", "Карточка→Слот"),
    ("slot_selectors", "booking_starters", "Слот→Форма"),
    ("booking_starters", "bookings_completed", "Форма→Запись"),
]
KEY: List[str] = ["week_start", "platform", "category", "region_type"]

PERIOD_KEYS = ("base_start", "base_end", "current_start", "current_end")


def json_clean(value):
    """Убирает из результата то, что не переживает round-trip JSON.

    -0.0 -> 0.0 (JSON.stringify(-0) даёт "0", мост MCP считает это потерей),
    NaN и бесконечности -> None, numpy-скаляры -> питоновские.
    """
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return 0.0 if value == 0 else value
    if isinstance(value, dict):
        return {str(k): json_clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_clean(v) for v in value]
    if hasattr(value, "item"):
        return json_clean(value.item())
    return value


def check(name: str, ok: bool, detail: str) -> Dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


def wilson(k: int, n: int, z: float = 1.959963984540054) -> Tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


def two_prop_z(x1: int, n1: int, x2: int, n2: int) -> Tuple[float, float]:
    if min(n1, n2) == 0:
        return (0.0, 1.0)
    p1, p2 = x1 / n1, x2 / n2
    p = (x1 + x2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return (0.0, 1.0)
    z = (p2 - p1) / se
    pval = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))
    return (z, pval)


# ---------------------------------------------------------------- загрузка
def load(csv_path: str) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Читает CSV, дедуплицирует, проверяет качество.

    Возвращает (дедуплицированный df, отчёт о качестве).
    """
    raw = pd.read_csv(csv_path, parse_dates=["week_start"])
    missing = [c for c in KEY + STAGES if c not in raw.columns]
    if missing:
        raise ValueError(f"нет колонок: {missing}; есть: {list(raw.columns)}")

    dup_mask = raw.duplicated(KEY, keep=False)
    dups = raw[dup_mask]
    identical = True
    if len(dups):
        identical = bool(dups.groupby(KEY)[STAGES].nunique().max().max() == 1)

    df = raw.drop_duplicates(KEY, keep="first").copy()
    df = df.sort_values(KEY).reset_index(drop=True)

    weeks = sorted(df.week_start.unique())
    per_week = df.groupby("week_start").size()
    platforms = sorted(df.platform.unique())
    cats = sorted(df.category.unique())
    regions = sorted(df.region_type.unique())
    expected = len(weeks) * len(platforms) * len(cats) * len(regions)

    n_dup_keys = dups.groupby(KEY).ngroups if len(dups) else 0
    checks = [
        check("ключ уникален", len(df) == len(raw.drop_duplicates(KEY)), f"{len(df)} уникальных из {len(raw)} строк"),
        check("дубли идентичны", identical,
              f"{n_dup_keys} ключ / {len(dups)} строки; значения этапов {'совпадают' if identical else 'РАЗЛИЧАЮТСЯ'}"),
        check("сетка полная", len(df) == expected,
              f"недель {len(weeks)} × платформ {len(platforms)} × категорий {len(cats)} × регионов {len(regions)} = {expected}, факт {len(df)}"),
        check("недели сбалансированы", int(per_week.min()) == int(per_week.max()),
              f"строк в неделю: {int(per_week.min())}" if int(per_week.min()) == int(per_week.max())
              else f"строк в неделю: {int(per_week.min())}–{int(per_week.max())}"),
        check("нет нулей", int((df[STAGES] <= 0).sum().sum()) == 0, f"нулевых/отрицательных значений: {int((df[STAGES] <= 0).sum().sum())}"),
        check("нет пропусков", int(df[STAGES].isna().sum().sum()) == 0, f"NaN: {int(df[STAGES].isna().sum().sum())}"),
    ]

    mono = []
    for a, b, label in TRANSITIONS:
        bad = df[df[b] > df[a]]
        mono.append({"transition": label, "violations": int(len(bad)),
                     "examples": [f"{r.week_start.date()}/{r.platform}/{r.category}/{r.region_type}" for r in bad.head(5).itertuples()]})
    checks.append(check("воронка монотонна", sum(m["violations"] for m in mono) == 0,
                        "проверены все 4 перехода: " + ", ".join(f"{m['transition']}={m['violations']}" for m in mono)))

    dup_list = []
    if len(dups):
        for keyvals, count in dups.groupby(KEY).size().items():
            kv = dict(zip(KEY, keyvals))
            kv["week_start"] = str(pd.Timestamp(kv["week_start"]).date())
            dup_list.append({"key": kv, "rows": int(count),
                             "identical_stage_values": identical})

    report = {
        "rows_raw": int(len(raw)),
        "rows_unique": int(len(df)),
        "columns": list(raw.columns),
        "weeks": [str(pd.Timestamp(w).date()) for w in weeks],
        "platforms": platforms,
        "categories": cats,
        "regions": regions,
        "rows_expected": int(expected),
        "rows_per_week": {str(pd.Timestamp(k).date()): int(v) for k, v in per_week.items()},
        "duplicates": dup_list,
        "monotonicity": mono,
        "checks": checks,
        "df": df,
    }
    return df, report


# ---------------------------------------------------------------- периоды
def assign_periods(df: pd.DataFrame, **p: str) -> Dict[str, Any]:
    """Размечает недели по периодам и проверяет границы.

    Ошибка, если неделя попала в оба периода (пересечение) — это защита от
    ошибки спецификации, из-за которой база и текущий период делят одну неделю.
    """
    starts = {k: pd.Timestamp(p[k]) for k in PERIOD_KEYS}
    weeks = [pd.Timestamp(w) for w in sorted(df.week_start.unique())]

    def in_range(w, a, b):
        return a <= w <= b

    base_weeks = [w for w in weeks if in_range(w, starts["base_start"], starts["base_end"])]
    cur_weeks = [w for w in weeks if in_range(w, starts["current_start"], starts["current_end"])]
    both = [w for w in weeks if w in base_weeks and w in cur_weeks]
    neither = [w for w in weeks if w not in base_weeks and w not in cur_weeks]

    def boundary_ok(start: pd.Timestamp, end: pd.Timestamp, name: str) -> Dict[str, Any]:
        """Границы должны совпадать с понедельником и воскресеньем недель."""
        ok_start = start.weekday() == 0
        ok_end = (end - pd.Timedelta(days=6)).weekday() == 0
        return check(f"{name}: границы по неделям", ok_start and ok_end,
                     f"{start.date()}({['пн','вт','ср','чт','пт','сб','вс'][start.weekday()]}) … "
                     f"{end.date()}({['пн','вт','ср','чт','пт','сб','вс'][end.weekday()]})")

    available = f"{_dmy(weeks[0])} - {_dmy(weeks[-1])}" if weeks else "недели не найдены"

    checks = [
        check("периоды не пересекаются", not both,
              "нет общих недель" if not both else "общие недели: " + ", ".join(str(w.date()) for w in both)),
        check("нет неприсвоенных недель", not neither,
              "все недели разобраны" if not neither else
              "вне периодов: " + ", ".join(str(w.date()) for w in neither) + "; они не попадут в расчёт"),
        check("база не пустая", len(base_weeks) > 0,
              f"недель: {len(base_weeks)}" if base_weeks else f"в файле нет недель за этот период; доступно: {available}"),
        check("текущий не пустой", len(cur_weeks) > 0,
              f"недель: {len(cur_weeks)}" if cur_weeks else f"в файле нет недель за этот период; доступно: {available}"),
        boundary_ok(starts["base_start"], starts["base_end"], "база"),
        boundary_ok(starts["current_start"], starts["current_end"], "текущий"),
        check("периоды стыкуются", (starts["current_start"] - starts["base_end"]).days == 1,
              f"разрыв {int((starts['current_start'] - starts['base_end']).days) - 1} дн."),
    ]

    week_owner = {}
    for w in weeks:
        if w in base_weeks:
            week_owner[str(w.date())] = "base"
        elif w in cur_weeks:
            week_owner[str(w.date())] = "current"
        else:
            week_owner[str(w.date())] = "none"

    return {
        "ok": all(c["ok"] for c in checks),
        "periods": {"base": [str(w.date()) for w in base_weeks], "current": [str(w.date()) for w in cur_weeks]},
        "week_owner": week_owner,
        "checks": checks,
    }


def _periods_df(df: pd.DataFrame, assign: Dict[str, Any]) -> Dict[str, pd.DataFrame]:
    owner = assign["week_owner"]
    df = df.copy()
    df["period"] = df.week_start.dt.strftime("%Y-%m-%d").map(owner)
    return {name: df[df.period == name] for name in ("base", "current")}


# ---------------------------------------------------------------- воронка
def funnel(df, assign, **p):
    parts = _periods_df(df, assign)
    res: Dict[str, Any] = {"periods": {}, "checks": []}
    for name in ("base", "current"):
        d = parts[name]
        raw = {s: int(d[s].sum()) for s in STAGES}
        rates = {}
        for a, b, label in TRANSITIONS:
            rates[label] = raw[b] / raw[a] if raw[a] else 0.0
        overall = raw["bookings_completed"] / raw["visitors"] if raw["visitors"] else 0.0
        lo, hi = wilson(raw["bookings_completed"], raw["visitors"])
        res["periods"][name] = {
            "raw": raw,
            "rates": rates,
            "overall": overall,
            "overall_ci": [lo, hi],
            "share_of_visitors": {s: (raw[s] / raw["visitors"] if raw["visitors"] else 0.0) for s in STAGES},
            "rows": int(len(d)),
            "weeks": sorted(d.week_start.dt.strftime("%Y-%m-%d").unique().tolist()),
        }
    b, c = res["periods"]["base"], res["periods"]["current"]
    res["delta_pp"] = {k: (c["rates"][k] - b["rates"][k]) * 100 for k in b["rates"]}
    res["overall_delta_pp"] = (c["overall"] - b["overall"]) * 100
    res["overall_delta_rel_pct"] = ((c["overall"] / b["overall"]) - 1) * 100 if b["overall"] else 0.0
    res["checks"] = [
        check("суммы по периодам сходятся", b["raw"]["visitors"] + c["raw"]["visitors"]
              == int(df.visitors.sum()), f"{b['raw']['visitors']} + {c['raw']['visitors']} = {int(df.visitors.sum())}"),
        check("строки разобраны по периодам", b["rows"] + c["rows"] == int(len(df)),
              f"{b['rows']} + {c['rows']} = {len(df)}"),
    ]
    return res


# ---------------------------------------------------------------- разложение
def decompose(df, assign, **p):
    f = funnel(df, assign, **p)
    b, c = f["periods"]["base"]["rates"], f["periods"]["current"]["rates"]
    total_log = math.log(f["periods"]["current"]["overall"] / f["periods"]["base"]["overall"])
    rows = []
    for a, bb, label in TRANSITIONS:
        rb, rc = b[label], c[label]
        lr = math.log(rc / rb)
        rows.append({
            "transition": label,
            "base": rb,
            "current": rc,
            "delta_pp": (rc - rb) * 100,
            "log_ratio": lr,
            "share_pct": lr / total_log * 100 if total_log else 0.0,
        })
    prod = 1.0
    for a, bb, label in TRANSITIONS:
        prod *= c[label]
    checks = [
        check("произведение этапных CVR = общей конверсии",
              abs(prod - f["periods"]["current"]["overall"]) < 1e-9,
              f"{prod:.6f} vs {f['periods']['current']['overall']:.6f}"),
        check("сумма ln-вкладов = ln(Δ общей)", abs(sum(r["log_ratio"] for r in rows) - total_log) < 1e-9,
              f"{sum(r['log_ratio'] for r in rows):.6f} vs {total_log:.6f}"),
        check("сумма долей = 100%", abs(sum(r["share_pct"] for r in rows) - 100) < 1e-6,
              f"{sum(r['share_pct'] for r in rows):.2f}%"),
    ]
    return {**f, "stages": rows, "total_log": total_log, "checks": f["checks"] + checks}


# ---------------------------------------------------------------- сегменты
def segments(df, assign, by: Sequence[str], **p):
    parts = _periods_df(df, assign)
    frames = []
    for name, d in parts.items():
        g = d.groupby(list(by))[STAGES].sum()
        g["period"] = name
        frames.append(g.reset_index())
    long = pd.concat(frames, ignore_index=True)
    wide = long.pivot_table(index=list(by), columns="period", values=STAGES, aggfunc="sum")

    rows = []
    for key, r in wide.iterrows():
        key = key if isinstance(key, tuple) else (key,)
        vb, vc = float(r[("visitors", "base")]), float(r[("visitors", "current")])
        cb = float(r[("bookings_completed", "base")]) / vb if vb else 0.0
        cc = float(r[("bookings_completed", "current")]) / vc if vc else 0.0
        stage = {}
        for a, bb, label in TRANSITIONS:
            nb, nc = float(r[(a, "base")]), float(r[(a, "current")])
            xb, xc = float(r[(bb, "base")]), float(r[(bb, "current")])
            stage[label] = ((xc / nc if nc else 0) - (xb / nb if nb else 0)) * 100
        rows.append({
            "segment": dict(zip(by, key)),
            "visitors_base": int(vb),
            "visitors_current": int(vc),
            "conv_base": cb,
            "conv_current": cc,
            "delta_pp": (cc - cb) * 100,
            "stage_delta_pp": stage,
            "bookings_base": int(r[("bookings_completed", "base")]),
            "bookings_current": int(r[("bookings_completed", "current")]),
        })

    tot_vb = sum(r["visitors_base"] for r in rows)
    tot_vc = sum(r["visitors_current"] for r in rows)
    for r in rows:
        wb = r["visitors_base"] / tot_vb if tot_vb else 0.0
        wc = r["visitors_current"] / tot_vc if tot_vc else 0.0
        r["visitors_share_base_pct"] = wb * 100
        r["visitors_share_current_pct"] = wc * 100
        # вклад = изменение доли записей: суммы по сегментам точно дают Δ общей конверсии
        r["rate_part_pp"] = wb * (r["conv_current"] - r["conv_base"]) * 100
        r["contribution_pp"] = r["rate_part_pp"]  # вклад поведения, как в отчёте
        r["total_contribution_pp"] = (wc * r["conv_current"] - wb * r["conv_base"]) * 100
        r["mix_part_pp"] = (wc - wb) * r["conv_base"] * 100
        r["interaction_pp"] = (wc - wb) * (r["conv_current"] - r["conv_base"]) * 100
    rows.sort(key=lambda r: r["contribution_pp"])

    overall = decompose(df, assign, **p)["overall_delta_pp"]

    # rate / mix / interaction — сумма частей по сегментам
    rate = sum(r["rate_part_pp"] for r in rows)
    mix = sum(r["mix_part_pp"] for r in rows)
    inter = sum(r["interaction_pp"] for r in rows)

    checks = [
        check("сумма визитов = итог", tot_vb == int(parts["base"].visitors.sum()) and tot_vc == int(parts["current"].visitors.sum()),
              f"{tot_vb} / {tot_vc}"),
        check("сумма rate-вкладов = rate-эффект",
              abs(sum(r["contribution_pp"] for r in rows) - rate) < 1e-6,
              f"{sum(r['contribution_pp'] for r in rows):.4f} vs {rate:.4f}"),
        check("сумма полных вкладов = Δ общей",
              abs(sum(r["total_contribution_pp"] for r in rows) - overall) < 1e-6,
              f"{sum(r['total_contribution_pp'] for r in rows):.4f} vs {overall:.4f}"),
        check("rate+mix+interaction = Δ общей", abs(rate + mix + inter - overall) < 1e-6,
              f"{rate:.4f}+{mix:.4f}+{inter:.4f} vs {overall:.4f}"),
    ]
    return {
        "by": list(by),
        "rows": rows,
        "total_visitors": {"base": tot_vb, "current": tot_vc},
        "rate_effect_pp": rate,
        "mix_effect_pp": mix,
        "interaction_pp": inter,
        "overall_delta_pp": overall,
        "checks": checks,
    }


# ---------------------------------------------------------------- недели
def weekly(df, assign, platform: str | None = None, event_date: str | None = None):
    d = df.copy()
    d["week"] = d.week_start.dt.strftime("%Y-%m-%d")
    weeks = sorted(d.week.unique())

    def series(frame: pd.DataFrame) -> Dict[str, Dict[str, float]]:
        out = {}
        for w in weeks:
            wd = frame[frame.week == w]
            if wd.empty:
                out[w] = {"conv": 0.0, "k": 0, "n": 0}
                continue
            out[w] = {"conv": float(wd.bookings_completed.sum()) / float(wd.visitors.sum()),
                      "k": int(wd.bookings_completed.sum()), "n": int(wd.visitors.sum())}
        return out

    platforms = sorted(d.platform.unique()) if platform is None else [platform]
    overall = series(d)
    by_platform = {p: series(d[d.platform == p]) for p in platforms}
    stage_series = {}
    for p in platforms:
        sub = d[d.platform == p]
        for a, b, label in TRANSITIONS:
            stage_series[(p, label)] = {
                w: (int(sub[sub.week == w][b].sum()) / int(sub[sub.week == w][a].sum())
                    if int(sub[sub.week == w][a].sum()) else 0.0)
                for w in weeks
            }

    # перелом: максимальный по модулю перепад неделя-к-неделе
    breaks = []

    def add_break(name, s):
        vals = [(w, s[w]["conv"] if isinstance(s[w], dict) else s[w]) for w in weeks]
        for (w0, v0), (w1, v1) in zip(vals, vals[1:]):
            breaks.append({"series": name, "from": w0, "to": w1, "delta_pp": (v1 - v0) * 100})

    add_break("overall", overall)
    for p in platforms:
        add_break(f"{p}: запись", by_platform[p])
        for a, b, label in TRANSITIONS:
            add_break(f"{p}: {label}", stage_series[(p, label)])
    breaks.sort(key=lambda x: abs(x["delta_pp"]), reverse=True)

    onset = None
    if event_date:
        onset = _onset(weeks, stage_series, by_platform, d, event_date, platform)

    checks = [
        check("недельные суммы = итог периода", all(
            abs(sum(overall[w]["k"] for w in weeks if assign["week_owner"][w] == per)
                - int(_periods_df(df, assign)[per].bookings_completed.sum())) == 0
            for per in ("base", "current")), "суммы записей по неделям сходятся"),
    ]
    return {
        "weeks": weeks,
        "overall": overall,
        "by_platform": by_platform,
        "stage_series": {f"{p} | {label}": v for (p, label), v in stage_series.items()},
        "breakpoints": breaks[:6],
        "onset": onset,
        "checks": checks,
    }


def _onset(weeks: List[str], stage_series, by_platform, d, event_date: str, platform: str | None):
    ev = pd.Timestamp(event_date)
    ev_week = (ev - pd.Timedelta(days=ev.weekday())).strftime("%Y-%m-%d")
    if ev_week not in weeks:
        return {"ok": False, "reason": f"неделя события {ev_week} отсутствует в данных"}
    idx = weeks.index(ev_week)
    pre = weeks[:idx]
    post = weeks[idx + 1:]
    if not pre or not post:
        return {"ok": False, "reason": "недостаточно недель до/после события"}

    plats = [platform] if platform else sorted(d.platform.unique())
    target = "mobile_web" if "mobile_web" in plats else plats[0]
    # стадия с максимальным падением в целевом сегменте
    stage_label = min(TRANSITIONS, key=lambda t: stage_series[(target, t[2])][post[0]]
                      - stage_series[(target, t[2])][pre[0]])[2]

    s = stage_series[(target, stage_label)]
    k_pre = n_pre = k_post = n_post = 0
    sub = d[d.platform == target]
    a, b = next((x, y) for x, y, lab in TRANSITIONS if lab == stage_label)
    for w in pre:
        k_pre += int(sub[sub.week == w][b].sum())
        n_pre += int(sub[sub.week == w][a].sum())
    for w in post:
        k_post += int(sub[sub.week == w][b].sum())
        n_post += int(sub[sub.week == w][a].sum())
    p0 = k_pre / n_pre if n_pre else 0.0
    p1 = k_post / n_post if n_post else 0.0
    obs = s[ev_week]
    n_obs = int(sub[sub.week == ev_week][a].sum())

    days_after = 7 - ev.weekday()
    frac = days_after / 7
    expected = (1 - frac) * p0 + frac * p1
    se = math.sqrt(expected * (1 - expected) / n_obs) if n_obs else 0.0
    z = (obs - expected) / se if se else 0.0
    pval = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2)))) if se else 1.0
    implied = (p0 - obs) / (p0 - p1) if p0 != p1 else float("nan")

    return {
        "ok": True,
        "platform": target,
        "stage": stage_label,
        "event_date": str(ev.date()),
        "event_week": ev_week,
        "p_before": p0,
        "p_full": p1,
        "observed": obs,
        "expected_if_onset_on_event": expected,
        "affected_fraction_of_week": frac,
        "z": z,
        "p_value": pval,
        "implied_affected_share_of_week": implied,
        "n_observed": n_obs,
        "verdict": (
            "эффект был всю неделю, а не с даты события" if implied > 0.9
            else "частичное совпадение с датой события" if 0.3 < implied <= 0.9
            else "данные не подтверждают дату события"
        ),
    }


# ---------------------------------------------------------------- журнал
def events(df, assign, change_log_path: str, **p):
    ch = pd.read_csv(change_log_path, parse_dates=["date"])
    seg = segments(df, assign, ["platform"], **p)
    by_platform = {r["segment"]["platform"]: r for r in seg["rows"]}
    overall = seg["overall_delta_pp"]
    all_stage = {r["transition"]: r for r in decompose(df, assign, **p)["stages"]}

    rows = []
    for r in ch.itertuples():
        scope = str(r.scope)
        date = pd.Timestamp(r.date)
        row = {"date": str(date.date()), "scope": scope, "type": str(r.event_type),
               "description": str(r.description)}
        if scope in by_platform:
            row["delta_scope_pp"] = by_platform[scope]["delta_pp"]
        else:
            row["delta_scope_pp"] = overall
        rows.append(row)

    # контроль для платформенных событий: средневзвешенная конверсия прочих платформ
    base_conv = {k: v["conv_base"] for k, v in by_platform.items()}
    for row in rows:
        if row["scope"] in by_platform:
            others = {k: v for k, v in by_platform.items() if k != row["scope"]}
            w = {k: v["visitors_base"] for k, v in others.items()}
            cb = sum(base_conv[k] * w[k] for k in others) / sum(w.values())
            cc = sum(others[k]["conv_current"] * w[k] for k in others) / sum(w.values())
            row["delta_control_pp"] = (cc - cb) * 100
            row["gap_vs_control_pp"] = row["delta_scope_pp"] - row["delta_control_pp"]
            worst = min(by_platform[row["scope"]]["stage_delta_pp"], key=by_platform[row["scope"]]["stage_delta_pp"].get)
            row["worst_stage"] = worst
            row["worst_stage_delta_pp"] = by_platform[row["scope"]]["stage_delta_pp"][worst]
        else:
            row["delta_control_pp"] = None
            row["gap_vs_control_pp"] = None
            row["worst_stage"] = min(all_stage, key=lambda k: all_stage[k]["delta_pp"])
            row["worst_stage_delta_pp"] = all_stage[row["worst_stage"]]["delta_pp"]

    # вердикт: эвристика, помечена как эвристика
    deltas = {k: v["delta_pp"] for k, v in by_platform.items()}
    worst_platform = min(deltas, key=deltas.get)
    wk = weekly(df, assign, platform=None, event_date=None)
    break_week = next((b["to"] for b in wk["breakpoints"] if b["series"] == "overall"), None)

    for row in rows:
        ev_week = (pd.Timestamp(row["date"]) - pd.Timedelta(days=pd.Timestamp(row["date"]).weekday())
                   ).strftime("%Y-%m-%d")
        row["timing_excluded"] = False
        if row["scope"] in by_platform:
            o = weekly(df, assign, platform=row["scope"], event_date=row["date"])["onset"]
            if (o and o.get("ok") and abs(o["p_before"] - o["p_full"]) > 0.005
                    and o["implied_affected_share_of_week"] > 0.9):
                row["timing_excluded"] = True

        if row["timing_excluded"]:
            row["verdict"] = "не может быть причиной этого перелома: падение началось раньше релиза"
        elif row["type"] == "support_signal":
            row["verdict"] = "механизм, не доказательство"
        elif row["scope"] in by_platform:
            if (row["scope"] == worst_platform and row["delta_scope_pp"] <= -1.0
                    and (row["gap_vs_control_pp"] or 0) <= -1.0):
                row["verdict"] = "совпадает: спад в этом scope"
            elif abs(row["delta_scope_pp"]) < 1.0:
                row["verdict"] = "следов в данных нет"
            else:
                row["verdict"] = "спад есть, но он не в худшем сегменте"
        else:  # scope == all
            if break_week and ev_week == break_week:
                row["verdict"] = "совпадает по времени с переломом"
            elif break_week and ev_week < break_week:
                row["verdict"] = "до перелома: следов нет"
            elif break_week:
                row["verdict"] = "после перелома"
            else:
                row["verdict"] = "нет данных о переломе"

    return {
        "set": "change_log",
        "rows": rows,
        "checks": [check("журнал прочитан", len(rows) > 0, f"{len(rows)} событий")],
        "note": "совпадение по времени не доказывает причинность; вердикты — эвристика по данным",
    }


# ---------------------------------------------------------------- осмотр файла
def inspect(csv_path: str) -> Dict[str, Any]:
    """Читает файл и предлагает периоды: последние 2 недели — текущий, предыдущие — база."""
    df, report = load(csv_path)
    weeks = sorted(pd.Timestamp(w) for w in df.week_start.unique())
    mondays = [str(w.date()) for w in weeks if w.weekday() != 0]
    gaps = [
        {"from": str(weeks[i].date()), "to": str(weeks[i + 1].date()),
         "days": int((weeks[i + 1] - weeks[i]).days)}
        for i in range(len(weeks) - 1) if (weeks[i + 1] - weeks[i]).days != 7
    ]

    suggestion = None
    if len(weeks) >= 3:
        cur_n = 2
        base_n = len(weeks) - cur_n
        suggestion = {
            "base_start": str(weeks[0].date()),
            "base_end": str((weeks[-cur_n - 1] + pd.Timedelta(days=6)).date()),
            "current_start": str(weeks[-cur_n].date()),
            "current_end": str((weeks[-1] + pd.Timedelta(days=6)).date()),
            "base_weeks": base_n,
            "current_weeks": cur_n,
        }

    checks = [
        check("недели начинаются с понедельника", not mondays,
              "все week_start — понедельники" if not mondays else "не понедельники: " + ", ".join(mondays)),
        check("недели идут подряд", not gaps,
              "разрывов по 7 дней нет" if not gaps else f"разрывы: {gaps}"),
        check("периоды можно предложить", suggestion is not None,
              f"база {suggestion['base_start']}…{suggestion['base_end']}, "
              f"текущий {suggestion['current_start']}…{suggestion['current_end']}" if suggestion
              else "нужно минимум 3 недели"),
    ]

    return {
        "csv_path": str(csv_path),
        "rows_raw": report["rows_raw"],
        "rows_unique": report["rows_unique"],
        "duplicates": len(report["duplicates"]),
        "weeks": [str(w.date()) for w in weeks],
        "weeks_count": len(weeks),
        "first_week": str(weeks[0].date()) if weeks else None,
        "last_week": str(weeks[-1].date()) if weeks else None,
        "platforms": report["platforms"],
        "categories": report["categories"],
        "regions": report["regions"],
        "rows_per_week": report["rows_per_week"],
        "suggested_periods": suggestion,
        "checks": checks,
        "ok": all(c["ok"] for c in checks),
    }


# ---------------------------------------------------------------- записка
MEMO_DIR = Path(__file__).resolve().parent.parent / "memos"


def save_memo(text: str, name: str | None = None) -> Dict[str, Any]:
    """Сохраняет записку в memos/ и возвращает путь и размер."""
    body = str(text or "").strip()
    if not body:
        raise ValueError("пустой текст записки")
    if len(body.encode("utf-8")) > 200_000:
        raise ValueError("текст больше 200 КБ")
    MEMO_DIR.mkdir(parents=True, exist_ok=True)
    filename = name or f"memo_{datetime.now():%Y-%m-%d_%H%M%S}"
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(filename)).strip("._") or "memo"
    if not safe.endswith(".md"):
        safe += ".md"
    path = MEMO_DIR / safe
    counter = 2
    while path.exists():
        path = MEMO_DIR / (safe[:-3] + f"_{counter}.md")
        counter += 1
    path.write_text(body + "\n", encoding="utf-8")
    return {"path": str(path.relative_to(MEMO_DIR.parent)), "bytes": len(body.encode("utf-8"))}


# ---------------------------------------------------------------- детальный расчёт
def _fmt(x: float, n: int = 2) -> str:
    s = f"{x:,.{n}f}".replace(",", " ").replace(".", ",")
    return s


def _dmy(value) -> str:
    """Дата в формате ДД.ММ.ГГ."""
    t = pd.Timestamp(value)
    return f"{t.day:02d}.{t.month:02d}.{t.year % 100:02d}"


def _md_table(headers: List[str], rows: List[List[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def detailed_calc(df, assign, platform: str | None = None,
                  change_log_path: str | None = None,
                  event_date: str | None = None, **p) -> Dict[str, Any]:
    """Пошаговый расчёт с формулами и промежуточными числами (для объяснения)."""
    f = funnel(df, assign, **p)
    dec = decompose(df, assign, **p)
    seg = segments(df, assign, ["platform"], **p)
    target = platform or seg["rows"][0]["segment"]["platform"]

    if event_date is None and change_log_path:
        ch = pd.read_csv(change_log_path, parse_dates=["date"])
        ch = ch[ch.date <= pd.Timestamp(p["current_end"])]
        for mask in (
            (ch.scope == target) & ch.event_type.isin(["feature_flag", "release"]),
            ch.scope == target,
            ch.scope == "all",
        ):
            cand = ch[mask]
            if len(cand):
                event_date = str(cand.sort_values("date").iloc[0].date)
                break
    if event_date is not None:
        event_date = str(pd.Timestamp(event_date).date())

    wk = weekly(df, assign, platform=target, event_date=event_date)
    ev = events(df, assign, change_log_path, **p) if change_log_path else None

    row = next(r for r in seg["rows"] if r["segment"]["platform"] == target)
    expected = row["conv_base"] * row["visitors_current"]
    lost = expected - row["bookings_current"]
    impact = {
        "platform": target,
        "visitors_current": row["visitors_current"],
        "bookings_current": row["bookings_current"],
        "expected": expected,
        "lost": lost,
        "lost_share_platform_pct": lost / expected * 100 if expected else 0.0,
        "lost_share_all_pct": lost / f["periods"]["current"]["raw"]["bookings_completed"] * 100
        if f["periods"]["current"]["raw"]["bookings_completed"] else 0.0,
    }

    checks = f["checks"] + [c for c in dec["checks"] if c not in f["checks"]] \
        + seg["checks"] + wk["checks"]

    b, c = f["periods"]["base"], f["periods"]["current"]
    md: List[str] = []
    md.append("# Детальный расчёт\n")
    ev_txt = _dmy(event_date) if event_date else "не задано"
    md.append(
        f"В расчёте {len(df)} строк воронки: {len(assign['periods']['base'])} недель базы "
        f"({_dmy(assign['periods']['base'][0])} - "
        f"{_dmy(pd.Timestamp(assign['periods']['base'][-1]) + pd.Timedelta(days=6))}) и "
        f"{len(assign['periods']['current'])} недели текущего "
        f"({_dmy(assign['periods']['current'][0])} - "
        f"{_dmy(pd.Timestamp(assign['periods']['current'][-1]) + pd.Timedelta(days=6))}). Проблемный сегмент — "
        f"{target}, ближайшее событие в журнале — {ev_txt}.\n"
    )

    md.append("## 1. Трафик на каждом шаге\n")
    rows = []
    for stage in STAGES:
        rb, rc = b["raw"][stage], c["raw"][stage]
        rows.append([STAGE_RU[stage], f"{rb:,}".replace(",", " "), f"{rc:,}".replace(",", " "),
                     _fmt(rb / b["raw"]["visitors"] * 100, 2),
                     _fmt(rc / c["raw"]["visitors"] * 100, 2)])
    md.append(_md_table(["шаг", "людей в базе", "людей сейчас", "доля от визитов, %", "доля сейчас, %"], rows))
    md.append("\nДоля считается от всех визитов периода: из 100 пришедших до записи в базе доходили "
              f"{_fmt(b['overall']*100, 2)}, сейчас — {_fmt(c['overall']*100, 2)}.\n")

    md.append("## 2. Где теряется трафик\n")
    rows = []
    for s_ in dec["stages"]:
        rows.append([s_["transition"], _fmt(s_["base"] * 100, 2), _fmt(s_["current"] * 100, 2),
                     _fmt(s_["delta_pp"], 2), _fmt(s_["share_pct"], 1)])
    md.append(_md_table(["переход", "доходило в базе, %", "доходит сейчас, %", "изменение, п.п.",
                         "вклад в общее падение, %"], rows))
    md.append("\nДоля считается от визитов: сколько из пришедших доходит до следующего шага. "
              "Вклад показывает, какая часть общего падения приходится на этот переход; "
              "в сумме вклады дают 100%.\n")

    md.append("## 3. На каких платформах\n")
    rows = []
    for r in seg["rows"]:
        rows.append([r["segment"]["platform"], f"{r['visitors_base']:,}".replace(",", " "),
                     f"{r['visitors_current']:,}".replace(",", " "),
                     _fmt(r["conv_base"] * 100, 2), _fmt(r["conv_current"] * 100, 2),
                     _fmt(r["delta_pp"], 2), _fmt(r["contribution_pp"], 2)])
    md.append(_md_table(["платформа", "визитов в базе", "визитов сейчас", "конверсия в базе, %",
                         "конверсия сейчас, %", "изменение, п.п.", "вклад, п.п."], rows))
    md.append("\nВклад платформы — сколько процентных пунктов общего падения она даёт; "
              f"в сумме это {_fmt(seg['rate_effect_pp'], 2)} п.п.\n")
    md.append(f"Состав трафика не менялся: доля {target} в визитах "
              f"{_fmt(row['visitors_share_base_pct'], 2)}% → {_fmt(row['visitors_share_current_pct'], 2)}%, "
              f"поэтому спад нельзя объяснить перетоком людей.\n")

    md.append("## 4. Когда началось падение\n")
    worst = max(dec["stages"], key=lambda s_: abs(s_["delta_pp"]))
    series = wk["stage_series"].get(f"{target} | {worst['transition']}")
    if series:
        md.append(f"Доля перешедших на шаг «{worst['transition']}» в {target} по неделям: "
                  + " · ".join(f"{_dmy(w)} — {_fmt(v*100, 2)}%" for w, v in series.items()) + "\n")
    for bp in wk["breakpoints"][:3]:
        frm = _dmy(bp["from"])
        to = _dmy(bp["to"])
        series_name = bp["series"].replace("overall", "все платформы вместе").replace(": ", ", ")
        md.append(f"- перепад {series_name}: {frm} → {to}: {_fmt(bp['delta_pp'], 2)} п.п.")
    o = wk["onset"]
    if o and o.get("ok"):
        ed = pd.Timestamp(o["event_date"])
        ew = pd.Timestamp(o["event_week"])
        n_obs = f"{o['n_observed']:,}".replace(",", " ")
        md.append("")
        md.append(f"Событие {_dmy(ed)} попадает внутрь недели {_dmy(ew)}. "
                  f"До него доля перешедших держалась на {_fmt(o['p_before']*100, 2)}%, "
                  f"после — {_fmt(o['p_full']*100, 2)}%, а в неделе события — {_fmt(o['observed']*100, 2)}%.")
        md.append(f"Если бы падение началось ровно {_dmy(ed)}, за эту неделю вышло бы "
                  f"{_fmt(o['expected_if_onset_on_event']*100, 2)}% против фактических "
                  f"{_fmt(o['observed']*100, 2)}% (слот выбрали {n_obs} раз).")
        md.append("Расхождение слишком велико для случайного: падение действовало всю неделю, "
                  "а значит, началось раньше даты события.\n")

    if ev:
        md.append("## 5. Что было в журнале изменений\n")
        md.append(f"Источник — файл `{change_log_path}`, в нём {len(ev['rows'])} записей. "
                  "Для каждой записи показано падение конверсии в её сегменте и в остальных "
                  "платформах, а также вывод по данным.\n")
        rows = []
        for e in ev["rows"]:
            ed = pd.Timestamp(e["date"])
            rows.append([_dmy(ed), e["description"],
                         _fmt(e["delta_scope_pp"], 2),
                         "—" if e.get("delta_control_pp") is None else _fmt(e["delta_control_pp"], 2),
                         e["verdict"]])
        md.append(_md_table(["дата", "событие", "падение в его сегменте, п.п.",
                             "падение в остальных, п.п.", "вывод"], rows))
        md.append("\nДля событий масштаба всего продукта в колонке «в его сегменте» показано "
                  "общее падение за период, а не эффект события.\n")

    md.append("## 6. Сколько записей потеряно\n")
    fact = f"{impact['bookings_current']:,}".replace(",", " ")
    md.append(f"Если бы {target} сохранил прежнюю конверсию, за две недели он дал бы "
              f"{_fmt(impact['expected'], 1)} записей. Фактически — {fact}. "
              f"Потеря — {_fmt(impact['lost'], 1)} записей: {_fmt(impact['lost_share_platform_pct'], 1)}% "
              f"записей платформы и {_fmt(impact['lost_share_all_pct'], 1)}% всех записей периода.\n")

    md.append("## 7. Проверки\n")
    check_names = {
        "суммы по периодам сходятся": "суммы по периодам сходятся",
        "строки разобраны по периодам": "все строки разложены по периодам",
        "произведение этапных CVR = общей конверсии": "произведение долей по шагам равно итоговой конверсии",
        "сумма ln-вкладов = ln(Δ общей)": "сумма вкладов шагов равна общему изменению",
        "сумма долей = 100%": "сумма долей равна 100%",
        "сумма визитов = итог": "сумма визитов по платформам равна итогу",
        "сумма rate-вкладов = rate-эффект": "сумма вкладов платформ равна изменению внутри платформ",
        "сумма полных вкладов = Δ общей": "сумма вкладов платформ с учётом трафика равна общему изменению",
        "rate+mix+interaction = Δ общей": "внутри платформ + сдвиг состава + совместный эффект = общее изменение",
        "недельные суммы = итог периода": "суммы по неделям равны итогу периода",
    }
    for ch_ in checks:
        name = check_names.get(ch_["name"], ch_["name"])
        detail = str(ch_["detail"]).replace(" vs ", " = ").replace(".", ",")
        md.append(f"- {'✔' if ch_['ok'] else '✘'} {name}: {detail}")

    return {
        "target_platform": target,
        "event_date": event_date,
        "stages": dec["stages"],
        "segments": seg["rows"],
        "rate_mix": {"rate": seg["rate_effect_pp"], "mix": seg["mix_effect_pp"],
                     "interaction": seg["interaction_pp"],
                     "total": seg["rate_effect_pp"] + seg["mix_effect_pp"] + seg["interaction_pp"]},
        "breakpoints": wk["breakpoints"][:5],
        "onset": wk["onset"],
        "events": ev["rows"] if ev else None,
        "impact": impact,
        "checks": checks,
        "markdown": "\n".join(md),
    }
