"""Top status bar."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Static


def _lat(ms: float, lo: float = 50, hi: float = 200) -> str:
    if ms < lo:
        return f"[green]{ms:.0f}[/][dim]ms[/]"
    if ms < hi:
        return f"[yellow]{ms:.0f}[/][dim]ms[/]"
    return f"[red]{ms:.0f}[/][dim]ms[/]"


class MetricsBar(Static):

    DEFAULT_CSS = """
    MetricsBar {
        height: 1;
        padding: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("", id="bar-text")

    def update_metrics(self, state: dict) -> None:
        ws = state.get("ws_state", {})
        connected = ws.get("connected", False)
        ws_lat = ws.get("latency_ms", 0)
        subs = ws.get("subscribed", 0)
        recon = ws.get("reconnects", 0)
        msg_age = ws.get("last_msg_age_s", 0)
        clob_lat = state.get("clob_latency_ms", 0)
        uptime = state.get("uptime_s", 0)
        paused = state.get("paused", False)

        if connected and msg_age < 5:
            ws_icon = "[green]\u25c9[/]"
        elif connected:
            ws_icon = "[yellow]\u25ce[/]"
        else:
            ws_icon = "[red]\u25cb[/]"

        status = "[red]PAUSED[/]" if paused else "[green]\u25b6 LIVE[/]"

        h = int(uptime // 3600)
        m = int((uptime % 3600) // 60)
        s = int(uptime % 60)

        recon_str = f"  [dim]\u21bb[/][red]{recon}[/]" if recon > 0 else ""

        text = (
            f"[bold]yuga[/]  "
            f"{status}  "
            f"{ws_icon} [dim]ws[/] {_lat(ws_lat)}  "
            f"\u25c7 [dim]clob[/] {_lat(clob_lat, 100, 500)}  "
            f"[dim]subs[/] {subs}{recon_str}  "
            f"[dim]{h:02d}:{m:02d}:{s:02d}[/]"
        )

        self.query_one("#bar-text", Static).update(text)
