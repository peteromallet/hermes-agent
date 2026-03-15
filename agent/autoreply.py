"""Auto-reply engine — shared logic for CLI and gateway.

Handles config parsing, state management, prompt building, and LLM calls.
Consumers (CLI, gateway) only handle injection into their respective message loops.
"""

from typing import Any, Dict, List, Optional, Tuple

_DEFAULT_MAX_TURNS = 20


def parse_autoreply_args(args: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Parse /autoreply command arguments.

    Returns (status_message, config_or_none).
    - When enabling: returns (message, new_config_dict)
    - When disabling/status/error: returns (message, None)

    The caller is responsible for storing/clearing the config and
    formatting the message for their platform (markdown vs plain text).
    """
    # /autoreply off
    if args.lower() in ("off", "disable", "stop"):
        return "off", None

    # /autoreply max <N>
    if args.lower() == "max" or (args.lower().startswith("max ") and len(args) > 4):
        parts = args.split(None, 1)
        if len(parts) == 2:
            try:
                n = int(parts[1])
                if n < 1:
                    return "error:Max turns must be at least 1.", None
                return f"max:{n}", None
            except ValueError:
                return "error:Usage: /autoreply max <number>", None
        return "error:Usage: /autoreply max <number>", None

    # /autoreply (no args)
    if not args:
        return "status", None

    # Extract flags from args: --literal, --max N, --forever
    literal = False
    max_turns = _DEFAULT_MAX_TURNS
    remaining_parts = []

    parts = args.split()
    i = 0
    while i < len(parts):
        if parts[i] == "--literal":
            literal = True
        elif parts[i] == "--forever":
            max_turns = 0  # 0 = unlimited
        elif parts[i] == "--max" and i + 1 < len(parts):
            i += 1
            try:
                n = int(parts[i])
                if n < 1:
                    return "error:--max must be at least 1.", None
                max_turns = n
            except ValueError:
                return "error:--max requires a number.", None
        else:
            remaining_parts.append(parts[i])
        i += 1

    prompt = " ".join(remaining_parts).strip()

    if literal:
        if not prompt:
            return "error:Usage: /autoreply --literal <message>", None
        return "enabled", {
            "prompt": prompt,
            "model": None,
            "max_turns": max_turns,
            "turn_count": 0,
            "literal": True,
        }

    if not prompt:
        return "error:Usage: /autoreply <instructions>", None

    return "enabled", {
        "prompt": prompt,
        "model": None,
        "max_turns": max_turns,
        "turn_count": 0,
    }


def build_autoreply_messages(config: Dict[str, Any],
                             history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build the message list for the auto-reply LLM call.

    Filters history to recent user/assistant messages, prepends a system
    prompt with the user's instructions, and appends the generation request.
    """
    recent = [
        m for m in (history or [])
        if m.get("role") in ("user", "assistant") and m.get("content")
    ][-20:]

    system_prompt = (
        "You are generating a reply on behalf of the user in a conversation "
        "with an AI assistant.\n\n"
        "THE USER'S INSTRUCTIONS — follow these as your top priority:\n"
        f"{config['prompt']}\n\n"
        "Output ONLY the user's reply message. No labels, no meta-commentary, "
        "no 'User:' prefix."
    )

    messages = [{"role": "system", "content": system_prompt}]
    for m in recent:
        content = m["content"]
        # Flatten list-type content to plain text
        if isinstance(content, list):
            content = "\n".join(
                p.get("text", "") for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            )
        messages.append({"role": m["role"], "content": content})
    messages.append({
        "role": "user",
        "content": "Based on the conversation above and your instructions, "
                   "generate the next user reply.",
    })
    return messages


def check_and_advance(config: Dict[str, Any]) -> Tuple[Optional[str], bool]:
    """Check turn cap and handle literal mode.

    Returns (reply_text, cap_reached).
    - (None, False): config is None / not applicable (caller didn't pass config)
    - (None, True): turn cap reached, config should be removed
    - (text, False): literal mode reply, turn_count incremented
    - None for LLM mode: caller should proceed to call the LLM

    For LLM mode, returns a sentinel to indicate the caller should proceed:
    the return is (None, False) when turn_count < max_turns and not literal.
    But we can't distinguish "no config" from "LLM needed" with just (None, False).

    So the contract is: caller checks config existence first, then calls this.
    If this returns (None, True) → cap reached. If it returns a string → use it.
    If it returns (None, False) → proceed to LLM call.
    """
    if config["max_turns"] > 0 and config["turn_count"] >= config["max_turns"]:
        return None, True

    if config.get("literal"):
        config["turn_count"] += 1
        return config["prompt"], False

    return None, False


def format_status(config: Dict[str, Any]) -> str:
    """Format the auto-reply status for display."""
    mode = "literal" if config.get("literal") else "LLM-generated"
    label = "Message" if config.get("literal") else "Prompt"
    prompt_preview = config["prompt"][:100]
    if len(config["prompt"]) > 100:
        prompt_preview += "..."
    return (
        f"Auto-reply active ({mode})\n"
        f"  {label}: {prompt_preview}\n"
        f"  Turns: {config['turn_count']}/{'∞' if config['max_turns'] == 0 else config['max_turns']}"
    )
