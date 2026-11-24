"""Определения инструментов: схема для LLM + привязка к логике.

Формат схем совместим с function calling: name / description / parameters.
`TOOLS` — реестр для вызова из Python, `schemas()` — список для модели.
"""
from __future__ import annotations

from typing import Any, Dict, List

from . import core


class ToolError(ValueError):
    """Ошибка вызова инструмента: неверные аргументы или непрошедшие проверки."""


PERIOD_PROPS: Dict[str, Any] = {
    "base_start": {"type": "string", "description": "Первый день базового периода, YYYY-MM-DD (должен быть понедельником)."},
    "base_end": {"type": "string", "description": "Последний день базового периода, YYYY-MM-DD (должен быть воскресеньем)."},
    "current_start": {"type": "string", "description": "Первый день текущего периода, YYYY-MM-DD (понедельник, следующий за base_end)."},
    "current_end": {"type": "string", "description": "Последний день текущего периода, YYYY-MM-DD (воскресенье)."},
}

CSV_PROP = {"csv_path": {"type": "string", "description": "Путь к CSV с недельной воронкой."}}

SCHEMAS: List[Dict[str, Any]] = [
    {
        "name": "funnel_check",
        "description": (
            "Проверяет файл воронки перед расчётом: дедуплицирует по ключу "
            "неделя+платформа+категория+регион, проверяет полноту сетки, нули, "
            "пропуски и монотонность всех четырёх переходов. Возвращает число "
            "удалённых дублей, список нарушений и набор проверок."
        ),
        "parameters": {"type": "object", "properties": {**CSV_PROP}, "required": ["csv_path"]},
    },
    {
        "name": "funnel_periods",
        "description": (
            "Считает воронку по двум периодам: суммы, конверсии на каждом шаге, "
            "общую конверсию и её изменение. Отказывает, если периоды пересекаются, "
            "есть разрыв или граница режет неделю."
        ),
        "parameters": {"type": "object", "properties": {**CSV_PROP, **PERIOD_PROPS},
                       "required": ["csv_path", *PERIOD_PROPS]},
    },
    {
        "name": "funnel_decompose",
        "description": (
            "Показывает, какой шаг воронки дал основное изменение и какую долю "
            "общего падения он объясняет. Проверяет, что вклады в сумме дают "
            "общее изменение."
        ),
        "parameters": {"type": "object", "properties": {**CSV_PROP, **PERIOD_PROPS},
                       "required": ["csv_path", *PERIOD_PROPS]},
    },
    {
        "name": "funnel_segments",
        "description": (
            "Считает конверсию и вклад в общее изменение по платформам, "
            "категориям и регионам. Отдельно проверяет, не изменился ли состав "
            "трафика. Возвращает визиты-знаменатели и изменения по шагам."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                **CSV_PROP, **PERIOD_PROPS,
                "by": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["platform", "category", "region_type"]},
                    "description": "Измерения сегментации; одна или несколько колонок.",
                },
            },
            "required": ["csv_path", *PERIOD_PROPS, "by"],
        },
    },
    {
        "name": "funnel_weekly",
        "description": (
            "Показывает недельную динамику, находит неделю перелома и проверяет, "
            "могла ли причина начаться в указанную дату: сравнивает фактическую "
            "неделю с ожидаемой, если бы эффект стартовал ровно в этот день."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                **CSV_PROP, **PERIOD_PROPS,
                "platform": {"type": "string", "description": "Ограничить расчёт одной платформой; по умолчанию все."},
                "event_date": {"type": "string", "description": "Дата события из журнала изменений, YYYY-MM-DD. Без неё onset-тест не считается."},
            },
            "required": ["csv_path", *PERIOD_PROPS],
        },
    },
    {
        "name": "funnel_events",
        "description": (
            "Сопоставляет журнал изменений с данными: для каждого события "
            "показывает падение в его сегменте и в остальных, а также вывод. "
            "Совпадение по времени не считается доказательством причинности."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                **CSV_PROP, **PERIOD_PROPS,
                "change_log_path": {"type": "string", "description": "Путь к CSV с журналом изменений."},
            },
            "required": ["csv_path", *PERIOD_PROPS, "change_log_path"],
        },
    },
    {
        "name": "funnel_detailed_calc",
        "description": (
            "Пошаговый расчёт для объяснения: трафик на шагах, где он теряется, "
            "платформы, дата начала падения, журнал изменений, потери и проверки. "
            "Возвращает готовый markdown. Вызывай после короткой записки, чтобы "
            "показать, откуда взялись числа."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                **CSV_PROP, **PERIOD_PROPS,
                "change_log_path": {"type": "string", "description": "Путь к CSV с журналом изменений."},
                "platform": {"type": "string", "description": "Целевой сегмент; по умолчанию худшая платформа."},
                "event_date": {"type": "string", "description": "Дата события для onset-теста; по умолчанию берётся из журнала."},
            },
            "required": ["csv_path", *PERIOD_PROPS],
        },
    },
    {
        "name": "funnel_save_memo",
        "description": (
            "Сохраняет готовую записку в markdown-файл в memos/ и возвращает путь. "
            "Вызывай после того, как оформил записку по шаблону, и укажи этот путь "
            "пользователю."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Полный текст записки в markdown."},
                "name": {"type": "string", "description": "Необязательное имя файла без пути."},
            },
            "required": ["text"],
        },
    },
    {
        "name": "funnel_inspect",
        "description": (
            "Читает CSV с воронкой и возвращает состав файла: недели, платформы, "
            "категории, регионы, дубли и проверку, что недели — подряд идущие "
            "понедельники. Предлагает базовый и текущий периоды (текущий — "
            "последние 2 недели, база — предыдущие). Вызывай первым, когда "
            "пользователь дал только путь к файлу."
        ),
        "parameters": {"type": "object", "properties": {**CSV_PROP}, "required": ["csv_path"]},
    },
    {
        "name": "analyze_funnel",
        "description": (
            "Все расчёты в одном вызове: проверка данных, воронка по двум периодам, "
            "вклады шагов, сегменты, недельная динамика, журнал изменений и график. "
            "Удобен, когда нужен общий обзор."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                **CSV_PROP, **PERIOD_PROPS,
                "change_log_path": {"type": "string", "description": "Путь к CSV с журналом изменений."},
                "group_by": {"type": "string", "enum": ["platform", "category", "region_type"],
                             "description": "Измерение сегментации для блока segments; по умолчанию platform."},
                "event_date": {"type": "string", "description": "Дата события для onset-теста, YYYY-MM-DD."},
            },
            "required": ["csv_path", *PERIOD_PROPS],
        },
    },
]


