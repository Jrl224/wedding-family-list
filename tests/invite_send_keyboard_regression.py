#!/usr/bin/env python3
"""Keyboard regression coverage for disabled WhatsApp invite actions."""

import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from playwright.sync_api import sync_playwright


REPO = Path(__file__).resolve().parents[1]
FAMILIES = [
    {
        "id": "event2-family",
        "name": "Test Family",
        "phone": "+1 202-555-0123",
        "side": "bride",
        "count": 2,
        "event2": True,
        "church_only": False,
        "added_by": "fixture",
    }
]


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def run_browser(browser_type, browser_name, base_url):
    browser = browser_type.launch(headless=True)
    page = browser.new_page(viewport={"width": 390, "height": 844})
    page_errors = []
    family_mutations = []

    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.add_init_script(
        """
        if (location.protocol === "http:" || location.protocol === "https:") {
          localStorage.setItem("wedSide", "bride");
          localStorage.setItem("wedOrg", "master");
          localStorage.setItem("wedLang", "en");
          sessionStorage.setItem(
            "__inviteTestBoots",
            String(Number(sessionStorage.getItem("__inviteTestBoots") || 0) + 1)
          );
        }
        """
    )

    def mock_supabase(route):
        request = route.request
        if "wedding_families" in request.url and request.method != "GET":
            family_mutations.append((request.method, request.url))
        body = []
        if "wedding_settings" in request.url:
            body = [
                {
                    "bride_cap": 200,
                    "groom_cap": 200,
                    "event2_name_ar": "حفلة الحنة",
                    "event2_name_en": "Henna Party",
                }
            ]
        elif "wedding_families" in request.url:
            body = FAMILIES
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    page.route("**/*.supabase.co/**", mock_supabase)
    page.route(
        "**/invites/reception-invitation.*",
        lambda route: route.fulfill(status=404, body="missing test fixture"),
    )

    page.goto(base_url, wait_until="domcontentloaded")
    page.evaluate("openOrg()")
    page.wait_for_function("all.length === 1")
    page.evaluate("setFilter('ev2')")
    page.locator(".send-invite").click()
    page.wait_for_function("inviteAssetState === 'ready'")

    whatsapp = page.locator("#inviteWaBtn")
    assert whatsapp.get_attribute("href") is None
    assert whatsapp.get_attribute("aria-disabled") == "true"
    assert whatsapp.evaluate("element => element.tabIndex") == -1

    # Locked Henna is skipped by Tab and Enter cannot reload/navigate.
    page.locator("#inviteTypes button.on").focus()
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.id") == "inviteClose"
    before_url = page.url
    before_boots = page.evaluate("sessionStorage.getItem('__inviteTestBoots')")
    whatsapp.focus()
    page.keyboard.press("Enter")
    page.wait_for_timeout(100)
    assert page.url == before_url
    assert page.evaluate("sessionStorage.getItem('__inviteTestBoots')") == before_boots

    # A ready, unlocked invite restores proper link semantics.
    page.get_by_role("button", name="Church").click()
    page.wait_for_function("inviteAssetState === 'ready'")
    assert whatsapp.get_attribute("href").startswith("https://wa.me/12025550123?text=")
    assert whatsapp.get_attribute("aria-disabled") is None
    assert whatsapp.evaluate("element => element.tabIndex") == 0

    # Missing art returns to a semantic disabled state; Enter remains inert.
    page.get_by_role("button", name="Wedding hall").click()
    page.wait_for_function("inviteAssetState === 'missing'")
    assert whatsapp.get_attribute("href") is None
    assert whatsapp.get_attribute("aria-disabled") == "true"
    assert whatsapp.evaluate("element => element.tabIndex") == -1
    before_url = page.url
    before_boots = page.evaluate("sessionStorage.getItem('__inviteTestBoots')")
    whatsapp.focus()
    page.keyboard.press("Enter")
    page.wait_for_timeout(100)
    assert page.url == before_url
    assert page.evaluate("sessionStorage.getItem('__inviteTestBoots')") == before_boots

    assert not family_mutations
    assert not page_errors
    browser.close()
    print(
        f"PASS {browser_name}: locked/missing WhatsApp actions are unfocusable "
        "and Enter-inert; enabled state restores href"
    )


def main():
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(QuietHandler, directory=str(REPO))
    )
    Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.server_port}/"
    try:
        with sync_playwright() as playwright:
            run_browser(playwright.chromium, "Chromium", base_url)
            run_browser(playwright.webkit, "WebKit", base_url)
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
