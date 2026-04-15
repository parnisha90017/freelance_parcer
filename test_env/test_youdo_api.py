from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

TARGET_URL = "https://youdo.com/tasks-all-opened-all"
API_MARKER = "api/tasks/tasks"


async def main() -> None:
    try:
        from scrapling.fetchers import DynamicFetcher

        await asyncio.to_thread(
            DynamicFetcher.fetch,
            TARGET_URL,
            headless=True,
            network_idle=True,
            load_dom=False,
            disable_resources=True,
            google_search=False,
            timeout=60000,
        )
        print("Scrapling fetch completed, but network interception is not exposed here.")
    except Exception as exc:
        print(f"Scrapling fetch failed or was unavailable: {exc}")

    await _run_playwright_probe()


async def _run_playwright_probe() -> None:
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:
        print(f"Playwright import failed: {exc}")
        return

    matched_request = None
    matched_response = None
    matched_response_body = ""

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 900},
        )
        page = await context.new_page()

        async def on_request(request) -> None:
            nonlocal matched_request
            if API_MARKER in request.url and matched_request is None:
                matched_request = request
                print("\n=== API REQUEST ===")
                print(f"URL: {request.url}")
                print(f"METHOD: {request.method}")
                print("HEADERS:")
                print(json.dumps(dict(request.headers), ensure_ascii=False, indent=2))
                print("PARAMS:")
                print(json.dumps(parse_qs(urlparse(request.url).query), ensure_ascii=False, indent=2))

        async def on_response(response) -> None:
            nonlocal matched_response, matched_response_body
            if API_MARKER in response.url and matched_response is None:
                matched_response = response
                print("\n=== API RESPONSE ===")
                print(f"URL: {response.url}")
                print(f"STATUS: {response.status}")
                print("HEADERS:")
                print(json.dumps(dict(response.headers), ensure_ascii=False, indent=2))
                try:
                    matched_response_body = await response.text()
                except Exception as exc:
                    matched_response_body = f"<failed to read response body: {exc}>"
                print("BODY[:2000]:")
                print(matched_response_body[:2000])

        page.on("request", on_request)
        page.on("response", on_response)

        try:
            await page.goto(TARGET_URL, wait_until="networkidle", timeout=60000)
        except Exception as exc:
            print(f"page.goto error: {exc}")

        try:
            await page.wait_for_timeout(10000)
        except Exception as exc:
            print(f"wait_for_timeout error: {exc}")

        if matched_request is None:
            print("\nNo API request matched the marker.")
        if matched_response is None:
            print("\nNo API response matched the marker.")

        await context.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
