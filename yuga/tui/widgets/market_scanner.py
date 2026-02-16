"""Market scanner panel."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Static, DataTable


class MarketScanner(Static):

    DEFAULT_CSS = """
    MarketScanner {
        height: 100%;
        border: tall $surface-lighten-1;
        padding: 0 1;
    }
    MarketScanner .panel-title {
        text-style: bold;
        height: 1;
    }
    MarketScanner DataTable {
        height: 1fr;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static(
            "\u25c8 [bold]MARKETS[/] [dim]0[/]",
            classes="panel-title", id="sc-title",
        )
        yield DataTable(id="sc-table")

    def on_mount(self) -> None:
        t = self.query_one("#sc-table", DataTable)
        t.cursor_type = "row"
        t.zebra_stripes = True
        t.add_columns("\u25cf", "market", "y.bid", "y.ask", "n.bid", "n.ask", "\u03a3", "\u26a1")

    def update_markets(self, markets: list[dict], signals: dict) -> None:
        t = self.query_one("#sc-table", DataTable)
        t.clear()

        ready = sum(1 for m in markets if m["ready"])
        sig_count = len(signals)
        parts = [
            f"\u25c8 [bold]MARKETS[/] {len(markets)}",
            f"[green]\u25cf {ready}[/]" if ready else "[dim]\u25cb 0[/]",
        ]
        if sig_count:
            parts.append(f"[yellow]\u26a1{sig_count}[/]")
        self.query_one("#sc-title", Static).update("  ".join(parts))

        for m in markets:
            sig = signals.get(m["id"])
            sa = m["spread"]

            if sig:
                dot = "[yellow]\u2738[/]"
            elif m["ready"]:
                dot = "[green]\u25cf[/]"
            else:
                dot = "[dim]\u25cb[/]"

            if sa < 0.995:
                sc = f"[bold green]{sa:.4f}[/]"
            elif sa > 1.005:
                sc = f"[bold red]{sa:.4f}[/]"
            else:
                sc = f"[dim]{sa:.4f}[/]"

            if sig:
                kind = "\u25b2" if "BUY" in sig["type"] else "\u25bc"
                sig_text = f"[bold yellow]{kind} {sig['spread_bps']:.0f}bp[/]"
            else:
                sig_text = "[dim]\u2014[/]"

            t.add_row(
                dot,
                m["question"][:30],
                f"{m['yes_bid']:.3f}",
                f"{m['yes_ask']:.3f}",
                f"{m['no_bid']:.3f}",
                f"{m['no_ask']:.3f}",
                sc,
                sig_text,
            )
