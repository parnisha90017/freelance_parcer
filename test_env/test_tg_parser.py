from __future__ import annotations

import asyncio
import inspect
import sys
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

TARGET_CHANNELS = ["python_jobs", "durov"]


def _print_exports(module) -> None:
    names = sorted(name for name in dir(module) if not name.startswith("_"))
    print("Exports:")
    for name in names:
        value = getattr(module, name)
        kind = "module"
        if inspect.isclass(value):
            kind = "class"
        elif inspect.iscoroutinefunction(value):
            kind = "async function"
        elif inspect.isfunction(value):
            kind = "function"
        elif callable(value):
            kind = "callable"
        print(f"- {name} ({kind})")


async def _try_get_messages(module, channel: str) -> list[dict[str, str]] | None:
    candidate_names = [
        "get_messages",
        "fetch_messages",
        "parse_channel",
        "parse_messages",
        "get_channel_messages",
        "messages",
    ]

    for name in candidate_names:
        candidate = getattr(module, name, None)
        if candidate is None or not callable(candidate):
            continue

        try:
            result = candidate(channel)
            if inspect.isawaitable(result):
                result = await result
            if result is None:
                continue
            if isinstance(result, list):
                return result
            if hasattr(result, "messages"):
                messages = getattr(result, "messages")
                if isinstance(messages, list):
                    return messages
            if hasattr(result, "__iter__"):
                try:
                    return list(result)
                except TypeError:
                    continue
        except TypeError:
            continue
        except Exception as exc:
            print(f"{name}({channel}) failed: {exc}")

    return None


async def main() -> None:
    try:
        import tg_parser  # type: ignore
    except Exception as exc:
        print(f"Failed to import tg_parser: {exc}")
        return

    _print_exports(tg_parser)

    required_bits = [
        name
        for name in ("API_ID", "API_HASH", "api_id", "api_hash", "TOKEN", "token")
        if hasattr(tg_parser, name)
    ]
    if required_bits:
        print("Possible credential/config names exposed by module:")
        for name in required_bits:
            print(f"- {name}: {getattr(tg_parser, name)}")

    if any(name in sys.modules for name in ("telethon", "pyrogram")):
        print("Detected Telegram client library imports in environment.")

    for channel in TARGET_CHANNELS:
        print(f"\n=== Channel: {channel} ===")
        messages = await _try_get_messages(tg_parser, channel)

        if messages is None:
            print(
                "No callable message getter found. If the library requires API_ID/API_HASH, "
                "print those requirements from its documentation or usage error output."
            )
            continue

        print(f"Messages found: {len(messages)}")
        if not messages:
            print("No messages returned.")
            continue

        print("Last 5 messages:")
        for index, message in enumerate(messages[-5:], start=1):
            if isinstance(message, dict):
                text = str(
                    message.get("text")
                    or message.get("message")
                    or message.get("content")
                    or message.get("title")
                    or ""
                ).strip()
            else:
                text = str(message).strip()
            print(f"{index}. {text[:200]}")


if __name__ == "__main__":
    asyncio.run(main())
