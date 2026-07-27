#!/usr/bin/env python3
"""Regression coverage for temporary invitation locks and preserved delivery logic."""

import argparse
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
        "name": "Reception Test Family",
        "phone": "+1 202-555-0123",
        "side": "bride",
        "count": 2,
        "event2": True,
        "church_only": False,
        "added_by": "fixture",
    },
    {
        "id": "church-family",
        "name": "Church Test Family",
        "phone": "+20 100 123 4567",
        "side": "bride",
        "count": 2,
        "event2": False,
        "church_only": True,
        "added_by": "fixture",
    },
]


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def assert_disabled_delivery(page, expected_status):
    share = page.locator("#inviteShareBtn")
    whatsapp = page.locator("#inviteWaBtn")

    assert share.is_disabled()
    assert whatsapp.get_attribute("href") is None
    assert whatsapp.get_attribute("aria-disabled") == "true"
    assert whatsapp.evaluate("element => element.tabIndex") == -1
    assert page.locator("#inviteStatus").inner_text() == expected_status
    assert page.locator("#inviteLimit").is_hidden()


def run_browser(browser_type, browser_name, base_url):
    browser = browser_type.launch(headless=True)
    page = browser.new_page(viewport={"width": 390, "height": 844})
    page_errors = []
    family_mutations = []
    whatsapp_requests = []
    download_events = []

    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.on(
        "request",
        lambda request: whatsapp_requests.append(request.url)
        if request.url.startswith("https://wa.me/")
        else None,
    )
    page.on("download", lambda download: download_events.append(download.suggested_filename))
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
          window.__shareCalls = 0;
          window.__sharedFileNames = [];
          Object.defineProperty(navigator, "canShare", {
            configurable: true,
            value: data => Boolean(data && data.files && data.files.length)
          });
          Object.defineProperty(navigator, "share", {
            configurable: true,
            value: async data => {
              window.__shareCalls += 1;
              window.__sharedFileNames = (data.files || []).map(file => file.name);
            }
          });
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

    page.goto(base_url, wait_until="domcontentloaded")
    page.evaluate("openOrg()")
    page.wait_for_function("all.length === 2")
    page.wait_for_function("document.querySelectorAll('.send-invite').length === 2")

    assert page.locator(".ver").inner_text() == "١٩٫٣ · 19.3"
    assert page.evaluate("whatsappNumber('+1 202-555-0123')") == "12025550123"
    assert (
        page.evaluate("STR.ar.inviteArtworkReviewLock")
        == "🔒 التصميم الجديد قيد المراجعة"
    )

    reception_row = page.locator(".fam").filter(has_text="Reception Test Family")
    church_row = page.locator(".fam").filter(has_text="Church Test Family")
    assert (
        reception_row.locator(".send-invite").inner_text()
        == "🔒 New artwork in review"
    )
    assert church_row.locator(".send-invite").inner_text() == "🔒 New artwork in review"
    assert "locked" in reception_row.locator(".send-invite").get_attribute("class")
    assert "locked" in church_row.locator(".send-invite").get_attribute("class")

    # Reception: the old asset may preview, but every send/share path stays locked.
    reception_row.locator(".send-invite").click()
    page.wait_for_function("inviteAssetState === 'ready'")
    assert page.locator("#inviteTypes button").count() == 3
    assert_disabled_delivery(page, "🔒 New artwork in review")

    page.evaluate(
        """
        window.__downloadCalls = 0;
        downloadPreparedInvite = () => {
          window.__downloadCalls += 1;
          return true;
        };
        void 0;
        """
    )
    assert page.evaluate("window.__downloadCalls") == 0
    locked_state = page.evaluate(
        "({ kind: inviteKind, locked: INVITES[inviteKind].locked, "
        "assetState: inviteAssetState, hasPrepared: Boolean(invitePrepared) })"
    )
    assert locked_state["locked"] is True, locked_state
    page.evaluate("shareInviteImage()")
    assert page.evaluate("window.__shareCalls") == 0
    locked_download_calls = page.evaluate("window.__downloadCalls")
    assert locked_download_calls == 0, locked_download_calls

    whatsapp = page.locator("#inviteWaBtn")
    before_url = page.url
    before_boots = page.evaluate("sessionStorage.getItem('__inviteTestBoots')")
    whatsapp.focus()
    page.keyboard.press("Enter")
    page.wait_for_timeout(100)
    assert page.url == before_url
    assert page.evaluate("sessionStorage.getItem('__inviteTestBoots')") == before_boots

    # Church: it has the same artwork-review lock.
    page.locator("#inviteTypes button").filter(has_text="Church").click()
    page.wait_for_function("inviteAssetState === 'ready'")
    assert_disabled_delivery(page, "🔒 New artwork in review")

    # Temporarily unlock only inside this mocked browser to prove normalization,
    # WhatsApp href generation, Web Share, and download fallback remain intact.
    page.evaluate("INVITES.church.locked = false; renderInviteModal()")
    assert not page.locator("#inviteShareBtn").is_disabled()
    assert whatsapp.get_attribute("href").startswith("https://wa.me/12025550123?text=")
    assert whatsapp.get_attribute("aria-disabled") is None
    assert whatsapp.evaluate("element => element.tabIndex") == 0
    assert page.locator("#inviteLimit").is_visible()

    page.evaluate("shareInviteImage()")
    assert page.evaluate("window.__shareCalls") == 1
    assert page.evaluate("window.__sharedFileNames.length") == 1
    assert page.evaluate("window.__downloadCalls") == 0

    page.evaluate(
        """
        Object.defineProperty(navigator, "canShare", {
          configurable: true,
          value: () => false
        });
        """
    )
    page.evaluate("shareInviteImage()")
    assert page.evaluate("window.__shareCalls") == 1
    assert page.evaluate("window.__downloadCalls") == 1

    page.evaluate("INVITES.church.locked = true; renderInviteModal()")
    assert_disabled_delivery(page, "🔒 New artwork in review")

    # Henna retains its distinct venue-confirmation lock and is skipped by Tab.
    page.locator("#inviteTypes button").filter(has_text="Henna").click()
    page.wait_for_function("inviteAssetState === 'ready'")
    assert_disabled_delivery(
        page,
        "🔒 DRAFT — the henna venue needs one factual confirmation. "
        "Sending stays locked until Dar Air Defense is confirmed over the older "
        "9 Sakaliya pin.",
    )
    page.locator("#inviteTypes button.on").focus()
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.id") == "inviteClose"

    page.locator("#inviteClose").click()
    page.evaluate("setFilter('ev2')")
    henna_row = page.locator(".fam").filter(has_text="Reception Test Family")
    assert henna_row.locator(".send-invite").inner_text() == "🔒 Henna invite — DRAFT"
    assert "locked" in henna_row.locator(".send-invite").get_attribute("class")

    assert not family_mutations
    assert not whatsapp_requests
    assert not download_events
    assert not page_errors
    browser.close()
    print(
        f"PASS {browser_name}: reception/church artwork-review locks and henna "
        "venue lock are inert; mocked Web Share/fallback and phone normalization "
        "remain intact; zero family mutations, WhatsApp requests, or downloads"
    )


def run_suite(base_url):
    with sync_playwright() as playwright:
        run_browser(playwright.chromium, "Chromium", base_url)
        run_browser(playwright.webkit, "WebKit", base_url)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--url",
        help="Test a deployed base URL instead of serving the local repository.",
    )
    args = parser.parse_args()

    if args.url:
        run_suite(args.url)
        return

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(QuietHandler, directory=str(REPO))
    )
    Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.server_port}/"
    try:
        run_suite(base_url)
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
