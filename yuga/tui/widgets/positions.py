"""Positions and risk panel."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Static


def _bar(value: float, max_val: float, width: int = 10) -> str:
    if max_val <= 0:
        return "[#3c3836]" + "\u2591" * width + "[/]"
    ratio = min(abs(value) / max_val, 1.0)
    filled = int(ratio * width)
    c = "#fb4934" if ratio > 0.8 else "#fabd2f" if ratio > 0.5 else "#b8bb26"
    blocks = "\u2588" * max(0, filled - 1) + "\u2593" if filled > 0 else ""
    return f"[{c}]{blocks}[/][#3c3836]{'\u2591' * (width - filled)}[/]"


def _kv(icon: str, label: str, value: str) -> str:
    return f"  {icon} [#928374]{label}[/] {value}"


class PositionsPanel(Static):

    DEFAULT_CSS = """
    PositionsPanel {
        height: 100%;
        border: round #83a598 30%;
        background: #282828;
        padding: 0 1;
    }
    PositionsPanel .panel-title {
        text-style: bold;
        height: 1;
        color: #83a598;
        background: #83a598 12%;
        padding: 0 1;
    }
    PositionsPanel .panel-body {
        height: 1fr;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("\u25c8 [bold #83a598]RISK & PNL[/]", classes="panel-title")
        yield Static("", classes="panel-body", id="risk-body")

    def update_state(self, exec_stats: dict, risk: dict, inventory: list[dict]) -> None:
        pnl = exec_stats.get("cumulative_pnl", 0)
        daily = risk.get("daily_pnl", 0)
        pc = "#b8bb26" if pnl >= 0 else "#fb4934"
        dc = "#b8bb26" if daily >= 0 else "#fb4934"
        pnl_icon = "\u25b2" if pnl >= 0 else "\u25bc"
        spread_pnl = exec_stats.get("spread_capture_pnl", 0)
        rebates = exec_stats.get("liquidity_rewards", 0)

        fills = exec_stats.get("total_fills", 0)
        orders = exec_stats.get("total_orders", 0)
        rejects = exec_stats.get("total_rejects", 0)
        cancels = exec_stats.get("total_cancels", 0)
        fr = exec_stats.get("fill_rate", 0)
        lat = exec_stats.get("avg_latency_ms", 0)
        fc = "#b8bb26" if fr > 70 else "#fabd2f" if fr > 40 else "#fb4934"
        lc = "#b8bb26" if lat < 100 else "#fabd2f" if lat < 300 else "#fb4934"

        cb = risk.get("circuit_breaker_active", False)
        consec = risk.get("consecutive_losses", 0)
        max_loss = risk.get("max_daily_loss", 50)

        lines = [
            f"  [{pc}]{pnl_icon}[/] [bold {pc}]${pnl:+.4f}[/]  [#928374]all-time[/]",
            f"  [{dc}]{'\u25b2' if daily >= 0 else '\u25bc'}[/] [{dc}]${daily:+.4f}[/]  [#928374]today[/]",
            f"  [#83a598]\u25ce[/] [#83a598]${spread_pnl:+.4f}[/] [#928374]spread[/]",
            f"  [#fabd2f]\u26a1[/] [#fabd2f]${rebates:+.4f}[/] [#928374]rebates[/]",
            "[#504945]  \u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504[/]",
            _kv("\u2660", "orders ", f"{orders}"),
            _kv("\u2714", "fills  ", f"[#b8bb26]{fills}[/]  "
                f"[#928374]\u2716[/][#fb4934]{rejects}[/]  "
                f"[#928374]\u2718[/][#928374]{cancels}[/]"),
            _kv("\u25ce", "rate   ", f"[{fc}]{fr:.0f}%[/]  {_bar(fr, 100, 8)}"),
            _kv("\u25d0", "lat    ", f"[{lc}]{lat:.0f}ms[/]"),
            "[#504945]  \u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504[/]",
        ]

        if cb:
            rem = risk.get("circuit_breaker_remaining_s", 0)
            reason = risk.get("circuit_breaker_reason", "")
            lines.append(f"  [bold #fb4934]\u26a0 BREAKER TRIPPED[/] [#928374]{rem:.0f}s[/]")
            lines.append(f"    [#fb4934]{reason}[/]")
        else:
            lines.append(_kv("\u26a1", "breaker", "[#b8bb26]\u25cf ok[/]"))

        lines.append(
            _kv("\u2620", "streak ", f"{consec}[#928374]/5[/]  {_bar(consec, 5, 5)}")
        )
        lines.append("")

        loss_used = abs(min(daily, 0))
        lines.append(
            _kv("\u25c6", "daily  ",
                f"{_bar(loss_used, max_loss, 8)} [#928374]${loss_used:.1f}/${max_loss:.0f}[/]")
        )

        if inventory:
            lines.append("")
            lines.append("  [bold #83a598]inventory[/]")
            for row in inventory[:6]:
                lines.append(
                    f"  [#928374]{row.get('condition_id','')[:6]}[/] "
                    f"{row.get('outcome',''):<3} "
                    f"[#83a598]{row.get('size',0):+.1f}[/]"
                )

        self.query_one("#risk-body", Static).update("\n".join(lines))
