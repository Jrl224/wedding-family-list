#!/usr/bin/env python3
"""Regression coverage for v23.1 invitation delivery (church-universal model).

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
Edit and writes church_only under the inverted "Reception?" label); the
single per-family sent record with rollback on a failed PATCH; bilingual
guest names — displayName() shows name_en in the English UI and name in the
Arabic UI, search matches either language, the Edit modal persists an optional
name_en, and the Excel export gains a "Name (English)" column; and the v22.1
label cleanup — no church-note text or ⛪ card chip, Reception/Henna are plain
checkboxes in add/edit and the send modal, and the send modal has a pinned
header (‹ N/M name › ✕) that stays visible while the body scrolls. Runs fully
against a mocked Supabase, so no live guest row is ever touched.
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
VERSION = "٢٣٫٢ · 23.2"
TEMPLATE_VERSION = "21.0"
ARABIC = re.compile(r"[؀-ۿ]")
FAMILIES = [
    {
        "id": "henna-family",
        "name": "عيلة الحنة",
        "name_en": "Henna Test Family",
        "phone": "+1 202-555-0123",
        "side": "bride",
        "count": 2,
        "event2": True,
        "church_only": False,
        "confirmed": True,
        "members": ["Shenouda", "Malak"],
        "added_by": "fixture",
        "invite_sent": {},
    },
    {
        "id": "church-family",
        "name": "عيلة الكنيسة",
        "name_en": "Church Test Family",
        "phone": "+20 100 123 4567",
        "side": "bride",
        "count": 2,
        "event2": False,
        "church_only": True,
        "confirmed": False,
        "parent_id": "henna-family",
        "added_by": "fixture",
        "invite_sent": {},
    },
    {
        "id": "no-phone-family",
        "name": "عيلة بدون رقم",
        "name_en": "No Phone Family",
        "phone": "",
        "side": "bride",
        "count": 3,
        "event2": False,
        "church_only": False,
        "confirmed": False,
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

    deleted = {}

    def mock_supabase(route):
        request = route.request
        if "wedding_families" in request.url and request.method != "GET":
            family_mutations.append((request.method, request.url, request.post_data))
            pbody = json.loads(request.post_data or "{}")
            if request.method == "PATCH" and isinstance(pbody, dict) and "deleted_at" in pbody:
                rid = request.url.split("id=eq.")[1].split("&")[0]
                deleted[rid] = pbody["deleted_at"]
            # v23.1: mirror live NOT NULL constraints — a null here rejected the whole payload in prod
            # (waitlist is NOT NULL DEFAULT false, migrated for v24; confirmed likewise)
            notnull = {"name", "count", "side", "event2", "church_only", "confirmed", "waitlist"}
            for row_ in (pbody if isinstance(pbody, list) else [pbody]):
                if isinstance(row_, dict) and any(row_.get(c) is None for c in notnull if c in row_):
                    route.fulfill(status=400, content_type="application/json", body='{"message":"null value violates not-null constraint"}')
                    return
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
            body = [dict(f, deleted_at=deleted.get(f["id"])) for f in FAMILIES]
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

    page.goto(base_url, wait_until="domcontentloaded")
    # v23.2: the Filters trigger lives inside orgView — contributors never see it
    assert page.locator("#filtersBtn").is_hidden()
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

    # ── D1: missing-number badge
    henna_row = page.locator("#fullList .fam[data-id='henna-family']")
    church_row = page.locator("#fullList .fam[data-id='church-family']")
    nophone_row = page.locator("#fullList .fam[data-id='no-phone-family']")
    assert henna_row.locator(".nophone-badge").count() == 0
    assert nophone_row.locator(".nophone-badge").inner_text() == "📵 No number"

    # ── v23.2: filter sheet replaces the 11-chip strip — trigger pill, hidden badge, bottom sheet.
    # The sheet itself starts closed (#filterBg hidden), so descendant text/visibility is read via
    # JS DOM properties below (innerText/is_hidden would report through the hidden ancestor).
    def sheet_prop(id_, prop):
        return page.evaluate(f"document.getElementById('{id_}').{prop}")
    assert page.locator("#filters").count() == 0  # old chip strip is gone
    assert page.locator("#filtersBtn").inner_text().strip() == "Filters"
    assert page.locator("#filtersBadge").is_hidden()  # no active filters ⇒ badge hidden
    assert sheet_prop("fsSideSec", "hidden") is False  # master role sees the Side segment
    assert sheet_prop("cntFNoPhone", "textContent") == "1"
    # zero-count checkbox rows are hidden — no wrong-number family exists yet
    assert sheet_prop("rowFWrong", "hidden") is True
    assert sheet_prop("cntFWrong", "textContent") == "0"
    # segment options always render even when a segment's count would read 0
    assert sheet_prop("segSideGroom", "hidden") is False

    # ── v23.2: checkbox rows stay AND-stacking on the same activeFilters Set
    page.evaluate("toggleFilterCb('nophone', true)")
    assert page.locator("#fullList .fam").count() == 1
    assert "No Phone Family" in page.locator("#fullList .fam").first.inner_text()
    assert not page.locator("#filtersBadge").is_hidden()
    assert page.locator("#filtersBadge").inner_text() == "· 1"
    assert page.locator("#filtersBtn").get_attribute("class") == "fs-trigger on"
    echo = page.locator("#filtersEcho .echo-chip")
    assert echo.count() == 1 and "No number" in echo.inner_text()
    page.evaluate("removeActiveFilter('nophone')")  # the mini-chip's own ✕ handler
    page.wait_for_function("document.querySelectorAll('#fullList .fam').length === 3")
    assert page.locator("#filtersBadge").is_hidden()

    # ── v23.2: exclusive pairs are segments now — FILTER_EXCLUSIVE is gone, the control
    # itself makes Sent+Not-sent / Bride+Groom unrepresentable; same activeFilters Set underneath
    page.evaluate("toggleFilterCb('ev2', true); setSegment(['sent','unsent'],'unsent')")  # henna AND not-sent
    assert page.evaluate("[...activeFilters].sort().join(',')") == "ev2,unsent"
    assert page.locator("#fullList .fam").count() == 1
    assert "Henna Test Family" in page.locator("#fullList .fam").first.inner_text()
    page.evaluate("setSegment(['sent','unsent'],'sent')")  # picking Sent clears Not-sent — same keys
    assert page.evaluate("activeFilters.has('unsent')") is False
    assert page.evaluate("activeFilters.has('sent')") is True
    assert sheet_prop("segInviteSent", "className") == "on"
    assert sheet_prop("segInviteNot", "className") == ""
    page.evaluate("setSegment(['bride','groom'],'bride'); setSegment(['bride','groom'],'groom')")  # Bride/Groom exclusive
    assert page.evaluate("activeFilters.has('bride')") is False
    assert page.evaluate("activeFilters.has('groom')") is True

    # ── v23.2: Trash is an exclusive control that clears AND disables every other row
    page.evaluate("toggleTrashFilter(true)")
    assert page.evaluate("[...activeFilters]") == ["deleted"]
    assert sheet_prop("cbFTrash", "checked") is True
    assert "fs-disabled" in sheet_prop("rowFReception", "className")
    assert sheet_prop("cbFReception", "disabled") is True
    assert sheet_prop("segSideBride", "disabled") is True
    page.evaluate("toggleTrashFilter(false)")
    assert page.evaluate("activeFilters.size") == 0
    assert sheet_prop("cbFReception", "disabled") is False

    # ── v23.2: Done shows the live result count; Clear does a full reset
    page.evaluate("toggleFilterCb('confirmed', true)")
    assert page.locator("#fullList .fam").count() == 1
    assert sheet_prop("filterDoneBtn", "textContent") == "Done · 1"
    page.evaluate("clearAllFilters()")
    assert page.evaluate("activeFilters.size") == 0
    page.wait_for_function("document.querySelectorAll('#fullList .fam').length === 3")
    assert page.locator("#filtersBadge").is_hidden()
    assert sheet_prop("filterDoneBtn", "textContent") == "Done · 3"

    # ── D3: no inline delete button remains in any row (delete moved into Edit)
    assert page.locator("#fullList .fam .del").count() == 0
    assert page.locator("#fullList .fam .edit-btn").count() == 3

    # ── Addendum: event chips — 🏛 iff invited to reception, 🌿 iff henna; NO ⛪ church chip
    # (church is universal and never shown on a card)
    def chips(row):
        return {c: row.locator(f".card-chip.{c}").count() for c in ("reception", "henna")}
    assert chips(henna_row) == {"reception": 1, "henna": 1}       # reception + henna
    assert chips(church_row) == {"reception": 0, "henna": 0}      # church only ⇒ no chips
    assert chips(nophone_row) == {"reception": 1, "henna": 0}     # reception, no henna
    assert page.locator("#fullList .card-chip.church").count() == 0
    # organizer chips carry text labels
    assert "Reception" in henna_row.locator(".card-chip.reception").inner_text()
    assert "Henna" in henna_row.locator(".card-chip.henna").inner_text()

    # ── v23 item 5: ✅ Confirmed chip shows only on a confirmed family
    assert henna_row.locator(".card-chip.confirmed").count() == 1
    assert church_row.locator(".card-chip.confirmed").count() == 0

    # ── v23 item 9: number-validity states (missing / wrong / ok) per country code
    assert page.evaluate("numberState({phone: ''})") == "missing"
    assert page.evaluate("numberState({phone: '+20 100 123 4567'})") == "ok"
    assert page.evaluate("numberState({phone: '+1 202-555-0123'})") == "ok"
    assert page.evaluate("numberState({phone: '+20 55 66'})") == "wrong"
    assert page.evaluate("numberState({phone: '+20 200 123 4567'})") == "wrong"  # EG mobile must start with 1
    # amber card + ⚠️ badge render for a wrong number (mutate church family, then restore)
    page.evaluate("all.find(r => r.id === 'church-family').phone = '+20 200 123 4567'; renderAll()")
    wrong_row = page.locator("#fullList .fam[data-id='church-family']")
    assert "wrong" in (wrong_row.get_attribute("class") or "")
    assert wrong_row.locator(".wrong-badge").count() == 1
    # ── v23.2: the Issues row appears once its live count leaves zero, and isolates wrong-number families
    assert sheet_prop("rowFWrong", "hidden") is False
    assert sheet_prop("cntFWrong", "textContent") == "1"
    page.evaluate("toggleFilterCb('wrong', true)")
    assert page.locator("#fullList .fam").count() == 1
    assert "Church Test Family" in page.locator("#fullList .fam").first.inner_text()
    page.evaluate("toggleFilterCb('wrong', false)")
    page.evaluate("all.find(r => r.id === 'church-family').phone = '+20 100 123 4567'; renderAll()")
    assert "wrong" not in (page.locator("#fullList .fam[data-id='church-family']").get_attribute("class") or "")
    assert sheet_prop("rowFWrong", "hidden") is True  # count back to zero ⇒ hidden again

    # ── v23 item 8: henna headcount helpers + card chip number when overridden
    assert page.evaluate("hennaSeats({count: 5, seats: {henna: 3}})") == 3
    assert page.evaluate("hennaSeats({count: 5})") == 5
    assert page.evaluate("hennaOverridden({event2: true, count: 5, seats: {henna: 3}})") is True
    assert page.evaluate("hennaOverridden({event2: true, count: 5, seats: {henna: 5}})") is False
    page.evaluate("all.find(r => r.id === 'henna-family').seats = {henna: 1}; renderAll()")
    assert "🌿 1" in henna_row.locator(".card-chip.henna").inner_text()
    page.evaluate("all.find(r => r.id === 'henna-family').seats = null; renderAll()")
    assert "🌿 1" not in henna_row.locator(".card-chip.henna").inner_text()

    # ── v23.2: relationship tree — parent shows a 👪 rollup chip, child shows a ↳ breadcrumb chip
    assert henna_row.locator(".card-chip.rollup").count() == 1        # henna is Church's parent
    assert "👪" in henna_row.locator(".card-chip.rollup").inner_text()
    assert church_row.locator(".card-chip.crumb").count() == 1        # church is inside henna
    assert "Henna Test Family" in church_row.locator(".card-chip.crumb").inner_text()
    assert "↳" in church_row.locator(".card-chip.crumb").inner_text()  # LTR glyph in the EN UI
    assert nophone_row.locator(".card-chip.rollup").count() == 0      # root, no children
    assert nophone_row.locator(".card-chip.crumb").count() == 0
    # Tree view: a parent exists → the toggle is available; children render indented under the root
    assert not page.locator("#sortTree").is_hidden()
    page.evaluate("toggleTreeView()")
    page.wait_for_function("() => { const c = document.querySelector('#fullList .fam[data-id=\"church-family\"]'); return c && c.style.marginInlineStart && c.style.marginInlineStart !== '0px'; }")
    # in the tree, the child renders immediately after its parent; the unlinked (childless, parentless)
    # no-phone family is not part of any tree and renders flat, below the tree — not interleaved A–Z with it
    order = page.evaluate("() => [...document.querySelectorAll('#fullList .fam')].map(f => f.dataset.id)")
    assert order.index("church-family") == order.index("henna-family") + 1, order
    assert order.index("no-phone-family") > order.index("church-family"), order
    no_phone_card = page.locator("#fullList .fam[data-id='no-phone-family']")
    assert not no_phone_card.get_attribute("style") or "margin-inline-start" not in (no_phone_card.get_attribute("style") or "")
    page.evaluate("toggleTreeView()")
    page.wait_for_function("() => { const c = document.querySelector('#fullList .fam[data-id=\"church-family\"]'); return !c.style.marginInlineStart || c.style.marginInlineStart === '0px'; }")

    # ── v23.2: a trashed parent leaves its child at root with a GREYED breadcrumb; Restore reassembles
    page.evaluate("all.find(r => r.id === 'henna-family').deleted_at = new Date().toISOString(); renderAll()")
    crumb = page.locator("#fullList .fam[data-id='church-family'] .card-chip.crumb")
    assert crumb.count() == 1                                      # breadcrumb retained (not dropped)
    assert "trashed" in (crumb.get_attribute("class") or "")      # greyed
    assert page.locator("#fullList .fam[data-id='church-family']").count() == 1  # child still visible at root
    page.evaluate("all.find(r => r.id === 'henna-family').deleted_at = null; renderAll()")
    crumb2 = page.locator("#fullList .fam[data-id='church-family'] .card-chip.crumb")
    assert crumb2.count() == 1 and "trashed" not in (crumb2.get_attribute("class") or "")  # reassembled — live

    # ── v23.2: filtering never severs trees — a matching child surfaces flat with its
    # breadcrumb chip even when its root doesn't match the active filter (church_only
    # matches only church-family; its parent henna-family is not church_only)
    page.evaluate("toggleFilterCb('church', true)")
    page.wait_for_function("document.querySelectorAll('#fullList .fam').length === 1")
    severed_child = page.locator("#fullList .fam[data-id='church-family']")
    assert severed_child.count() == 1
    assert severed_child.locator(".card-chip.crumb").count() == 1
    assert "Henna Test Family" in severed_child.locator(".card-chip.crumb").inner_text()
    page.evaluate("toggleFilterCb('church', false)")
    page.wait_for_function("document.querySelectorAll('#fullList .fam').length === 3")

    # ── v23: cards are READ-ONLY indicators — no +/− count buttons, no 🌿/🏛 quick-toggles;
    # only ✏️ Edit and 📨 Send invite are interactive. Count renders as plain text.
    assert henna_row.locator(".cnt button").count() == 0
    assert henna_row.locator(".cot").count() == 0
    assert henna_row.locator(".ev2t").count() == 0
    assert henna_row.locator(".cnt").inner_text().strip() == "2"
    assert henna_row.locator(".edit-btn").count() == 1
    assert henna_row.locator(".send-invite").count() == 1

    # ── v23: missing-number card is red (.nophone); a sent family stays green (.sent wins)
    assert "nophone" in (nophone_row.get_attribute("class") or "")
    assert "nophone" not in (henna_row.get_attribute("class") or "")

    # ── v23: default sort (📵 first) floats missing-number families to the top
    assert page.locator("#sortNoPhone").count() == 1
    assert "No Phone Family" in page.locator("#fullList .fam").first.inner_text()
    # A–Z sort orders by displayName; the no-phone family is no longer forced first
    page.evaluate("setSort('az')")
    az_names = page.evaluate("() => [...document.querySelectorAll('#fullList .fam .info b')].map(b => b.textContent)")
    assert az_names == sorted(az_names, key=str.lower), az_names
    page.evaluate("setSort('nophone')")
    assert "No Phone Family" in page.locator("#fullList .fam").first.inner_text()

    # ── Addendum #2: bilingual names — displayName follows the UI language toggle
    assert page.evaluate("displayName(all.find(r => r.id === 'henna-family'))") == "Henna Test Family"
    assert "Henna Test Family" in henna_row.inner_text()  # EN UI shows name_en
    # flip the UI to Arabic → rows show the Arabic name
    page.evaluate("toggleLang(); renderAll(); renderMine()")
    assert page.evaluate("L") == "ar"
    assert page.evaluate("displayName(all.find(r => r.id === 'henna-family'))") == "عيلة الحنة"
    assert "عيلة الحنة" in page.locator("#fullList .fam[data-id='henna-family'] b").first.inner_text()
    # v23.2: the breadcrumb arrow isn't Unicode bidi-mirrored (U+21B3) — the app swaps the glyph
    # itself so it still points toward reading-start under RTL
    assert "↲" in page.locator("#fullList .fam[data-id='church-family'] .card-chip.crumb").inner_text()
    # search by ENGLISH name while the UI is Arabic still finds the family
    page.fill("#search", "Henna Test")
    page.wait_for_function("document.querySelectorAll('#fullList .fam').length === 1")
    assert "عيلة الحنة" in page.locator("#fullList .fam").first.inner_text()
    page.fill("#search", "")
    page.wait_for_function("document.querySelectorAll('#fullList .fam').length === 3")
    # flip back to English
    page.evaluate("toggleLang(); renderAll(); renderMine()")
    assert page.evaluate("L") == "en"
    # search by ARABIC name while the UI is English still finds the family
    page.fill("#search", "عيلة الكنيسة")
    page.wait_for_function("document.querySelectorAll('#fullList .fam').length === 1")
    assert "Church Test Family" in page.locator("#fullList .fam").first.inner_text()
    page.fill("#search", "")
    page.wait_for_function("document.querySelectorAll('#fullList .fam').length === 3")

    # ── D6 + D7: send modal has a ✕ close, ‹ › nav, and an N / M indicator
    page.locator("#fullList .fam").first.locator(".send-invite").click()
    page.wait_for_selector("#inviteChannels button")
    assert page.locator("#inviteX").get_attribute("aria-label") == "Close"
    assert page.locator("#invitePrev").count() == 1 and page.locator("#inviteNext").count() == 1
    # nav follows the current rendered order (default 📵-first sort ⇒ no-phone family first)
    nav_ids = page.evaluate("inviteNav")
    assert len(nav_ids) == 3 and set(nav_ids) == {f["id"] for f in FAMILIES}
    assert nav_ids[0] == "no-phone-family"
    assert page.evaluate("inviteRow.id") == nav_ids[0]
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
    # the recipient line uses displayName (English name in the EN UI)
    assert "Henna Test Family" in page.locator("#inviteRecipient").inner_text()
    # ── v23 item 2: member names show under the family name in the send modal
    assert not page.locator("#inviteMembers").is_hidden()
    assert "Shenouda" in page.locator("#inviteMembers").inner_text()
    assert "Malak" in page.locator("#inviteMembers").inner_text()
    assert page.locator("#inviteChannels button").count() == 4
    # language chooser is still gone; one bilingual message, dir=auto
    assert page.locator("#invite" + "Langs").count() == 0
    assert page.locator("#inviteMsg").get_attribute("dir") == "auto"

    # ── Addendum: the church-note text is GONE (church never stated in UI)
    assert page.locator("#inviteChurchNote").count() == 0

    # ── Addendum: pinned header stays visible while the modal body scrolls
    assert page.locator("#inviteHeader #invitePrev").count() == 1
    assert page.locator("#inviteHeader #inviteNext").count() == 1
    assert page.locator("#inviteHeader #invitePos").count() == 1
    assert page.locator("#inviteHeader #inviteX").count() == 1
    assert "Henna Test Family" in page.locator("#inviteHeader").inner_text()
    assert "/" in page.locator("#inviteHeader #invitePos").inner_text()
    page.evaluate("document.getElementById('inviteBody').scrollTop = 9999")
    header_pinned = page.evaluate(
        "() => {"
        " const m = document.querySelector('#inviteBg .modal').getBoundingClientRect();"
        " const h = document.getElementById('inviteHeader').getBoundingClientRect();"
        " return h.height > 0 && h.top >= m.top - 1 && h.bottom <= m.bottom + 1;"
        "}"
    )
    assert header_pinned

    # ── D5 (addendum): Reception + Henna are checkbox rows; Reception unchecked ⇒
    # church_only:true ⇒ church-only message + ?cat= link (same PATCH behaviour)
    henna_channel_msg = page.evaluate("inviteMessage(inviteRow)")
    assert henna_channel_msg.rstrip().endswith(INVITE_LINK)
    reception_box = page.locator("#inviteIncluded .check-row.reception input")
    henna_box = page.locator("#inviteIncluded .check-row.henna input")
    # henna family is invited to the reception and to henna → both boxes checked
    assert reception_box.is_checked()
    assert henna_box.is_checked()

    n = len(family_mutations)
    reception_box.click()
    mut = wait_new_mutation(n)
    assert mut[0] == "PATCH" and json.loads(mut[2]) == {"church_only": True}, mut
    page.wait_for_function("inviteMessage(inviteRow).includes('?cat=')")
    assert "?cat=" in page.locator("#inviteMsg").inner_text()
    assert not page.locator("#inviteIncluded .check-row.reception input").is_checked()

    n = len(family_mutations)
    page.locator("#inviteIncluded .check-row.reception input").click()
    mut = wait_new_mutation(n)
    assert json.loads(mut[2]) == {"church_only": False}, mut
    page.wait_for_function("!inviteMessage(inviteRow).includes('?cat=')")
    assert "?cat=" not in page.locator("#inviteMsg").inner_text()
    assert page.locator("#inviteIncluded .check-row.reception input").is_checked()

    # ── D5: Henna checkbox PATCHes event2 and folds the henna block in/out live
    n = len(family_mutations)
    page.locator("#inviteIncluded .check-row.henna input").click()
    mut = wait_new_mutation(n)
    assert json.loads(mut[2]) == {"event2": False}, mut
    page.wait_for_function("!inviteMessage(inviteRow).includes('Henna Party')")
    assert "Henna Party" not in page.locator("#inviteMsg").inner_text()

    n = len(family_mutations)
    page.locator("#inviteIncluded .check-row.henna input").click()
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
    # v23.2: Invite is a segment now (Sent/Not-sent have no per-option count, only checkbox rows do)
    assert page.evaluate("all.filter(FILTER_PREDS.sent).length") == 1
    assert page.evaluate("all.filter(FILTER_PREDS.unsent).length") == 2

    page.evaluate("setSegment(['sent','unsent'],'sent')")
    assert page.locator("#segInviteSent").get_attribute("class") == "on"
    assert page.locator("#fullList .fam").count() == 1
    assert "Henna Test Family" in page.locator("#fullList .fam").first.inner_text()
    page.evaluate("setSegment(['sent','unsent'],'')")
    assert page.locator("#segInviteAll").get_attribute("class") == "on"
    page.wait_for_function("document.querySelectorAll('#fullList .fam').length === 3")

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
    assert not page.locator("#inviteIncluded .check-row.reception input").is_checked()
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

    # ── Addendum: the edit flow writes church_only under the inverted "Reception?"
    # label AND persists the optional English name (name_en) in the same PATCH
    nophone_row.locator(".edit-btn").click()
    page.wait_for_function("!document.getElementById('editBg').hidden")
    # the English-name field is prefilled from name_en
    assert page.locator("#eNameEn").input_value() == "No Phone Family"
    # a reception-invited family defaults to the Reception checkbox checked
    assert page.locator("#eRecChk").is_checked()
    assert not page.locator("#eConfirmedChk").is_checked()  # no-phone fixture is unconfirmed
    page.fill("#eNameEn", "No Phone Family EN")
    page.locator("#eConfirmedChk").check()  # v23: mark confirmed
    # ── v23.2 relationships + v23 members save in the same Edit PATCH
    page.evaluate("document.getElementById('eMore').open = true")
    # parent picker never lists self (cycle safety layer 1)
    parent_opts = page.evaluate("() => [...document.querySelectorAll('#eParent option')].map(o => o.value)")
    assert "no-phone-family" not in parent_opts, parent_opts   # excludes self
    assert "henna-family" in parent_opts                       # other families offered
    page.select_option("#eParent", "henna-family")             # file no-phone under henna
    assert "No Phone Family" in page.locator("#eMembers .member-contact").inner_text()  # contact = person #1
    member_inputs = page.locator("#eMembers input.member-in")
    assert member_inputs.count() == 2  # count - 1 additional-member inputs (contact is #1)
    member_inputs.nth(0).fill("Mina")
    n = len(family_mutations)
    page.locator("#eRecChk").uncheck()  # not invited to reception ⇒ church_only:true
    page.locator("#editSave").click()
    mut = wait_new_mutation(n)
    edit_body = json.loads(mut[2])
    assert edit_body.get("church_only") is True and "name" in edit_body, mut
    assert edit_body.get("name_en") == "No Phone Family EN", mut
    assert edit_body.get("confirmed") is True, mut
    assert edit_body.get("parent_id") == "henna-family", mut
    assert edit_body.get("members") and edit_body["members"][0] == "Mina", mut
    page.wait_for_function("document.getElementById('editBg').hidden")

    # ── v23.2 cycle safety: a parent's picker excludes its own descendants (layer 1)
    henna_row.locator(".edit-btn").click()
    page.wait_for_function("!document.getElementById('editBg').hidden")
    page.evaluate("document.getElementById('eMore').open = true")
    henna_opts = page.evaluate("() => [...document.querySelectorAll('#eParent option')].map(o => o.value)")
    assert "henna-family" not in henna_opts and "church-family" not in henna_opts, henna_opts  # self + descendant excluded
    page.locator("#editCancel").click()
    page.wait_for_function("document.getElementById('editBg').hidden")

    # ── v23.2 acceptance #9: contributor PIN edits never show the parent picker (mineWrap's own-family
    # edit button re-uses this same #editBg modal, so relationships must stay organizer-only there)
    page.evaluate("localStorage.removeItem('wedOrg')")
    page.evaluate("openEdit(all.find(r => r.id === 'no-phone-family'))")
    page.wait_for_function("!document.getElementById('editBg').hidden")
    assert page.locator("#eParentLabel").is_hidden()
    assert page.locator("#eParent").is_hidden()
    page.locator("#editCancel").click()
    page.wait_for_function("document.getElementById('editBg').hidden")
    page.evaluate("localStorage.setItem('wedOrg', 'master')")  # restore organizer context

    # ── v23 item 8: the Edit henna stepper writes seats {henna:n} in the PATCH
    henna_row.locator(".edit-btn").click()
    page.wait_for_function("!document.getElementById('editBg').hidden")
    assert page.locator("#eEv2Chk").is_checked()               # henna family
    assert not page.locator("#eHennaSeatsRow").is_hidden()     # stepper visible when henna on
    n = len(family_mutations)
    page.locator("#eHennaSeatsRow button").first.click()       # − : 2 → 1 (override)
    assert page.locator("#eHennaSeats").inner_text() == "1"
    # v23.1: contact is person #1 → count-1 member inputs; the over-length fixture array trims
    assert "Henna Test Family" in page.locator("#eMembers .member-contact").inner_text()
    assert page.locator("#eMembers input.member-in").count() == 1  # count 2 ⇒ 1 additional slot
    page.locator("#editSave").click()
    mut = wait_new_mutation(n)
    assert json.loads(mut[2]).get("seats") == {"henna": 1}, mut
    assert len(json.loads(mut[2]).get("members") or []) == 1, mut  # 2-member fixture trimmed to count-1
    page.wait_for_function("document.getElementById('editBg').hidden")

    # ── v23.1 HOTFIX: a full default-family save round-trips (the seats:null NOT-NULL bug fix).
    # The mock now 400s any null into a NOT NULL column; a plain save must be accepted (success toast).
    church_row.locator(".edit-btn").click()
    page.wait_for_function("!document.getElementById('editBg').hidden")
    n = len(family_mutations)
    page.locator("#editSave").click()
    mut = wait_new_mutation(n)
    rt = json.loads(mut[2])
    assert not any(rt.get(c) is None for c in ("name", "count", "event2", "church_only", "confirmed")), rt
    page.wait_for_selector(".toast:has-text('Updated')")  # success — payload accepted, not rejected

    # ── v23 item 6: Delete in Edit SOFT-deletes via PATCH deleted_at — no API DELETE
    nophone_row.locator(".edit-btn").click()
    page.wait_for_function("!document.getElementById('editBg').hidden")
    assert page.locator("#editDelete").inner_text() == "Delete family"
    n = len(family_mutations)
    page.locator("#editDelete").click()
    mut = wait_new_mutation(n)
    assert mut[0] == "PATCH" and json.loads(mut[2]).get("deleted_at"), mut
    assert "id=eq.no-phone-family" in mut[1], mut
    assert page.evaluate("document.getElementById('editBg').hidden") is True
    # the row leaves the default list once the soft delete round-trips
    page.wait_for_function("document.querySelectorAll('#fullList .fam').length === 2")
    assert page.locator("#fullList .fam[data-id='no-phone-family']").count() == 0
    # ── v23 item 6: 🗑 Trash shows the deleted family dimmed with a Restore button (no Edit/Send)
    page.evaluate("toggleTrashFilter(true)")
    page.wait_for_function("document.querySelectorAll('#fullList .fam').length === 1")
    del_card = page.locator("#fullList .fam").first
    assert "deleted" in (del_card.get_attribute("class") or "")
    assert del_card.locator(".restore-btn").count() == 1
    assert del_card.locator(".edit-btn").count() == 0
    assert del_card.locator(".send-invite").count() == 0
    # Restore → PATCH deleted_at:null → family returns intact
    n = len(family_mutations)
    del_card.locator(".restore-btn").click()
    mut = wait_new_mutation(n)
    assert mut[0] == "PATCH" and json.loads(mut[2]) == {"deleted_at": None}, mut
    page.evaluate("toggleTrashFilter(false)")  # leave the 🗑 view
    page.wait_for_function("document.querySelectorAll('#fullList .fam').length === 3")

    # ── Excel export: "Name (English)" + "Confirmed" columns + henna override "YES (n)"
    page.evaluate("all.find(r => r.id === 'henna-family').seats = {henna: 1}; renderAll()")
    with page.expect_download() as dl:
        page.evaluate("exportExcel()")
    export_text = Path(dl.value.path()).read_text(encoding="utf-8")
    header_line = export_text.splitlines()[0]
    assert '"Name (الاسم)","Name (English)"' in header_line, header_line
    assert "Confirmed" in header_line, header_line
    assert "Family" in header_line and "Members" in header_line, header_line  # v23.2: Family path column
    assert "Henna Test Family" in export_text  # a name_en value made it into the export
    assert "Henna Test Family › Church Test Family" in export_text  # v23.2: full family path
    assert "Shenouda" in export_text  # members exported
    assert "YES (1)" in export_text  # henna override renders the seat count
    page.evaluate("all.find(r => r.id === 'henna-family').seats = null; renderAll()")

    # sends only ever leave as user-initiated deep links, never background fetches
    assert not outbound_requests
    assert not image_requests
    assert not page_errors
    methods = [m[0] for m in family_mutations]
    # v23: nothing is ever hard-deleted — the app must never issue an API DELETE
    assert "DELETE" not in methods, methods
    assert set(methods) == {"PATCH"}, methods
    assert len(sent_patches()) == 3, family_mutations
    toggle_patches = [
        m for m in family_mutations
        if m[0] == "PATCH" and set(json.loads(m[2] or "{}")) <= {"church_only", "event2"}
        and json.loads(m[2] or "{}")
    ]
    assert len(toggle_patches) == 4, toggle_patches
    edit_patches = [m for m in family_mutations if m[0] == "PATCH" and "name" in json.loads(m[2] or "{}")]
    assert len(edit_patches) == 3, edit_patches  # no-phone edit + henna seats edit + church round-trip
    delete_patches = [m for m in family_mutations if m[0] == "PATCH" and "deleted_at" in json.loads(m[2] or "{}")]
    assert len(delete_patches) == 2, delete_patches  # soft-delete + restore
    assert methods.count("PATCH") == 12, methods

    # ── v23.1: the mock mirrors live column constraints EXACTLY (asserted last — these direct
    # probes intentionally add to family_mutations, so they run after the counts above).
    st_wait = page.evaluate("async () => (await fetch(SUPA + '?id=eq.henna-family', {method:'PATCH', headers:H, body: JSON.stringify({waitlist: null})})).status")
    assert st_wait == 400, st_wait   # waitlist NOT NULL → rejected
    st_conf = page.evaluate("async () => (await fetch(SUPA + '?id=eq.henna-family', {method:'PATCH', headers:H, body: JSON.stringify({confirmed: null})})).status")
    assert st_conf == 400, st_conf   # confirmed NOT NULL → rejected
    st_null = page.evaluate("async () => (await fetch(SUPA + '?id=eq.henna-family', {method:'PATCH', headers:H, body: JSON.stringify({seats:null, members:null, tags:null, group_name:null, name_en:null, parent_id:null})})).status")
    assert st_null == 200, st_null   # nullable columns accept null both ways
    browser.close()
    print(
        f"PASS {browser_name}: v23.2 — relationships (parent_id self-FK): native parent picker in "
        "Edit's More options replaces group_name, cycle safety (self+descendants excluded from the "
        "picker, save-time ancestor-walk toast, corrupt-cycle-safe renderer), breadcrumb + rollup "
        "chips (RTL-correct arrow glyph swap), Tree sort (trees A–Z, unlinked families flat below, "
        "trashed-parent orphans stay visible + Restore reassembles), breadcrumb search, CSV family-path "
        "column, mom's wizard loses the group field entirely. Filter sheet replaces the 11-chip strip: "
        "Filters trigger pill + badge, segments for Side/Invite (FILTER_EXCLUSIVE retired), checkboxes "
        "for Events/Status/Issues with zero-count rows hidden, Trash exclusive (clears+disables the "
        "rest), live Done-count + Clear, removable mini-chip echo, filtering never severs trees. Plus "
        "v23.1 HOTFIX: default-family Edit save round-trips (mock 400s null→NOT NULL, guarding the "
        "seats bug); members = contact is person #1 (count-1 inputs, contact line, over-length arrays "
        "trimmed, send-modal/export dedupe). Plus v23: read-only cards (count text, only Send+Edit); "
        "red 📵 + amber ⚠️ + green sent precedence, 📵-first/A–Z/last-name sort; ✅ Confirmed; soft "
        "delete via deleted_at (no API DELETE) + 🗑 dimmed Restore; henna headcount stepper → seats "
        "{henna:n}, card 🌿 n, export YES (n); member names (Edit inputs, send-modal, export); all on "
        "top of v22.1 bilingual names + church-universal model"
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
