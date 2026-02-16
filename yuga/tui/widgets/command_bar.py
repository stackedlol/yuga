"""Command input bar."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Static, Input
from textual.message import Message


class CommandBar(Static):

    DEFAULT_CSS = """
    CommandBar {
        height: 3;
        padding: 0 1;
        layout: vertical;
        background: #0f172a;
        border-top: solid #1e293b;
    }
    CommandBar Input {
        height: 1;
        border: none;
        padding: 0;
        background: #0f172a;
        color: #e2e8f0;
    }
    CommandBar .cmd-out {
        height: 1;
    }
    CommandBar .cmd-hint {
        height: 1;
        color: #64748b;
    }
    """

    class CommandSubmitted(Message):
        def __init__(self, command: str) -> None:
            super().__init__()
            self.command = command

    def compose(self) -> ComposeResult:
        yield Static(
            "[#06b6d4]pause[/][#334155] \u2502 [/][#06b6d4]resume[/][#334155] \u2502 [/]"
            "[#06b6d4]cancel-all[/][#334155] \u2502 [/][#06b6d4]reload[/][#334155] \u2502 [/]"
            "[#06b6d4]status[/][#334155] \u2502 [/][#06b6d4]reset-cb[/][#334155] \u2502 [/]"
            "[#06b6d4]quit[/]",
            classes="cmd-hint",
        )
        yield Input(placeholder="\u276f command", id="cmd-input")
        yield Static("", classes="cmd-out", id="cmd-out")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        cmd = event.value.strip().lower()
        if cmd:
            self.post_message(self.CommandSubmitted(cmd))
        event.input.value = ""

    def set_output(self, text: str) -> None:
        self.query_one("#cmd-out", Static).update(text)
