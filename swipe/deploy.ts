// One-command deploy: compress round3 renders → swipe/img, rebuild manifest.js from
// manifest-plan.json + judge results, bump version marker, commit+push.
// Usage: bun deploy.ts [--push] [--judges <json-file>]
import { execSync } from "child_process";
import { readFileSync, writeFileSync, readdirSync, existsSync, statSync } from "fs";

const R3 = "/Users/josephlabib/Downloads/2-sister-wedding/invite/renderings/round3";
const SWIPE = "/Users/josephlabib/code/wedding-family-list/swipe";
const push = process.argv.includes("--push");
const jIdx = process.argv.indexOf("--judges");

const plan = JSON.parse(readFileSync(`${R3}/manifest-plan.json`, "utf8"));

// 1 — compress every finished PNG that is new or changed
let compressed = 0;
for (const f of readdirSync(R3).filter(f => f.endsWith(".png"))) {
  const src = `${R3}/${f}`;
  const dst = `${SWIPE}/img/${f.replace(/\.png$/, ".jpg")}`;
  if (existsSync(dst) && statSync(dst).mtimeMs > statSync(src).mtimeMs) continue;
  execSync(`sips -s format jpeg -s formatOptions 72 "${src}" --out "${dst}"`, { stdio: "pipe" });
  compressed++;
}

// 2 — judge results (accumulate across phases in critiques.json)
const critFile = `${SWIPE}/critiques.json`;
let crit: Record<string, any> = existsSync(critFile) ? JSON.parse(readFileSync(critFile, "utf8")) : {};
if (jIdx > -1) {
  const incoming = JSON.parse(readFileSync(process.argv[jIdx + 1], "utf8"));
  for (const r of incoming) if (r && r.name) crit[r.name] = { verdict: r.verdict, score: r.score, critique: r.critique, tags: r.tags };
}
writeFileSync(critFile, JSON.stringify(crit, null, 1));

// 3 — manifest: only themes whose invite jpg exists and judge did not fail
const deck = plan.themes
  .filter((t: any) => existsSync(`${SWIPE}/img/${t.id}--invite.jpg`))
  .filter((t: any) => (crit[`${t.id}--invite`]?.verdict ?? "pass") !== "fail")
  .map((t: any) => ({ theme: t.id, theme_en: t.en, theme_ar: t.ar }));
writeFileSync(`${SWIPE}/manifest.js`, "window.DECK = " + JSON.stringify(deck) + ";\n");

// 4 — version bump in index.html (Arabic-Indic + Latin marker)
const AR = ["٠","١","٢","٣","٤","٥","٦","٧","٨","٩"];
let html = readFileSync(`${SWIPE}/index.html`, "utf8");
html = html.replace(/<p class="ver">.*?<\/p>/, (m) => {
  const n = (parseInt((m.match(/· (\d+)/) || [])[1] || "1", 10)) + 1;
  const ar = String(n).split("").map(d => AR[+d]).join("");
  return `<p class="ver">${ar} · ${n}</p>`;
});
writeFileSync(`${SWIPE}/index.html`, html);

// 5 — parse gate before any push
const blocks = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)].filter(m => !/src=/.test(m[0]));
for (const m of blocks) new Function(m[1]);

console.log(`compressed=${compressed} deck=${deck.length} critiques=${Object.keys(crit).length}`);
if (push) {
  execSync(`cd "${SWIPE}/.." && git add -A && git commit -q -m "swipe: deck ${deck.length} themes, ${Object.keys(crit).length} judged" && git push -q origin main`, { stdio: "pipe" });
  console.log("PUSHED");
}
