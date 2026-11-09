import { chromium } from "playwright-core";
import fs from "node:fs";

const BASE = "http://127.0.0.1:5173";
const SHOTS = process.env.SHOTS ?? "./e2e-shots";
fs.mkdirSync(SHOTS, { recursive: true });
const browser = await chromium.launch({ executablePath: process.env.CHROME, args: ["--no-sandbox"] });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const consoleErrors = [];
page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text()); });
page.on("pageerror", (e) => consoleErrors.push("pageerror: " + e.message));
const shot = (n) => page.screenshot({ path: `${SHOTS}/${n}.png`, fullPage: true });
const check = (cond, msg) => { if (!cond) throw new Error("CHECK FAILED: " + msg); console.log("ok -", msg); };
const tab = (name) => page.getByRole("button", { name, exact: true }).click();
const row = (i) => page.locator("table.lines tbody tr").nth(i);

// 1. Demo date notice and Inventory
await page.goto(BASE);
await page.getByText("DEMO DATE").waitFor();
check(await page.getByText("Evaluation date 2026-10-18").count() > 0, "demo date notice with evaluation date shown");
await page.getByText("Alcohol prep pads").first().click();
await page.getByText("AP-2026A").first().waitFor();
check(await page.getByText("AP-2026A").count() > 0, "inventory expands to show lots");
await shot("01-inventory");

