"""
可视化智能推荐
启发式规则, 不用 LLM 浪费 token — 图表类型是确定性逻辑
"""

from typing import Any

import pandas as pd


def recommend_chart(
    df: pd.DataFrame, *, category_column: str = "",
) -> tuple[str, dict[str, Any]]:
    """
    分析 DataFrame 结构, 推荐图表类型 + 生成 ECharts option
    返回: (chart_type, echarts_option)

    规则:
    - 单行单值 → card
    - 1 列时间 + N 列数值 → line
    - 1 列分类 + 1 列数值 → pie (≤6 unique) / bar (>6)
    - LLM 指定 category_column 时直接用, 多数值列启发式选 value 列
    - 其他 → table
    """
    if df.empty:
        return "table", {}

    rows, cols = df.shape

    # ── 单行单值: 数字卡片 ──
    if rows == 1 and cols == 1:
        val = df.iloc[0, 0]
        return "card", {"value": _to_serializable(val), "label": df.columns[0]}

    # ── 单行多值: 也用卡片组 ──
    if rows == 1 and cols <= 4:
        cards = [{"label": c, "value": _to_serializable(df.iloc[0][c])} for c in df.columns]
        return "card", {"cards": cards}

    # ── 分析列类型 ──
    time_cols = [c for c in df.columns if _is_time_column(df[c])]  # type: ignore[arg-type]
    num_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    cat_cols = [c for c in df.columns if c not in time_cols and c not in num_cols]

    # ── 多分类维度 (≥2 个分类列): 2D 图表 (bar/line/pie) 无法无损表达, 落表格 ──
    # 必须置于 category_column / 自动检测分支之前: LLM 传 category_column 时会优先
    # 命中那个分支出 bar, 放后面拦不住 (实证 trace 7270955a).
    if len(cat_cols) >= 2:
        return "table", {}

    # ── 时间 + 数值 → 折线图 ──
    if len(time_cols) == 1 and len(num_cols) >= 1:
        return "line", _build_line_option(df, time_cols[0], num_cols)

    # ── LLM 指定了 category_column 且在 DataFrame 中 ──
    if category_column and category_column in df.columns:
        value_cols = [c for c in num_cols if c != category_column]
        if not value_cols:
            return "table", {}
        value_col = _pick_value_column(value_cols)
        unique_count = df[category_column].nunique()
        if unique_count <= 6:
            return "pie", _build_pie_option(df, category_column, value_col)
        return "bar", _build_bar_option(df, category_column, value_col)

    # ── 分类 + 数值 (自动检测) ──
    if len(cat_cols) == 1 and len(num_cols) >= 1:
        value_col = _pick_value_column(num_cols) if len(num_cols) > 1 else num_cols[0]
        unique_count = df[cat_cols[0]].nunique()
        if unique_count <= 6:
            return "pie", _build_pie_option(df, cat_cols[0], value_col)
        return "bar", _build_bar_option(df, cat_cols[0], value_col)

    # ── 兜底: 表格 ──
    return "table", {}


# ════════════════════════════════════════════
#  内部工具函数
# ════════════════════════════════════════════

_VALUE_KEYWORDS = ("count", "sum", "total", "amount", "数量", "cnt")


def _pick_value_column(num_cols: list[str]) -> str:
    """多数值列时启发式选度量列: 列名含 count/sum/total 等优先, 否则取第一个."""
    for col in num_cols:
        lower = col.lower()
        if any(kw in lower for kw in _VALUE_KEYWORDS):
            return col
    return num_cols[0]


def _is_time_column(series: pd.Series) -> bool:
    if pd.api.types.is_datetime64_any_dtype(series):
        return True
    name = series.name.lower() if isinstance(series.name, str) else ""
    return any(kw in name for kw in ("date", "time", "day", "month", "year", "日期", "时间"))


def _to_serializable(val):
    if pd.isna(val):
        return None
    if hasattr(val, "item"):
        return val.item()
    return val


def _build_line_option(df: pd.DataFrame, time_col: str, num_cols: list[str]) -> dict:
    x_data = df[time_col].astype(str).tolist()
    series = [
        {"name": c, "type": "line", "data": [_to_serializable(v) for v in df[c].tolist()]}
        for c in num_cols
    ]
    return {
        "xAxis": {"type": "category", "data": x_data},
        "yAxis": {"type": "value"},
        "series": series,
        "tooltip": {"trigger": "axis"},
        "legend": {"data": num_cols} if len(num_cols) > 1 else {},
    }


def _build_pie_option(df: pd.DataFrame, cat_col: str, num_col: str) -> dict:
    data = [
        {"name": str(row[cat_col]), "value": _to_serializable(row[num_col])}
        for _, row in df.iterrows()
    ]
    return {
        "series": [{"type": "pie", "data": data, "radius": "60%"}],
        "tooltip": {"trigger": "item"},
    }


def _build_bar_option(df: pd.DataFrame, cat_col: str, num_col: str) -> dict:
    return {
        "xAxis": {"type": "category", "data": df[cat_col].astype(str).tolist()},
        "yAxis": {"type": "value"},
        "series": [{"type": "bar", "data": [_to_serializable(v) for v in df[num_col].tolist()]}],
        "tooltip": {"trigger": "axis"},
    }
