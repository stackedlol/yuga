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
    }
    CommandBar Input {
        height: 1;
        border: none;
        padding: 0;
    }
    CommandBar .cmd-out {
        height: 1;
    }
    CommandBar .cmd-hint {
        height: 1;
    }
    """

    class CommandSubmitted(Message):
        def __init__(self, command: str) -> None:
            super().__init__()
            self.command = command

    def compose(self) -> ComposeResult:
        yield Static(
            "[dim]pause \u2502 resume \u2502 cancel-all \u2502 "
            "reload \u2502 status \u2502 reset-cb \u2502 quit[/]",
            classes="cmd-hint",
        )
        yield Input(placeholder="\u276f", id="cmd-input")
        yield Static("", classes="cmd-out", id="cmd-out")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        cmd = event.value.strip().lower()
        if cmd:
            self.post_message(self.CommandSubmitted(cmd))
        event.input.value = ""

    def set_output(self, text: str) -> None:
        self.query_one("#cmd-out", Static).update(text)
