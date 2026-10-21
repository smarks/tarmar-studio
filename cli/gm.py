"""Tarmar AI Game Master -- Interactive CLI for local LLM-powered tabletop RPG sessions."""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.prompt import Prompt
    from rich.text import Text
except ImportError:
    print("Missing dependency: rich")
    print("Install with: uv sync --group cli")
    sys.exit(1)

import httpx

from common.services import ollama_client
from common.services.sse import ThinkingFilter
from gm.character_schema import (
    EXTRACTION_TEMPERATURE,
    character_json_schema,
    extraction_messages,
    extraction_to_import_payload,
    parse_extraction_reply,
)
from gm.engine import (
    DEFAULT_RULES_DIR,
    load_rules_from_dir,
    normalise_mode,
    roll_dice,
    system_prompt_for,
)
from gm.messages import (
    build_roll_message,
    build_rules_lookup_message,
    build_state_request_message,
)
from gm.model_config import GMModelConfig, GMRetrievalConfig
from gm.retrieval import (
    ChatMessage,
    canon_entry_for_turn,
    is_canon_message,
    model_messages,
)

from .ui import Spinner

# Model, context window and temperature come from GM_MODEL / GM_NUM_CTX /
# GM_TEMPERATURE -- the same variables and defaults the web GM reads
# (gm/model_config.py is the single source of truth). There is no custom
# ``tarmar-gm`` model any more; the full GM prompt is ``system_prompt_for``'s below.
GM_CONFIG = GMModelConfig.from_environ()
MODEL = GM_CONFIG.model
# Same canonical rules tree the web GM defaults to (settings.RULES_DIR) --
# see gm/engine.py for the single source of truth on this path and loader.
RULES_DIR = DEFAULT_RULES_DIR
# The CLI has no login, so it cannot read a role off an account. It plays as a
# player unless this variable says otherwise (issue #352): TARMAR_GM_DM=true
# runs the offline session with the Game Master's prompt and the Game Master's
# extraction bounds. Read once at import, like the model config, and spelled
# the way ``settings.DEBUG`` reads its own flag.
GAME_MASTER_ENV = "TARMAR_GM_DM"
RUNNING_AS_GAME_MASTER = os.environ.get(GAME_MASTER_ENV, "False").lower() == "true"
# What the person at the terminal wants the GM to do, separately from what
# they are allowed to do (issue #372): TARMAR_GM_MODE=play|aide. The web app
# stores this on the session and offers a control for it; the CLI has no
# sessions and no page, so it is an environment variable read once at import
# like everything else here. Unset, it takes the mode the same role opens a
# web session in -- so TARMAR_GM_DM=true alone still runs the aide, exactly as
# it did before, and TARMAR_GM_DM=true TARMAR_GM_MODE=play is the offline
# equivalent of a Game Master choosing to play a game.
GM_MODE_ENV = "TARMAR_GM_MODE"
RUNNING_MODE = normalise_mode(
    os.environ.get(GM_MODE_ENV), is_game_master=RUNNING_AS_GAME_MASTER
)
SAVES_DIR = Path(__file__).resolve().parent / "saves"

# Canon retrieval (issue #325) -- the same knobs as the web GM's GM_* settings,
# read from the environment because the CLI runs without Django. The variable
# names, defaults and parsing are gm/model_config.py's, so the CLI and
# settings.py cannot drift apart (issue #337). Build the index with:
# uv run python manage.py build_gm_index
OLLAMA_BASE_URL = ollama_client.base_url_from_environ()
GM_RETRIEVAL_CONFIG = GMRetrievalConfig.from_environ()
INDEX_PATH = GM_RETRIEVAL_CONFIG.index_path
RETRIEVAL_TOP_K = GM_RETRIEVAL_CONFIG.top_k
RETRIEVAL_MAX_TOKENS = GM_RETRIEVAL_CONFIG.max_excerpt_tokens
RETRIEVAL_ENABLED = GM_RETRIEVAL_CONFIG.enabled

console = Console()


def load_rules() -> str:
    """Load all markdown files from the rules directory into a single string."""
    rules_text = load_rules_from_dir(RULES_DIR)
    if rules_text is None:
        if not RULES_DIR.exists():
            console.print(
                f"[red]Rules directory not found: {RULES_DIR}[/red]\n"
                "Copy your Tarmar markdown files there before starting."
            )
        else:
            console.print(
                f"[red]No markdown files found in {RULES_DIR}[/red]\n"
                "Copy your Tarmar markdown files there before starting."
            )
        sys.exit(1)

    rules_files = sorted(RULES_DIR.rglob("*.md"))
    console.print(
        f"[dim]Loaded {len(rules_files)} rules files "
        f"({len(rules_text):,} characters)[/dim]"
    )
    return rules_text


