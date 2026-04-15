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

from parsers.freelancehunt import FreelanceHuntParser


async def main() -> None:
    parser = FreelanceHuntParser()
    projects = await parser.parse()
    print(f"FreelanceHunt: found {len(projects)} projects")

    if not projects:
        print("No projects found or site unavailable")
        return

    for index, project in enumerate(projects[:3], start=1):
        print(f"\n=== PROJECT {index} ===")
        print(f"Title: {project.get('title', '')}")
        print(f"Price: {project.get('price', '')}")
        print(f"Description: {project.get('description', '')}")
        print(f"Link: {project.get('link', '')}")
        print(f"Source: {project.get('source', '')}")


if __name__ == "__main__":
    asyncio.run(main())
