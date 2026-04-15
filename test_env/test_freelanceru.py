from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, ".")

from parsers.freelanceru import FreelanceRuParser


async def main() -> None:
    parser = FreelanceRuParser(max_pages=2)
    projects = await parser.parse()
    print(f"Найдено: {len(projects)}")
    for p in projects[:3]:
        print(f"  {p['title'][:50]} | {p['link']}")


if __name__ == "__main__":
    asyncio.run(main())
