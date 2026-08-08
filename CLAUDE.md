# wedding-family-list — Work Queue & Rules

Single-file guest-list app (index.html) + Playwright suite (tests/). Supabase project `wydodvbkqhpoyefcwpnf`, GitHub Pages deploy from `main`. Nova (main session) reviews, commits, pushes, and live-verifies; Forge/codex implements. **No commits by implementers. Never delete or `git clean` files you didn't create — this file is authoritative. Suite must be green on Chromium + WebKit before any report.**

## THE QUEUE — strictly one version at a time, top to bottom

Work ONLY the topmost unshipped version. Do not start the next until the current one is reported, reviewed, committed, pushed, and live-verified. No mid-run scope additions: new asks get appended here by Nova and wait their turn.

### ✅ v27.2 — SHIPPED 2026-08-08: sticky add-side + organizer side-switch (suite green Chromium+WebKit; aggregate PATCH-count pins at tests:1123/1126 moved 3→4 and 12→13 for the new side-switch save)
1. **Sticky master add-side:** `addSide` initializes from `localStorage.getItem("wedAddSide") || ""`; `setAddSide()` persists to `wedAddSide`. Side-locked roles keep ignoring it (`targetSide()` unchanged for them); contributors already sticky via `wedSide`. Driving complaint: "you don't need to keep adding bride side once you already indicated it's bride side."
2. **Side switch in Edit (organizer-only):** new `fs-section` `eSideSec` in the edit modal (place right after the Confirmed check-row), hidden unless `isOrg()`. Label re-uses `sideLinkLbl`; segment buttons re-use `fB`/`fG` (no new i18n keys). `openEdit` seeds `eSideVal = r.side`; `saveEdit` includes `side: eSideVal` in the PATCH body for org roles and updates the local row so side totals re-render. No cap gate on moves (caps stay visual). Driving case: بابا's 8/5 batch put هاني حسانين + عزيز سركيس on groom by mistake.
3. Version ٢٧٫٢ · 27.2 in BOTH index.html `.ver` AND tests `VERSION` constant. Suite: add org side-switch scenario (PATCH body carries `side`, totals move, contributor modal shows no side segment); green Chromium + WebKit before report.

