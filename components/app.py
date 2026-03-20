from typing import Optional
from textual.app import App, ComposeResult, on
from textual.containers import Vertical, Horizontal
from textual.widgets import Header, Footer, Input, Markdown, Static, Label
from textual.binding import Binding
from rich.console import Console

from agent.agent import Agent
from agent.types import (
    AgentConfig, AgentEvent, ToolStartEvent, ToolEndEvent, 
    AnswerChunkEvent, DoneEvent, LogEvent
)


class StatusPanel(Static):
    """Status panel showing current tool execution."""
    
    def update_status(self, message: str) -> None:
        self.update(f"[bold yellow]Status:[/bold yellow] {message}")


import os

class PolyCodeApp(App):
    """Full Textual TUI application for PolyTrade."""

    CSS = """
    Screen {
        background: $surface;
    }

    #main_container {
        height: 1fr;
        padding: 1;
    }

    #result_view {
        height: 1fr;
        border: solid $accent;
        padding: 1;
        overflow-y: scroll;
    }

    #status_bar {
        height: 3;
        padding: 1;
        background: $surface-lighten-1;
        color: $text;
        border-top: solid $primary;
    }

    #input_container {
        height: auto;
        dock: bottom;
        padding: 1;
    }

    Input {
        border: tall $primary;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
        Binding("ctrl+c", "quit", "Quit", show=False),
        Binding("ctrl+l", "clear_results", "Clear Results", show=True),
    ]

    def __init__(self, model: Optional[str] = None, provider: Optional[str] = None):
        super().__init__()
        self.model_name = model or os.getenv("MODEL", "grok-3")
        self.provider = provider or os.getenv("MODEL_PROVIDER", "xai")
        self.agent: Optional[Agent] = None
        self.chat_history: list[dict] = []
        self.full_answer = ""

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="main_container"):
            yield Static(f"[bold cyan]PolyTrade[/bold cyan] | Agentic Financial Research | [yellow]{self.model_name}[/yellow]", id="intro")
            yield Markdown("", id="result_view")
        yield StatusPanel("Ready", id="status_bar")
        with Horizontal(id="input_container"):
            yield Input(placeholder="Ask a financial research question...", id="query_input")
        yield Footer()

    async def on_mount(self) -> None:
        """Initialize the agent when the app starts."""
        config = AgentConfig(model=self.model_name, model_provider=self.provider)
        self.agent = Agent.create(config)
        self.query_one("#query_input").focus()

    @on(Input.Submitted)
    async def handle_query(self, event: Input.Submitted) -> None:
        """Process the user query."""
        query = event.value.strip()
        if not query:
            return

        # Reset UI for new query
        input_widget = self.query_one("#query_input", Input)
        status_panel = self.query_one("#status_bar", StatusPanel)
        markdown_view = self.query_one("#result_view", Markdown)
        
        input_widget.value = ""
        # input_widget.disabled = True  <- Remove this to allow user to type/quit while researching
        self.full_answer = ""
        self.research_log = f"# Research: {query}\n\n"
        markdown_view.update(self.research_log)
        
        status_panel.update_status(f"Starting research: '{query}'...")
        self.chat_history.append({"role": "user", "content": query})

        try:
            async for agent_event in self.agent.run(query, self.chat_history):
                if isinstance(agent_event, LogEvent):
                    if agent_event.level == "thought":
                        self.research_log += f"\n> [italic]Thought: {agent_event.message.strip()}[/italic]\n"
                    elif agent_event.level == "tool":
                        self.research_log += f"\n🔧 **Action**: {agent_event.message}\n"
                    else:
                        self.research_log += f"\nℹ️ {agent_event.message}\n"
                    markdown_view.update(self.research_log)
                
                elif isinstance(agent_event, ToolStartEvent):
                    status_panel.update_status(f"🔧 Using {agent_event.tool}...")
                elif isinstance(agent_event, ToolEndEvent):
                    status_panel.update_status(f"✓ {agent_event.tool} completed")
                elif isinstance(agent_event, AnswerChunkEvent):
                    if "## Final Answer" not in self.research_log:
                        self.research_log += "\n---\n## Final Answer\n\n"
                    self.full_answer += agent_event.chunk
                    markdown_view.update(self.research_log + self.full_answer)
                elif isinstance(agent_event, DoneEvent):
                    status_panel.update_status("Task Complete")
                    self.chat_history.append({"role": "assistant", "content": agent_event.answer})
                    markdown_view.update(self.research_log + "\n---\n## Final Answer\n\n" + agent_event.answer)

        except Exception as e:
            self.notify(f"Error: {str(e)}", severity="error")
            status_panel.update_status(f"Error: {str(e)}")
        finally:
            input_widget.disabled = False
            input_widget.focus()

    def action_clear_results(self) -> None:
        """Clear the results view."""
        self.query_one("#result_view", Markdown).update("")
        self.chat_history = []
        self.notify("History cleared")
