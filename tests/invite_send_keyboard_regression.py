#!/usr/bin/env python3
"""Regression coverage for v22.0 invitation delivery (church-universal model).

Everyone is always invited to church; the per-family choices are Reception and
Henna. `church_only:true` means "church only, not invited to the reception"; the
UI presents its inverse as "Reception". The invitation is one bilingual
English+Arabic message per family (each English line with its Arabic line
directly underneath): church-only families get church-only copy plus the
church-scoped RSVP link, henna-flagged families get the henna block folded in,
everyone else gets the full wedding message. This suite guards the v22 send-modal
UX and the addendum on top of it: the four channels (WhatsApp / iMessage /
Messenger / Copy); the iMessage sms: deep link launched through a hidden iframe
so the top page never unloads; the event chips on every card (⛪ always, 🏛
Reception iff invited, 🌿 Henna iff flagged); the in-modal Reception + Henna
pills that PATCH church_only / event2 and recompose the message live, above a
static "church is universal" note; the ‹ / › family navigation with an N / M
indicator; the ✕ close button; the missing-number badge + no-phone filter chip;
the green sent-card class; the inline row delete removed (delete now lives inside
Edit and writes church_only under the inverted "Reception?" label); and the
single per-family sent record with rollback on a failed PATCH. Runs fully against
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
VERSION = "٢٢٫٠ · 22.0"
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

    def wait_new_mutation(n_before, timeout=10):
        deadline = time.monotonic() + timeout
        while len(family_mutations) <= n_before:
            assert time.monotonic() < deadline, family_mutations
            page.wait_for_timeout(30)
        return family_mutations[-1]

    def sent_patches():
        return [m for m in family_mutations if m[0] == "PATCH" and "invite_sent" in (m[2] or "")]

    def on(locator):
        # token-exact class check — the "reception" class literally contains "on"
        return "on" in (locator.get_attribute("class") or "").split()

    page.goto(base_url, wait_until="domcontentloaded")
    page.evaluate("openOrg()")
    page.wait_for_function("all.length === 3")
    page.wait_for_function("document.querySelectorAll('#fullList .send-invite').length === 3")

    assert page.locator(".ver").inner_text() == VERSION
    assert page.evaluate("whatsappNumber('+1 202-555-0123')") == "12025550123"
    assert page.evaluate("INVITE_LINK") == INVITE_LINK
    assert page.evaluate("INVITE_LINK_CHURCH_ONLY") == CHURCH_LINK
    assert page.evaluate("INVITE_TEMPLATE_VERSION") == TEMPLATE_VERSION
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
        assert "Mark 10" not in text and "مرقس" not in text and "10:9" not in text, entry
        assert "Raafat" not in text and "رأفت" not in text, entry
        assert len(text.splitlines()) <= 30, entry
        assert ARABIC.search(text), entry
        assert re.search(r"[A-Za-z]", text), entry
        if entry["church"]:
            for leak in ("Reception", "Al Masa", "حفل الزفاف", "الماسة", "Henna", "الحنة", "🌿"):
                assert leak not in text, (leak, entry)
        if entry["henna"]:
            assert "Henna Party" in text and "August 14" in text, entry
            assert "حفلة الحنة" in text and "١٤ أغسطس" in text, entry

    henna_text = next(e["text"] for e in messages if e["id"] == "henna-family")
    henna_lines = henna_text.splitlines()
    assert henna_lines[0].startswith("You’re invited")
    assert henna_lines[1].startswith("يسرنا")
    assert henna_text.index("You’re invited") < henna_text.index("يسرنا")

    # ── D1: missing-number badge + no-phone filter chip
    henna_row = page.locator("#fullList .fam").filter(has_text="Henna Test Family")
    church_row = page.locator("#fullList .fam").filter(has_text="Church Test Family")
    nophone_row = page.locator("#fullList .fam").filter(has_text="No Phone Family")
    assert henna_row.locator(".nophone-badge").count() == 0
    assert nophone_row.locator(".nophone-badge").inner_text() == "📵 No number"
    assert page.locator("#fNoPhone").inner_text() == "📵 1"
    page.evaluate("setFilter('nophone')")
    assert page.locator("#fullList .fam").count() == 1
    assert "No Phone Family" in page.locator("#fullList .fam").first.inner_text()
    page.evaluate("setFilter('all')")

    # ── D3: no inline delete button remains in any row (delete moved into Edit)
    assert page.locator("#fullList .fam .del").count() == 0
    assert page.locator("#fullList .fam .edit-btn").count() == 3

    # ── Addendum: event chips on every card — ⛪ always, 🏛 iff invited to reception, 🌿 iff henna
    def chips(row):
        return {c: row.locator(f".card-chip.{c}").count() for c in ("church", "reception", "henna")}
    assert chips(henna_row) == {"church": 1, "reception": 1, "henna": 1}       # reception + henna
    assert chips(church_row) == {"church": 1, "reception": 0, "henna": 0}      # church only
    assert chips(nophone_row) == {"church": 1, "reception": 1, "henna": 0}     # reception, no henna
    # organizer chips carry text labels
    assert "Church" in henna_row.locator(".card-chip.church").inner_text()
    assert "Reception" in henna_row.locator(".card-chip.reception").inner_text()
    assert "Henna" in henna_row.locator(".card-chip.henna").inner_text()
    # organizer row reception quick-toggle (🏛) is ON iff invited to the reception
    assert "🏛" in henna_row.locator(".cot").inner_text()
    assert on(henna_row.locator(".cot"))
    assert not on(church_row.locator(".cot"))

    # ── D6 + D7: send modal has a ✕ close, ‹ › nav, and an N / M indicator
    page.locator("#fullList .fam").first.locator(".send-invite").click()
    page.wait_for_selector("#inviteChannels button")
    assert page.locator("#inviteX").get_attribute("aria-label") == "Close"
    assert page.locator("#invitePrev").count() == 1 and page.locator("#inviteNext").count() == 1
    nav_ids = page.evaluate("inviteNav")
    assert nav_ids == [f["id"] for f in FAMILIES]
    assert page.evaluate("inviteNavIdx") == 0
    assert page.locator("#invitePos").inner_text() == "1 / 3"
    assert page.locator("#invitePrev").is_disabled()
    assert not page.locator("#inviteNext").is_disabled()

    # ── D7: › advances family 1 → family 2 and recomposes the previewed message
    page.locator("#inviteNext").click()
    assert page.evaluate("inviteNavIdx") == 1
    assert page.evaluate("inviteRow.id") == nav_ids[1]
    assert page.locator("#invitePos").inner_text() == "2 / 3"
    assert page.locator("#inviteMsg").inner_text().strip() == page.evaluate("inviteMessage(inviteRow)").strip()
    page.locator("#invitePrev").click()
    assert page.evaluate("inviteNavIdx") == 0
    page.locator("#inviteClose").click()

    # ── open the henna family for the modal-behaviour tests
    henna_row.locator(".send-invite").click()
    page.wait_for_selector("#inviteChannels button")
    assert page.evaluate("inviteRow.id") == "henna-family"
    assert page.locator("#inviteChannels button").count() == 4
    # language chooser is still gone; one bilingual message, dir=auto
    assert page.locator("#invite" + "Langs").count() == 0
    assert page.locator("#inviteMsg").get_attribute("dir") == "auto"

    # ── D5 + addendum: church is universal (static note); the Reception pill PATCHes
    # church_only and swaps the link live (Reception OFF ⇒ church-only + ?cat= link)
    assert page.locator("#inviteChurchNote").inner_text() == "⛪ Church — everyone is invited"
    henna_channel_msg = page.evaluate("inviteMessage(inviteRow)")
    assert henna_channel_msg.rstrip().endswith(INVITE_LINK)
    reception_pill = page.locator("#inviteIncluded .inc-pill.reception")
    henna_pill = page.locator("#inviteIncluded .inc-pill.henna")
    # henna family is invited to the reception, so the Reception pill starts ON
    assert on(reception_pill)
    assert on(henna_pill)

    n = len(family_mutations)
    reception_pill.click()
    mut = wait_new_mutation(n)
    assert mut[0] == "PATCH" and json.loads(mut[2]) == {"church_only": True}, mut
    page.wait_for_function("inviteMessage(inviteRow).includes('?cat=')")
    assert "?cat=" in page.locator("#inviteMsg").inner_text()
    assert not on(page.locator("#inviteIncluded .inc-pill.reception"))

    n = len(family_mutations)
    page.locator("#inviteIncluded .inc-pill.reception").click()
    mut = wait_new_mutation(n)
    assert json.loads(mut[2]) == {"church_only": False}, mut
    page.wait_for_function("!inviteMessage(inviteRow).includes('?cat=')")
    assert "?cat=" not in page.locator("#inviteMsg").inner_text()
    assert on(page.locator("#inviteIncluded .inc-pill.reception"))

    # ── D5: henna pill PATCHes event2 and folds the henna block in/out live
    n = len(family_mutations)
    page.locator("#inviteIncluded .inc-pill.henna").click()
    mut = wait_new_mutation(n)
    assert json.loads(mut[2]) == {"event2": False}, mut
    page.wait_for_function("!inviteMessage(inviteRow).includes('Henna Party')")
    assert "Henna Party" not in page.locator("#inviteMsg").inner_text()

    n = len(family_mutations)
    page.locator("#inviteIncluded .inc-pill.henna").click()
    mut = wait_new_mutation(n)
    assert json.loads(mut[2]) == {"event2": True}, mut
    page.wait_for_function("inviteMessage(inviteRow).includes('Henna Party')")
    assert "Henna Party" in page.locator("#inviteMsg").inner_text()

    # state restored: henna family back to full wedding + henna, no church link
    expected = page.evaluate("inviteMessage(inviteRow)")
    assert expected.rstrip().endswith(INVITE_LINK)
    assert "Henna Party" in expected

    # ── WhatsApp: real deep link carrying the exact previewed message
    page.locator("#inviteChannels button").filter(has_text="WhatsApp").click()
    page.wait_for_function("window.__opened.length === 1")
    wa_url = page.evaluate("window.__opened[0]")
    assert wa_url.startswith("https://wa.me/12025550123?text=")
    assert page.evaluate("url => decodeURIComponent(url.split('?text=')[1])", wa_url) == expected
    assert page.evaluate("inviteChannelUsed") == "whatsapp"

    # ── iMessage builder: sms: deep link with the previewed message in the body
    im_url = page.evaluate(
        "CHANNELS.imessage.url(whatsappNumber(inviteRow.phone), inviteMessage(inviteRow))"
    )
    assert im_url.startswith("sms:+12025550123?&body="), im_url
    assert page.evaluate("url => decodeURIComponent(url.split('&body=')[1])", im_url) == expected

    # ── D4: clicking iMessage launches sms: via a hidden iframe, NOT a top navigation
    copies_before = page.evaluate("window.__copied.length")
    page.locator("#inviteChannels button").filter(has_text="iMessage").click()
    page.wait_for_function("!!document.querySelector(\"iframe[src^='sms:']\")")
    assert page.evaluate("document.querySelector(\"iframe[src^='sms:']\").src").startswith(
        "sms:+12025550123?&body="
    )
    assert page.url.rstrip("/") == base_url.rstrip("/")
    assert not page.locator("#inviteBg").is_hidden()
    assert page.evaluate("inviteChannelUsed") == "imessage"
    page.wait_for_function(f"window.__copied.length === {copies_before + 1}")
    assert page.evaluate("window.__copied[window.__copied.length - 1]") == expected

    # ── Messenger: copies first (no public prefill deep link), then opens Messenger
    page.locator("#inviteChannels button").filter(has_text="Messenger").click()
    page.wait_for_function("window.__opened.length === 2")
    assert page.evaluate("window.__opened[1]") == "https://www.messenger.com/"
    assert page.evaluate("window.__copied[window.__copied.length - 1]") == expected
    assert not page.locator("#inviteHint").is_hidden()

    # ── after using a channel the app asks to confirm the send
    assert page.evaluate("inviteChannelUsed") == "messenger"
    mark = page.locator("#inviteSent button")
    assert mark.inner_text() == "Did it go out? Mark as sent ✓"
    assert "ask" in mark.get_attribute("class")

    # ── mark as sent: one overall record, right schema
    mark.click()
    page.wait_for_function("isInviteSent(inviteRow)")
    assert len(sent_patches()) == 1, family_mutations
    record = json.loads(sent_patches()[0][2])["invite_sent"]["overall"]
    assert record["via"] == "messenger"
    assert record["lang"] == "both"
    assert record["at"]
    assert record["church_only"] is False
    assert record["henna"] is True
    assert record["link"] == INVITE_LINK
    assert record["template"] == TEMPLATE_VERSION
    assert page.locator("#inviteSent .sent-done").is_visible()
    page.locator("#inviteClose").click()

    # ── D2: the sent row is now a green sent-card and carries the bigger badge
    assert "sent" in (henna_row.get_attribute("class") or "")
    assert henna_row.locator(".sent-badge").inner_text().startswith("✓ Sent")
    assert page.locator("#fSent").inner_text() == "✓ Sent 1"
    assert page.locator("#fUnsent").inner_text() == "◻︎ Not sent 2"

    page.evaluate("setFilter('sent')")
    assert page.locator("#fullList .fam").count() == 1
    assert "Henna Test Family" in page.locator("#fullList .fam").first.inner_text()
    page.evaluate("setFilter('all')")

    # ── undo clears the mark back out of the database
    henna_row.locator(".send-invite").click()
    page.wait_for_selector("#inviteSent .sent-done")
    page.locator("#inviteSent .sent-done button").click()
    page.wait_for_function("!isInviteSent(inviteRow)")
    assert len(sent_patches()) == 2, family_mutations
    assert json.loads(sent_patches()[1][2]) == {"invite_sent": {}}

    # ── a failed PATCH rolls the optimistic sent mark back
    state["fail_patch"] = True
    n = len(sent_patches())
    page.locator("#inviteSent button").click()
    deadline = time.monotonic() + 15
    while len(sent_patches()) < 3:
        assert time.monotonic() < deadline, family_mutations
        page.wait_for_timeout(50)
    page.wait_for_function("!isInviteSent(inviteRow)")
    assert len(sent_patches()) == 3, family_mutations
    page.locator("#inviteClose").click()
    assert "sent" not in (henna_row.get_attribute("class") or "")

    # ── church-only family: NOT invited to reception ⇒ Reception pill OFF, church-scoped link
    church_row.locator(".send-invite").click()
    page.wait_for_selector("#inviteChannels button")
    assert not on(page.locator("#inviteIncluded .inc-pill.reception"))
    church_msg = page.evaluate("inviteMessage(inviteRow)")
    assert church_msg.rstrip().endswith(CHURCH_LINK)
    assert "Reception" not in church_msg and "Henna" not in church_msg
    page.locator("#inviteClose").click()

    # ── no-phone family: WhatsApp blocked, copy still works
    nophone_row.locator(".send-invite").click()
    page.wait_for_selector("#inviteChannels button")
    assert page.locator("#inviteChannels button").filter(has_text="WhatsApp").is_disabled()
    copies_before = page.evaluate("window.__copied.length")
    page.locator("#inviteChannels button").filter(has_text="Copy message").click()
    page.wait_for_function(f"window.__copied.length === {copies_before + 1}")
    assert page.evaluate("inviteChannelUsed") == "copy"
    assert INVITE_LINK in page.evaluate("window.__copied[window.__copied.length - 1]")
    page.locator("#inviteClose").click()

    # ── Addendum: the edit flow writes church_only under the inverted "Reception?" label
    nophone_row.locator(".edit-btn").click()
    page.wait_for_function("!document.getElementById('editBg').hidden")
    # a reception-invited family defaults to Reception = Yes highlighted
    assert on(page.locator("#eChurchYes"))
    assert not on(page.locator("#eChurchNo"))
    n = len(family_mutations)
    page.locator("#eChurchNo").click()  # Reception No ⇒ church_only:true
    page.locator("#editSave").click()
    mut = wait_new_mutation(n)
    edit_body = json.loads(mut[2])
    assert edit_body.get("church_only") is True and "name" in edit_body, mut
    page.wait_for_function("document.getElementById('editBg').hidden")

    # ── D3: delete now lives inside the Edit modal and DELETEs via the API
    nophone_row.locator(".edit-btn").click()
    page.wait_for_function("!document.getElementById('editBg').hidden")
    assert page.locator("#editDelete").inner_text() == "Delete family"
    n = len([m for m in family_mutations if m[0] == "DELETE"])
    page.locator("#editDelete").click()
    deadline = time.monotonic() + 10
    while len([m for m in family_mutations if m[0] == "DELETE"]) <= n:
        assert time.monotonic() < deadline, family_mutations
        page.wait_for_timeout(30)
    delete_mut = [m for m in family_mutations if m[0] == "DELETE"][-1]
    assert "id=eq.no-phone-family" in delete_mut[1], delete_mut
    assert page.evaluate("document.getElementById('editBg').hidden") is True
    # the undo-toast delete path is intact (removeRow still offers an undo);
    # removeRow shows the toast only after the DELETE resolves, so wait for it
    page.wait_for_selector(".toast button")

    # sends only ever leave as user-initiated deep links, never background fetches
    assert not outbound_requests
    assert not image_requests
    assert not page_errors
    methods = [m[0] for m in family_mutations]
    assert set(methods) <= {"PATCH", "DELETE"}, methods
    assert methods.count("DELETE") == 1, methods
    assert len(sent_patches()) == 3, family_mutations
    toggle_patches = [
        m for m in family_mutations
        if m[0] == "PATCH" and set(json.loads(m[2] or "{}")) <= {"church_only", "event2"}
        and json.loads(m[2] or "{}")
    ]
    assert len(toggle_patches) == 4, toggle_patches
    edit_patches = [m for m in family_mutations if m[0] == "PATCH" and "name" in json.loads(m[2] or "{}")]
    assert len(edit_patches) == 1, edit_patches
    assert methods.count("PATCH") == 8, methods
    browser.close()
    print(
        f"PASS {browser_name}: v22 church-universal — every card shows ⛪ + 🏛(reception) + "
        "🌿(henna) chips; send-modal Reception/Henna pills PATCH church_only/event2 and "
        "recompose live under a 'church is universal' note; Reception OFF ⇒ ?cat= church link; "
        "edit flow writes church_only under the inverted Reception label; ‹ › family nav + N / M; "
        "✕ close; iMessage sms: via hidden iframe (top page never unloads); WhatsApp/Messenger/"
        "Copy wired; no-number badge + 📵 filter; green sent-cards; row delete lives in Edit; "
        "sent tracking round-trips and rolls back"
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
