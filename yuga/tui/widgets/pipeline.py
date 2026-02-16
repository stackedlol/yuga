"""Full-width execution pipeline visualization."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Static


_STAGES = [
    ("SCANNING",   "\u25ce", "scan"),
    ("BOOK",       "\u2261", "book"),
    ("QUOTING",    "\u21e8", "quote"),
    ("MONITORING", "\u25d0", "fill"),
    ("RESOLVING",  "\u2714", "done"),
]

_STAGE_NAMES = [s[0] for s in _STAGES]


def _build_pipeline(active: str, paused: bool) -> str:
    parts: list[str] = []
    active_idx = _STAGE_NAMES.index(active) if active in _STAGE_NAMES else -1

    for i, (name, icon, label) in enumerate(_STAGES):
        if paused:
            parts.append(f"[#64748b]{icon} {label}[/]")
        elif i == active_idx:
            parts.append(f"[bold #06b6d4 on #06b6d4 15%] {icon} {label} [/]")
        elif i < active_idx:
            parts.append(f"[#22c55e]{icon} {label}[/]")
        else:
            parts.append(f"[#64748b]{icon} {label}[/]")

    result: list[str] = []
    for i, part in enumerate(parts):
        result.append(part)
        if i < len(parts) - 1:
            if not paused and i < active_idx:
                result.append("[#22c55e]\u2501\u2501\u25b8[/]")
            elif not paused and i == active_idx:
                result.append("[#f59e0b]\u2501\u2501\u25b8[/]")
            else:
                result.append("[#334155]\u2501\u2501\u25b8[/]")

    return "".join(result)


class PipelinePanel(Static):

    DEFAULT_CSS = """
    PipelinePanel {
        height: 5;
        width: 100%;
        border: round #06b6d4 30%;
        background: #0f172a;
        padding: 0 1;
    }
    PipelinePanel .panel-title {
        text-style: bold;
        height: 1;
        color: #06b6d4;
        background: #06b6d4 12%;
        padding: 0 1;
    }
    PipelinePanel .pipe-vis {
        height: 1;
    }
    PipelinePanel .pipe-stats {
        height: 1;
        color: #94a3b8;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("\u25c8 [bold #06b6d4]PIPELINE[/]", classes="panel-title", id="pipe-title")
        yield Static("", classes="pipe-vis", id="pipe-vis")
        yield Static("", classes="pipe-stats", id="pipe-stats")

    def update_pipeline(self, stage: str, mm_stats: dict, exec_stats: dict,
                        recent_orders: list[dict] | None = None) -> None:
        paused = exec_stats.get("paused", False)

        if paused:
            indicator = "[#f43f5e]\u23f8 PAUSED[/]"
        else:
            indicator = f"[#22c55e]\u25b6[/] {stage.lower()}"

        self.query_one("#pipe-title", Static).update(
            f"\u25c8 [bold #06b6d4]PIPELINE[/]  {indicator}"
        )

        self.query_one("#pipe-vis", Static).update(_build_pipeline(stage, paused))

        quotes = mm_stats.get("active_quotes", 0)
        scans = mm_stats.get("total_scans", 0)
        total_quotes = mm_stats.get("total_quotes", 0)
        cycles = exec_stats.get("active_quotes", 0)
        markets_ready = mm_stats.get("markets_ready", 0)
        markets_total = mm_stats.get("markets_tracked", 0)

        sep = "[#334155]\u2502[/]"
        stats = (
            f"\u26a1 [#f59e0b]{quotes}[/] [#64748b]q[/]  {sep}  "
            f"\u25ce [#06b6d4]{cycles}[/] [#64748b]cyc[/]  {sep}  "
            f"\u25c8 {scans} [#64748b]scans[/]  {sep}  "
            f"\u2714 [#22c55e]{total_quotes}[/] [#64748b]sent[/]  {sep}  "
            f"\u25c9 {markets_ready}[#64748b]/{markets_total} mkts[/]"
        )

        self.query_one("#pipe-stats", Static).update(stats)
