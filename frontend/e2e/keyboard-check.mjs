// Keyboard-only workflow check. No mouse clicks: focus moves only with Tab and Shift+Tab, and actions use Enter or
// Space. It covers all five screens, the Receive error focus, the Issue error focus, the Counts row error focus,
// the pending-request banner with keyboard Retry, and that each focused control shows a visible outline (nonzero
// width). Run it on a FRESH database: it posts one receipt, one issue, one count start/submit/cancel, and one retry.
import { chromium } from "playwright-core";
import fs from "node:fs";

const BASE = process.env.BASE ?? "http://127.0.0.1:5173";
fs.mkdirSync("./a11y-shots", { recursive: true });
const browser = await chromium.launch({ executablePath: process.env.CHROME, args: ["--no-sandbox"] });
const page = await (await browser.newContext({ viewport: { width: 1280, height: 900 } })).newPage();
let failed = 0;
const check = (cond, msg) => { console.log((cond ? "ok   - " : "FAIL - ") + msg); if (!cond) failed++; };

// Press Tab until the focused element matches the target (by aria-label or text, optionally with a selector).
async function tabTo(target, max = 200) {
  for (let i = 0; i < max; i++) {
    const hit = await page.evaluate((t) => {
      const norm = (e) => (e.getAttribute("aria-label") || e.textContent || "").replace(/\s+/g, " ").trim();
      const el = document.activeElement;
      if (!el) return false;
      if (t.selector && !el.matches(t.selector)) return false;
      return t.text === undefined || norm(el) === t.text;
    }, target);
    if (hit) return;
    await page.keyboard.press("Tab");
  }
  throw new Error(`Tab could not reach ${JSON.stringify(target)}`);
}
// Outline is visible: the style is not none and its width is above zero.
async function ringOk() {
  return page.evaluate(() => {
    const cs = getComputedStyle(document.activeElement);
    return cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) > 0;
  });
}
const focusedIs = (selector) => page.evaluate((s) => document.activeElement?.matches(s) ?? false, selector);
const visibleText = (t) => page.getByText(t).first().waitFor({ timeout: 10000 }).then(() => true, () => false);
const heading = (re) => page.waitForFunction((src) => new RegExp(src).test(document.querySelector("section h2")?.textContent ?? ""), re.source, { timeout: 10000 });

await page.goto(BASE);
await page.getByText("DEMO DATE").waitFor();

// 1. Inventory: expand a product with the keyboard.
await tabTo({ selector: "button.row-toggle" });
check(await ringOk(), "Inventory: row toggle shows a visible focus outline");
await page.keyboard.press("Enter");
check(await page.locator("button.row-toggle").first().getAttribute("aria-expanded") === "true", "Inventory: Enter expands the product");
check(await page.locator("tr.lot-row").first().isVisible(), "Inventory: lot rows are shown after expanding");
await page.keyboard.press("Space");
check(await page.locator("button.row-toggle").first().getAttribute("aria-expanded") === "false", "Inventory: Space collapses it again");

// 2. History: switch tab with the keyboard, expand a transaction, then collapse it with Space.
await tabTo({ text: "History" });
await page.keyboard.press("Enter");
await heading(/^History/);
await tabTo({ selector: "button.row-toggle" });
check(await ringOk(), "History: transaction toggle shows a visible focus outline");
await page.keyboard.press("Enter");
check(await visibleText("Occurred (UTC)"), "History: Enter expands the movements table");
await page.keyboard.press("Space");
check(await page.locator("button.row-toggle").first().getAttribute("aria-expanded") === "false", "History: Space collapses it");

