"""Live order feed panel."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Static, DataTable


_STATUS = {
    "FILLED":    ("#22c55e", "\u2714"),
    "PARTIAL":   ("#f59e0b", "\u25d1"),
    "OPEN":      ("#06b6d4", "\u25ce"),
    "PENDING":   ("#64748b", "\u25cb"),
    "CANCELLED": ("#64748b", "\u2718"),
    "REJECTED":  ("#f43f5e", "\u2716"),
}


class OrderFeed(Static):

    DEFAULT_CSS = """
    OrderFeed {
        height: 100%;
        border: round #06b6d4 30%;
        background: #0f172a;
        padding: 0 1;
    }
    OrderFeed .panel-title {
        text-style: bold;
        height: 1;
        color: #06b6d4;
        background: #06b6d4 12%;
        padding: 0 1;
    }
    OrderFeed DataTable {
        height: 1fr;
        background: #0f172a;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("\u25b9 [bold #06b6d4]ORDERS[/]", classes="panel-title", id="of-title")
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

        parts = [f"\u25b9 [bold #06b6d4]ORDERS[/] {len(orders)}"]
        if live:
            parts.append(f"[#06b6d4]\u25ce{live}[/]")
        if fills:
            parts.append(f"[#22c55e]\u2714{fills}[/]")
        if rejects:
            parts.append(f"[#f43f5e]\u2716{rejects}[/]")
        self.query_one("#of-title", Static).update("  ".join(parts))

        for o in reversed(orders):
            color, icon = _STATUS.get(o["status"], ("#64748b", "?"))
            side = "[#22c55e]\u25b2[/]" if o["side"] == "BUY" else "[#f43f5e]\u25bc[/]"
            out = "[#06b6d4]Y[/]" if o["outcome"] == "YES" else "[#a855f7]N[/]"

            lat = o["latency_ms"]
            lc = "#22c55e" if lat < 100 else "#f59e0b" if lat < 300 else "#f43f5e"

            age = o["age_s"]
            ac = "#64748b" if age < 5 else "#f59e0b" if age < 30 else "#f43f5e"

            t.add_row(
                f"[{color}]{icon}[/]",
                f"[#64748b]{o['id']}[/]",
                side,
                out,
                f"{o['price']:.4f}",
                f"{o['size']:.1f}",
                f"{o['filled']:.1f}",
                f"[{lc}]{lat:.0f}[/]",
                f"[{ac}]{age:.0f}s[/]",
            )