def _require(args: Dict[str, Any], name: str) -> Any:
    if name not in args or args[name] in (None, "", []):
        raise ToolError(f"не передан обязательный аргумент '{name}'")
    return args[name]


def _periods(args: Dict[str, Any]) -> Dict[str, str]:
    out = {}
    for k in core.PERIOD_KEYS:
        out[k] = str(pd_timestamp(_require(args, k)))
    return out


def pd_timestamp(value: str) -> str:
    """Проверяет формат даты и возвращает YYYY-MM-DD."""
    from datetime import datetime

    try:
        return datetime.strptime(str(value), "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError as exc:
        raise ToolError(f"дата '{value}' не в формате YYYY-MM-DD") from exc


def _load(args: Dict[str, Any]):
    csv_path = _require(args, "csv_path")
    df, report = core.load(str(csv_path))
    return df, report


def _by(args: Dict[str, Any]) -> List[str]:
    by = args.get("by")
    if isinstance(by, str):
        by = [p.strip() for p in by.split(",") if p.strip()]
    if not by:
        raise ToolError("аргумент 'by' пуст")
    allowed = {"platform", "category", "region_type"}
    unknown = [b for b in by if b not in allowed]
    if unknown:
        raise ToolError(f"неизвестные измерения {unknown}; допустимы {sorted(allowed)}")
    return list(by)


def call(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Вызывает инструмент по имени. Возвращает результат или бросает ToolError."""
    if name not in TOOLS:
        raise ToolError(f"неизвестный инструмент '{name}'; есть {sorted(TOOLS)}")

    if name == "analyze_funnel":
        from .compat import analyze_funnel as _analyze

        return {"tool": name, "ok": True, "result": _analyze(args)}

    if name == "funnel_detailed_calc":
        df, _ = _load(args)
        assign = core.assign_periods(df, **_periods(args))
        if not assign["ok"]:
            raise ToolError("периоды не прошли проверку")
        res = core.detailed_calc(df, assign, platform=args.get("platform"),
                                 change_log_path=args.get("change_log_path"),
                                 event_date=args.get("event_date"), **_periods(args))
        return {"tool": name, "ok": True, "result": res}

    if name == "funnel_save_memo":
        try:
            res = core.save_memo(str(_require(args, "text")), args.get("name"))
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        return {"tool": name, "ok": True, "result": res}

    if name == "funnel_inspect":
        res = core.inspect(str(_require(args, "csv_path")))
        return {"tool": name, "ok": bool(res["ok"]), "result": res}

    if name == "funnel_check":
        df, report = _load(args)
        ok = all(c["ok"] for c in report["checks"])
        return {"tool": name, "ok": ok, "result": {k: v for k, v in report.items() if k != "df"}}

    if name == "funnel_periods":
        df, report = _load(args)
        assign = core.assign_periods(df, **_periods(args))
        if not assign["ok"]:
            bad = [c for c in assign["checks"] if not c["ok"]]
            raise ToolError("периоды не прошли проверку: " + "; ".join(f"{c['name']} ({c['detail']})" for c in bad))
        table = core.funnel(df, assign, **_periods(args))
        return {"tool": name, "ok": True, "result": {"assign": assign, "funnel": table,
                                                     "quality": {k: v for k, v in report.items() if k != "df"}}}

    if name == "funnel_decompose":
        df, _ = _load(args)
        assign = core.assign_periods(df, **_periods(args))
        if not assign["ok"]:
            raise ToolError("периоды не прошли проверку")
        res = core.decompose(df, assign, **_periods(args))
        return {"tool": name, "ok": all(c["ok"] for c in res["checks"]),
                "result": {"stages": res["stages"], "overall_delta_pp": res["overall_delta_pp"],
                           "overall": {"base": res["periods"]["base"]["overall"],
                                       "current": res["periods"]["current"]["overall"]},
                           "checks": res["checks"]}}

    if name == "funnel_segments":
        df, _ = _load(args)
        assign = core.assign_periods(df, **_periods(args))
        if not assign["ok"]:
            raise ToolError("периоды не прошли проверку")
        res = core.segments(df, assign, _by(args), **_periods(args))
        # если сегментируем по платформе — показываем разрез худшего сегмента по категориям и регионам
        drill = None
        if _by(args) == ["platform"] and res["rows"]:
            worst = res["rows"][0]["segment"]["platform"]
            drill = {"platform": worst}
            for dim in ("category", "region_type"):
                sub = core.segments(df[df.platform == worst], assign, [dim], **_periods(args))
                drill[dim] = sub["rows"]
        return {"tool": name, "ok": all(c["ok"] for c in res["checks"]),
                "result": {**{k: v for k, v in res.items() if k != "checks"},
                           "checks": res["checks"], "drilldown": drill}}

    if name == "funnel_weekly":
        df, _ = _load(args)
        assign = core.assign_periods(df, **_periods(args))
        if not assign["ok"]:
            raise ToolError("периоды не прошли проверку")
        res = core.weekly(df, assign, platform=args.get("platform"), event_date=args.get("event_date"))
        return {"tool": name, "ok": all(c["ok"] for c in res["checks"]), "result": res}

    if name == "funnel_events":
        df, _ = _load(args)
        assign = core.assign_periods(df, **_periods(args))
        if not assign["ok"]:
            raise ToolError("периоды не прошли проверку")
        res = core.events(df, assign, _require(args, "change_log_path"), **_periods(args))
        return {"tool": name, "ok": True, "result": res}

    raise ToolError(f"инструмент '{name}' не реализован")


TOOLS: Dict[str, Dict[str, Any]] = {
    s["name"]: {"spec": s, "fn": (lambda a, _n=s["name"]: call(_n, a))} for s in SCHEMAS
}
