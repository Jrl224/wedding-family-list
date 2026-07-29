#!/usr/bin/env python3
"""Regression coverage for v21.0 invitation delivery.

The invitation now composes itself from each family's checkmarks: church-only
families get church-only copy plus the church-scoped RSVP link, henna-flagged
families get the henna block folded into the same message, everyone else gets
the full wedding message. Guards the single bilingual English+Arabic builder
(each English line with its Arabic line directly underneath) composed per
family (full / church-only / henna), the four channels (WhatsApp / iMessage /
Messenger / Copy), the single per-family sent record with rollback on a
failed PATCH, and the absence of any image attachment path. Runs fully against
a mocked Supabase, so no live guest row is ever touched.
"""

import argparse
import json
import re
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from playwright.sync_api import sync_playwright


REPO = Path(__file__).resolve().parents[1]
INVITE_LINK = "https://www.wooowinvites.com/invite/22f37518"
CHURCH_LINK = (
    "https://www.wooowinvites.com/invite/22f37518"
    "?cat=3eff4b55-85f1-4322-8ab6-f1bf8fd8c85c"
    "&g=rsvp-7110440c-c1ca-4577-abac-3cae79bce03c"
)
VERSION = "٢١٫٠ · 21.0"
TEMPLATE_VERSION = "21.0"
ARABIC = re.compile(r"[؀-ۿ]")
FAMILIES = [
    {
        "id": "henna-family",
        "name": "Henna Test Family",
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
    image_requests = []
    state = {"fail_patch": False}

    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.on(
        "request",
        lambda request: (
            outbound_requests.append(request.url)
            if request.url.startswith(("https://wa.me/", "https://www.messenger.com/"))
            else image_requests.append(request.url)
            if "/invites/" in request.url
            else None
        ),
    )
    page.add_init_script(
        """
        if (location.protocol === "http:" || location.protocol === "https:") {
          localStorage.setItem("wedSide", "bride");
          localStorage.setItem("wedOrg", "master");
          localStorage.setItem("wedLang", "en");
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
            if state["fail_patch"] and request.method == "PATCH":
                state["fail_patch"] = False
                route.fulfill(status=500, content_type="application/json", body="{}")
                return
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
    # Every family gets a send path now — no-phone families still have Messenger/Copy.
    page.wait_for_function("document.querySelectorAll('#fullList .send-invite').length === 3")

    assert page.locator(".ver").inner_text() == VERSION
    assert page.evaluate("whatsappNumber('+1 202-555-0123')") == "12025550123"
    assert page.evaluate("INVITE_LINK") == INVITE_LINK
    assert page.evaluate("INVITE_LINK_CHURCH_ONLY") == CHURCH_LINK
    # The manual event picker is gone — composition is automatic per family.
    assert page.locator("#inviteTypes").count() == 0

    # ── every family: one bilingual message, right link, right blocks, verse dropped
    messages = page.evaluate(
        "() => all.map(r => ({"
        " id: r.id, church: !!r.church_only, henna: !!r.event2,"
        " text: inviteMessage(r) }))"
    )
    assert len(messages) == 3
    for entry in messages:
        text = entry["text"]
        link = CHURCH_LINK if entry["church"] else INVITE_LINK
        assert text.rstrip().endswith(link), entry
        assert text.count("wooowinvites") == 1, entry
        if not entry["church"]:
            assert "?cat=" not in text, entry
        # the verse and the long formal wording are gone
        assert "Mark 10" not in text and "مرقس" not in text and "10:9" not in text, entry
        assert "Raafat" not in text and "رأفت" not in text, entry
        # one composed bilingual message stays glanceable
        assert len(text.splitlines()) <= 30, entry
        # every message is bilingual: English body AND Arabic script both present
        assert ARABIC.search(text), entry
        assert re.search(r"[A-Za-z]", text), entry
        # church-only copy carries zero reception or henna trace
        if entry["church"]:
            for leak in ("Reception", "Al Masa", "حفل الزفاف", "الماسة", "Henna", "الحنة", "🌿"):
                assert leak not in text, (leak, entry)
        # henna-flagged families get BOTH the EN and AR henna blocks folded in
        if entry["henna"]:
            assert "Henna Party" in text and "August 14" in text, entry
            assert "حفلة الحنة" in text and "١٤ أغسطس" in text, entry

    henna_text = next(e["text"] for e in messages if e["id"] == "henna-family")
    # bilingual = each English line with its Arabic line directly underneath
    henna_lines = henna_text.splitlines()
    assert henna_lines[0].startswith("You’re invited")
    assert henna_lines[1].startswith("يسرنا")
    assert henna_text.index("You’re invited") < henna_text.index("يسرنا")

    # ── the send modal (henna family: full wedding + henna, auto-composed)
    henna_row = page.locator("#fullList .fam").filter(has_text="Henna Test Family")
    assert henna_row.locator(".send-invite").inner_text() == "📨 Send invite"
    henna_row.locator(".send-invite").click()
    page.wait_for_selector("#inviteChannels button")

    assert page.locator("#inviteIncluded").inner_text() == "Holy Matrimony + Reception + Henna Party"
    assert page.locator("#inviteChannels button").count() == 4

    # the language chooser is gone: one bilingual message, always dir=auto, no persistence key
    assert page.locator("#invite" + "Langs").count() == 0
    assert page.locator("#inviteStepLang").count() == 0
    assert page.locator("#inviteMsg").get_attribute("dir") == "auto"
    assert page.evaluate("localStorage.getItem('wedInvite' + 'Lang')") is None
    assert page.evaluate("typeof window['invite' + 'Lang']") == "undefined"
    assert page.evaluate("typeof window['INVITE_' + 'LANGS']") == "undefined"
    assert page.locator("#inviteMsg").inner_text().strip() == henna_text.strip()

    # ── WhatsApp: real deep link carrying the exact previewed message
    expected = page.evaluate("inviteMessage(inviteRow)")
    page.locator("#inviteChannels button").filter(has_text="WhatsApp").click()
    page.wait_for_function("window.__opened.length === 1")
    wa_url = page.evaluate("window.__opened[0]")
    assert wa_url.startswith("https://wa.me/12025550123?text=")
    assert page.evaluate("url => decodeURIComponent(url.split('?text=')[1])", wa_url) == expected
    assert page.evaluate("inviteChannelUsed") == "whatsapp"

    # ── iMessage: sms: deep link with the exact previewed message in the body (no click —
    # the sms: external-protocol navigation can hang/fail the headless engines, so we assert
    # the builder output and decode-compare it against the rendered message)
    im_url = page.evaluate(
        "CHANNELS.imessage.url(whatsappNumber(inviteRow.phone), inviteMessage(inviteRow))"
    )
    assert im_url.startswith("sms:+12025550123?&body="), im_url
    assert page.evaluate("url => decodeURIComponent(url.split('&body=')[1])", im_url) == expected
    assert INVITE_LINK in expected

    # ── Messenger: copies first (no public prefill deep link), then opens Messenger
    page.locator("#inviteChannels button").filter(has_text="Messenger").click()
    page.wait_for_function("window.__copied.length === 1")
    assert page.evaluate("window.__copied[0]") == expected
    assert page.evaluate("window.__opened[1]") == "https://www.messenger.com/"
    assert not page.locator("#inviteHint").is_hidden()

    # ── after using a channel the app asks to confirm the send
    assert page.evaluate("inviteChannelUsed") == "messenger"
    mark = page.locator("#inviteSent button")
    assert mark.inner_text() == "Did it go out? Mark as sent ✓"
    assert "ask" in mark.get_attribute("class")

    # ── mark as sent: persists one overall record, badges the row, feeds the filter
    mark.click()
    page.wait_for_function("isInviteSent(inviteRow)")
    patches = [m for m in family_mutations if m[0] == "PATCH"]
    assert len(patches) == 1, family_mutations
    payload = json.loads(patches[0][2])
    assert list(payload) == ["invite_sent"]
    record = payload["invite_sent"]["overall"]
    assert record["via"] == "messenger"
    assert record["lang"] == "both"
    assert record["at"]
    assert record["church_only"] is False
    assert record["henna"] is True
    assert record["link"] == INVITE_LINK
    assert record["template"] == TEMPLATE_VERSION

    assert page.locator("#inviteSent .sent-done").is_visible()
    page.locator("#inviteClose").click()
    assert henna_row.locator(".sent-badge").inner_text().startswith("✓ Sent")
    assert page.locator("#fSent").inner_text() == "✓ Sent 1"
    assert page.locator("#fUnsent").inner_text() == "◻︎ Not sent 2"

    page.evaluate("setFilter('unsent')")
    assert page.locator("#fullList .fam").count() == 2
    page.evaluate("setFilter('sent')")
    assert page.locator("#fullList .fam").count() == 1
    assert "Henna Test Family" in page.locator("#fullList .fam").first.inner_text()

    # ── undo clears the mark back out of the database
    page.evaluate("setFilter('all')")
    henna_row.locator(".send-invite").click()
    page.wait_for_selector("#inviteSent .sent-done")
    page.locator("#inviteSent .sent-done button").click()
    page.wait_for_function("!isInviteSent(inviteRow)")
    patches = [m for m in family_mutations if m[0] == "PATCH"]
    assert len(patches) == 2, family_mutations
    assert json.loads(patches[1][2]) == {"invite_sent": {}}

    # ── a failed PATCH rolls the optimistic sent mark back
    state["fail_patch"] = True
    page.locator("#inviteSent button").click()
    deadline = time.monotonic() + 15
    while len([m for m in family_mutations if m[0] == "PATCH"]) < 3:
        assert time.monotonic() < deadline, family_mutations
        page.wait_for_timeout(50)
    page.wait_for_function("!isInviteSent(inviteRow)")
    patches = [m for m in family_mutations if m[0] == "PATCH"]
    assert len(patches) == 3, family_mutations
    page.locator("#inviteClose").click()
    assert henna_row.locator(".sent-badge").count() == 0

    # ── church-only family: church copy + church-scoped RSVP link
    church_row = page.locator("#fullList .fam").filter(has_text="Church Test Family")
    church_row.locator(".send-invite").click()
    page.wait_for_selector("#inviteChannels button")
    assert page.locator("#inviteIncluded").inner_text() == "Holy Matrimony only"
    church_msg = page.evaluate("inviteMessage(inviteRow)")
    assert church_msg.rstrip().endswith(CHURCH_LINK)
    assert "Reception" not in church_msg and "Henna" not in church_msg
    wa_church = page.evaluate(
        "CHANNELS.whatsapp.url(whatsappNumber(inviteRow.phone), inviteMessage(inviteRow))"
    )
    assert wa_church.startswith("https://wa.me/201001234567?text=")
    page.locator("#inviteClose").click()

    # ── no-phone family: WhatsApp is blocked, copy still works
    page.locator("#fullList .fam").filter(has_text="No Phone Family").locator(".send-invite").click()
    page.wait_for_selector("#inviteChannels button")
    wa_button = page.locator("#inviteChannels button").filter(has_text="WhatsApp")
    assert wa_button.is_disabled()
    copies_before = page.evaluate("window.__copied.length")
    page.locator("#inviteChannels button").filter(has_text="Copy message").click()
    page.wait_for_function(f"window.__copied.length === {copies_before + 1}")
    assert page.evaluate("inviteChannelUsed") == "copy"
    assert INVITE_LINK in page.evaluate("window.__copied[window.__copied.length - 1]")
    page.locator("#inviteClose").click()

    # sends only ever leave as user-initiated deep links, never as background fetches
    assert not outbound_requests
    # the image-attachment path is gone entirely — nothing ever fetches invites/*
    assert not image_requests
    assert not page_errors
    # the only writes were the three deliberate sent-mark toggles (one rolled back)
    assert [m[0] for m in family_mutations] == ["PATCH", "PATCH", "PATCH"], family_mutations
    browser.close()
    print(
        f"PASS {browser_name}: auto-composed invitations (full/church/henna) as one "
        "bilingual English+Arabic message all carry the right wooowinvites link and drop "
        "the verse; WhatsApp/iMessage/Messenger/Copy wired; sent tracking round-trips, "
        "rolls back on failure, and filters; no image attachment path remains"
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
