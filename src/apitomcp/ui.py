"""Interactive CLI utilities using InquirerPy for modern UX."""

from contextlib import contextmanager
from typing import Callable, Generator

from InquirerPy import inquirer
from InquirerPy.base.control import Choice
from InquirerPy.utils import get_style
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.status import Status
from rich.table import Table
from rich.theme import Theme

# Prompt styling - like create-next-app
QMARK = "?"  # Question mark for unanswered questions
AMARK = "✔"  # Checkmark for answered questions

# InquirerPy style customization
PROMPT_STYLE = get_style({
    "questionmark": "#5f87ff",  # Blue question mark
    "answermark": "#5fd75f",    # Green checkmark
    "answer": "#5fd75f",        # Green answer text
    "input": "#00d9ff",         # Cyan input text (distinct from prompt)
    "question": "",             # Default question color
    "answered_question": "",    # Default answered question color
    "instruction": "#7f7f7f",   # Gray instructions
    "pointer": "#5f87ff",       # Blue pointer
    "checkbox": "#5f87ff",      # Blue checkbox
    "separator": "",
    "skipped": "#7f7f7f",
    "validator": "",
    "marker": "#5fd75f",        # Green marker
    "fuzzy_prompt": "#5f87ff",
    "fuzzy_info": "#7f7f7f",
    "fuzzy_border": "#5f87ff",
    "fuzzy_match": "#5fd75f",
}, style_override=False)

# Custom theme for Rich console output (tables, panels, etc.)
theme = Theme(
    {
        "info": "cyan",
        "success": "green",
        "warning": "yellow",
        "error": "red bold",
        "highlight": "magenta",
        "muted": "dim",
    }
)

# Force terminal mode for immediate output (no buffering)
console = Console(theme=theme, force_terminal=True)


def print_header(text: str) -> None:
    """Print a styled header."""
    console.print()
    console.print(Panel(text, style="bold cyan", border_style="cyan"))
    console.print()


def print_success(text: str) -> None:
    """Print a success message."""
    console.print(f"[success]{text}[/success]")


def print_error(text: str) -> None:
    """Print an error message."""
    console.print(f"[error]{text}[/error]")


def print_warning(text: str) -> None:
    """Print a warning message."""
    console.print(f"[warning]{text}[/warning]")


def print_info(text: str) -> None:
    """Print an info message."""
    console.print(f"[info]{text}[/info]")


def print_muted(text: str) -> None:
    """Print muted/secondary text."""
    console.print(f"[muted]{text}[/muted]")


def prompt_text(
    message: str,
    default: str | None = None,
    password: bool = False,
    validate: Callable | None = None,
) -> str:
    """
    Prompt for text input with InquirerPy styling.
    
    Args:
        message: The prompt message
        default: Default value if user presses Enter
        password: Whether to mask input (for secrets)
        validate: Optional validation function
    
    Returns:
        The user's input string
    """
    if password:
        result = inquirer.secret(
            message=message,
            default=default or "",
            validate=validate,
            qmark=QMARK,
            amark=AMARK,
            style=PROMPT_STYLE,
        ).execute()
    else:
        result = inquirer.text(
            message=message,
            default=default or "",
            validate=validate,
            qmark=QMARK,
            amark=AMARK,
            style=PROMPT_STYLE,
        ).execute()
    return result or ""


def prompt_confirm(message: str, default: bool = True) -> bool:
    """
    Prompt for yes/no confirmation with InquirerPy styling.
    
    Args:
        message: The confirmation message
        default: Default value (True = Yes)
    
    Returns:
        True if user confirms, False otherwise
    """
    return inquirer.confirm(
        message=message,
        default=default,
        qmark=QMARK,
        amark=AMARK,
        style=PROMPT_STYLE,
    ).execute()


def prompt_choice(
    message: str,
    choices: list[str] | list[Choice],
    default: str | None = None,
) -> str:
    """
    Prompt for single selection with arrow keys.
    
    Args:
        message: The prompt message
        choices: List of choices (strings or Choice objects)
        default: Default selection
    
    Returns:
        The selected choice string
    """
    return inquirer.select(
        message=message,
        choices=choices,
        default=default,
        qmark=QMARK,
        amark=AMARK,
        style=PROMPT_STYLE,
        pointer="❯",
    ).execute()


def prompt_select_multiple(
    message: str,
    choices: list[str] | list[Choice],
    default: list[str] | None = None,
) -> list[str]:
    """
    Prompt for multiple selection with checkboxes.
    
    Args:
        message: The prompt message
        choices: List of choices
        default: List of pre-selected values
    
    Returns:
        List of selected choice strings
    """
    return inquirer.checkbox(
        message=message,
        choices=choices,
        default=default,
        qmark=QMARK,
        amark=AMARK,
        style=PROMPT_STYLE,
        pointer="❯",
    ).execute()


def prompt_fuzzy(
    message: str,
    choices: list[str] | list[Choice],
    default: str | None = None,
) -> str:
    """
    Prompt for selection with fuzzy search filtering.
    
    Args:
        message: The prompt message
        choices: List of choices
        default: Default selection
    
    Returns:
        The selected choice string
    """
    return inquirer.fuzzy(
        message=message,
        choices=choices,
        default=default,
        qmark=QMARK,
        amark=AMARK,
        style=PROMPT_STYLE,
        pointer="❯",
    ).execute()


@contextmanager
def spinner(message: str) -> Generator[None, None, None]:
    """Show a spinner during a long operation."""
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        progress.add_task(description=message, total=None)
        yield


class LiveStatus:
    """A live-updating status display with animated spinner."""

    def __init__(self) -> None:
        self._status: Status | None = None

    def __enter__(self) -> "LiveStatus":
        self._status = console.status("", spinner="dots")
        self._status.__enter__()
        return self

    def __exit__(self, *args) -> None:
        if self._status:
            self._status.__exit__(*args)

    def update(self, message: str) -> None:
        """Update the status message."""
        if self._status:
            self._status.update(message)


def create_table(title: str, columns: list[str]) -> Table:
    """Create a styled table."""
    table = Table(title=title, show_header=True, header_style="bold cyan")
    for col in columns:
        table.add_column(col)
    return table


def print_table(table: Table) -> None:
    """Print a table to the console."""
    console.print()
    console.print(table)
    console.print()


def print_key_value(key: str, value: str) -> None:
    """Print a key-value pair."""
    console.print(f"  [bold]{key}:[/bold] {value}")


def print_divider() -> None:
    """Print a horizontal divider."""
    console.print()
    console.rule(style="muted")
    console.print()
