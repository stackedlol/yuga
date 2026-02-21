"""PnL sparkline chart."""

from __future__ import annotations

from collections import deque

from textual.app import ComposeResult
from textual.widgets import Static, Sparkline


class PnLChart(Static):

    DEFAULT_CSS = """
    PnLChart {
        height: 100%;
        border: round #83a598 30%;
        background: #282828;
        padding: 0 1;
    }
    PnLChart .panel-title {
        text-style: bold;
        height: 1;
        color: #83a598;
        background: #83a598 12%;
        padding: 0 1;
    }
    PnLChart Sparkline {
        height: 1fr;
        min-height: 2;
    }
    PnLChart .chart-footer {
        height: 1;
        color: #928374;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._data: deque[float] = deque(maxlen=120)
        self._data.append(0.0)

    def compose(self) -> ComposeResult:
        yield Static("\u25c8 [bold #83a598]PNL[/]", classes="panel-title", id="pnl-title")
        yield Sparkline(list(self._data), id="pnl-spark")
        yield Static("", classes="chart-footer", id="pnl-footer")

    def update_pnl(self, pnl_history: list[tuple[float, float]], exec_stats: dict) -> None:
        if pnl_history:
            self._data.clear()
            for _, v in pnl_history[-120:]:
                self._data.append(v)
        else:
            val = exec_stats.get("cumulative_pnl", 0)
            if len(self._data) <= 1:
                self._data.append(val)

        self.query_one("#pnl-spark", Sparkline).data = list(self._data)

        pnl = exec_stats.get("cumulative_pnl", 0)
        pc = "#b8bb26" if pnl >= 0 else "#fb4934"
        icon = "\u25b2" if pnl >= 0 else "\u25bc"
        fills = exec_stats.get("total_fills", 0)
        orders = exec_stats.get("total_orders", 0)
        fr = exec_stats.get("fill_rate", 0)

        self.query_one("#pnl-title", Static).update(
            f"\u25c8 [bold #83a598]PNL[/]  [{pc}]{icon} ${pnl:+.4f}[/]"
        )
        self.query_one("#pnl-footer", Static).update(
            f"[#928374]\u2714 {fills}/{orders}  \u25ce {fr:.0f}%[/]"
        )
