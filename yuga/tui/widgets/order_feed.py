"""Live order feed panel."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Static, DataTable


_STATUS = {
    "FILLED":    ("green",  "\u2714"),
    "PARTIAL":   ("yellow", "\u25d1"),
    "OPEN":      ("cyan",   "\u25ce"),
    "PENDING":   ("dim",    "\u25cb"),
    "CANCELLED": ("dim",    "\u2718"),
    "REJECTED":  ("red",    "\u2716"),
}


class OrderFeed(Static):

    DEFAULT_CSS = """
    OrderFeed {
        height: 100%;
        border: tall $surface-lighten-1;
        padding: 0 1;
    }
    OrderFeed .panel-title {
        text-style: bold;
        height: 1;
    }
    OrderFeed DataTable {
        height: 1fr;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("\u25b9 [bold]ORDERS[/]", classes="panel-title", id="of-title")
        yield DataTable(id="of-table")

    def on_mount(self) -> None:
        t = self.query_one("#of-table", DataTable)
        t.cursor_type = "row"
        t.zebra_stripes = True
        t.add_columns("\u25cf", "id", "\u2195", "out", "price", "size", "fill", "lat", "age")

    def update_orders(self, orders: list[dict]) -> None:
        t = self.query_one("#of-table", DataTable)
        t.clear()

        live = sum(1 for o in orders if o["status"] in ("OPEN", "PENDING", "PARTIAL"))
        fills = sum(1 for o in orders if o["status"] == "FILLED")
        rejects = sum(1 for o in orders if o["status"] == "REJECTED")

        parts = [f"\u25b9 [bold]ORDERS[/] {len(orders)}"]
        if live:
            parts.append(f"[cyan]\u25ce{live}[/]")
        if fills:
            parts.append(f"[green]\u2714{fills}[/]")
        if rejects:
            parts.append(f"[red]\u2716{rejects}[/]")
        self.query_one("#of-title", Static).update("  ".join(parts))

        for o in reversed(orders):
            color, icon = _STATUS.get(o["status"], ("dim", "?"))
            side = "[green]\u25b2[/]" if o["side"] == "BUY" else "[red]\u25bc[/]"
            out = "[cyan]Y[/]" if o["outcome"] == "YES" else "[magenta]N[/]"

            lat = o["latency_ms"]
            lc = "green" if lat < 100 else "yellow" if lat < 300 else "red"

            age = o["age_s"]
            ac = "dim" if age < 5 else "yellow" if age < 30 else "red"

            t.add_row(
                f"[{color}]{icon}[/]",
                f"[dim]{o['id']}[/]",
                side,
                out,
                f"{o['price']:.4f}",
                f"{o['size']:.1f}",
                f"{o['filled']:.1f}",
                f"[{lc}]{lat:.0f}[/]",
                f"[{ac}]{age:.0f}s[/]",
            )
