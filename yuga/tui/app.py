"""Main TUI application."""

from __future__ import annotations

import time

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Static
from textual.timer import Timer

from yuga.engine import Engine
from yuga.tui.widgets.market_scanner import MarketScanner
from yuga.tui.widgets.orderbook import OrderBookPanel
from yuga.tui.widgets.order_feed import OrderFeed
from yuga.tui.widgets.positions import PositionsPanel
from yuga.tui.widgets.pnl_chart import PnLChart
from yuga.tui.widgets.odds_chart import OddsChart
from yuga.tui.widgets.pipeline import PipelinePanel
from yuga.tui.widgets.metrics import MetricsBar
from yuga.tui.widgets.command_bar import CommandBar


CSS = """
#status-bar {
    height: 1;
    dock: top;
}

#main-row {
    height: 1fr;
}

#left-col {
    width: 5fr;
    min-width: 60;
}

#right-col {
    width: 2fr;
    min-width: 34;
}

#orderbook-box {
    height: 3fr;
}

#scanner-box {
    height: 2fr;
}

#orders-box {
    height: 2fr;
}

#risk-box {
    height: 2fr;
}

#odds-box {
    height: 2fr;
}

#pnl-box {
    height: 1fr;
}

#pipeline-box {
    height: 5;
    width: 100%;
}

#cmd-box {
    height: 3;
    dock: bottom;
}
"""


