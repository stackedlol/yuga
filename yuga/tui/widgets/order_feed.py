"""Live order feed panel."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Static, DataTable


_STATUS = {
    "FILLED":    ("#b8bb26", "\u2714"),
    "PARTIAL":   ("#fabd2f", "\u25d1"),
    "OPEN":      ("#83a598", "\u25ce"),
    "PENDING":   ("#928374", "\u25cb"),
    "CANCELLED": ("#928374", "\u2718"),
    "REJECTED":  ("#fb4934", "\u2716"),
}


class OrderFeed(Static):

    DEFAULT_CSS = """
    OrderFeed {
        height: 100%;
        border: round #83a598 30%;
        background: #282828;
        padding: 0 1;
    }
    OrderFeed .panel-title {
        text-style: bold;
        height: 1;
        color: #83a598;
        background: #83a598 12%;
        padding: 0 1;
    }
    OrderFeed DataTable {
        height: 1fr;
        background: #282828;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("\u25b9 [bold #83a598]ORDERS[/]", classes="panel-title", id="of-title")
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

        parts = [f"\u25b9 [bold #83a598]ORDERS[/] {len(orders)}"]
        if live:
            parts.append(f"[#83a598]\u25ce{live}[/]")
        if fills:
            parts.append(f"[#b8bb26]\u2714{fills}[/]")
        if rejects:
            parts.append(f"[#fb4934]\u2716{rejects}[/]")
        self.query_one("#of-title", Static).update("  ".join(parts))

        for o in reversed(orders):
            color, icon = _STATUS.get(o["status"], ("#928374", "?"))
            side = "[#b8bb26]\u25b2[/]" if o["side"] == "BUY" else "[#fb4934]\u25bc[/]"
            out = "[#83a598]Y[/]" if o["outcome"] == "YES" else "[#d3869b]N[/]"

            lat = o["latency_ms"]
            lc = "#b8bb26" if lat < 100 else "#fabd2f" if lat < 300 else "#fb4934"

            age = o["age_s"]
            ac = "#928374" if age < 5 else "#fabd2f" if age < 30 else "#fb4934"

            t.add_row(
                f"[{color}]{icon}[/]",
                f"[#928374]{o['id']}[/]",
                side,
                out,
                f"{o['price']:.4f}",
                f"{o['size']:.1f}",
                f"{o['filled']:.1f}",
                f"[{lc}]{lat:.0f}[/]",
                f"[{ac}]{age:.0f}s[/]",
            )
