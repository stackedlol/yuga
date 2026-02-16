"""Market scanner panel."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Static, DataTable


class MarketScanner(Static):

    DEFAULT_CSS = """
    MarketScanner {
        height: 100%;
        border: round #06b6d4 30%;
        background: #0f172a;
        padding: 0 1;
    }
    MarketScanner .panel-title {
        text-style: bold;
        height: 1;
        color: #06b6d4;
        background: #06b6d4 12%;
        padding: 0 1;
    }
    MarketScanner DataTable {
        height: 1fr;
        background: #0f172a;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static(
            "\u25c8 [bold #06b6d4]MARKETS[/] [#64748b]0[/]",
            classes="panel-title", id="sc-title",
        )
        yield DataTable(id="sc-table")

    def on_mount(self) -> None:
        t = self.query_one("#sc-table", DataTable)
        t.cursor_type = "row"
        t.zebra_stripes = True
        t.add_columns("\u25cf", "market", "y.mid", "n.mid", "y.spread", "ready", "\u26a1")

    def update_markets(self, markets: list[dict], signals: dict) -> None:
        t = self.query_one("#sc-table", DataTable)
        t.clear()

        ready = sum(1 for m in markets if m["ready"])
        sig_count = len(signals)
        parts = [
            f"\u25c8 [bold #06b6d4]MARKETS[/] {len(markets)}",
            f"[#22c55e]\u25cf {ready}[/]" if ready else "[#64748b]\u25cb 0[/]",
        ]
        if sig_count:
            parts.append(f"[#f59e0b]\u26a1{sig_count}[/]")
        self.query_one("#sc-title", Static).update("  ".join(parts))

        for m in markets:
            sig = signals.get(m["id"])
            sa = m.get("spread_bps", 0)

            if sig:
                dot = "[#f59e0b]\u2738[/]"
            elif m["ready"]:
                dot = "[#22c55e]\u25cf[/]"
            else:
                dot = "[#64748b]\u25cb[/]"

            if sa < 20:
                sc = f"[bold #22c55e]{sa:.1f}bp[/]"
            elif sa > 80:
                sc = f"[bold #f43f5e]{sa:.1f}bp[/]"
            else:
                sc = f"[#94a3b8]{sa:.1f}bp[/]"

            if sig:
                sig_text = f"[bold #f59e0b]\u26a1 {sig['spread_bps']:.0f}bp[/]"
            else:
                sig_text = "[#64748b]\u2013[/]"

            t.add_row(
                dot,
                m["question"][:30],
                f"{m['yes_mid']:.3f}",
                f"{m['no_mid']:.3f}",
                sc,
                "[#22c55e]\u25cf[/]" if m["ready"] else "[#64748b]\u25cb[/]",
                sig_text,
            )