// 3. Receive: error focus on an empty lot, then a successful receipt from the keyboard.
await tabTo({ text: "Receive supplies" });
await page.keyboard.press("Enter");
await heading(/^Receive supplies/);
await tabTo({ selector: 'input[aria-label="Lot for line 1"]' });
check(await ringOk(), "Receive: lot field shows a visible focus outline");
await tabTo({ text: "Post receipt" });
await page.keyboard.press("Enter");
check(await visibleText("Enter the lot number."), "Receive: an empty lot shows a field error");
check(await focusedIs('input[aria-label="Lot for line 1"]'), "Receive: focus moves to the first invalid field");
await page.keyboard.type("KB-LOT-1");
await tabTo({ selector: 'input[aria-label="Quantity for line 1"]' });
await page.keyboard.type("2");
await tabTo({ text: "Post receipt" });
check(await ringOk(), "Receive: Post button shows a visible focus outline");
await page.keyboard.press("Enter");
check(await visibleText(/Receipt #\d+ posted/), "Receive: receipt posts from the keyboard");

// 4. Issue: error focus on an empty quantity, then a successful issue.
await tabTo({ text: "Issue supplies" });
await page.keyboard.press("Enter");
await heading(/^Issue supplies/);
await tabTo({ text: "Issue" });
await page.keyboard.press("Enter");
check(await visibleText("Enter a whole number of at least 1."), "Issue: empty quantity shows a field error");
check(await focusedIs("#issue-qty"), "Issue: focus moves to the quantity field");
await page.keyboard.type("1");
await tabTo({ text: "Issue" });
await page.keyboard.press("Enter");
check(await visibleText("Issue #"), "Issue: issue posts from the keyboard");

// 5. Counts: start, enter a discrepancy with row error focus, submit, then cancel with confirmation.
await tabTo({ text: "Counts" });
await page.keyboard.press("Enter");
await heading(/^Counts/);
await tabTo({ text: "Start count" });
await page.keyboard.press("Enter");
check(await visibleText("Area locked for counting"), "Counts: start count from the keyboard");
await tabTo({ selector: 'input[aria-label^="Physical count"]' });
await page.keyboard.type("x");
await tabTo({ text: "Submit" });
await page.keyboard.press("Enter");
check(await focusedIs('input[aria-label^="Physical count"]'), "Counts: a non-number count moves focus back to the row input");
await page.keyboard.press("Control+A");
await page.keyboard.type(String((Number((await page.locator("tr.needs-count").first().locator("td").nth(2).innerText()).trim()) + 5)));
await tabTo({ text: "Submit" });
await page.keyboard.press("Enter");
check(await visibleText("Recount"), "Counts: an out-of-tolerance count is marked for recount");
await tabTo({ text: "Cancel count…" });
await page.keyboard.press("Enter");
await tabTo({ selector: "button.danger" });
check(await ringOk(), "Counts: the confirmation button shows a visible focus outline");
await page.keyboard.press("Enter");
check(await visibleText("Count cancelled"), "Counts: the cancelled count releases the area");

// 6. Pending request: one dropped response, focus moves to the banner, Retry from the keyboard recovers.
let first = true;
await page.route("**/api/receipts", (route) => {
  if (first) { first = false; return route.abort(); }
  return route.continue();
});
await tabTo({ text: "Receive supplies" });
await page.keyboard.press("Enter");
await heading(/^Receive supplies/);
await tabTo({ selector: 'input[aria-label="Lot for line 1"]' });
await page.keyboard.type("KB-LOT-2");
await tabTo({ selector: 'input[aria-label="Quantity for line 1"]' });
await page.keyboard.type("3");
await tabTo({ text: "Post receipt" });
await page.keyboard.press("Enter");
check(await visibleText("Outcome unknown"), "Pending: the dropped response is reported as an unknown outcome");
check(await focusedIs(".focus-target") || (await page.evaluate(() => !!document.activeElement?.closest(".focus-target"))),
  "Pending: focus moves to the pending banner");
check(await ringOk(), "Pending: the banner shows a visible focus outline");
await tabTo({ text: "Retry same request" });
await page.keyboard.press("Enter");
check(await visibleText(/Receipt #\d+ posted/), "Pending: Retry from the keyboard completes the same request");
await page.unroute("**/api/receipts");
await page.screenshot({ path: "./a11y-shots/keyboard-complete.png", fullPage: true });

await browser.close();
console.log(failed ? `${failed} keyboard check(s) failed` : "KEYBOARD CHECKS PASSED");
process.exitCode = failed ? 1 : 0;
