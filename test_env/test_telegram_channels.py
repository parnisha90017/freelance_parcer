from __future__ import annotations

import asyncio
import sys
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from parsers.telegram_channels import TelegramChannelsParser


async def main() -> None:
    parser = TelegramChannelsParser()
    projects = await parser.parse()
    print(f"Telegram channels: found {len(projects)} messages")

    if not projects:
        print("No messages found")
        return

    for index, project in enumerate(projects[-5:], start=1):
        print(f"\n=== MESSAGE {index} ===")
        print(f"Title: {project.get('title', '')}")
        print(f"Description: {project.get('description', '')}")
        print(f"Link: {project.get('link', '')}")
        print(f"Source: {project.get('source', '')}")


if __name__ == "__main__":
    asyncio.run(main())