def check_ollama() -> None:
    """Verify Ollama is running and the model exists."""
    try:
        model_names = ollama_client.list_models(base_url=OLLAMA_BASE_URL)
    except httpx.HTTPError as error:
        console.print(
            f"[red]Cannot connect to Ollama: {error}[/red]\n"
            "Start it with: brew services start ollama"
        )
        sys.exit(1)
    if not any(MODEL in name for name in model_names):
        console.print(
            f"[red]Model '{MODEL}' not found.[/red]\nPull it with: ollama pull {MODEL}"
        )
        sys.exit(1)


def visible_message_count(history: list[ChatMessage]) -> int:
    """Turns a reader would see: canon excerpt blocks are for the model only."""
    return sum(1 for message in history if not is_canon_message(message))


def save_session(name: str, history: list[ChatMessage], rules_text: str) -> Path:
    """Save conversation history to a JSON file."""
    SAVES_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^\w\-]", "_", name)
    save_path = SAVES_DIR / f"{safe_name}.json"

    data = {
        "name": name,
        # An aware instant with its UTC offset (issue #144): the file is read
        # back on whatever machine and in whatever zone, and a naive clock
        # reading names no moment there. Not common.instants.labelled_instant,
        # which needs Django's settings that this offline CLI runs without.
        "saved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "model": MODEL,
        "history": history,
    }
    save_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    console.print(f"[green]Session saved to {save_path}[/green]")
    return save_path


def load_session(name: str) -> list[ChatMessage] | None:
    """Load conversation history from a JSON file."""
    safe_name = re.sub(r"[^\w\-]", "_", name)
    save_path = SAVES_DIR / f"{safe_name}.json"

    if not save_path.exists():
        console.print(f"[red]Save file not found: {save_path}[/red]")
        available = list(SAVES_DIR.glob("*.json"))
        if available:
            console.print("[yellow]Available saves:[/yellow]")
            for f in sorted(available):
                data = json.loads(f.read_text(encoding="utf-8"))
                console.print(f"  {f.stem} -- saved {data.get('saved_at', 'unknown')}")
        return None

    data = json.loads(save_path.read_text(encoding="utf-8"))
    history = data["history"]
    console.print(
        f"[green]Loaded session '{data['name']}' "
        f"({visible_message_count(history)} messages, saved {data['saved_at']})[/green]"
    )
    return history


def list_saves() -> None:
    """List all available save files."""
    if not SAVES_DIR.exists():
        console.print("[yellow]No saves directory yet.[/yellow]")
        return
    saves = sorted(SAVES_DIR.glob("*.json"))
    if not saves:
        console.print("[yellow]No saved sessions found.[/yellow]")
        return
    console.print("[bold]Saved Sessions:[/bold]")
    for f in saves:
        data = json.loads(f.read_text(encoding="utf-8"))
        msg_count = visible_message_count(data.get("history", []))
        saved_at = data.get("saved_at", "unknown")
        console.print(
            f"  [cyan]{f.stem}[/cyan] -- {msg_count} messages, saved {saved_at}"
        )


def stream_response(
    history: list[ChatMessage],
    *,
    query: str | None = None,
) -> str:
    """Stream a response from Ollama and return the full text.

    When ``query`` is given (an ordinary player turn), the retrieved canon
    excerpts are filed into ``history`` just before the player's message and
    stay there -- /save writes them, /load restores them -- so the prompt
    prefix the model sees only ever grows and Ollama's cache covers every
    earlier turn (issue #331). The model receives ``history`` reduced to
    role and content, with the oldest excerpt blocks left out if the whole
    conversation would crowd out the reply.
    """
    if query is not None and RETRIEVAL_ENABLED:
        canon_entry = canon_entry_for_turn(
            history,
            query,
            index_path=INDEX_PATH,
            base_url=OLLAMA_BASE_URL,
            top_k=RETRIEVAL_TOP_K,
            max_tokens=RETRIEVAL_MAX_TOKENS,
            num_ctx=GM_CONFIG.num_ctx,
        )
        if canon_entry is not None:
            history.insert(len(history) - 1, canon_entry)
    messages = model_messages(history, num_ctx=GM_CONFIG.num_ctx)
    return _stream_to_terminal(messages)


