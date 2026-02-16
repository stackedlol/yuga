"""Live order book panel with active quotes."""

from __future__ import annotations

from math import isfinite

from textual.app import ComposeResult
from textual.widgets import Static


class OrderBookPanel(Static):

    DEFAULT_CSS = """
    OrderBookPanel {
        height: 100%;
        border: tall $primary-lighten-2;
        background: $surface-darken-2;
        padding: 0 1;
    }
    OrderBookPanel .panel-title {
        text-style: bold;
        height: 1;
        color: $text;
        background: $primary 18%;
    }
    OrderBookPanel .panel-body {
        height: 1fr;
        color: $text;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("[bold]ORDERBOOK  |  DEPTH VIEW[/]", classes="panel-title")
        yield Static("", classes="panel-body", id="ob-body")

    def update_book(self, view: dict) -> None:
        if not view or not view.get("market_id"):
            self.query_one("#ob-body", Static).update("[dim]waiting for books...[/]")
            return

        quotes = view.get("quotes", [])
        qset = {(q["outcome"], q["side"], round(q["price"], 3)) for q in quotes}
        rows_per_side = 5

        def _bar(size: float, max_size: float, side: str) -> str:
            if not isfinite(size) or size <= 0 or max_size <= 0:
                return "[dim]............[/]"
            width = max(1, min(12, int(round((size / max_size) * 12))))
            fill = "█" * width
            rest = "·" * (12 - width)
            color = "red" if side == "ask" else "green"
            return f"[{color}]{fill}[/][dim]{rest}[/]"

        def _fmt_side(
            side: str,
            rows: list[tuple[float, float]],
            outcome: str,
            mid: float | None,
        ) -> list[str]:
            max_sz = max((float(s) for _, s in rows), default=0.0)
            out: list[str] = []
            shown = rows[:rows_per_side]
            cum = 0.0
            for idx, (p, s) in enumerate(shown):
                cum += float(s)
                quote_side = "SELL" if side == "ask" else "BUY"
                tag = "[bold yellow]  •Q[/]" if (outcome, quote_side, round(p, 3)) in qset else ""
                side_label = "[red]A[/]" if side == "ask" else "[green]B[/]"
                bps_txt = "--.-"
                if mid and mid > 0:
                    bps = ((p - mid) / mid) * 10000
                    bps_txt = f"{bps:+6.1f}"
                px_fmt = f"[bold bright_white]{p:>5.3f}[/]" if idx == 0 else f"[bold]{p:>5.3f}[/]"
                out.append(
                    f" {side_label} {px_fmt}  "
                    f"[bright_white]{s:>7.1f}[/]  [dim]{cum:>7.1f}[/]  "
                    f"[dim]{bps_txt}bp[/]  {_bar(float(s), max_sz, side)}{tag}"
                )
            for _ in range(rows_per_side - len(shown)):
                out.append(" [dim]·  ---.---     ---.-    ---.-   --.-bp  ............[/]")
            return out

        def _fmt_header(
            outcome: str, bids: list[tuple[float, float]], asks: list[tuple[float, float]]
        ) -> list[str]:
            bb = bids[0][0] if bids else None
            ba = asks[0][0] if asks else None
            mid = (bb + ba) / 2 if bb is not None and ba is not None else None
            spr = (ba - bb) if bb is not None and ba is not None else None
            bid_top = sum(s for _, s in bids[:rows_per_side])
            ask_top = sum(s for _, s in asks[:rows_per_side])
            total_top = max(bid_top + ask_top, 0.0001)
            buy_pct = (bid_top / total_top) * 100.0
            bar_fill = int(round((buy_pct / 100.0) * 16))
            bar_fill = max(0, min(16, bar_fill))
            pressure = f"[green]{'█' * bar_fill}[/][red]{'█' * (16 - bar_fill)}[/]"
            header = [f"[bold]{outcome}[/]  [dim]px      size      cum      dmid      depth         quote[/]"]
            if bb is None or ba is None:
                header.append("[dim] bb ---.---         ba ---.---[/]")
            else:
                header.append(f"[green] bb {bb:.3f}[/] [dim]        [/][red]ba {ba:.3f}[/]")
            if mid is None or spr is None:
                header.append("[dim] mid ---.---   spr ---.---   top5 b/a --.-/--.-[/]")
            else:
                header.append(
                    f"[bright_white] mid {mid:.3f}[/]   [bright_white]spr {spr:.3f}[/]   "
                    f"[dim]top5 b/a {bid_top:.1f}/{ask_top:.1f} ({buy_pct:>4.1f}% b)[/] {pressure}"
                )
            return header, mid

        def fmt_rows(outcome: str, bids: list, asks: list) -> list[str]:
            bid_rows = [(float(p), float(s)) for p, s in bids]
            ask_rows = [(float(p), float(s)) for p, s in asks]
            header, mid = _fmt_header(outcome, bid_rows, ask_rows)
            lines = header
            lines.append("[dim] ------------------------------- asks -------------------------------[/]")
            lines += _fmt_side("ask", ask_rows, outcome, mid)
            lines.append("[dim] ------------------------------- bids -------------------------------[/]")
            lines += _fmt_side("bid", bid_rows, outcome, mid)
            return lines

        market = view.get("market_id", "")
        mode = view.get("rotate_mode", "MANUAL")
        pos = int(view.get("book_pos", 0))
        total = int(view.get("book_total", 0))
        ready_total = int(view.get("ready_total", 0))
        nav_line = f"[dim]book {pos}/{total} | ready {ready_total} | mode {mode} | [ / ] cycle | o toggle[/]"
        if view.get("is_live", True):
            if mode == "AUTO":
                status_line = f"[green]LIVE[/] [dim]| rotate {view.get('rotate_in_s', 0.0):.1f}s[/]"
            else:
                status_line = "[green]LIVE[/] [dim]| manual book select[/]"
        else:
            status_line = (
                f"[yellow]STALE[/] [dim]{view.get('stale_age_s', 0.0):.1f}s old | searching refresh...[/]"
            )
        lines: list[str] = [
            f"[bold]{view.get('question', '')[:52]}[/]",
            f"{status_line}  [dim]| mkt {market[:14]}[/]",
            nav_line,
            "",
        ]
        lines += fmt_rows("YES", view.get("yes_bids", []), view.get("yes_asks", []))
        lines.append("")
        lines += fmt_rows("NO", view.get("no_bids", []), view.get("no_asks", []))

        self.query_one("#ob-body", Static).update("\n".join(lines))
