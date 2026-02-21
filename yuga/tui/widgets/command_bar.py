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
        background: #282828;
        border-top: solid #3c3836;
    }
    CommandBar Input {
        height: 1;
        border: none;
        padding: 0;
        background: #282828;
        color: #ebdbb2;
    }
    CommandBar .cmd-out {
        height: 1;
    }
    CommandBar .cmd-hint {
        height: 1;
        color: #928374;
    }
    """

    class CommandSubmitted(Message):
        def __init__(self, command: str) -> None:
            super().__init__()
            self.command = command

    def compose(self) -> ComposeResult:
        yield Static(
            "[#83a598]pause[/][#504945] \u2502 [/][#83a598]resume[/][#504945] \u2502 [/]"
            "[#83a598]cancel-all[/][#504945] \u2502 [/][#83a598]reload[/][#504945] \u2502 [/]"
            "[#83a598]status[/][#504945] \u2502 [/][#83a598]reset-cb[/][#504945] \u2502 [/]"
            "[#83a598]quit[/]",
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
