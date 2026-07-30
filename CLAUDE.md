# wedding-family-list — Work Queue & Rules

Single-file guest-list app (index.html) + Playwright suite (tests/). Supabase project `wydodvbkqhpoyefcwpnf`, GitHub Pages deploy from `main`. Nova (main session) reviews, commits, pushes, and live-verifies; Forge/codex implements. **No commits by implementers. Never delete or `git clean` files you didn't create — this file is authoritative. Suite must be green on Chromium + WebKit before any report.**

## THE QUEUE — strictly one version at a time, top to bottom

Work ONLY the topmost unshipped version. Do not start the next until the current one is reported, reviewed, committed, pushed, and live-verified. No mid-run scope additions: new asks get appended here by Nova and wait their turn.

### ⏳ v22.1 — IN FLIGHT (finish + report)
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
- Confirm روماني برسيم = روماني برسوم (phone +201220900401 applied to برسوم).
- People counts for new rows مارينا ميلاد, استفانوس (both inserted as 1).
- Recheck 5 malformed numbers: كيرستين برسوم، برسوم سلامه، ماري ميخائيل، نيفين مكرم، ملكه سلامه.
- wooow site: delete 0000 duplicates, then import `~/Downloads/Wooow Import — Guests Awaiting Numbers.csv` rows as numbers arrive.
- Mark church-only families (Reception checkbox off) — 0 marked so far.
