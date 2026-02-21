"""Market scanner panel."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Static, DataTable


class MarketScanner(Static):

    DEFAULT_CSS = """
    MarketScanner {
        height: 100%;
        border: round #83a598 30%;
        background: #282828;
        padding: 0 1;
    }
    MarketScanner .panel-title {
        text-style: bold;
        height: 1;
        color: #83a598;
        background: #83a598 12%;
        padding: 0 1;
    }
    MarketScanner DataTable {
        height: 1fr;
        background: #282828;
        scrollbar-color: #504945;
        scrollbar-color-hover: #665c54;
        scrollbar-color-active: #7c6f64;
        scrollbar-background: #282828;
        scrollbar-background-hover: #282828;
        scrollbar-background-active: #282828;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static(
            "\u25c8 [bold #83a598]MARKETS[/] [#928374]0[/]",
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
            f"\u25c8 [bold #83a598]MARKETS[/] {len(markets)}",
            f"[#b8bb26]\u25cf {ready}[/]" if ready else "[#928374]\u25cb 0[/]",
        ]
        if sig_count:
            parts.append(f"[#fabd2f]\u26a1{sig_count}[/]")
        self.query_one("#sc-title", Static).update("  ".join(parts))

        for m in markets:
            sig = signals.get(m["id"])
            sa = m.get("spread_bps", 0)

            if sig:
                dot = "[#fabd2f]\u2738[/]"
            elif m["ready"]:
                dot = "[#b8bb26]\u25cf[/]"
            else:
                dot = "[#928374]\u25cb[/]"

            if sa < 20:
                sc = f"[bold #b8bb26]{sa:.1f}bp[/]"
            elif sa > 80:
                sc = f"[bold #fb4934]{sa:.1f}bp[/]"
            else:
                sc = f"[#a89984]{sa:.1f}bp[/]"

            if sig:
                sig_text = f"[bold #fabd2f]\u26a1 {sig['spread_bps']:.0f}bp[/]"
            else:
                sig_text = "[#928374]\u2013[/]"

            t.add_row(
                dot,
                m["question"][:30],
                f"{m['yes_mid']:.3f}",
                f"{m['no_mid']:.3f}",
                sc,
                "[#b8bb26]\u25cf[/]" if m["ready"] else "[#928374]\u25cb[/]",
                sig_text,
            )
