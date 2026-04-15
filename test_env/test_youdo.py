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

from parsers.youdo import YouDoParser


async def main() -> None:
    parser = YouDoParser()
    tasks = await parser.parse()
    print(f"YouDo API: found {len(tasks)} tasks")

    if not tasks:
        print("Site unavailable or no tasks found")
        return

    for index, task in enumerate(tasks[:3], start=1):
        print(f"\n=== TASK {index} ===")
        print(f"Title: {task.get('title', '')}")
        print(f"Price: {task.get('price', '')}")
        print(f"Description: {task.get('description', '')}")
        print(f"Link: {task.get('link', '')}")
        print(f"Source: {task.get('source', '')}")


if __name__ == "__main__":
    asyncio.run(main())