class YugaApp(App):
    TITLE = "yuga"
    CSS = CSS

    BINDINGS = [
        Binding("q", "quit", "quit", show=True, priority=True),
        Binding("p", "toggle_pause", "pause", show=True),
        Binding("c", "cancel_all", "cancel all", show=True),
        Binding("r", "reload_config", "reload", show=True),
        Binding("s", "show_status", "status", show=True),
        Binding("[", "prev_book", "prev book", show=True),
        Binding("]", "next_book", "next book", show=True),
        Binding("o", "toggle_book_rotation", "book auto", show=True),
    ]

    def __init__(self, engine: Engine, **kwargs) -> None:
        super().__init__(**kwargs)
        self.engine = engine
        self._refresh_timer: Timer | None = None

    def compose(self) -> ComposeResult:
        yield MetricsBar(id="status-bar")
        with Horizontal(id="main-row"):
            with Vertical(id="left-col"):
                yield OrderBookPanel(id="orderbook-box")
                yield MarketScanner(id="scanner-box")
                yield OrderFeed(id="orders-box")
            with Vertical(id="right-col"):
                yield PositionsPanel(id="risk-box")
                yield OddsChart(id="odds-box")
                yield PnLChart(id="pnl-box")
        yield PipelinePanel(id="pipeline-box")
        yield CommandBar(id="cmd-box")
        yield Footer()

    async def on_mount(self) -> None:
        try:
            await self.engine.start()
        except Exception as e:
            self._update_log(f"[bold red]engine start failed: {e}[/]")
        self._refresh_timer = self.set_interval(0.5, self._refresh_ui)

    async def on_unmount(self) -> None:
        if self._refresh_timer:
            self._refresh_timer.stop()
        try:
            await self.engine.stop()
        except Exception:
            pass

    async def _refresh_ui(self) -> None:
        try:
            state = self.engine.get_state()
        except Exception:
            return

        for widget_id, cls, method, args in [
            ("status-bar", MetricsBar, "update_metrics", (state,)),
            ("orderbook-box", OrderBookPanel, "update_book",
             (state.get("orderbook_view", {}),)),
            ("scanner-box", MarketScanner, "update_markets",
             (state.get("markets", []), state.get("active_quotes", {}))),
            ("orders-box", OrderFeed, "update_orders", (state.get("recent_orders", []),)),
            ("risk-box", PositionsPanel, "update_state",
             (state.get("exec_stats", {}), state.get("risk_status", {}),
              state.get("inventory", []))),
            ("pnl-box", PnLChart, "update_pnl",
             (state.get("pnl_history", []), state.get("exec_stats", {}))),
            ("odds-box", OddsChart, "update_odds",
             (state.get("odds_view", {}),)),
            ("pipeline-box", PipelinePanel, "update_pipeline",
             (state.get("pipeline_stage", "IDLE"),
              state.get("mm_stats", {}), state.get("exec_stats", {}),
              state.get("recent_orders", []))),
        ]:
            try:
                w = self.query_one(f"#{widget_id}", cls)
                getattr(w, method)(*args)
            except Exception:
                pass

    # -- Commands --

    async def on_command_bar_command_submitted(self, event: CommandBar.CommandSubmitted) -> None:
        cmd = event.command
        bar = self.query_one("#cmd-box", CommandBar)

        handlers = {
            "pause": self._cmd_pause,
            "resume": self._cmd_resume,
            "cancel-all": self._cmd_cancel,
            "cancel": self._cmd_cancel,
            "reload": self._cmd_reload,
            "reload-config": self._cmd_reload,
            "status": self._cmd_status,
            "next-book": self._cmd_next_book,
            "prev-book": self._cmd_prev_book,
            "ob-next": self._cmd_next_book,
            "ob-prev": self._cmd_prev_book,
            "book-auto": self._cmd_toggle_book_rotation,
            "book-auto-on": self._cmd_book_auto_on,
            "book-auto-off": self._cmd_book_auto_off,
            "reset-cb": self._cmd_reset_cb,
            "reset": self._cmd_reset_cb,
            "quit": lambda b: self.exit(),
            "exit": lambda b: self.exit(),
            "q": lambda b: self.exit(),
        }

        handler = handlers.get(cmd)
        if handler:
            await handler(bar) if cmd not in ("quit", "exit", "q") else handler(bar)
        else:
            bar.set_output(f"[bold red]\u2718 unknown: {cmd}[/]")

    async def _cmd_pause(self, bar: CommandBar) -> None:
        await self.engine.pause()
        bar.set_output("[yellow]\u23f8 paused[/]")

    async def _cmd_resume(self, bar: CommandBar) -> None:
        await self.engine.resume()
        bar.set_output("[green]\u25b6 resumed[/]")

    async def _cmd_cancel(self, bar: CommandBar) -> None:
        n = await self.engine.cancel_all()
        bar.set_output(f"\u2718 cancelled {n} orders")

    async def _cmd_reload(self, bar: CommandBar) -> None:
        await self.engine.reload_config()
        bar.set_output("[green]\u21bb config reloaded[/]")

    async def _cmd_status(self, bar: CommandBar) -> None:
        s = self.engine.get_state()["exec_stats"]
        a = self.engine.get_state()["mm_stats"]
        bar.set_output(
            f"orders {s['total_orders']}  "
            f"fills {s['total_fills']}  "
            f"pnl ${s['cumulative_pnl']:+.4f}  "
            f"markets {a['markets_tracked']}"
        )

    async def _cmd_next_book(self, bar: CommandBar) -> None:
        if self.engine.cycle_orderbook(1):
            bar.set_output("[green]orderbook -> next[/]")
        else:
            bar.set_output("[yellow]orderbook: no books yet[/]")

    async def _cmd_prev_book(self, bar: CommandBar) -> None:
        if self.engine.cycle_orderbook(-1):
            bar.set_output("[green]orderbook -> prev[/]")
        else:
            bar.set_output("[yellow]orderbook: no books yet[/]")

    async def _cmd_toggle_book_rotation(self, bar: CommandBar) -> None:
        enabled = self.engine.toggle_orderbook_auto_rotate()
        bar.set_output(
            "[green]orderbook auto: ON[/]" if enabled else "[yellow]orderbook auto: OFF[/]"
        )

    async def _cmd_book_auto_on(self, bar: CommandBar) -> None:
        self.engine.set_orderbook_auto_rotate(True)
        bar.set_output("[green]orderbook auto: ON[/]")

    async def _cmd_book_auto_off(self, bar: CommandBar) -> None:
        self.engine.set_orderbook_auto_rotate(False)
        bar.set_output("[yellow]orderbook auto: OFF[/]")

    async def _cmd_reset_cb(self, bar: CommandBar) -> None:
        self.engine.risk.reset_circuit_breaker()
        bar.set_output("[green]\u26a1 circuit breaker reset[/]")

    async def action_toggle_pause(self) -> None:
        bar = self.query_one("#cmd-box", CommandBar)
        if self.engine.executor.paused:
            await self._cmd_resume(bar)
        else:
            await self._cmd_pause(bar)

    async def action_cancel_all(self) -> None:
        await self._cmd_cancel(self.query_one("#cmd-box", CommandBar))

    async def action_reload_config(self) -> None:
        await self._cmd_reload(self.query_one("#cmd-box", CommandBar))

    async def action_show_status(self) -> None:
        await self._cmd_status(self.query_one("#cmd-box", CommandBar))

    async def action_next_book(self) -> None:
        await self._cmd_next_book(self.query_one("#cmd-box", CommandBar))

    async def action_prev_book(self) -> None:
        await self._cmd_prev_book(self.query_one("#cmd-box", CommandBar))

    async def action_toggle_book_rotation(self) -> None:
        await self._cmd_toggle_book_rotation(self.query_one("#cmd-box", CommandBar))

    def _update_log(self, text: str) -> None:
        try:
            bar = self.query_one("#cmd-box", CommandBar)
            bar.set_output(text)
        except Exception:
            pass