### ✅ QUEUE EMPTY — v23→v27 roadmap COMPLETE (2026-08-02 overnight run)
All shipped, live-verified, 25-scenario live battery green at v27.0 `9dd1602`: v23.1 `0a661b3` · v23.2 `df0cd13` · v24 `fcd7cf8` · v25 `8629003` · v26 `8f9bf2a` · **v26.1 `83d5098` gate hardening** (organizer-only gallery — closed v25 anon gallery leak; role chip + switchPin exit; side-scoped org fetches; PIN-gate regression tests; NOTE: version marker bumps must update BOTH index.html `.ver` AND the suite `VERSION` constant) · **v26.2 `59e207d` relationship groups** (contributor chip row, canonical AR values in new `relationship_group` column — never `group_name`; filter facet; by-group clustering; master ✏️ rename; Excel Group col; grouped snapshot) · **v27 `9dd1602` seating** (tables CRUD w/ guarded empty-only hard-delete — schema has no soft-delete col, FK RESTRICT protects occupied; tap-to-place; people-count meters; over-cap amber+confirm; print overview; Excel Table col — populates after visiting #tables once/session; master-only). Green-SHA ledger + battery evidence: `~/.claude/PAI/MEMORY/WORK/wedding-app-overnight/`.
Backlog (unqueued): auto-seat suggestions by group/side/tag · venue share link · security architecture (anon-key DB/storage exposure — Joseph's call, see checklist page).

### 🔥 v23.1 — HOTFIX (SHIPPED — reference)
Root cause of live "nothing saves" (2026-07-30): saveEdit PATCHed `seats:null`; column was NOT NULL → PostgREST rejected the whole payload → every Edit save silently rolled back. DB fixed by Nova (seats now nullable, verified end-to-end in the live UI). App fixes: (1) member-input accumulation (6 inputs rendered for count-4; idempotent render required); (2) member semantics = contact IS person #1 → count−1 inputs, contact displayName shown as fixed line 1, send-modal/export dedupe, one-time normalization of over-length members arrays; (3) suite hardening — mocked Supabase enforces live column constraints (400 on null into NOT NULL) + full-default-family save round-trip test. Version ٢٣٫١ · 23.1.

### v23.2 — Relationships + filter sheet
Full spec: `/private/tmp/claude-501/-Users-josephlabib/550cbbf3-ffef-4e7d-96fb-e4e5fa426dad/tasks/wxjycydkz.output` (read it — verified against the live schema and current index.html line anchors). Core: `parent_id` self-FK (ALREADY MIGRATED by Nova, indexed) — the container IS a card; native-select parent picker in Edit's More options (replaces group_name row); breadcrumb + rollup chips; Tree sort; cycle safety ×3; mom's wizard LOSES the group field. Filter chip strip replaced by a bottom-sheet with typed controls (segments for exclusive pairs, zero-count rows hidden, live-apply, Done·N count, removable mini-chip echo). ONE release: parent picker in + every group_name surface out together. Totals/caps sum individual rows only. Version ٢٣٫٢ · 23.2.

### v24 — Tags + Waitlist (STASHED WIP — resume after v23.2)

### ✅ SHIPPED: v22.1 (0a8373b), v23 (7537922) — see git log

### ⏳ v22.1 — SHIPPED (reference)
- Bilingual guest names: displayName() follows UI language (name_en fallback name); search matches both; optional English-name field in Edit; export "Name (English)" column.
- Label fix: no "church for everyone" text anywhere; no ⛪ chip on cards; add/edit = bare checkboxes 🏛 Reception (default ✓; unchecked ⇒ church_only:true) / 🌿 Henna Party; send-modal pills as checkbox rows; label "Included"/"الدعوة شاملة".
- Send modal phone fixes: sticky pinned header (‹ · N / M + name · › · ✕) visible at any scroll; arrows ≥44px gold; swipe with horizontal-intent detection (|dx|≥60 ∧ |dx|>|dy|·1.5); responsive at 390×844.

### v23 — Organizer batch (one build)
1. Groups: optional group_name via "＋ More options" expander in add/edit (datalist of existing); group chip on card; "By group" organizer view with counts; export Group column.
2. Member names: optional per-person name inputs matching count (members jsonb); under same expander; export Members column; shown small in send modal.
3. Read-only cards: remove +/− and 🌿/🏛 quick-toggles from cards; count as text; only Send invite + Edit buttons remain. Count stepper lives in Edit.
4. Red no-number cards (.nophone red border/tint; 📵 badge stays; green .sent wins) + missing-number families always sort to top.
5. ✅ Confirmed: bare checkbox in Edit (ar ✅ أكدوا); ✅ chip on card; filter with count; export column. Column `confirmed` exists.
6. Soft delete: Edit's Delete PATCHes deleted_at (no API DELETE anywhere); default views/counts/caps/nav/export exclude deleted; 🗑 filter (exclusive) shows dimmed cards with Restore/استرجاع; undo = PATCH null. Column `deleted_at` exists.
7. Unified stacking filters: Bride/Groom side selector becomes chips in the one filter row; multi-select AND semantics; exclusive pairs auto-swap (Sent/Not-sent, Bride/Groom, 🗑 vs rest); chips: Sent/Not-sent/✅/🌿/🏛 Reception/⛪ only/📵/🗑 + live counts; sort control 📵-first (default) / A–Z / Last name (last token of displayName, localeCompare per UI lang).
8. **Henna headcount (MUST — Joseph 07-30, pulled forward from v26):** per-family henna seats. In Edit, when 🌿 Henna Party is checked, a mini stepper next to it (range 0..count) writes `seats` jsonb, v26-spec semantics scoped to henna: no key = full count (dim/inherited); override = {"henna": n} (gold); stepping to count deletes the key; stepping to 0 unchecks Henna (event2=false) and deletes the key; count changes re-clamp in the same PATCH. Card henna chip shows the number when overridden (🌿 ٣ / 🌿 3). Export: Henna column "YES (3)" when overridden. Driving case: شنوده مسعود / ملكه سلامه — 5 at the wedding, immediate family only at the henna. Column `seats` exists. Message copy unchanged.
9. **Invalid-number indicator (Joseph 07-30):** distinct from 📵 missing. Validate digit count per country code (+20 ⇒ local mobile 10 digits starting with 1; +1 ⇒ 10 digits; else lenient 8–15). Mismatch ⇒ amber `⚠️` badge on the card (en `⚠️ Check number`, ar `⚠️ راجعوا الرقم`) + amber card border (below red/green precedence: green sent > red missing > amber wrong), counted in a ⚠️ filter chip. Never blocks saving or sending. Suspects driving this: 5 families with +20 numbers of wrong length (possibly +20 slapped on non-Egyptian numbers).
- Version ٢٣٫٠ · 23.0.

### v24 — Tags + Waitlist
Full spec: `/private/tmp/claude-501/-Users-josephlabib/550cbbf3-ffef-4e7d-96fb-e4e5fa426dad/tasks/w17sbmbll.output` (read it). Columns `tags` jsonb + `waitlist` bool exist. Reconciliation rulings (v23 wins): cards stay read-only; tag row is an AND term over the multi-select filters; ⏳ exclusive like 🗑; deleted waitlist rows show under 🗑 only. Version ٢٤٫٠ · 24.0.

### v25 — IA restructure
Full spec (V25 section): `/private/tmp/claude-501/-Users-josephlabib/550cbbf3-ffef-4e7d-96fb-e4e5fa426dad/tasks/wa3quh2os.output` (read it). Hash router; role-driven hamburger (mom: 3 rows max, master adds Tables/Waitlist/Deleted/Tags/Settings); photos → own screen wholesale; one-screen add (Church pill dims Reception pill — that interaction IS the explanation); fast bulk lane → own screen unchanged. Zero schema change. Version ٢٥٫٠ · 25.0.

### v26 — Event Setup phase 1
Full spec: `/private/tmp/claude-501/-Users-josephlabib/550cbbf3-ffef-4e7d-96fb-e4e5fa426dad/tasks/wccs514z2.output` (read it). Table `wedding_events` + `seats` jsonb column exist, seeded (church universal, reception 400 = 200 bride + derived groom, henna cap 75). Legacy booleans stay authoritative for core events; seats stores exceptions only; groom alloc always derived. Settings sheet master-only. Version ٢٦٫٠.

### v27 — Seating chart phase 1
Full spec (labeled "V26" INSIDE the same file — numbering offset, seating is OUR v27): `/private/tmp/claude-501/-Users-josephlabib/550cbbf3-ffef-4e7d-96fb-e4e5fa426dad/tasks/wa3quh2os.output`. seating_tables DDL (event_key aligns with wedding_events.id), unseated pool, touch-first tap/drag assignment, fill meters. Nova runs the DDL migration at hand-off. Version ٢٧٫٠ · 27.0.

## INVARIANTS (every version)
- Labels are bare nouns. No sentences, no questions, no explainer text, never state facts true of everyone. (Joseph escalated over this once — never again.)
- Mom's contributor flow only ever gets simpler. Master-only surfaces stay master-only.
- Cards are read-only indicators (from v23 on). Edit is the only mutation surface (send-modal Included checkboxes are the one exception).
- Arabic strings byte-exact as specced; Egyptian colloquial register in the ar table.
- Message copy/link routing (INVITE_COPY, inviteLink, church cat/g link) changes only on explicit instruction.
- No hard DELETE API calls (from v23 on). No auto-sending ever — every channel opens a draft only.
- INVITE_TEMPLATE_VERSION bumps only when message copy changes.

## OPEN ITEMS (Joseph, not implementers)
- ~~روماني برسوم~~ CONFIRMED correct 07-30. ~~wooow 0000 dupes~~ DELETED by Joseph 07-30 — import file now usable as numbers arrive. ~~new-family counts~~ Joseph adds manually.
- Recheck 5 malformed numbers (possibly +20 prefixed onto non-EG numbers): كيرستين برسوم، برسوم سلامه، ماري ميخائيل، نيفين مكرم، ملكه سلامه — v23 item 9 will surface these with ⚠️.
- Church-only: NO current guests are church-only (all invited to church + reception). The ⛪-off path stays available for the groom side later.
