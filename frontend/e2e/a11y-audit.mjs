// Automated accessibility audit (axe-core, pinned in devDependencies) on each P1 screen in five states:
// desktop light/dark, phone (375 px) light/dark, and 640 CSS px reflow (not browser zoom). It records axe violations and
// horizontal page scroll. Exits 1 if any state has a violation or horizontal scroll, so it can gate a run.
// Scope: automated rules only. It does not replace screen-reader or keyboard checks (see keyboard-check.mjs).
import { chromium } from "playwright-core";
import fs from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const axeSource = fs.readFileSync(require.resolve("axe-core/axe.min.js", { paths: [process.cwd()] }), "utf8");
const BASE = process.env.BASE ?? "http://127.0.0.1:5173";
const OUT = process.env.OUT ?? "./a11y-shots";
fs.mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({ executablePath: process.env.CHROME, args: ["--no-sandbox"] });
const screens = [
  { tab: "Inventory", heading: /^Inventory/ },
  { tab: "Receive supplies", heading: /^Receive supplies/ },
  { tab: "Issue supplies", heading: /^Issue supplies/ },
  { tab: "Counts", heading: /^Counts/ },
  { tab: "History", heading: /^History/ },
];
const states = [
  { mode: "desktop-light", width: 1280, scheme: "light" },
  { mode: "desktop-dark", width: 1280, scheme: "dark" },
  { mode: "phone-light", width: 375, scheme: "light" },
  { mode: "phone-dark", width: 375, scheme: "dark" },
  { mode: "reflow640-light", width: 640, scheme: "light" }, // 640 CSS px reflow viewport (not browser zoom)
];

const results = {};
let failures = 0;
for (const st of states) {
  const ctx = await browser.newContext({ viewport: { width: st.width, height: 844 }, colorScheme: st.scheme });
  const page = await ctx.newPage();
  await page.goto(BASE);
  await page.getByText("DEMO DATE").waitFor({ timeout: 15000 });
  for (const s of screens) {
    await page.getByRole("button", { name: s.tab, exact: true }).click();
    // Wait for the screen's own heading and for the loading state to clear, not a fixed delay.
    await page.waitForFunction(
      (src) => {
        const h = document.querySelector("section h2");
        return !!h && new RegExp(src).test(h.textContent ?? "") && !document.body.innerText.includes("Loading…");
      },
      s.heading.source,
      { timeout: 15000 },
    );
    await page.waitForLoadState("networkidle");
    await page.evaluate(axeSource);
    const violations = await page.evaluate(async () => {
      const res = await window.axe.run(document, { resultTypes: ["violations"] });
      return res.violations.map((v) => ({
        id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.length,
        sample: v.nodes.slice(0, 2).map((n) => n.target.join(" ")),
      }));
    });
    const horizontalScroll = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
    const key = `${st.mode}/${s.tab}`;
    results[key] = { violations, horizontalScroll };
    if (violations.length || horizontalScroll) failures++;
    await page.screenshot({ path: `${OUT}/${st.mode}-${s.tab.replace(/\s+/g, "-").toLowerCase()}.png`, fullPage: true });
    console.log(`${violations.length || horizontalScroll ? "FAIL" : "ok  "} ${key}` +
      (violations.length ? ` violations=${violations.map((v) => v.id).join(",")}` : "") +
      (horizontalScroll ? " horizontal-scroll" : ""));
  }
  await ctx.close();
}
await browser.close();
fs.writeFileSync(`${OUT}/audit.json`, JSON.stringify(results, null, 2));
console.log(`${Object.keys(results).length} checks, ${failures} with findings`);
process.exitCode = failures ? 1 : 0;
