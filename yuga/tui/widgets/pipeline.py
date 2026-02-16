"""Full-width execution pipeline visualization."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Static


_STAGES = [
    ("SCANNING",   "\u25ce", "scan"),
    ("SNAPSHOT",   "\u2261", "book"),
    ("CANDIDATE",  "\u26a1", "arb"),
    ("PLACING",    "\u21e8", "place"),
    ("MONITORING", "\u25d0", "fill"),
    ("RESOLVING",  "\u2714", "done"),
]

_STAGE_NAMES = [s[0] for s in _STAGES]


def _build_pipeline(active: str, paused: bool) -> str:
    parts: list[str] = []
    active_idx = _STAGE_NAMES.index(active) if active in _STAGE_NAMES else -1

    for i, (name, icon, label) in enumerate(_STAGES):
        if paused:
            parts.append(f"[dim]{icon} {label}[/]")
        elif i == active_idx:
            parts.append(f"[bold reverse] {icon} {label} [/]")
        elif i < active_idx:
            parts.append(f"{icon} {label}")
        else:
            parts.append(f"[dim]{icon} {label}[/]")

    result: list[str] = []
    for i, part in enumerate(parts):
        result.append(part)
        if i < len(parts) - 1:
            if not paused and i < active_idx:
                result.append("\u2500\u2500\u25b8")
            elif not paused and i == active_idx:
                result.append("[yellow]\u2500\u2500\u25b8[/]")
            else:
                result.append("[dim]\u2500\u2500\u25b8[/]")

    return "".join(result)


class PipelinePanel(Static):

    DEFAULT_CSS = """
    PipelinePanel {
        height: 5;
        width: 100%;
        border: tall $surface-lighten-1;
        padding: 0 1;
    }
    PipelinePanel .panel-title {
        text-style: bold;
        height: 1;
    }
    PipelinePanel .pipe-vis {
        height: 1;
    }
    PipelinePanel .pipe-stats {
        height: 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("\u25c8 [bold]PIPELINE[/]", classes="panel-title", id="pipe-title")
        yield Static("", classes="pipe-vis", id="pipe-vis")
        yield Static("", classes="pipe-stats", id="pipe-stats")

    def update_pipeline(self, stage: str, arb_stats: dict, exec_stats: dict,
                        recent_orders: list[dict] | None = None) -> None:
        paused = exec_stats.get("paused", False)

        if paused:
            indicator = "[red]\u23f8 PAUSED[/]"
        else:
            indicator = f"[green]\u25b6[/] {stage.lower()}"

        self.query_one("#pipe-title", Static).update(
            f"\u25c8 [bold]PIPELINE[/]  {indicator}"
        )

        self.query_one("#pipe-vis", Static).update(_build_pipeline(stage, paused))

        signals = arb_stats.get("active_signals", 0)
        scans = arb_stats.get("total_scans", 0)
        total_sig = arb_stats.get("total_signals", 0)
        missed = arb_stats.get("missed_opportunities", 0)
        cycles = exec_stats.get("active_cycles", 0)
        markets_ready = arb_stats.get("markets_ready", 0)
        markets_total = arb_stats.get("markets_tracked", 0)

        stats = (
            f"\u26a1 [yellow]{signals}[/] [dim]sig[/]   "
            f"\u25ce [cyan]{cycles}[/] [dim]cyc[/]   "
            f"\u25c8 {scans} [dim]scans[/]   "
            f"\u2714 [green]{total_sig}[/] [dim]found[/]   "
            f"\u2718 [red]{missed}[/] [dim]miss[/]   "
            f"\u25c9 {markets_ready}[dim]/{markets_total} mkts[/]"
        )

        self.query_one("#pipe-stats", Static).update(stats)
