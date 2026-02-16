"""Selected market odds chart styled like a market probability timeline."""

from __future__ import annotations

from collections import deque

from textual.app import ComposeResult
from textual.widgets import Static


class OddsChart(Static):

    DEFAULT_CSS = """
    OddsChart {
        height: 100%;
        border: tall $surface-lighten-1;
        background: $surface-darken-2;
        padding: 0 1;
    }
    OddsChart .panel-title {
        text-style: bold;
        height: 1;
    }
    OddsChart .panel-body {
        height: 1fr;
    }
    OddsChart .chart-footer {
        height: 1;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._yes: deque[float] = deque(maxlen=180)
        self._no: deque[float] = deque(maxlen=180)
        self._yes.append(50.0)
        self._no.append(50.0)

    def compose(self) -> ComposeResult:
        yield Static("[bold]ODDS[/]  [dim](historical %)[/]", classes="panel-title", id="odds-title")
        yield Static("", classes="panel-body", id="odds-body")
        yield Static("", classes="chart-footer", id="odds-footer")

    def _resample(self, vals: list[float], width: int) -> list[float]:
        if not vals:
            return []
        if len(vals) <= width:
            return vals
        out: list[float] = []
        for i in range(width):
            idx = int(round(i * (len(vals) - 1) / max(1, width - 1)))
            out.append(vals[idx])
        return out

    def _to_row(self, v: float, height: int) -> int:
        # Fixed 0..100 scale for comparability between snapshots.
        v = max(0.0, min(100.0, v))
        y = int(round((v / 100.0) * (height - 1)))
        return (height - 1) - y

    def _plot_series(
        self,
        grid: list[list[str]],
        vals: list[float],
        color: str,
        mark: str,
    ) -> None:
        h = len(grid)
        w = len(grid[0]) if h else 0
        if not vals or w <= 0 or h <= 0:
            return
        pts = self._resample(vals, w)
        ys = [self._to_row(v, h) for v in pts]

        for x in range(1, len(ys)):
            y0 = ys[x - 1]
            y1 = ys[x]
            steps = max(1, abs(y1 - y0))
            for i in range(steps + 1):
                xi = x - 1 + int(round(i / steps))
                yi = int(round(y0 + (y1 - y0) * (i / steps)))
                xi = max(0, min(w - 1, xi))
                yi = max(0, min(h - 1, yi))
                token = f"[{color}]{mark}[/]"
                cell = grid[yi][xi]
                if cell not in (" ", "[#334155]·[/]", "[#263648]·[/]"):
                    token = "[bright_white]◆[/]"
                grid[yi][xi] = token
            end_token = f"[{color}]●[/]"
            end_cell = grid[y1][x]
            if end_cell not in (" ", "[#334155]·[/]", "[#263648]·[/]"):
                end_token = "[bright_white]◆[/]"
            grid[y1][x] = end_token

        y_start = ys[0]
        start_token = f"[{color}]●[/]"
        start_cell = grid[y_start][0]
        if start_cell not in (" ", "[#334155]·[/]", "[#263648]·[/]"):
            start_token = "[bright_white]◆[/]"
        grid[y_start][0] = start_token

    def _render_market_chart(
        self, yes_vals: list[float], no_vals: list[float], width: int, height: int
    ) -> list[str]:
        width = max(28, width)
        height = max(10, height)
        grid = [[" " for _ in range(width)] for _ in range(height)]

        ticks = [0, 25, 50, 75, 100]
        tick_rows = {self._to_row(t, height): t for t in ticks}
        for r, t in tick_rows.items():
            for x in range(width):
                grid[r][x] = "[#334155]·[/]" if t not in (0, 100) else "[#263648]·[/]"

        # Distinct colors for immediate separation.
        self._plot_series(grid, yes_vals, "#22c55e", "╱")
        self._plot_series(grid, no_vals, "#f43f5e", "╲")

        # Endpoint glow markers.
        if yes_vals:
            y = self._to_row(yes_vals[-1], height)
            grid[y][-1] = "[#4ade80]●[/]"
        if no_vals:
            y = self._to_row(no_vals[-1], height)
            grid[y][-1] = "[#fb7185]●[/]"

        out: list[str] = []
        for r, row in enumerate(grid):
            axis = f"[#6b7f98]{tick_rows[r]:>3}%[/]" if r in tick_rows else "    "
            out.append("".join(row) + " " + axis)
        out.append(f"[#334155]{'─' * width}[/]")
        out.append("[#6b7f98]oldest[/]" + " " * max(1, width - 12) + "[#6b7f98]now[/]")
        return out

    def update_odds(self, odds_view: dict) -> None:
        yes = odds_view.get("yes", [])
        no = odds_view.get("no", [])
        yes_now = float(odds_view.get("yes_now", 0.0))
        no_now = float(odds_view.get("no_now", 0.0))

        if yes:
            self._yes.clear()
            self._yes.extend(float(v) for v in yes[-180:])
        elif yes_now > 0:
            if not self._yes or abs(self._yes[-1] - yes_now) >= 0.01:
                self._yes.append(yes_now)

        if no:
            self._no.clear()
            self._no.extend(float(v) for v in no[-180:])
        elif no_now > 0:
            if not self._no or abs(self._no[-1] - no_now) >= 0.01:
                self._no.append(no_now)

        q = odds_view.get("question", "")
        yes_now = yes_now or float(self._yes[-1] if self._yes else 0.0)
        no_now = no_now or float(self._no[-1] if self._no else 0.0)
        is_live = bool(odds_view.get("is_live", False))
        stale_age_s = float(odds_view.get("stale_age_s", 0.0))
        samples = int(odds_view.get("samples", 0))

        self.query_one("#odds-title", Static).update(
            f"[bold]ODDS[/]  [green]YES {yes_now:5.2f}%[/]  [red]NO {no_now:5.2f}%[/]"
        )

        yes_vals = list(self._yes)
        no_vals = list(self._no)
        yes_first = yes_vals[0] if yes_vals else yes_now
        no_first = no_vals[0] if no_vals else no_now
        yes_dp = yes_now - yes_first
        no_dp = no_now - no_first

        width = self.size.width - 9 if self.size.width > 14 else 56
        chart = self._render_market_chart(yes_vals, no_vals, width=width, height=10)
        feed = "[green]LIVE[/]" if is_live else f"[yellow]STALE {stale_age_s:.1f}s[/]"
        legend = (
            "[#22c55e]● YES odds[/]  [#f43f5e]● NO odds[/]  "
            f"[#22c55e]{yes_dp:+.2f}pt[/]  [#f43f5e]{no_dp:+.2f}pt[/]  "
            f"{feed}  [dim]{samples} samples[/]"
        )

        self.query_one("#odds-body", Static).update("\n".join(chart + [legend]))
        self.query_one("#odds-footer", Static).update(f"[dim]{q[:64]}[/]")
