#!/usr/bin/env python3
"""Yuga - Polymarket Arbitrage Bot

Usage:
    python main.py              # Start with TUI dashboard
    python main.py --headless   # Start without TUI (logging only)
    python main.py --dry-run    # Start in paused mode for observation
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv


def setup_logging(level: str = "INFO", log_file: str = "yuga.log") -> None:
    fmt = "%(asctime)s %(name)s %(levelname)s %(message)s"
    handlers: list[logging.Handler] = [logging.FileHandler(log_file)]

    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
                        format=fmt, handlers=handlers)

    # Quiet noisy libraries
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)


def main() -> None:
    parser = argparse.ArgumentParser(description="Yuga - Polymarket Arbitrage Bot")
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
    parser.add_argument("--headless", action="store_true", help="Run without TUI")
    parser.add_argument("--dry-run", action="store_true", help="Start paused (observe only)")
    args = parser.parse_args()

    load_dotenv()

    from yuga.config import load_config
    config = load_config(args.config)

    setup_logging(config.logging.level, config.logging.file)

    from yuga.engine import Engine
    engine = Engine(config)

    if args.dry_run:
        engine.executor.paused = True

    if args.headless:
        _run_headless(engine)
    else:
        _run_tui(engine)


def _run_tui(engine) -> None:
    from yuga.tui.app import YugaApp
    app = YugaApp(engine)
    app.run()


def _run_headless(engine) -> None:
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(console_handler)

    async def run() -> None:
        await engine.start()
        logging.info("Yuga engine running in headless mode. Ctrl+C to stop.")
        try:
            while True:
                await asyncio.sleep(10)
                state = engine.get_state()
                stats = state["exec_stats"]
                arb = state["arb_stats"]
                logging.info(
                    "Markets: %d | Signals: %d | Orders: %d | Fills: %d | PnL: $%.4f",
                    arb["markets_tracked"], arb["active_signals"],
                    stats["total_orders"], stats["total_fills"],
                    stats["cumulative_pnl"],
                )
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await engine.stop()

    asyncio.run(run())


if __name__ == "__main__":
    main()
