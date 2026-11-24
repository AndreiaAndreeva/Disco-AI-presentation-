"""Набор инструментов для анализа воронки записи.

Использование из Python:

    from funnel_tools import TOOLS, schemas, call
    call("funnel_check", {"csv_path": "data/funnel_weekly_raw.csv"})

Схемы для function calling:

    from funnel_tools import schemas
    schemas()  # list[dict] с name / description / parameters
"""
from .specs import SCHEMAS, TOOLS, ToolError, call

__all__ = ["SCHEMAS", "TOOLS", "ToolError", "call", "schemas"]


def schemas():
    """Копия списка схем инструментов (для передачи модели)."""
    return [dict(s) for s in SCHEMAS]
