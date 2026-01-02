"""Interactive CLI utilities using InquirerPy for modern UX."""

import sys
from contextlib import contextmanager
from typing import Callable, Generator

from InquirerPy import inquirer
from InquirerPy.base.control import Choice
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.theme import Theme

# Track whether we can use interactive prompts
_use_interactive = None


def _can_use_interactive() -> bool:
    """Check if we can use interactive InquirerPy prompts."""
    global _use_interactive
    
    if _use_interactive is not None:
        return _use_interactive
    
    # Check if stdin is a TTY
    if not sys.stdin.isatty():
        _use_interactive = False
        return False
    
    # On Windows, prompt_toolkit can spawn a new console window which is jarring.
    # Use Rich prompts on Windows for more reliable behavior.
    if sys.platform == "win32":
        _use_interactive = False
        return False
    
    # On Unix-like systems, InquirerPy works well
    _use_interactive = True
    return _use_interactive

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
    if _can_use_interactive():
        try:
            if password:
                result = inquirer.secret(
                    message=message,
                    default=default or "",
                    validate=validate,
                ).execute()
            else:
                result = inquirer.text(
                    message=message,
                    default=default or "",
                    validate=validate,
                ).execute()
            return result or ""
        except Exception:
            pass  # Fall back to Rich prompt
    
    # Fallback to Rich prompt
    if password:
        import getpass
        prompt_str = f"{message}"
        if default:
            prompt_str += f" ({default})"
        prompt_str += ": "
        console.print(f"[bold]{prompt_str}[/bold]", end="")
        result = getpass.getpass("")
        return result if result else (default or "")
    else:
        result = Prompt.ask(message, default=default or "", console=console)
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
    if _can_use_interactive():
        try:
            return inquirer.confirm(
                message=message,
                default=default,
            ).execute()
        except Exception:
            pass  # Fall back to Rich prompt
    
    # Fallback to Rich prompt
    return Confirm.ask(message, default=default, console=console)


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
    if _can_use_interactive():
        try:
            return inquirer.select(
                message=message,
                choices=choices,
                default=default,
            ).execute()
        except Exception:
            pass  # Fall back to numbered selection
    
    # Fallback to numbered selection with Rich
    # Extract string values from Choice objects if needed
    choice_values = []
    choice_names = []
    for c in choices:
        if isinstance(c, Choice):
            choice_values.append(c.value)
            choice_names.append(c.name if c.name else str(c.value))
        else:
            choice_values.append(c)
            choice_names.append(c)
    
    console.print(f"\n[bold]{message}[/bold]\n")
    for i, name in enumerate(choice_names, 1):
        if default and choice_values[i-1] == default:
            console.print(f"  [magenta]{i}.[/magenta] {name} [dim](default)[/dim]")
        else:
            console.print(f"  [magenta]{i}.[/magenta] {name}")
    console.print()
    
    while True:
        default_num = None
        if default and default in choice_values:
            default_num = str(choice_values.index(default) + 1)
        
        response = Prompt.ask("Enter number", default=default_num, console=console)
        try:
            index = int(response) - 1
            if 0 <= index < len(choice_values):
                return choice_values[index]
        except ValueError:
            pass
        console.print("[red]Invalid selection. Please enter a number.[/red]")


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
    if _can_use_interactive():
        try:
            return inquirer.checkbox(
                message=message,
                choices=choices,
                default=default,
            ).execute()
        except Exception:
            pass  # Fall back to comma-separated input
    
    # Fallback: show choices and ask for comma-separated numbers
    choice_values = []
    choice_names = []
    for c in choices:
        if isinstance(c, Choice):
            choice_values.append(c.value)
            choice_names.append(c.name if c.name else str(c.value))
        else:
            choice_values.append(c)
            choice_names.append(c)
    
    console.print(f"\n[bold]{message}[/bold]\n")
    for i, name in enumerate(choice_names, 1):
        selected = default and choice_values[i-1] in default
        marker = "[green]✓[/green]" if selected else " "
        console.print(f"  {marker} [magenta]{i}.[/magenta] {name}")
    console.print()
    
    response = Prompt.ask("Enter numbers (comma-separated)", console=console)
    selected = []
    for part in response.split(","):
        try:
            index = int(part.strip()) - 1
            if 0 <= index < len(choice_values):
                selected.append(choice_values[index])
        except ValueError:
            pass
    
    return selected if selected else (default or [])


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
    if _can_use_interactive():
        try:
            return inquirer.fuzzy(
                message=message,
                choices=choices,
                default=default,
            ).execute()
        except Exception:
            pass  # Fall back to regular choice
    
    # Fallback to regular prompt_choice
    return prompt_choice(message, choices, default)


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
