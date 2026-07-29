#!/usr/bin/env python3
"""Regression coverage for v20 invitation delivery.

Guards the three-language message builder, the four delivery channels, the
sent-tracking round trip, and the fact that the artwork-image attachment stays
locked while the link message sends normally. Runs fully against a mocked
Supabase, so no live guest row is ever touched.
"""

import argparse
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from playwright.sync_api import sync_playwright


REPO = Path(__file__).resolve().parents[1]
INVITE_LINK = "https://www.wooowinvites.com/invite/22f37518"
VERSION = "٢٠٫٠ · 20.0"
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
        "invite_sent": {},
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
        "invite_sent": {},
    },
    {
        "id": "no-phone-family",
        "name": "No Phone Family",
        "phone": "",
        "side": "bride",
        "count": 3,
        "event2": False,
        "church_only": False,
        "added_by": "fixture",
        "invite_sent": {},
    },
]


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def run_browser(browser_type, browser_name, base_url):
    browser = browser_type.launch(headless=True)
    page = browser.new_page(viewport={"width": 390, "height": 844})
    page_errors = []
    family_mutations = []
    outbound_requests = []
    download_events = []

    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.on(
        "request",
        lambda request: outbound_requests.append(request.url)
        if request.url.startswith(("https://wa.me/", "https://www.messenger.com/"))
        else None,
    )
    page.on("download", lambda download: download_events.append(download.suggested_filename))
    page.add_init_script(
        """
        if (location.protocol === "http:" || location.protocol === "https:") {
          localStorage.setItem("wedSide", "bride");
          localStorage.setItem("wedOrg", "master");
          localStorage.setItem("wedLang", "en");
          localStorage.setItem("wedInviteLang", "both");
          window.__copied = [];
          window.__opened = [];
          Object.defineProperty(navigator, "clipboard", {
            configurable: true,
            value: { writeText: async text => { window.__copied.push(text); } }
          });
          window.open = (url) => { window.__opened.push(url); return null; };
        }
        """
    )

    def mock_supabase(route):
        request = route.request
        if "wedding_families" in request.url and request.method != "GET":
            family_mutations.append((request.method, request.url, request.post_data))
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
        elif "wedding_families" in request.url and request.method == "GET":
            body = FAMILIES
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    page.route("**/*.supabase.co/**", mock_supabase)

    page.goto(base_url, wait_until="domcontentloaded")
    page.evaluate("openOrg()")
    page.wait_for_function("all.length === 3")
    # Only the two families with a usable number get a send button.
    page.wait_for_function("document.querySelectorAll('#fullList .send-invite').length === 2")

    assert page.locator(".ver").inner_text() == VERSION
    assert page.evaluate("whatsappNumber('+1 202-555-0123')") == "12025550123"
    assert page.evaluate("INVITE_LINK") == INVITE_LINK

    # ── every message, every event, every language: carries the link, drops the verse
    messages = page.evaluate(
        "['reception','church','henna'].flatMap(k => INVITE_LANGS.map(l => "
        "({ kind:k, lang:l, text: inviteMessage(k,l) })))"
    )
    assert len(messages) == 9
    for entry in messages:
        assert INVITE_LINK in entry["text"], entry
        assert "Mark 10" not in entry["text"] and "مرقس" not in entry["text"], entry
        assert "10:9" not in entry["text"], entry
        # short: single-language stays a glanceable block, bilingual is two of them
        cap = 14 if entry["lang"] == "both" else 8
        assert len(entry["text"].splitlines()) <= cap, entry

    by_lang = {e["lang"]: e["text"] for e in messages if e["kind"] == "reception"}
    assert by_lang["en"].startswith("You’re invited")
    assert "يسرنا" not in by_lang["en"]
    assert by_lang["ar"].startswith("يسرنا")
    assert "You’re invited" not in by_lang["ar"]
    # bilingual = English first, Arabic underneath, one link at the end
    assert by_lang["both"].index("You’re invited") < by_lang["both"].index("يسرنا")
    assert by_lang["both"].count(INVITE_LINK) == 1

    # ── the send modal
    reception_row = page.locator("#fullList .fam").filter(has_text="Reception Test Family")
    assert reception_row.locator(".send-invite").inner_text() == "📨 Send invite"
    reception_row.locator(".send-invite").click()
    page.wait_for_selector("#inviteChannels button")

    assert page.locator("#inviteTypes button").count() == 3  # reception, church, henna
    assert page.locator("#inviteLangs button").count() == 3
    assert page.locator("#inviteChannels button").count() == 4

    # language chips drive the previewed text and its direction
    page.locator("#inviteLangs button").filter(has_text="العربية فقط").click()
    assert page.locator("#inviteMsg").get_attribute("dir") == "rtl"
    assert page.evaluate("inviteLang") == "ar"
    page.locator("#inviteLangs button").filter(has_text="English only").click()
    assert page.locator("#inviteMsg").get_attribute("dir") == "ltr"
    assert page.evaluate("localStorage.getItem('wedInviteLang')") == "en"

    # ── artwork attachment stays locked; the message path does not
    assert page.evaluate("INVITES[inviteKind].artLocked") is True
    page.wait_for_function("inviteAssetState !== 'loading'")
    assert page.locator("#inviteShareBtn").is_disabled()
    page.evaluate("shareInviteImage()")
    assert not download_events
    for channel in page.locator("#inviteChannels button").all():
        assert not channel.is_disabled()

    # ── WhatsApp: real deep link carrying the exact previewed message
    expected = page.evaluate("inviteMessage(inviteKind, inviteLang)")
    wa_url = page.evaluate("CHANNELS.whatsapp.url(whatsappNumber(inviteRow.phone), inviteMessage(inviteKind, inviteLang))")
    assert wa_url.startswith("https://wa.me/12025550123?text=")
    assert page.evaluate("url => decodeURIComponent(url.split('?text=')[1])", wa_url) == expected

    # ── iMessage: sms: scheme with the body prefilled
    im_url = page.evaluate("CHANNELS.imessage.url(whatsappNumber(inviteRow.phone), inviteMessage(inviteKind, inviteLang))")
    assert im_url.startswith("sms:+12025550123?&body=")
    assert page.evaluate("url => decodeURIComponent(url.split('&body=')[1])", im_url) == expected

    # ── Messenger: copies first (no public prefill deep link), then opens Messenger
    page.locator("#inviteChannels button").filter(has_text="Messenger").click()
    page.wait_for_function("window.__copied.length === 1")
    assert page.evaluate("window.__copied[0]") == expected
    assert page.evaluate("window.__opened") == ["https://www.messenger.com/"]
    assert not page.locator("#inviteHint").is_hidden()

    # ── after using a channel the app asks to confirm the send
    assert page.evaluate("inviteChannelUsed") == "messenger"
    mark = page.locator("#inviteSent button")
    assert mark.inner_text() == "Did it go out? Mark as sent ✓"
    assert "ask" in mark.get_attribute("class")

    # ── mark as sent: persists, badges the row, feeds the filter
    mark.click()
    page.wait_for_function("sentKinds(inviteRow).length === 1")
    patches = [m for m in family_mutations if m[0] == "PATCH"]
    assert len(patches) == 1, family_mutations
    payload = json.loads(patches[0][2])
    assert list(payload) == ["invite_sent"]
    assert payload["invite_sent"]["reception"]["via"] == "messenger"
    assert payload["invite_sent"]["reception"]["lang"] == "en"
    assert payload["invite_sent"]["reception"]["at"]

    assert page.locator("#inviteSent .sent-done").is_visible()
    page.locator("#inviteClose").click()
    assert reception_row.locator(".sent-badge").inner_text() == "✓ Sent (Wedding hall)"
    assert page.locator("#fSent").inner_text() == "✓ Sent 1"
    assert page.locator("#fUnsent").inner_text() == "◻︎ Not sent 1"

    page.evaluate("setFilter('unsent')")
    assert page.locator("#fullList .fam").count() == 1
    assert "Church Test Family" in page.locator("#fullList .fam").first.inner_text()
    page.evaluate("setFilter('sent')")
    assert page.locator("#fullList .fam").count() == 1
    assert "Reception Test Family" in page.locator("#fullList .fam").first.inner_text()

    # ── undo clears the mark back out of the database
    page.evaluate("setFilter('all')")
    reception_row.locator(".send-invite").click()
    page.wait_for_selector("#inviteSent .sent-done")
    page.locator("#inviteSent .sent-done button").click()
    page.wait_for_function("sentKinds(inviteRow).length === 0")
    patches = [m for m in family_mutations if m[0] == "PATCH"]
    assert len(patches) == 2, family_mutations
    assert json.loads(patches[1][2]) == {"invite_sent": {}}
    page.locator("#inviteClose").click()
    assert reception_row.locator(".sent-badge").count() == 0

    # ── a family with no usable number never exposes a send path
    assert page.locator("#fullList .fam").filter(has_text="No Phone Family").locator(".send-invite").count() == 0

    # sends only ever leave as user-initiated deep links, never as background fetches
    assert not outbound_requests
    assert not download_events
    assert not page_errors
    # the only writes were the two deliberate sent-mark toggles
    assert [m[0] for m in family_mutations] == ["PATCH", "PATCH"], family_mutations
    browser.close()
    print(
        f"PASS {browser_name}: 3 languages x 3 events all carry the invite link and "
        "drop the verse; WhatsApp/iMessage/Messenger/Copy channels wired; sent "
        "tracking round-trips and filters; artwork attachment still locked"
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
