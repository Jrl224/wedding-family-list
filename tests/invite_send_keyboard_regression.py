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
header (‹ N/M name › ✕) that stays visible while the body scrolls. Also covers
v24 (tags + waitlist): freeform master-only tags with Arabic-fold matchKey
dedupe (typo/case/hamza variants toggle the existing tag, never a duplicate),
zero-use tags vanishing from the picker/filter row, a master-only ⏳ Waitlist
toggle that is exclusive like 🗑 Trash and invisible to every cap/headcount
and filter but its own (and to side-role org views entirely), unsendable
waitlisted cards (dimmed, no Send button, openInvite a hard no-op), the
promote/demote two-way lock against invite_sent, a tag-filter row inside the
v23.2 filter sheet that ANDs with the existing controls (Dad's queue = one
tag + Not sent → one forwardable WhatsApp snapshot sourced from exactly the
filtered rows on screen), and the Tags/Waitlist Excel export columns. Runs
fully against a mocked Supabase, so no live guest row is ever touched.
"""

import argparse
import csv
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
VERSION = "٢٦٫٠ · 26.0"
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
        "tags": None,
        "waitlist": False,
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
        "tags": None,
        "waitlist": False,
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
        "tags": None,
        "waitlist": False,
    },
]

# v26: wedding_events seed — mirrors the live migration verbatim (church universal/uncapped,
# reception cap_total 400 = 200 bride + derived groom, henna cap_total 75 with a null bride_alloc
# ⇒ 38/37 50-50 split). Legacy family columns stay authoritative for membership; this table only
# adds capacity/venue/schedule metadata + capacity math on top.
EVENTS_SEED = [
    {
        "id": "church", "sort": 0, "emoji": "⛪", "name_ar": "الكنيسة", "name_en": "Church",
        "event_date": "2026-08-22", "time_start": "17:00:00", "time_end": None,
        "venue_ar": "كنيسة السيدة العذراء بالرحاب", "venue_en": "St. Mary Church – El Rehab",
        "address_ar": None, "address_en": None, "map_url": None,
        "cap_total": None, "bride_alloc": None, "universal": True, "core": True, "active": True,
        "meal_style": None, "meal_options": [], "extras": {},
    },
    {
        "id": "reception", "sort": 1, "emoji": "💍", "name_ar": "حفل الزفاف", "name_en": "Reception",
        "event_date": "2026-08-22", "time_start": "19:00:00", "time_end": None,
        "venue_ar": "فندق الماسة – مدينة نصر", "venue_en": "Al Masa Hotel – Nasr City",
        "address_ar": None, "address_en": None, "map_url": None,
        "cap_total": 400, "bride_alloc": 200, "universal": False, "core": True, "active": True,
        "meal_style": None, "meal_options": [], "extras": {},
    },
    {
        "id": "henna", "sort": 2, "emoji": "🌿", "name_ar": "حفلة الحنة", "name_en": "Henna Party",
        "event_date": "2026-08-14", "time_start": "20:00:00", "time_end": "00:00:00",
        "venue_ar": "دار الدفاع الجوي – مدينة نصر", "venue_en": "Dar Air Defense – Nasr City",
        "address_ar": None, "address_en": None, "map_url": None,
        "cap_total": 75, "bride_alloc": None, "universal": False, "core": True, "active": True,
        "meal_style": None, "meal_options": [], "extras": {},
    },
]
EVENTS_NOTNULL = {"name_ar", "name_en", "universal", "core", "active", "sort", "meal_options", "extras"}


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
    patched = {}
    event_mutations = []
    event_patched = {}
    event_inserted = []
    family_gets = []

    def mock_supabase(route):
        request = route.request
        # v26: wedding_events — same live-constraint enforcement pattern as wedding_families below
        # (400 on null into a NOT NULL column), kept as its own table/mutation log since it's a
        # separate resource with its own id space (church/reception/henna + ev_xxxxxx customs).
        if "wedding_events" in request.url and request.method != "GET":
            event_mutations.append((request.method, request.url, request.post_data))
            ebody = json.loads(request.post_data or "{}")
            erows = ebody if isinstance(ebody, list) else [ebody]
            for row_ in erows:
                if isinstance(row_, dict) and any(row_.get(c) is None for c in EVENTS_NOTNULL if c in row_):
                    route.fulfill(status=400, content_type="application/json", body='{"message":"null value violates not-null constraint"}')
                    return
            eid = request.url.split("id=eq.")[1].split("&")[0] if "id=eq." in request.url else None
            if request.method == "PATCH" and eid:
                event_patched.setdefault(eid, {}).update(ebody if isinstance(ebody, dict) else {})
            elif request.method == "POST":
                event_inserted.extend(r for r in erows if isinstance(r, dict))
            route.fulfill(status=200, content_type="application/json", body=json.dumps(erows))
            return
        if "wedding_families" in request.url and request.method != "GET":
            family_mutations.append((request.method, request.url, request.post_data))
            pbody = json.loads(request.post_data or "{}")
            rid = request.url.split("id=eq.")[1].split("&")[0] if "id=eq." in request.url else None
            if request.method == "PATCH" and isinstance(pbody, dict) and "deleted_at" in pbody:
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
            # v24: persist accepted PATCHes so a subsequent GET (including the periodic org
            # auto-refresh) reflects them — mirrors real PostgREST instead of silently reverting
            # fields the mock previously left untracked (deleted_at was the only one before v24).
            if request.method == "PATCH" and isinstance(pbody, dict) and rid:
                patched.setdefault(rid, {}).update(pbody)
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
            family_gets.append(request.url)
            side_m = re.search(r"side=eq\.(\w+)", request.url)
            src = [f for f in FAMILIES if not side_m or f["side"] == side_m.group(1)]
            body = [
                dict(f, **{k: v for k, v in patched.get(f["id"], {}).items() if k != "deleted_at"}, deleted_at=deleted.get(f["id"]))
                for f in src
            ]
        elif "wedding_events" in request.url and request.method == "GET":
            body = [dict(e, **event_patched.get(e["id"], {})) for e in EVENTS_SEED]
            body += [dict(e, **event_patched.get(e["id"], {})) for e in event_inserted]
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    page.route("**/*.supabase.co/**", mock_supabase)

    def wait_new_mutation(n_before, timeout=10):
        deadline = time.monotonic() + timeout
        while len(family_mutations) <= n_before:
            assert time.monotonic() < deadline, family_mutations
            page.wait_for_timeout(30)
        return family_mutations[-1]

    def wait_new_event_mutation(n_before, timeout=10):
        deadline = time.monotonic() + timeout
        while len(event_mutations) <= n_before:
            assert time.monotonic() < deadline, event_mutations
            page.wait_for_timeout(30)
        return event_mutations[-1]

    def sent_patches():
        return [m for m in family_mutations if m[0] == "PATCH" and "invite_sent" in (m[2] or "")]

    def wait_new_family_get(n_before, timeout=10):
        deadline = time.monotonic() + timeout
        while len(family_gets) <= n_before:
            assert time.monotonic() < deadline, family_gets
            page.wait_for_timeout(30)
        return family_gets[-1]

    page.goto(base_url, wait_until="domcontentloaded")
    # v25: #add is the default landing screen for every role; the organizer #list screen
    # (and its Filters trigger) is unreachable until navigated to explicitly via the router
    assert page.evaluate("location.hash") == "#add"
    assert page.locator("#scrList").is_hidden()
    assert page.locator("#filtersBtn").is_hidden()
    page.evaluate("location.hash = '#list'")
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
    # v25: the hash router means page.url carries a #screen fragment now — compare paths only
    assert page.url.split("#")[0].rstrip("/") == base_url.rstrip("/")
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

    # ══════════════════════════ v24: Tags + Waitlist ══════════════════════════
    # The 4-second org auto-refresh would otherwise race the assertions below (refreshAll()
    # replaces `all` wholesale from the mock's GET; anything not PATCHed to the mock server —
    # e.g. probe rows injected below via all.push — would be silently wiped mid-test).
    page.evaluate("if (orgTimer) { clearInterval(orgTimer); orgTimer = null; }")

    # ── widened fetch: refreshSideTotals now selects waitlist (refreshAll's select=* already
    # covers tags+waitlist trivially — confirmed by the fixture round-trip below)
    assert "waitlist" in page.evaluate("refreshSideTotals.toString()")
    assert page.evaluate("all.find(r => r.id === 'church-family').hasOwnProperty('tags')")
    assert page.evaluate("all.find(r => r.id === 'church-family').hasOwnProperty('waitlist')")

    # ── acceptance #1: one-screen add payload carries neither tags nor waitlist
    page.evaluate("location.hash = '#add'")
    page.wait_for_function("!document.getElementById('scrAdd').hidden")
    n = len(family_mutations)
    page.fill("#addName", "عيلة اختبار الوسم")
    page.evaluate("saveFamilyOneScreen()")
    mut = wait_new_mutation(n)
    assert mut[0] == "POST", mut
    posted = json.loads(mut[2])
    posted_row = posted[0] if isinstance(posted, list) else posted
    assert "tags" not in posted_row and "waitlist" not in posted_row, posted_row

    # back to #list so renderAll()/refreshAll() (gated on the #list screen being active) keep
    # #fullList live for the rest of this suite
    page.evaluate("location.hash = '#list'")
    page.wait_for_function("!document.getElementById('scrList').hidden")

    # ── acceptance #1 (cont'd): master-only — hidden for the guest/contributor context and for
    # side-role orgs; mom's flows and side-org Edit are otherwise byte-identical
    page.evaluate("localStorage.removeItem('wedOrg')")
    page.evaluate("openEdit(all.find(r => r.id === 'church-family'))")
    page.wait_for_function("!document.getElementById('editBg').hidden")
    assert page.locator("#eTagsSec").is_hidden()
    assert page.locator("#eWaitlistSec").is_hidden()
    page.locator("#editCancel").click()
    page.wait_for_function("document.getElementById('editBg').hidden")

    page.evaluate("localStorage.setItem('wedOrg', 'bride')")
    page.evaluate("refreshAll()")
    page.wait_for_function("all.length >= 3")
    assert page.locator("#fsTagsSec").is_hidden()
    assert page.locator("#rowFWaitlist").is_hidden()
    page.evaluate("openEdit(all.find(r => r.id === 'church-family'))")
    page.wait_for_function("!document.getElementById('editBg').hidden")
    assert page.locator("#eTagsSec").is_hidden()
    assert page.locator("#eWaitlistSec").is_hidden()
    page.locator("#editCancel").click()
    page.wait_for_function("document.getElementById('editBg').hidden")
    page.evaluate("localStorage.setItem('wedOrg', 'master')")
    page.evaluate("refreshAll()")
    page.wait_for_function("document.querySelectorAll('#fullList .fam').length === 3")

    # ── acceptance #2: tags CRUD via Edit; normalize-on-commit is NFC → trim → collapse whitespace
    page.evaluate("openEdit(all.find(r => r.id === 'no-phone-family'))")
    page.wait_for_function("!document.getElementById('editBg').hidden")
    assert not page.locator("#eTagsSec").is_hidden()
    assert not page.locator("#eWaitlistSec").is_hidden()
    assert page.locator("#eTagPills .add-tag").inner_text().strip() == "+ Tag"
    # openEdit's own delayed focus() on #eName (pre-existing, unrelated to tags) can steal focus
    # from a tag input opened too soon after — let that settle first
    page.wait_for_timeout(120)
    page.locator("#eTagPills .add-tag").click()
    page.locator("#eTagPills input").fill("  أصحاب بابا  ")
    page.locator("#eTagPills input").press("Enter")
    assert page.evaluate("editingTags") == ["أصحاب بابا"]
    assert page.locator("#eTagPills .tag-pill.on").inner_text().strip() == "أصحاب بابا"
    n = len(family_mutations)
    page.locator("#editSave").click()
    mut = wait_new_mutation(n)
    edit_body = json.loads(mut[2])
    assert edit_body.get("tags") == ["أصحاب بابا"], edit_body
    page.wait_for_function("document.getElementById('editBg').hidden")
    page.wait_for_function("JSON.stringify(all.find(r => r.id === 'no-phone-family').tags) === JSON.stringify(['أصحاب بابا'])")
    page.wait_for_timeout(200)   # let saveEdit's own refreshAll() settle before the next direct mutation

    # ── acceptance #2 (cont'd): matchKey dedupe — a typo'd/hamza'd/re-cased variant of an
    # existing tag toggles the SAME tag (keeping its original spelling), never a duplicate
    page.evaluate("openEdit(all.find(r => r.id === 'church-family'))")
    page.wait_for_function("!document.getElementById('editBg').hidden")
    page.wait_for_timeout(120)   # let openEdit's delayed #eName focus() settle first
    vocab_before = page.evaluate("tagVocabulary().length")
    page.locator("#eTagPills .add-tag").click()
    page.locator("#eTagPills input").fill("اصحاب بابا")   # bare alef instead of أ, no padding whitespace
    page.locator("#eTagPills input").press("Enter")
    assert page.evaluate("tagVocabulary().length") == vocab_before   # no new vocabulary entry
    assert page.locator("#eTagPills .tag-pill.on").count() == 1
    assert page.locator("#eTagPills .tag-pill.on").inner_text().strip() == "أصحاب بابا"   # original spelling kept
    n = len(family_mutations)
    page.locator("#editSave").click()
    mut = wait_new_mutation(n)
    assert json.loads(mut[2]).get("tags") == ["أصحاب بابا"], mut
    page.wait_for_function("document.getElementById('editBg').hidden")
    page.wait_for_function("JSON.stringify(all.find(r => r.id === 'church-family').tags) === JSON.stringify(['أصحاب بابا'])")
    page.wait_for_timeout(200)

    # ── acceptance #3: removing a tag from its last family removes it from the Edit picker (the
    # picker's vocabulary is computed from live `all` rows, not the local edit buffer)
    page.evaluate("all.find(r => r.id === 'no-phone-family').tags = null; all.find(r => r.id === 'church-family').tags = null; renderAll()")
    assert page.evaluate("tagVocabulary().some(v => v.name === 'أصحاب بابا')") is False
    page.evaluate("openEdit(all.find(r => r.id === 'no-phone-family'))")
    page.wait_for_function("!document.getElementById('editBg').hidden")
    assert page.locator("#eTagPills .tag-pill.on").count() == 0
    assert page.locator("#eTagPills button", has_text="أصحاب بابا").count() == 0
    page.locator("#editCancel").click()
    page.wait_for_function("document.getElementById('editBg').hidden")

    # ── acceptance #4 + waitlist exclusion from headcounts: setting Waitlist=Yes removes the
    # row from "all" and every filter except ⏳; header totals + cap bars drop by its count.
    # henna-family is used here (not church-family, which is church_only:true from the v23.2
    # fixtures and so never counts toward the reception cap either way — a poor probe for this
    # specific delta). #tB (all bride headcount) and #orgCapBUsed (reception-cap usage,
    # church_only excluded) are compared against their OWN pre-waitlist baselines.
    bp_before = int(page.locator("#tB").inner_text())
    capB_before = int(page.locator("#orgCapBUsed").inner_text())
    henna_count = page.evaluate("all.find(r => r.id === 'henna-family').count")
    page.evaluate("openEdit(all.find(r => r.id === 'henna-family'))")
    page.wait_for_function("!document.getElementById('editBg').hidden")
    assert not page.locator("#eWaitYes").is_disabled()   # not currently sent
    page.locator("#eWaitYes").click()
    assert page.evaluate("eWaitlistVal") is True
    n = len(family_mutations)
    page.locator("#editSave").click()
    mut = wait_new_mutation(n)
    assert json.loads(mut[2]).get("waitlist") is True, mut
    page.wait_for_function("document.getElementById('editBg').hidden")
    page.wait_for_function("all.find(r => r.id === 'henna-family').waitlist === true")
    page.wait_for_function(f"Number(document.getElementById('tB').textContent) === {bp_before - henna_count}")
    page.wait_for_function(f"Number(document.getElementById('orgCapBUsed').textContent) === {capB_before - henna_count}")
    assert page.locator("#fullList .fam[data-id='henna-family']").count() == 0
    page.evaluate("toggleFilterCb('ev2', true)")   # any other AND-stacking filter still excludes it
    assert page.locator("#fullList .fam[data-id='henna-family']").count() == 0
    page.evaluate("toggleFilterCb('ev2', false)")
    # henna-family stays parked — the default view now shows only the 2 non-waitlisted families
    page.wait_for_function("document.querySelectorAll('#fullList .fam').length === 2")
    page.wait_for_timeout(200)

    # ── acceptance #5 + #6 + ⏳ exclusivity + no-send: a fresh waitlisted probe row (injected —
    # never PATCHed, so it can never appear via the network)
    page.evaluate(
        "all.push({ id: 'wl-probe', name: 'عيلة تجريبية للانتظار', name_en: 'WL Probe Family',"
        " phone: '+1 202-555-0177', side: 'groom', count: 3, event2: false, church_only: false,"
        " confirmed: false, waitlist: true, tags: null, invite_sent: {}, deleted_at: null,"
        " added_by: 'fixture' }); renderAll();"
    )
    assert page.locator("#fullList .fam[data-id='wl-probe']").count() == 0   # invisible outside ⏳
    page.evaluate("toggleWaitlistFilter(true)")
    # henna-family (parked above) + wl-probe are BOTH waitlisted — the ⏳ view shows exactly them
    page.wait_for_function("document.querySelectorAll('#fullList .fam').length === 2")
    wl_card = page.locator("#fullList .fam[data-id='wl-probe']")
    assert "waitlisted" in (wl_card.get_attribute("class") or "")
    assert wl_card.locator(".card-chip.waitlist").count() == 1
    assert "Waitlist" in wl_card.locator(".card-chip.waitlist").inner_text()
    assert wl_card.locator(".send-invite").count() == 0   # absent, not disabled
    assert wl_card.locator(".edit-btn").count() == 1
    assert page.evaluate("[...activeFilters]") == ["waitlist"]
    assert sheet_prop("cbFTrash", "disabled") is True   # exclusive like Trash — disables everything else
    assert sheet_prop("segSideBride", "disabled") is True
    page.evaluate("openInvite(all.find(r => r.id === 'wl-probe'))")   # belt-and-suspenders guard
    assert page.locator("#inviteBg").is_hidden()
    page.evaluate("toggleWaitlistFilter(false)")
    # henna-family is still parked at this point — default view is the 2 non-waitlisted families
    page.wait_for_function("document.querySelectorAll('#fullList .fam').length === 2")

    # ── acceptance #7 (deleted-waitlist under 🗑 only): a row that is BOTH deleted AND
    # waitlisted shows only in Trash, never resurfaces under ⏳
    page.evaluate("all.find(r => r.id === 'wl-probe').deleted_at = new Date().toISOString(); renderAll()")
    page.evaluate("toggleWaitlistFilter(true)")
    assert page.locator("#fullList .fam[data-id='wl-probe']").count() == 0
    page.evaluate("toggleWaitlistFilter(false)")
    page.evaluate("toggleTrashFilter(true)")
    assert page.locator("#fullList .fam[data-id='wl-probe']").count() == 1
    page.evaluate("toggleTrashFilter(false)")
    page.evaluate("all = all.filter(r => r.id !== 'wl-probe'); renderAll()")   # drop the probe row
    # henna-family is STILL parked (promotion happens next) — default view stays at 2
    page.wait_for_function("document.querySelectorAll('#fullList .fam').length === 2")

    # ── acceptance #6 (two-way lock): a sent family's Waitlist Yes chip is disabled in Edit;
    # a save leaves waitlist:false even after clicking it. henna-family is currently parked, so
    # no-phone-family stands in as the "sent" probe here.
    page.evaluate(
        "all.find(r => r.id === 'no-phone-family').invite_sent ="
        " { overall: { at: new Date().toISOString(), via: 'test', link: INVITE_LINK, template: INVITE_TEMPLATE_VERSION } };"
        " renderAll();"
    )
    page.evaluate("openEdit(all.find(r => r.id === 'no-phone-family'))")
    page.wait_for_function("!document.getElementById('editBg').hidden")
    assert page.locator("#eWaitYes").is_disabled()
    page.evaluate("setEWaitlist(true)")   # JS-level guard, defense in depth behind the disabled chip
    assert page.evaluate("eWaitlistVal") is False
    n = len(family_mutations)
    page.locator("#editSave").click()
    mut = wait_new_mutation(n)
    assert json.loads(mut[2]).get("waitlist") is False, mut
    page.wait_for_function("document.getElementById('editBg').hidden")
    page.wait_for_timeout(200)
    page.evaluate("all.find(r => r.id === 'no-phone-family').invite_sent = {}; renderAll()")   # restore

    # ── acceptance #7 (promotion): Waitlist Yes → No brings the row back as unsent, Send
    # reappears, caps increment back to their pre-waitlist baselines
    page.evaluate("openEdit(all.find(r => r.id === 'henna-family'))")
    page.wait_for_function("!document.getElementById('editBg').hidden")
    assert page.evaluate("eWaitlistVal") is True
    page.locator("#eWaitNo").click()
    n = len(family_mutations)
    page.locator("#editSave").click()
    mut = wait_new_mutation(n)
    assert json.loads(mut[2]).get("waitlist") is False, mut
    page.wait_for_function("document.getElementById('editBg').hidden")
    page.wait_for_function("all.find(r => r.id === 'henna-family').waitlist === false")
    page.wait_for_function(f"Number(document.getElementById('tB').textContent) === {bp_before}")
    page.wait_for_function(f"Number(document.getElementById('orgCapBUsed').textContent) === {capB_before}")
    henna_after = page.locator("#fullList .fam[data-id='henna-family']")
    assert henna_after.count() == 1
    assert henna_after.locator(".send-invite").count() == 1
    page.wait_for_timeout(200)

    # ── acceptance #8 (Dad's queue): a tag pill ANDed with Not-sent shows exactly the
    # intersection; the WhatsApp snapshot contains exactly those rows and never a waitlist row
    page.evaluate("all.find(r => r.id === 'no-phone-family').tags = ['أصحاب بابا']; renderAll()")
    page.evaluate("all.find(r => r.id === 'church-family').tags = ['أصحاب بابا']; renderAll()")
    page.evaluate(
        "all.push({ id: 'wl-dad-probe', name: 'وسيط الانتظار', name_en: 'Dad Queue Waitlist Probe',"
        " phone: '+1 202-555-0188', side: 'groom', count: 2, event2: false, church_only: false,"
        " confirmed: false, waitlist: true, tags: ['أصحاب بابا'], invite_sent: {}, deleted_at: null,"
        " added_by: 'fixture' }); renderAll();"
    )
    page.evaluate("setSegment(['sent','unsent'], 'unsent'); setTagFilter('أصحاب بابا')")
    shown_ids = page.evaluate("() => [...document.querySelectorAll('#fullList .fam')].map(f => f.dataset.id)")
    assert set(shown_ids) == {"no-phone-family", "church-family"}, shown_ids
    snap_ids = page.evaluate("snapshotRows().map(r => r.id)")
    assert set(snap_ids) == {"no-phone-family", "church-family"}, snap_ids
    snap_msg = page.evaluate("snapshotMessage()")
    dad_probe_display = page.evaluate("displayName({name: 'وسيط الانتظار', name_en: 'Dad Queue Waitlist Probe'})")
    assert "wl-dad-probe" not in snap_msg and dad_probe_display not in snap_msg
    np_display = page.evaluate("displayName(all.find(r => r.id === 'no-phone-family'))")
    ch_display = page.evaluate("displayName(all.find(r => r.id === 'church-family'))")
    assert np_display in snap_msg and ch_display in snap_msg, (np_display, ch_display, snap_msg)
    page.evaluate("all = all.filter(r => r.id !== 'wl-dad-probe'); setSegment(['sent','unsent'], ''); setTagFilter('أصحاب بابا')")
    page.wait_for_function("document.querySelectorAll('#fullList .fam').length === 3")

    # ── RTL @ 390px: the tag filter row + waitlist row live inside the existing filter sheet —
    # no separate horizontal strip, no body-level horizontal overflow
    page.evaluate("openFilterSheet()")
    page.wait_for_timeout(30)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    page.evaluate("closeFilterSheet()")

    # ── acceptance #9: Excel export gains Tags + Waitlist columns (positioned right after
    # Church Only), a tagged + waitlisted row exports its tags joined by "، " and "YES"
    page.evaluate("all.find(r => r.id === 'church-family').waitlist = true; renderAll()")
    with page.expect_download() as dl:
        page.evaluate("exportExcel()")
    export_text = Path(dl.value.path()).read_text(encoding="utf-8").lstrip("﻿")
    export_rows = list(csv.reader(export_text.splitlines()))
    header = export_rows[0]
    church_idx = header.index("Church Only (الكنيسة فقط)")
    assert header[church_idx + 1] == "Tags (تصنيفات)", header
    assert header[church_idx + 2] == "Waitlist (انتظار)", header
    tag_idx, wait_idx, name_idx = header.index("Tags (تصنيفات)"), header.index("Waitlist (انتظار)"), header.index("Name (الاسم)")
    church_csv_row = next(r for r in export_rows[1:] if r[name_idx] == "عيلة الكنيسة")
    assert church_csv_row[tag_idx] == "أصحاب بابا", church_csv_row
    assert church_csv_row[wait_idx] == "YES", church_csv_row
    page.evaluate(
        "all.find(r => r.id === 'church-family').waitlist = false;"
        " all.find(r => r.id === 'church-family').tags = null;"
        " all.find(r => r.id === 'no-phone-family').tags = null;"
        " renderAll();"
    )

    # ══════════════════════════ v25: IA restructure ══════════════════════════
    # Router: deep links, back/forward, reload, and role-gated redirects. Still master + #list.
    assert page.evaluate("location.hash") == "#list"

    # ── deep-link + role gating: #waitlist/#deleted re-home the existing filter-sheet states
    # into the router; #tags opens the filter sheet (no single-tag "screen" exists to route to)
    page.evaluate("location.hash = '#waitlist'")
    page.wait_for_function("[...activeFilters].join(',') === 'waitlist'")
    assert not page.locator("#scrList").is_hidden()
    page.evaluate("location.hash = '#deleted'")
    page.wait_for_function("[...activeFilters].join(',') === 'deleted'")
    page.evaluate("location.hash = '#tags'")
    page.wait_for_function("!document.getElementById('filterBg').hidden")
    assert page.evaluate("activeFilters.size") == 0
    page.evaluate("closeFilterSheet()")

    # ── migration shim: stale cached handlers from the old 3-step wizard (toStep/backStep)
    # must not throw against the new one-screen add
    page.evaluate("toStep(1); toStep(2); toStep(3); backStep();")
    assert page.evaluate("!!window.toStep && !!window.backStep")

    # ── back/forward across screens; reload preserves the deep link
    page.evaluate("location.hash = '#add'")
    page.wait_for_function("!document.getElementById('scrAdd').hidden")
    page.evaluate("location.hash = '#list'")
    page.wait_for_function("!document.getElementById('scrList').hidden")
    page.go_back()
    page.wait_for_function("location.hash === '#add'")
    assert not page.locator("#scrAdd").is_hidden()
    assert page.locator("#scrList").is_hidden()
    page.go_forward()
    page.wait_for_function("location.hash === '#list'")
    assert not page.locator("#scrList").is_hidden()
    page.evaluate("location.hash = '#photos'")
    page.wait_for_function("!document.getElementById('scrPhotos').hidden")
    page.reload(wait_until="domcontentloaded")
    page.wait_for_function("!document.getElementById('scrPhotos').hidden")
    assert page.evaluate("location.hash") == "#photos"

    # ── role gating: a de-authenticated device hitting #list/#waitlist redirects to #add;
    # re-navigating to #list once master again reaches it cleanly
    page.evaluate("localStorage.removeItem('wedOrg')")
    page.evaluate("location.hash = '#list'")
    page.wait_for_function("location.hash === '#add'")
    assert not page.locator("#scrAdd").is_hidden()
    page.evaluate("location.hash = '#waitlist'")
    page.wait_for_function("location.hash === '#add'")
    page.evaluate("localStorage.setItem('wedOrg', 'master')")
    page.evaluate("location.hash = '#list'")
    page.wait_for_function("all.length === 3")

    # ── per-role hamburger menu contents (exact rows; v26 adds master-only Settings after Tags;
    # Tables stays a dead row reserved for v27 seating)
    def menu_labels():
        return page.evaluate(
            "[...document.querySelectorAll('#menuRows .menu-row > span:first-child')]"
            ".map(s => s.textContent)"
        )

    page.click("#hamburgerBtn")
    page.wait_for_function("!document.getElementById('menuScrim').hidden")
    master_rows = menu_labels()
    assert master_rows == [
        "List", "Add", "Fast entry", "Photos", "Waitlist", "Deleted", "Tags", "Settings",
        "Excel", "Snapshot", "PIN", "بالعربي",
    ], master_rows
    assert "Tables" not in master_rows
    page.evaluate("closeMenu()")

    page.evaluate("localStorage.setItem('wedOrg', 'bride')")
    page.evaluate("location.hash = '#list'")
    page.wait_for_function("all.length >= 3")
    page.click("#hamburgerBtn")
    page.wait_for_function("!document.getElementById('menuScrim').hidden")
    side_rows = menu_labels()
    assert side_rows == ["List", "Add", "Fast entry", "Photos", "Excel", "Snapshot", "PIN", "بالعربي"], side_rows
    page.evaluate("closeMenu()")

    # contributor (no PIN, side-locked): 3 rows max, never an Organizers row on a fresh device
    page.evaluate("localStorage.removeItem('wedOrg')")
    assert page.evaluate("localStorage.getItem('wedEverPin')") in (None, "")
    page.evaluate("location.hash = '#add'")
    page.wait_for_function("location.hash === '#add'")
    page.click("#hamburgerBtn")
    page.wait_for_function("!document.getElementById('menuScrim').hidden")
    contrib_rows = menu_labels()
    assert contrib_rows == ["Photos", "Fast entry", "بالعربي"], contrib_rows
    page.evaluate("closeMenu()")

    # once a PIN has EVER been entered on this device, a 4th Organizers row appears; tapping it
    # opens the real PIN modal (mom herself never triggers this — she never types a PIN)
    page.evaluate("localStorage.setItem('wedEverPin', '1')")
    page.click("#hamburgerBtn")
    page.wait_for_function("!document.getElementById('menuScrim').hidden")
    assert menu_labels() == ["Photos", "Fast entry", "بالعربي", "Organizers"]
    page.locator("#menuRows .menu-row", has_text="Organizers").click()
    page.wait_for_function("!document.getElementById('pinBg').hidden")
    page.fill("#pinIn", "1994")
    page.click("#pinOpen")
    page.wait_for_function("location.hash === '#list'")
    page.wait_for_function("all.length === 3")
    assert page.evaluate("localStorage.getItem('wedOrg')") == "master"

    # ── Excel/Snapshot/PIN/Lang menu rows are ACTIONS, not navigable screens
    page.click("#hamburgerBtn")
    page.wait_for_function("!document.getElementById('menuScrim').hidden")
    with page.expect_download():
        page.locator("#menuRows .menu-row", has_text="Excel").click()
    assert page.evaluate("location.hash") == "#list"
    assert page.locator("#menuScrim").is_hidden()  # action rows close the menu behind them

    # ══════════════════════════ v25: one-screen add ══════════════════════════
    page.evaluate("location.hash = '#add'")
    page.wait_for_function("!document.getElementById('scrAdd').hidden")

    # master sees a Bride/Groom side segment; side-locked roles get a read-only pill instead
    assert not page.locator("#addSideSeg").is_hidden()
    assert page.locator("#addSidePillRo").is_hidden()
    assert page.get_attribute("#addSideBride", "class") == "on"

    # Save disabled while name is empty (or whitespace-only)
    page.fill("#addName", "")
    assert page.locator("#addSaveBtn").is_disabled()
    page.fill("#addName", "   ")
    assert page.locator("#addSaveBtn").is_disabled()

    # Church pill toggle DIMS the Reception pill — that dimming is the church_only explanation
    # (no sentence anywhere on this screen); Henna toggles independently; count stepper works
    page.fill("#addName", "Church Dim Probe")
    assert page.get_attribute("#pillReceptionBtn", "class") == "event-pill locked on"
    page.click("#pillChurchBtn")
    assert page.get_attribute("#pillReceptionBtn", "class") == "event-pill locked on dimmed"
    assert "on" in page.get_attribute("#pillChurchBtn", "class")
    page.click("#pillHennaBtn")
    assert "on" in page.get_attribute("#pillHennaBtn", "class")
    # count stepper: default 2, [+]/[−] adjust it, floor at 1
    assert page.inner_text("#addCount") == "2"
    page.locator("#scrAdd .count-row button", has_text="+").click()
    assert page.inner_text("#addCount") == "3"
    page.locator("#scrAdd .count-row button", has_text="−").click()
    page.locator("#scrAdd .count-row button", has_text="−").click()
    page.locator("#scrAdd .count-row button", has_text="−").click()
    assert page.inner_text("#addCount") == "1"  # floors at 1, never 0 or negative
    page.locator("#scrAdd .count-row button", has_text="+").click()
    assert page.inner_text("#addCount") == "2"
    n = len(family_mutations)
    page.click("#addSaveBtn")
    mut = wait_new_mutation(n)
    posted = json.loads(mut[2])[0]
    assert posted["church_only"] is True and posted["event2"] is True, posted
    assert posted["side"] == "bride", posted
    # form resets after a successful save (waits for the POST's response round-trip, not just
    # the request having been sent): name refocused/cleared, pills off
    page.wait_for_function("document.getElementById('addName').value === ''")
    assert page.input_value("#addName") == ""
    assert page.get_attribute("#pillReceptionBtn", "class") == "event-pill locked on"
    assert page.get_attribute("#pillHennaBtn", "class") == "event-pill"
    assert page.get_attribute("#pillChurchBtn", "class") == "event-pill"
    assert page.evaluate("document.activeElement.id") == "addName"

    # empty phone still saves (kills the old separate skip button — phone is just optional)
    page.fill("#addName", "No Phone Probe")
    n = len(family_mutations)
    page.click("#addSaveBtn")
    mut = wait_new_mutation(n)
    posted = json.loads(mut[2])[0]
    assert posted["phone"] == "", posted

    # master's segmented side pill controls which side the row is saved under
    page.click("#addSideGroom")
    assert page.get_attribute("#addSideGroom", "class") == "on"
    page.fill("#addName", "Groom Side Probe")
    n = len(family_mutations)
    page.click("#addSaveBtn")
    mut = wait_new_mutation(n)
    posted = json.loads(mut[2])[0]
    assert posted["side"] == "groom", posted
    page.click("#addSideBride")

    # duplicate guard: a matching phone (household fingerprint, script-agnostic) surfaces the
    # inline "Exists" chip WITHOUT saving; a second Save tap saves anyway (never a modal here —
    # #fast keeps the blocking dup modal, this screen never does). church-family, not
    # henna-family: the v23.1 nullable-column probe further down nulls henna-family's name_en.
    page.fill("#addName", "Some New Household")
    page.fill("#addPhone", "+20 100 123 4567")  # same number as the church-family fixture
    n = len(family_mutations)
    page.click("#addSaveBtn")
    page.wait_for_timeout(120)
    assert not page.locator("#addExistsChip").is_hidden()
    assert "Church Test Family" in page.inner_text("#addExistsChip")
    assert len(family_mutations) == n  # first tap never saves
    page.click("#addSaveBtn")
    mut = wait_new_mutation(n)
    assert mut[0] == "POST", mut
    page.wait_for_function("document.getElementById('addExistsChip').hidden")  # clears post-save

    # duplicate guard, name path: a diacritic/hamza variant of an existing Arabic name also
    # matches (normName folds ة/ه, أ/إ/آ/ا, and strips the "family" prefix on both sides)
    page.fill("#addName", "عيله الكنيسه")  # variant of "عيلة الكنيسة" — ta-marbuta folded to ه
    page.fill("#addPhone", "+1 555 999 0001")  # deliberately NOT a phone match — proves name path
    n = len(family_mutations)
    page.click("#addSaveBtn")
    page.wait_for_timeout(120)
    assert not page.locator("#addExistsChip").is_hidden()
    assert "Church Test Family" in page.inner_text("#addExistsChip")
    assert len(family_mutations) == n
    page.click("#addSaveBtn")
    wait_new_mutation(n)
    page.wait_for_function("document.getElementById('addExistsChip').hidden")
    page.fill("#addPhone", "")

    # cap guard: side at cap → Save turns amber, the row saves with waitlist:true, chip shown —
    # never blocking
    page.evaluate("CAPS.bride = 0; renderCaps()")
    assert "amber" in page.get_attribute("#addSaveBtn", "class")
    assert not page.locator("#addWaitlistChip").is_hidden()
    page.fill("#addName", "Cap Guard Probe")
    n = len(family_mutations)
    page.click("#addSaveBtn")
    mut = wait_new_mutation(n)
    posted = json.loads(mut[2])[0]
    assert posted["waitlist"] is True, posted
    page.evaluate("CAPS.bride = 200; renderCaps()")
    assert "amber" not in page.get_attribute("#addSaveBtn", "class")

    # ══════════════════════════ v25: photos screen ══════════════════════════
    def mock_photo_list(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps([{"name": "bride-fixture-1.jpg"}, {"name": "groom-fixture-1.jpg"}]),
        )

    page.route("**/storage/v1/object/list/guest-photos", mock_photo_list)
    page.evaluate("location.hash = '#photos'")
    page.wait_for_function("!document.getElementById('scrPhotos').hidden")
    page.wait_for_function("document.querySelectorAll('#photoGrid a').length === 2")
    assert page.locator("#photosUploadBtn").count() == 1  # its own upload entry point
    assert page.locator("#photoGrid .pdel").count() == 2  # master keeps delete

    page.evaluate("localStorage.setItem('wedOrg', 'bride'); loadPhotos()")
    page.wait_for_timeout(150)
    assert page.locator("#photoGrid a").count() == 1  # side-scoped: only bride's own photo
    assert page.locator("#photoGrid .pdel").count() == 0  # non-master: no delete
    page.evaluate("localStorage.setItem('wedOrg', 'master'); loadPhotos()")
    page.wait_for_timeout(150)
    page.unroute("**/storage/v1/object/list/guest-photos", mock_photo_list)

    # ══════════════════════════ v25: fast entry screen ══════════════════════════
    page.evaluate("location.hash = '#fast'")
    page.wait_for_function("!document.getElementById('scrFast').hidden")
    assert page.locator("#pasteArea").count() == 1

    page.fill("#pasteArea", "Fast Lane Family 3\nAnother Fast Family +1 202 555 0199 2")
    page.click("#sortBtn")
    page.wait_for_function("!document.getElementById('previewBg').hidden")
    assert page.locator("#previewList .fam").count() == 2
    n = len(family_mutations)
    page.click("#previewOk")
    mut = wait_new_mutation(n)
    assert mut[0] == "POST", mut
    posted = json.loads(mut[2])
    assert {r["name"] for r in posted} == {"Fast Lane Family", "Another Fast Family"}, posted
    page.wait_for_function("document.getElementById('previewBg').hidden")

    # dup modal is unchanged: a phone match surfaces it; the default (checked) skips the
    # duplicate, Save all inserts only the clean row
    page.fill("#pasteArea", "Duplicate By Phone +1 202 555 0123\nBrand New Fast Family 4")
    page.click("#sortBtn")
    page.wait_for_function("!document.getElementById('previewBg').hidden")
    n = len(family_mutations)
    page.click("#previewOk")
    page.wait_for_function("!document.getElementById('dupBg').hidden")
    assert "Duplicate By Phone" in page.inner_text("#dupList")
    page.click("#dupGo")
    mut = wait_new_mutation(n)
    posted = json.loads(mut[2])
    assert len(posted) == 1 and posted[0]["name"] == "Brand New Fast Family", posted
    page.wait_for_function("document.getElementById('previewBg').hidden")

    # ══════════════════════════ v26: Event Setup ══════════════════════════
    # Legacy booleans (church_only/event2/count) stay authoritative for membership on the three
    # core events; `seats` jsonb is exceptions-only. eff() below is an INDEPENDENT re-derivation
    # of the app's eventEff() (not a call into it) so the gauge assertions aren't just testing the
    # implementation against itself.
    def eff(fam, event_id):
        if fam.get("deleted_at") or fam.get("waitlist"):
            return 0
        if event_id == "henna":
            invited = bool(fam.get("event2"))
        elif event_id == "reception":
            invited = not fam.get("church_only")
        else:
            invited = False
        if not invited:
            return 0
        override = (fam.get("seats") or {}).get(event_id)
        n_ = override if isinstance(override, int) else fam["count"]
        return min(n_, fam["count"])

    page.evaluate("localStorage.setItem('wedOrg', 'master')")
    page.evaluate("location.hash = '#list'")
    page.wait_for_function("!document.getElementById('scrList').hidden")
    page.evaluate("async () => { await loadEvents(); await refreshSideTotals(); }")
    page.wait_for_function("EVENTS.length === 3")

    # ── master-only routing: side-role and de-authenticated hits on #settings redirect to #add
    # (router-level redirect, not just a hidden menu row — a deep link can't reach it either)
    page.evaluate("localStorage.setItem('wedOrg', 'bride')")
    page.evaluate("location.hash = '#settings'")
    page.wait_for_function("location.hash === '#add'")
    assert not page.locator("#scrAdd").is_hidden()
    page.evaluate("localStorage.removeItem('wedOrg')")
    page.evaluate("location.hash = '#settings'")
    page.wait_for_function("location.hash === '#add'")
    page.evaluate("localStorage.setItem('wedOrg', 'master')")
    page.evaluate("location.hash = '#list'")
    page.wait_for_function("all.length === 3")

    # ── Settings screen reached via the ☰ Settings row; one collapsed card per active event,
    # church locked "Everyone" with no capacity/split fields at all
    page.click("#hamburgerBtn")
    page.wait_for_function("!document.getElementById('menuScrim').hidden")
    page.locator("#menuRows .menu-row", has_text="Settings").click()
    page.wait_for_function("!document.getElementById('scrSettings').hidden")
    assert not page.locator("#scrSettings").is_hidden()
    page.wait_for_function("document.querySelectorAll('#settingsList .evt-card').length === 3")
    ev_ids = page.evaluate("[...document.querySelectorAll('#settingsList .evt-card')].map(c => c.dataset.id)")
    assert ev_ids == ["church", "reception", "henna"], ev_ids
    assert page.locator('.evt-card[data-id="church"] .evt-badge-everyone').count() == 1
    assert page.locator("#evt_church_cap").count() == 0  # capacity hidden for the universal event

    # ── henna cap 75 display: seeded cap shows in the field, split defaults to 50/50 (bride_alloc
    # is null in the seed ⇒ derived 38/37, no per-side number was ever set)
    page.locator('.evt-card[data-id="henna"] summary').click()
    page.wait_for_function("document.getElementById('evt_henna_cap').offsetParent !== null")
    assert page.input_value("#evt_henna_cap") == "75"
    assert "on" not in (page.get_attribute("#evt_henna_splitNum", "class") or "")
    assert page.evaluate("document.getElementById('evt_henna_splitFields').hidden") is True

    # ── event field edit round-trips via PATCH
    page.locator('.evt-card[data-id="reception"] summary').click()
    page.wait_for_function("document.getElementById('evt_reception_venueEn').offsetParent !== null")
    page.fill("#evt_reception_venueEn", "Al Masa Hotel — New Ballroom")
    n_ev = len(event_mutations)
    page.click("#settingsSaveBtn")
    mut = wait_new_event_mutation(n_ev)
    assert mut[0] == "PATCH" and "id=eq.reception" in mut[1], mut
    ev_body = json.loads(mut[2])
    assert ev_body["venue_en"] == "Al Masa Hotel — New Ballroom", ev_body
    page.wait_for_function("EVENTS.find(e => e.id === 'reception').venue_en === 'Al Masa Hotel — New Ballroom'")

    # ── derived groom math: bride/groom split is edited as ONE number (bride); groom is a
    # read-only line that updates live from cap − bride, never typed — 225 of 400 ⇒ 175 by
    # arithmetic, asserted literally, with no save required to see it
    page.wait_for_function("document.getElementById('evt_reception_bride').offsetParent !== null")
    assert page.input_value("#evt_reception_cap") == "400"
    assert page.input_value("#evt_reception_bride") == "200"
    page.fill("#evt_reception_bride", "225")
    page.wait_for_function("document.getElementById('evt_reception_groomReadout').textContent.trim() === '175'")
    page.fill("#evt_reception_bride", "200")  # revert — keeps this card a no-op for the save below
    page.wait_for_function("document.getElementById('evt_reception_groomReadout').textContent.trim() === '200'")

    # ── custom event add: name required, id auto-generated ev_xxxxxx, INSERTs active + non-core
    n_events_before = page.evaluate("document.querySelectorAll('#settingsList .evt-card').length")
    page.click("#settingsAddBtn")
    page.wait_for_function(f"document.querySelectorAll('#settingsList .evt-card').length === {n_events_before + 1}")
    new_id = page.evaluate("EVENTS.find(e => e._isNew).id")
    assert new_id.startswith("ev_"), new_id
    page.fill(f"#evt_{new_id}_nameAr", "حفلة الخطوبة")
    page.fill(f"#evt_{new_id}_nameEn", "Engagement Party")
    n_ev = len(event_mutations)
    page.click("#settingsSaveBtn")
    mut = wait_new_event_mutation(n_ev)
    assert mut[0] == "POST", mut
    posted_ev = json.loads(mut[2])
    assert posted_ev["id"] == new_id, posted_ev
    assert posted_ev["active"] is True and posted_ev["core"] is False, posted_ev
    assert posted_ev["name_en"] == "Engagement Party", posted_ev
    page.wait_for_function("!EVENTS.find(e => e._isNew)")  # cleared once the insert round-trips

    # ── delete (deactivate, never hard-delete): a non-core event's Delete button PATCHes
    # active:false and the card disappears; core events (church/reception/henna) never get one
    assert page.locator('.evt-card[data-id="church"] .evt-delete').count() == 0
    assert page.locator('.evt-card[data-id="reception"] .evt-delete').count() == 0
    n_ev = len(event_mutations)
    page.locator(f'.evt-card[data-id="{new_id}"] .evt-delete').click()
    mut = wait_new_event_mutation(n_ev)
    assert mut[0] == "PATCH" and f"id=eq.{new_id}" in mut[1], mut
    assert json.loads(mut[2]) == {"active": False}, mut
    page.wait_for_function("document.querySelectorAll('#settingsList .evt-card').length === 3")

    # ── live invited-vs-cap gauges: an additive card per capped active non-universal event beyond
    # reception (today: henna). Computed independently in Python from a fresh, direct fetch of the
    # mocked wedding_families table — not from the app's own in-memory state — so this is a real
    # check against whatever this suite's accumulated mutations left the fixture rows at.
    raw_families = page.evaluate("async () => (await (await fetch(SUPA + '?select=*')).json())")
    henna_bride = sum(eff(f, "henna") for f in raw_families if f["side"] == "bride")
    henna_groom = sum(eff(f, "henna") for f in raw_families if f["side"] == "groom")
    page.evaluate("location.hash = '#list'")
    page.wait_for_function("!document.getElementById('scrList').hidden")
    page.evaluate("async () => { await refreshSideTotals(); }")
    page.wait_for_function("document.querySelector('#orgCapEvents .cap-card[data-event=\"henna\"]') !== null")
    henna_gauge_text = page.inner_text('#orgCapEvents .cap-card[data-event="henna"]')
    # each side's bar reads against ITS OWN derived allocation, not the raw event total — henna's
    # bride_alloc is null (50/50) so 75 ⇒ ceil(75/2)=38 bride, 75-38=37 groom, matching brideCap()/
    # groomCap() exactly (odd seat goes to bride, same rounding rule as the settings-screen math)
    assert "Henna Party" in henna_gauge_text, henna_gauge_text
    assert f"{henna_bride} / 38" in henna_gauge_text, (henna_gauge_text, henna_bride)
    assert f"{henna_groom} / 37" in henna_gauge_text, (henna_gauge_text, henna_groom)

    # ── seats-exceptions semantics are untouched: henna's per-family override still lives at
    # seats.henna and clamps to count (v23 item 8 / this file's earlier henna-stepper assertions
    # at the top of the suite already exercise the Edit stepper end-to-end; this just confirms the
    # v26 read path — eventEff()/eventInvited() — agrees with the same live row)
    henna_fixture = next(f for f in raw_families if f["id"] == "henna-family")
    assert page.evaluate(
        "eventEff(all.find(r => r.id === 'henna-family'), eventById('henna'))"
    ) == eff(henna_fixture, "henna")

    # ── footer version marker
    assert page.locator(".ver").inner_text() == VERSION

    # ── v26: the mock mirrors live wedding_events column constraints exactly (asserted last —
    # these direct probes intentionally add to event_mutations, so they run after the counts
    # above). meal_options/extras/name_ar are NOT NULL with defaults; map_url is nullable.
    st_meal = page.evaluate(
        "async () => (await fetch(EVENTS_URL + '?id=eq.reception', {method:'PATCH', headers:H, body: JSON.stringify({meal_options: null})})).status"
    )
    assert st_meal == 400, st_meal
    st_extras = page.evaluate(
        "async () => (await fetch(EVENTS_URL + '?id=eq.reception', {method:'PATCH', headers:H, body: JSON.stringify({extras: null})})).status"
    )
    assert st_extras == 400, st_extras
    st_name = page.evaluate(
        "async () => (await fetch(EVENTS_URL + '?id=eq.reception', {method:'PATCH', headers:H, body: JSON.stringify({name_ar: null})})).status"
    )
    assert st_name == 400, st_name
    st_map = page.evaluate(
        "async () => (await fetch(EVENTS_URL + '?id=eq.reception', {method:'PATCH', headers:H, body: JSON.stringify({map_url: null})})).status"
    )
    assert st_map == 200, st_map  # nullable column accepts null

    # ══════════════════════════ v23.3: PIN role-gating + side-scoped fetch ══════════════════════════
    err_before = len(page_errors)

    # (a) fresh contributor (anon): the organizer #list is a gated route — a deep link bounces to
    #     #add; the shared photo gallery renders nobody else's photos, but the upload control stays.
    page.evaluate("localStorage.removeItem('wedOrg'); location.hash = '#add'")
    page.wait_for_function("location.hash === '#add'")
    page.evaluate("location.hash = '#list'")
    page.wait_for_function("location.hash === '#add'")
    assert page.locator("#scrList").is_hidden()
    assert not page.locator("#scrAdd").is_hidden()
    assert page.evaluate("localStorage.getItem('wedOrg')") is None
    assert page.locator("#roleChip").is_hidden()  # no role ⇒ no chip

    def mock_photo_gate(route):
        route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps([{"name": "bride-fixture-1.jpg"}, {"name": "groom-fixture-1.jpg"}]),
        )

    page.route("**/storage/v1/object/list/guest-photos", mock_photo_gate)
    page.evaluate("location.hash = '#photos'")
    page.wait_for_function("!document.getElementById('scrPhotos').hidden")
    page.evaluate("loadPhotos()")
    page.wait_for_timeout(150)
    assert page.locator("#photoGrid a").count() == 0       # gallery gated: no other-device photos
    assert page.locator("#photosUploadBtn").count() == 1    # contributor upload path preserved
    page.unroute("**/storage/v1/object/list/guest-photos", mock_photo_gate)
    page.evaluate("location.hash = '#add'")
    page.wait_for_function("location.hash === '#add'")

    # (b) PIN 1994 → master: sees every row, families fetch carries NO side filter, chip reads master
    page.evaluate("askPin()")
    page.wait_for_function("!document.getElementById('pinBg').hidden")
    page.fill("#pinIn", "1994")
    n_gets = len(family_gets)
    page.click("#pinOpen")
    page.wait_for_function("location.hash === '#list'")
    last_get = wait_new_family_get(n_gets)
    assert "side=eq." not in last_get, last_get
    assert page.evaluate("localStorage.getItem('wedOrg')") == "master"
    page.wait_for_function("all.length === 3")
    assert not page.locator(".tot.a").is_hidden()           # both-side 'All' total visible for master
    assert page.locator("#roleChip").inner_text().strip() == page.evaluate("t('roleChipMaster')")

    # (b) PIN 3882 → bride: bride rows only, fetch carries side=eq.bride
    page.evaluate("switchPin()")
    page.wait_for_function("!document.getElementById('pinBg').hidden")
    page.fill("#pinIn", "3882")
    n_gets = len(family_gets)
    page.click("#pinOpen")
    page.wait_for_function("location.hash === '#list'")
    last_get = wait_new_family_get(n_gets)
    assert "side=eq.bride" in last_get, last_get
    assert page.evaluate("localStorage.getItem('wedOrg')") == "bride"
    page.wait_for_function("all.length > 0 && all.every(r => r.side === 'bride')")
    assert page.locator("#roleChip").inner_text().strip() == page.evaluate("t('roleChipBride')")

    # (b) PIN 1360 → groom: none of the bride-only fixtures reach the client, fetch carries side=eq.groom
    page.evaluate("switchPin()")
    page.wait_for_function("!document.getElementById('pinBg').hidden")
    page.fill("#pinIn", "1360")
    n_gets = len(family_gets)
    page.click("#pinOpen")
    page.wait_for_function("location.hash === '#list'")
    last_get = wait_new_family_get(n_gets)
    assert "side=eq.groom" in last_get, last_get
    assert page.evaluate("localStorage.getItem('wedOrg')") == "groom"
    page.wait_for_function("all.length === 0")              # bride fixtures never delivered to groom
    assert page.locator("#roleChip").inner_text().strip() == page.evaluate("t('roleChipGroom')")

    # (c) wrong PIN 0000 rejected: error shown, role unchanged, modal stays open
    page.evaluate("switchPin()")
    page.wait_for_function("!document.getElementById('pinBg').hidden")
    assert page.evaluate("localStorage.getItem('wedOrg')") is None
    page.fill("#pinIn", "0000")
    page.click("#pinOpen")
    page.wait_for_function("document.getElementById('pinErr').style.display === 'block'")
    assert page.evaluate("localStorage.getItem('wedOrg')") is None
    assert not page.locator("#pinBg").is_hidden()

    # (d) switchPin exit → back to contributor on #add, role cleared, chip gone
    page.click("#pinBack")
    page.wait_for_function("document.getElementById('pinBg').hidden")
    assert page.evaluate("localStorage.getItem('wedOrg')") is None
    assert page.evaluate("location.hash") == "#add"
    assert not page.locator("#scrAdd").is_hidden()
    assert page.locator("#roleChip").is_hidden()

    # (e) reload with a persisted role → role retained, #list reachable, no PIN re-prompt
    page.evaluate("localStorage.setItem('wedOrg', 'master'); location.hash = '#list'")
    page.wait_for_function("location.hash === '#list'")
    page.reload(wait_until="domcontentloaded")
    page.wait_for_function("!document.getElementById('scrList').hidden")
    assert page.evaluate("localStorage.getItem('wedOrg')") == "master"
    assert page.locator("#pinBg").is_hidden()
    assert page.evaluate("location.hash") == "#list"

    assert len(page_errors) == err_before, page_errors[err_before:]

    browser.close()
    print(
        f"PASS {browser_name}: v26 — Event Setup: master-only #settings route (router-level "
        "redirect for every other role, deep links included) reached via the ☰ Settings row; one "
        "collapsed card per active wedding_events row (church locked Everyone, no capacity/split), "
        "field edits round-trip via PATCH, bride/groom split is one number with a live-derived "
        "read-only groom line (225 of 400 ⇒ 175, no save required to see it), custom events "
        "INSERT active+non-core with an auto-generated ev_xxxxxx id, Delete deactivates (never "
        "hard-deletes), an additive live gauge card per capped active non-universal event beyond "
        "reception (henna 75 seeded), mock enforces the same NOT NULL constraints as the live "
        "wedding_events table. On top of v25 — IA restructure: hash router (#list/#add/#fast/#photos/"
        "#waitlist/#deleted/#tags) with role-gated redirects, deep links surviving reload, "
        "back/forward navigation; ☰ hamburger menu built from one role table (contributor 3 "
        "rows max + conditional Organizers row once a PIN was ever entered on the device, side "
        "organizer, master with Waitlist/Deleted/Tags/Settings and no dead Tables row), "
        "Excel/Snapshot/PIN/Lang as actions not screens; one-screen Add replacing the 3-step "
        "wizard (master Bride/Groom segment vs. side-locked read-only pill, count stepper, "
        "Church pill dimming Reception with zero explainer text, empty-name disable, empty-phone "
        "save, inline Exists dup chip with second-tap-saves-anyway instead of a blocking modal, "
        "cap-guard amber Save + waitlist:true); #photos as its own screen with its own upload "
        "entry point, side-scoped gallery, master-only delete; #fast promoting the old adv box "
        "wholesale (paste/parse/preview, blocking dup modal unchanged). On top of v24 — tags + "
        "waitlist: freeform master-only tags with Arabic-fold "
        "matchKey dedupe (typo/case/hamza variants toggle the existing tag, never a duplicate), "
        "zero-use tags vanishing from the Edit picker + filter row on reload, add-wizard/mine/"
        "side-role payloads carry neither key; ⏳ Waitlist exclusive like 🗑 Trash, invisible to "
        "headcounts/caps/every filter but its own and to side-role org views entirely, unsendable "
        "waitlisted cards (dimmed, no Send button, openInvite a hard no-op), the sent⇒waitlist "
        "two-way lock, promote/demote round-trips caps back to baseline, a deleted+waitlisted row "
        "surfaces under 🗑 only; a tag-filter row inside the v23.2 filter sheet ANDs with the "
        "existing controls (Dad's queue = one tag + Not sent → one forwardable WhatsApp snapshot "
        "sourced from exactly the filtered rows on screen, never a waitlist row); Tags/Waitlist "
        "Excel export columns; widened select= fetches. On top of v23.2 — relationships "
        "(parent_id self-FK): native parent picker in Edit's More options replaces group_name, "
        "cycle safety (self+descendants excluded from the picker, save-time ancestor-walk toast, "
        "corrupt-cycle-safe renderer), breadcrumb + rollup chips (RTL-correct arrow glyph swap), "
        "Tree sort (trees A–Z, unlinked families flat below, trashed-parent orphans stay visible + "
        "Restore reassembles), breadcrumb search, CSV family-path column, mom's wizard loses the "
        "group field entirely. Filter sheet replaces the 11-chip strip: Filters trigger pill + "
        "badge, segments for Side/Invite (FILTER_EXCLUSIVE retired), checkboxes for Events/Status/"
        "Issues with zero-count rows hidden, Trash exclusive (clears+disables the rest), live "
        "Done-count + Clear, removable mini-chip echo, filtering never severs trees. Plus v23.1 "
        "HOTFIX: default-family Edit save round-trips (mock 400s null→NOT NULL, guarding the seats "
        "bug); members = contact is person #1 (count-1 inputs, contact line, over-length arrays "
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
