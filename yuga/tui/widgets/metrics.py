"""Top status bar."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Static


def _lat(ms: float, lo: float = 50, hi: float = 200) -> str:
    if ms < lo:
        return f"[#b8bb26]{ms:.0f}[/][#928374]ms[/]"
    if ms < hi:
        return f"[#fabd2f]{ms:.0f}[/][#928374]ms[/]"
    return f"[#fb4934]{ms:.0f}[/][#928374]ms[/]"


class MetricsBar(Static):

    DEFAULT_CSS = """
    MetricsBar {
        height: 1;
        padding: 0 1;
        background: #1d2021;
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
            ws_icon = "[#b8bb26]\u25c9[/]"
        elif connected:
            ws_icon = "[#fabd2f]\u25ce[/]"
        else:
            ws_icon = "[#fb4934]\u25cb[/]"

        if paused:
            status = "[#fb4934]\u23f8 PAUSED[/]"
        else:
            status = "[#b8bb26]\u25c9 LIVE[/]"

        h = int(uptime // 3600)
        m = int((uptime % 3600) // 60)
        s = int(uptime % 60)

        recon_str = f" [#928374]\u21bb[/][#fb4934]{recon}[/]" if recon > 0 else ""

        sep = "[#504945]\u2502[/]"

        text = (
            f"[bold #83a598]YUGA[/]  "
            f"{status}  {sep}  "
            f"{ws_icon} [#928374]ws[/] {_lat(ws_lat)}  {sep}  "
            f"\u25c7 [#928374]clob[/] {_lat(clob_lat, 100, 500)}  {sep}  "
            f"[#928374]subs[/] {subs}{recon_str}  {sep}  "
            f"[#928374]{h:02d}:{m:02d}:{s:02d}[/]"
        )

        self.query_one("#bar-text", Static).update(text)