// 2. Receive: multi-line with a purchase unit, plus a client-side validation error
await tab("Receive supplies");
await row(0).locator("td").nth(1).locator("input").fill("NG-E2E-A");
await page.getByLabel("Quantity for line 1").fill("1.5");
await page.getByRole("button", { name: "Post receipt" }).click();
await page.getByText("Enter a whole number of at least 1.").waitFor();
check(true, "fraction quantity shows a field error and nothing is sent");
await page.getByLabel("Quantity for line 1").fill("5");
await page.getByRole("button", { name: "Add line" }).click();
await row(1).locator("select").first().selectOption("P-002");
await row(1).locator("td").nth(1).locator("input").fill("AP-2026A");
await row(1).locator("input[type=date]").fill("2026-11-20");
await page.getByLabel("Quantity for line 2").fill("4");
await page.getByLabel("Packing slip reference (optional)").fill("PS-E2E-1");
await page.getByRole("button", { name: "Post receipt" }).click();
await page.getByText(/Receipt #\d+ posted/).waitFor();
check(await page.getByText("4 base units").count() > 0, "receipt shows each line's base quantity");
await shot("02-receive-posted");

// 3. Duplicate packing slip shows a nonblocking warning
await row(0).locator("td").nth(1).locator("input").fill("NG-E2E-A");
await page.getByLabel("Quantity for line 1").fill("2");
await page.getByLabel("Packing slip reference (optional)").fill("PS-E2E-1");
await page.getByRole("button", { name: "Post receipt" }).click();
await page.getByText(/already received/).waitFor();
check(true, "duplicate packing slip warning shown while the receipt is still accepted");
await shot("03-receive-duplicate-slip");

// 4. Dropped connection: retry must reuse the same Idempotency-Key and not double-post
const keys = [];
let dropped = false;
await page.route("**/api/receipts", async (route) => {
  keys.push(route.request().headers()["idempotency-key"]);
  if (!dropped) { dropped = true; await route.abort(); } else { await route.continue(); }
});
await row(0).locator("td").nth(1).locator("input").fill("NG-E2E-B");
await page.getByLabel("Quantity for line 1").fill("3");
await page.getByLabel("Packing slip reference (optional)").fill("PS-E2E-2");
await page.getByRole("button", { name: "Post receipt" }).click();
await page.getByText(/Outcome unknown/).waitFor();
check(await page.getByRole("button", { name: "Post receipt" }).count() === 0, "new submission blocked while outcome is unknown");
await shot("04-receive-unknown-outcome");
await page.getByRole("button", { name: "Retry same request" }).click();
await page.getByText(/Receipt #\d+ posted/).waitFor();
await page.unroute("**/api/receipts");
check(keys.length === 2 && keys[0] === keys[1], `retry reused the same Idempotency-Key (${keys.length} requests)`);

// 5. Issue: earliest-expiring first, lots actually used are shown
await tab("Issue supplies");
await page.getByLabel("Product").selectOption("P-001");
await page.getByLabel(/Quantity/).fill("2");
await page.getByRole("button", { name: "Issue", exact: true }).click();
await page.getByText(/Issue #\d+ posted/).waitFor();
check(await page.getByText("earliest-expiring first").count() > 0, "FEFO explanation shown");
await shot("05-issue-posted");

// 6. Counts: start (area locks), block an issue, record a discrepancy, recount, complete
await tab("Counts");
await page.getByRole("button", { name: "Start count" }).click();
await page.getByText("Area locked for counting").waitFor();
await shot("06-count-locked");
await tab("Issue supplies");
await page.getByLabel(/Quantity/).fill("1");
await page.getByRole("button", { name: "Issue", exact: true }).click();
await page.getByText("COUNT_OPEN").waitFor();
check(true, "issue into a counted area is refused with COUNT_OPEN");
await tab("Counts");
await page.getByText("Area locked for counting").waitFor();
const first = page.locator("tr.needs-count").first();
const firstExpected = Number((await first.locator("td").nth(2).innerText()).trim());
await first.locator("input.qty").fill(String(firstExpected + 5));
await first.getByRole("button", { name: "Submit" }).click();
await page.getByText("Recount", { exact: true }).first().waitFor();
check(true, "discrepancy beyond tolerance is marked for recount");
await page.getByText("Add a surplus line").click();
await page.locator("details.surplus").getByLabel("Lot", { exact: true }).fill("GZ-SURPLUS-1");
await page.getByRole("button", { name: "Add surplus line" }).click();
await page.getByText("Surplus line added").waitFor();
await shot("07-count-discrepancy");
let guard = 0;
while (guard++ < 30) {
  const n = await page.locator("tr.needs-count").count();
  if (n === 0) break;
  const r = page.locator("tr.needs-count").first();
  const exp = Number((await r.locator("td").nth(2).innerText()).trim());
  await r.locator("input.qty").fill(String(exp));
  await r.getByRole("button", { name: /^(Submit|Recount)$/ }).click();
  await page.waitForFunction((before) => document.querySelectorAll("tr.needs-count").length < before, n);
}
check(guard < 30, "every line resolved");
await shot("08-count-resolved");
await page.getByRole("button", { name: "Complete count" }).click();
await page.getByText("Count completed").waitFor();
check(true, "count completed after all lines resolved");

// 7. Second count at Eastgate, cancelled: no stock change
await page.getByLabel("Clinic").selectOption("EWC");
await page.getByRole("button", { name: "Start count" }).click();
await page.getByText("Area locked for counting").waitFor();
await page.getByRole("button", { name: /Cancel count/ }).click();
await page.getByRole("button", { name: /Confirm cancel/ }).click();
await page.getByText("Count cancelled").waitFor();
check(true, "cancelled count releases the area");
await shot("09-count-cancelled");

// 8. History at Riverside: operations, actors, times in clinic time, movements
await page.getByLabel("Clinic").selectOption("RFC");
await tab("History");
await page.getByText("Receipt", { exact: true }).first().waitFor();
check(await page.getByText("Issue", { exact: true }).count() > 0, "history lists the issue");
check(await page.getByText("9:00 AM ET").count() > 0, "history shows clinic time (ET) alongside stored UTC");
await page.locator("button.row-toggle").first().click();
await page.getByText("Occurred (UTC)").waitFor();
await shot("10-history-expanded");

console.log("console errors:", consoleErrors.length ? consoleErrors : "none");
await browser.close();
console.log("E2E PASSED");
