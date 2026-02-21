"""Live order book panel with active quotes."""

from __future__ import annotations

from math import isfinite

from textual.app import ComposeResult
from textual.widgets import Static


class OrderBookPanel(Static):

    DEFAULT_CSS = """
    OrderBookPanel {
        height: 100%;
        border: round #83a598 30%;
        background: #282828;
        padding: 0 1;
    }
    OrderBookPanel .panel-title {
        text-style: bold;
        height: 1;
        color: #83a598;
        background: #83a598 12%;
        padding: 0 1;
    }
    OrderBookPanel .panel-body {
        height: 1fr;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("[bold #83a598]ORDERBOOK[/]  [#504945]\u2502[/]  [#928374]DEPTH VIEW[/]", classes="panel-title")
        yield Static("", classes="panel-body", id="ob-body")

    def update_book(self, view: dict) -> None:
        if not view or not view.get("market_id"):
            self.query_one("#ob-body", Static).update("[#928374]waiting for books...[/]")
            return

        quotes = view.get("quotes", [])
        qset = {(q["outcome"], q["side"], round(q["price"], 3)) for q in quotes}
        rows_per_side = 5

        def _bar(size: float, max_size: float, side: str) -> str:
            if not isfinite(size) or size <= 0 or max_size <= 0:
                return "[#3c3836]\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591[/]"
            width = max(1, min(12, int(round((size / max_size) * 12))))
            color = "#fb4934" if side == "ask" else "#b8bb26"
            blocks = "\u2588" * max(0, width - 1) + "\u2593" if width > 0 else ""
            rest = "\u2591" * (12 - width)
            return f"[{color}]{blocks}[/][#3c3836]{rest}[/]"

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
                tag = "[bold #fabd2f]  \u25c6Q[/]" if (outcome, quote_side, round(p, 3)) in qset else ""
                side_label = "[#fb4934]A[/]" if side == "ask" else "[#b8bb26]B[/]"
                bps_txt = "--.-"
                if mid and mid > 0:
                    bps = ((p - mid) / mid) * 10000
                    bps_txt = f"{bps:+6.1f}"
                px_fmt = f"[bold bright_white]{p:>5.3f}[/]" if idx == 0 else f"[bold]{p:>5.3f}[/]"
                out.append(
                    f" {side_label} {px_fmt}  "
                    f"[bright_white]{s:>7.1f}[/]  [#928374]{cum:>7.1f}[/]  "
                    f"[#928374]{bps_txt}bp[/]  {_bar(float(s), max_sz, side)}{tag}"
                )
            for _ in range(rows_per_side - len(shown)):
                out.append(" [#928374]\u00b7  ---.---     ---.-    ---.-   --.-bp  \u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591[/]")
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
            pressure = f"[#b8bb26]{'\u2588' * bar_fill}[/][#fb4934]{'\u2588' * (16 - bar_fill)}[/]"
            header = [f"[bold #83a598]{outcome}[/]  [#928374]px      size      cum      dmid      depth         quote[/]"]
            if bb is None or ba is None:
                header.append("[#928374] bb ---.---         ba ---.---[/]")
            else:
                header.append(f"[#b8bb26] bb {bb:.3f}[/] [#928374]        [/][#fb4934]ba {ba:.3f}[/]")
            if mid is None or spr is None:
                header.append("[#928374] mid ---.---   spr ---.---   top5 b/a --.-/--.-[/]")
            else:
                header.append(
                    f"[bright_white] mid {mid:.3f}[/]   [bright_white]spr {spr:.3f}[/]   "
                    f"[#928374]top5 b/a {bid_top:.1f}/{ask_top:.1f} ({buy_pct:>4.1f}% b)[/] {pressure}"
                )
            return header, mid

        def fmt_rows(outcome: str, bids: list, asks: list) -> list[str]:
            bid_rows = [(float(p), float(s)) for p, s in bids]
            ask_rows = [(float(p), float(s)) for p, s in asks]
            header, mid = _fmt_header(outcome, bid_rows, ask_rows)
            lines = header
            lines.append("[#504945] \u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504 asks \u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504[/]")
            lines += _fmt_side("ask", ask_rows, outcome, mid)
            lines.append("[#504945] \u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504 bids \u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504\u2504[/]")
            lines += _fmt_side("bid", bid_rows, outcome, mid)
            return lines

        market = view.get("market_id", "")
        mode = view.get("rotate_mode", "MANUAL")
        pos = int(view.get("book_pos", 0))
        total = int(view.get("book_total", 0))
        ready_total = int(view.get("ready_total", 0))
        nav_line = f"[#928374]book {pos}/{total} \u2502 ready {ready_total} \u2502 mode {mode} \u2502 [ / ] cycle \u2502 o toggle[/]"
        if view.get("is_live", True):
            if mode == "AUTO":
                status_line = f"[#b8bb26]LIVE[/] [#928374]\u2502 rotate {view.get('rotate_in_s', 0.0):.1f}s[/]"
            else:
                status_line = "[#b8bb26]LIVE[/] [#928374]\u2502 manual book select[/]"
        else:
            status_line = (
                f"[#fabd2f]STALE[/] [#928374]{view.get('stale_age_s', 0.0):.1f}s old \u2502 searching refresh...[/]"
            )
        lines: list[str] = [
            f"[bold]{view.get('question', '')[:52]}[/]",
            f"{status_line}  [#928374]\u2502 mkt {market[:14]}[/]",
            nav_line,
            "",
        ]
        lines += fmt_rows("YES", view.get("yes_bids", []), view.get("yes_asks", []))
        lines.append("")
        lines += fmt_rows("NO", view.get("no_bids", []), view.get("no_asks", []))

        self.query_one("#ob-body", Static).update("\n".join(lines))
