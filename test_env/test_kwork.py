from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, ".")

from config import config
from parsers.kwork import KworkParser


async def main() -> None:
    parser = KworkParser(
        login=config.KWORK_LOGIN,
        password=config.KWORK_PASSWORD,
        phone_last=config.KWORK_PHONE_LAST,
    )
    projects = await parser.parse()
    print(f"Найдено: {len(projects)}")
    for p in projects[:3]:
        print(f"  {p['title'][:50]} | {p['link']}")
    await parser.close()


if __name__ == "__main__":
    asyncio.run(main())
