"""Top status bar."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Static


def _lat(ms: float, lo: float = 50, hi: float = 200) -> str:
    if ms < lo:
        return f"[#22c55e]{ms:.0f}[/][#64748b]ms[/]"
    if ms < hi:
        return f"[#f59e0b]{ms:.0f}[/][#64748b]ms[/]"
    return f"[#f43f5e]{ms:.0f}[/][#64748b]ms[/]"


class MetricsBar(Static):

    DEFAULT_CSS = """
    MetricsBar {
        height: 1;
        padding: 0 1;
        background: #0c1929;
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
            ws_icon = "[#22c55e]\u25c9[/]"
        elif connected:
            ws_icon = "[#f59e0b]\u25ce[/]"
        else:
            ws_icon = "[#f43f5e]\u25cb[/]"

        if paused:
            status = "[#f43f5e]\u23f8 PAUSED[/]"
        else:
            status = "[#22c55e]\u25c9 LIVE[/]"

        h = int(uptime // 3600)
        m = int((uptime % 3600) // 60)
        s = int(uptime % 60)

        recon_str = f" [#64748b]\u21bb[/][#f43f5e]{recon}[/]" if recon > 0 else ""

        sep = "[#334155]\u2502[/]"

        text = (
            f"[bold #06b6d4]YUGA[/]  "
            f"{status}  {sep}  "
            f"{ws_icon} [#64748b]ws[/] {_lat(ws_lat)}  {sep}  "
            f"\u25c7 [#64748b]clob[/] {_lat(clob_lat, 100, 500)}  {sep}  "
            f"[#64748b]subs[/] {subs}{recon_str}  {sep}  "
            f"[#64748b]{h:02d}:{m:02d}:{s:02d}[/]"
        )

        self.query_one("#bar-text", Static).update(text)