def _stream_to_terminal(messages: list[dict[str, str]]) -> str:
    """Stream one reply to the terminal and return what was shown.

    The offline GM keeps no character records, so it runs no plan: it answers
    as a plain conversation, and the player rolls with ``/roll`` (issue #590).

    Args:
        messages: The plain messages to send to Ollama.

    Returns:
        The reply as printed, or ``""`` when the call to Ollama failed (which
        is reported to the terminal, not swallowed).
    """
    thinking_filter = ThinkingFilter()
    shown = ""
    first_visible = False
    spinner = Spinner("Thinking")
    spinner.start()
    try:
        tokens = ollama_client.stream_chat(
            MODEL,
            messages,
            options=GM_CONFIG.options(),
            # think=False switches qwen3's reasoning off at the API (Ollama
            # 0.9+); ThinkingFilter below stays as the second line of defence.
            think=False,
            base_url=OLLAMA_BASE_URL,
        )
        for token in tokens:
            visible = thinking_filter.feed(token)
            if visible is None:
                continue
            if not first_visible:
                spinner.stop()
                first_visible = True
                print()
            shown += visible
            sys.stdout.write(visible)
            sys.stdout.flush()
        if not first_visible:
            spinner.stop()
        print()
    except Exception as e:
        spinner.stop()
        console.print(f"\n[red]Error from Ollama: {e}[/red]")
        return ""
    return shown


def extract_character_payload(history: list[ChatMessage]) -> dict:
    """Ask the model for the session's character as an importable document.

    Literally the same call the web GM's ``/character`` makes (issue #344):
    ``gm.character_schema`` owns the schema and the prompt, and
    ``common.services.ollama_client.chat_structured`` owns the call and its
    fallback for a runner that refuses ``format``. The CLI has no database,
    so the answer is reshaped into the ``tarmar-character`` file the site's
    importer validates and saves.

    Raises:
        json.JSONDecodeError: When the reply is not JSON.
    """
    session = model_messages(history, num_ctx=GM_CONFIG.num_ctx)

    def build_messages(*, schema_in_prompt: bool = False) -> list[dict[str, str]]:
        return extraction_messages(
            session,
            schema_in_prompt=schema_in_prompt,
            for_game_master=RUNNING_AS_GAME_MASTER,
        )

    reply = ollama_client.chat_structured(
        MODEL,
        build_messages,
        character_json_schema(for_game_master=RUNNING_AS_GAME_MASTER),
        options={**GM_CONFIG.options(), "temperature": EXTRACTION_TEMPERATURE},
        base_url=OLLAMA_BASE_URL,
    )
    return extraction_to_import_payload(parse_extraction_reply(reply))


def save_character_file(history: list[ChatMessage], name: str) -> Path | None:
    """Write the session's character as a file the site's importer accepts.

    Returns the path written, or ``None`` when the model's answer could not
    be read as JSON (reported on the console; nothing is written).
    """
    spinner = Spinner("Extracting character")
    spinner.start()
    try:
        payload = extract_character_payload(history)
    except json.JSONDecodeError as error:
        spinner.stop()
        console.print(f"[red]The GM's answer was not valid JSON: {error.msg}[/red]")
        return None
    finally:
        spinner.stop()
    SAVES_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(
        r"[^\w\-]", "_", name or str(payload["data"].get("name", "character"))
    )
    save_path = SAVES_DIR / f"character_{safe_name}.json"
    save_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    console.print(
        f"[green]Character written to {save_path}[/green] -- import it from "
        "the site's character list. A player's import drops the rolled, "
        "earned and ruled values in this file; a Game Master's keeps them."
    )
    return save_path


def show_help() -> None:
    """Display available commands."""
    help_text = """
## Commands

| Command | Description |
|---------|-------------|
| `/save <name>` | Save current session |
| `/load <name>` | Load a saved session |
| `/saves` | List all saved sessions |
| `/quit` | Save prompt then exit |
| `/state` | Ask the GM to summarize current game state |
| `/reset` | Start a fresh session |
| `/roll NdN` | Roll dice (e.g., `/roll 3d6`, `/roll 2d6+3`) |
| `/character [name]` | Write the character the GM has been creating as an importable JSON file |
| `/rules <question>` | Precise rules lookup (slower -- injects full rules text) |
| `/help` | Show this help message |
"""
    console.print(Markdown(help_text))


