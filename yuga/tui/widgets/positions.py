"""Positions and risk panel."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Static


def _bar(value: float, max_val: float, width: int = 10) -> str:
    if max_val <= 0:
        return "[dim]" + "\u2500" * width + "[/]"
    ratio = min(abs(value) / max_val, 1.0)
    filled = int(ratio * width)
    c = "red" if ratio > 0.8 else "yellow" if ratio > 0.5 else "green"
    return f"[{c}]{'\u2588' * filled}[/][dim]{'\u2500' * (width - filled)}[/]"


def _kv(icon: str, label: str, value: str) -> str:
    return f"  {icon} [dim]{label}[/] {value}"


class PositionsPanel(Static):

    DEFAULT_CSS = """
    PositionsPanel {
        height: 100%;
        border: tall $surface-lighten-1;
        padding: 0 1;
    }
    PositionsPanel .panel-title {
        text-style: bold;
        height: 1;
    }
    PositionsPanel .panel-body {
        height: 1fr;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("\u25c8 [bold]RISK & PNL[/]", classes="panel-title")
        yield Static("", classes="panel-body", id="risk-body")

    def update_state(self, exec_stats: dict, risk: dict, inventory: list[dict]) -> None:
        pnl = exec_stats.get("cumulative_pnl", 0)
        daily = risk.get("daily_pnl", 0)
        pc = "green" if pnl >= 0 else "red"
        dc = "green" if daily >= 0 else "red"
        pnl_icon = "\u25b2" if pnl >= 0 else "\u25bc"
        spread_pnl = exec_stats.get("spread_capture_pnl", 0)
        rebates = exec_stats.get("liquidity_rewards", 0)

        fills = exec_stats.get("total_fills", 0)
        orders = exec_stats.get("total_orders", 0)
        rejects = exec_stats.get("total_rejects", 0)
        cancels = exec_stats.get("total_cancels", 0)
        fr = exec_stats.get("fill_rate", 0)
        lat = exec_stats.get("avg_latency_ms", 0)
        fc = "green" if fr > 70 else "yellow" if fr > 40 else "red"
        lc = "green" if lat < 100 else "yellow" if lat < 300 else "red"

        cb = risk.get("circuit_breaker_active", False)
        consec = risk.get("consecutive_losses", 0)
        max_loss = risk.get("max_daily_loss", 50)

        lines = [
            f"  [{pc}]{pnl_icon}[/] [bold {pc}]${pnl:+.4f}[/]  [dim]all-time[/]",
            f"  [{dc}]{'\u25b2' if daily >= 0 else '\u25bc'}[/] [{dc}]${daily:+.4f}[/]  [dim]today[/]",
            f"  [cyan]\u25ce[/] [cyan]${spread_pnl:+.4f}[/] [dim]spread[/]",
            f"  [yellow]\u26a1[/] [yellow]${rebates:+.4f}[/] [dim]rebates[/]",
            "",
            _kv("\u2660", "orders ", f"{orders}"),
            _kv("\u2714", "fills  ", f"[green]{fills}[/]  "
                f"[dim]\u2716[/][red]{rejects}[/]  "
                f"[dim]\u2718[/][dim]{cancels}[/]"),
            _kv("\u25ce", "rate   ", f"[{fc}]{fr:.0f}%[/]  {_bar(fr, 100, 8)}"),
            _kv("\u25d0", "lat    ", f"[{lc}]{lat:.0f}ms[/]"),
            "",
        ]

        if cb:
            rem = risk.get("circuit_breaker_remaining_s", 0)
            reason = risk.get("circuit_breaker_reason", "")
            lines.append(f"  [bold red]\u26a0 BREAKER TRIPPED[/] [dim]{rem:.0f}s[/]")
            lines.append(f"    [red]{reason}[/]")
        else:
            lines.append(_kv("\u26a1", "breaker", "[green]\u25cf ok[/]"))

        lines.append(
            _kv("\u2620", "streak ", f"{consec}[dim]/5[/]  {_bar(consec, 5, 5)}")
        )
        lines.append("")

        loss_used = abs(min(daily, 0))
        lines.append(
            _kv("\u25c6", "daily  ",
                f"{_bar(loss_used, max_loss, 8)} [dim]${loss_used:.1f}/${max_loss:.0f}[/]")
        )

        if inventory:
            lines.append("")
            lines.append("  [bold]inventory[/]")
            for row in inventory[:6]:
                lines.append(
                    f"  [dim]{row.get('condition_id','')[:6]}[/] "
                    f"{row.get('outcome',''):<3} "
                    f"[cyan]{row.get('size',0):+.1f}[/]"
                )

        self.query_one("#risk-body", Static).update("\n".join(lines))