def build_system_message(rules_text: str) -> dict[str, str]:
    """Create the system message for the session.

    The prompt (gm/engine.py) already carries the full rules digest and world
    canon, so the raw ``rules_text`` is not sent every turn; the ``/rules``
    command injects it for one precise lookup when the player wants maximum
    accuracy at the cost of speed. Which of the four fixed prompts is used
    follows :data:`RUNNING_AS_GAME_MASTER` for authority and
    :data:`RUNNING_MODE` for manner (issue #372).
    """
    return {
        "role": "system",
        "content": system_prompt_for(RUNNING_AS_GAME_MASTER, RUNNING_MODE),
    }


def main() -> None:
    """Run the interactive GM session."""
    console.print(
        Panel(
            Text.from_markup(
                "[bold]Tarmar AI Game Master[/bold]\n"
                "[dim]A local LLM-powered tabletop RPG experience[/dim]\n"
                "[dim]Type /help for commands[/dim]"
            ),
            border_style="blue",
        )
    )

    # Check Ollama connection and model
    check_ollama()

    # Load rules (available for /rules command)
    rules_text = load_rules()

    # Initialize conversation -- the full GM prompt is the system message;
    # the raw rules text is kept out of every turn for fast responses
    system_msg = build_system_message(rules_text)
    history: list[ChatMessage] = [system_msg]

    console.print(
        "\n[bold green]Ready.[/bold green] "
        "Describe your character concept or say what you'd like to do.\n"
    )

    while True:
        try:
            user_input = Prompt.ask("[bold cyan]You[/bold cyan]")
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Exiting...[/dim]")
            break

        user_input = user_input.strip()
        if not user_input:
            continue

        # --- Slash commands ---

        if user_input.startswith("/"):
            parts = user_input.split(maxsplit=1)
            cmd = parts[0].lower()
            arg = parts[1].strip() if len(parts) > 1 else ""

            if cmd == "/quit":
                if len(history) > 1:
                    name = arg or f"autosave_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                    save_session(name, history, rules_text)
                console.print("[dim]Farewell, adventurer.[/dim]")
                break

            elif cmd == "/save":
                if not arg:
                    console.print("[red]Usage: /save <name>[/red]")
                    continue
                save_session(arg, history, rules_text)
                continue

            elif cmd == "/load":
                if not arg:
                    console.print("[red]Usage: /load <name>[/red]")
                    list_saves()
                    continue
                loaded = load_session(arg)
                if loaded is not None:
                    history = [system_msg] + [
                        msg for msg in loaded if msg["role"] != "system"
                    ]
                    console.print("[green]Session restored. Continue playing.[/green]")
                continue

            elif cmd == "/saves":
                list_saves()
                continue

            elif cmd == "/state":
                history.append(build_state_request_message())
                console.print(
                    Panel("[bold]Game State Summary[/bold]", border_style="yellow")
                )
                response = stream_response(history)
                if response:
                    history.append({"role": "assistant", "content": response})
                continue

            elif cmd == "/reset":
                history = [system_msg]
                console.print("[yellow]Session reset. Start fresh.[/yellow]")
                continue

            elif cmd == "/rules":
                # Inject full rules text for one precise lookup
                query = arg or "Look up the relevant rules for the current situation."
                history.append(build_rules_lookup_message(query, rules_text))
                console.print(Panel("[bold]Rules Lookup[/bold]", border_style="cyan"))
                response = stream_response(history)
                if response:
                    history.append({"role": "assistant", "content": response})
                continue

            elif cmd == "/roll":
                if not arg:
                    console.print("[red]Usage: /roll NdN (e.g., /roll 3d6)[/red]")
                    continue
                try:
                    rolls, total = roll_dice(arg)
                    roll_str = ", ".join(str(r) for r in rolls)
                    console.print(
                        Panel(
                            f"[bold]{arg}[/bold]: [{roll_str}] = [bold yellow]{total}[/bold yellow]",
                            title="Dice Roll",
                            border_style="green",
                        )
                    )
                    # Feed the roll to the GM for adjudication
                    history.append(build_roll_message(arg, rolls, total))
                    response = stream_response(history)
                    if response:
                        history.append({"role": "assistant", "content": response})
                except ValueError as e:
                    console.print(f"[red]{e}[/red]")
                continue

            elif cmd == "/character":
                save_character_file(history, arg)
                continue

            elif cmd == "/help":
                show_help()
                continue

            else:
                console.print(
                    f"[red]Unknown command: {cmd}[/red]. Type /help for commands."
                )
                continue

        # --- Normal player input ---

        history.append({"role": "user", "content": user_input})

        console.print(Panel("[bold]GM[/bold]", border_style="magenta"))
        response = stream_response(history, query=user_input)
        if response:
            history.append({"role": "assistant", "content": response})


if __name__ == "__main__":
    main()
