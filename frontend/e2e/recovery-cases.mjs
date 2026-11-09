// Retry-safety browser cases. The lot and packing-slip names are fixed (NG-503-A, NG-NAV-B, NG-COMMIT-C and
// their PS- slips). Run on a fresh database for a clean record: re-running on the same database posts more
// receipts with those names, and the second run then gets duplicate-slip warnings (non-blocking). Each case
// checks the request the browser actually sent.
import { chromium } from "playwright-core";
import fs from "node:fs";

const BASE = "http://127.0.0.1:5173";
const SHOTS = process.env.SHOTS ?? "./e2e-shots";
fs.mkdirSync(SHOTS, { recursive: true });
const browser = await chromium.launch({ executablePath: process.env.CHROME, args: ["--no-sandbox"] });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const consoleErrors = [];
page.on("pageerror", (e) => consoleErrors.push("pageerror: " + e.message));
const check = (cond, msg) => { if (!cond) throw new Error("CHECK FAILED: " + msg); console.log("ok -", msg); };
const tab = (name) => page.getByRole("button", { name, exact: true }).click();
const row = (i) => page.locator("table.lines tbody tr").nth(i);
const shot = (n) => page.screenshot({ path: `${SHOTS}/${n}.png`, fullPage: true });

async function openReceive() {
  await page.goto(BASE);
  await page.getByText("DEMO DATE").waitFor();
  await tab("Receive supplies");
}
async function fill(lot, qty, slip) {
  await row(0).locator("td").nth(1).locator("input").fill(lot); // P-001 needs no expiration
  await page.getByLabel("Quantity for line 1").fill(String(qty));
  await page.getByLabel("Packing slip reference (optional)").fill(slip);
  await page.getByRole("button", { name: "Post receipt" }).click();
}
// Server truth for one lot at Riverside, read outside the page so route interception does not touch it.
async function onHand(lot) {
  const r = await page.request.get(`${BASE}/api/balances?loc=RFC`);
  const body = await r.json();
  return body.balances.filter((b) => b.sku === "P-001" && b.lot === lot).reduce((n, b) => n + b.on_hand, 0);
}
const sameRequest = (a, b) => a.key === b.key && a.actor === b.actor && a.body === b.body;

// A. Structured 503: a JSON error body does not make a 5xx certain.
await openReceive();
const a = [];
let aCalls = 0;
await page.route("**/api/receipts", async (route) => {
  const r = route.request();
  a.push({ key: r.headers()["idempotency-key"], actor: r.headers()["x-actor-id"], body: r.postData() });
  if (aCalls++ === 0) {
    return route.fulfill({ status: 503, contentType: "application/json",
      body: JSON.stringify({ error: "SERVICE_UNAVAILABLE", message: "The service is restarting." }) });
  }
  return route.continue();
});
await fill("NG-503-A", 2, "PS-503-A");
await page.getByText(/Outcome unknown/).waitFor();
check(await page.getByText("The service is restarting.").count() > 0, "structured 503 shows the server message as unknown");
check(await page.getByRole("button", { name: "Post receipt" }).count() === 0, "503 leaves no way to submit a different receipt");
await shot("11-structured-503-unknown");
await page.getByRole("button", { name: "Retry same request" }).click();
await page.getByText(/Receipt #\d+ posted/).waitFor();
await page.unroute("**/api/receipts");
check(a.length === 2 && sameRequest(a[0], a[1]), "503 retry sent the same key, actor and body");

// B. Navigation and actor change after a dropped response: the pending write survives and is retried unchanged.
await openReceive();
const b = [];
let bFirst = true;
await page.route("**/api/receipts", (route) => {
  const r = route.request();
  b.push({ key: r.headers()["idempotency-key"], actor: r.headers()["x-actor-id"], body: r.postData() });
  if (bFirst) { bFirst = false; return route.abort(); }
  return route.continue();
});
await fill("NG-NAV-B", 3, "PS-NAV-B");
await page.getByText(/Outcome unknown/).waitFor();
await tab("Issue supplies");
check(await page.getByText(/Outcome unknown/).count() > 0, "pending banner stays visible after changing screen");
check(await page.getByLabel("Clinic").isDisabled(), "clinic cannot change while the outcome is unknown");
check(await page.getByLabel("Demo actor").isDisabled(), "actor cannot change while the outcome is unknown");
check(await page.getByRole("button", { name: "Issue", exact: true }).count() === 0, "Issue screen offers no submit while a write is pending");
await tab("Receive supplies"); // the form is rebuilt: the retry must not depend on it
check(await page.getByLabel("Packing slip reference (optional)").inputValue() === "", "receive form was rebuilt (retry uses the stored request)");
await shot("12-navigation-pending");
await page.getByRole("button", { name: "Retry same request" }).click();
await page.getByText(/Receipt #\d+ posted/).waitFor();
await page.unroute("**/api/receipts");
check(b.length === 2 && sameRequest(b[0], b[1]), "retry after navigation sent the original key, actor and body");
check(await page.getByLabel("Clinic").isEnabled(), "clinic unlocked once the pending write resolved");

// C. The server commits, then the response is lost. Retry must not create a second receipt.
await openReceive();
const before = await onHand("NG-COMMIT-C");
let committedBody = null;
const c = [];
await page.route("**/api/receipts", async (route) => {
  const r = route.request();
  c.push({ key: r.headers()["idempotency-key"], actor: r.headers()["x-actor-id"], body: r.postData() });
  const upstream = await route.fetch(); // the server processes and commits the receipt here
  committedBody = await upstream.json();
  await route.abort(); // the browser never sees that response
});
await fill("NG-COMMIT-C", 4, "PS-COMMIT-C");
await page.getByText(/Outcome unknown/).waitFor();
const committedAt = await onHand("NG-COMMIT-C");
check(committedAt === before + 4, "server committed the receipt before the response was lost");
await page.unroute("**/api/receipts");
await page.getByRole("button", { name: "Retry same request" }).click();
const notice = await page.getByText(/Receipt #\d+ posted/).textContent();
check(notice.includes(`#${committedBody.transaction_id} `), `retry returned the committed receipt #${committedBody.transaction_id}, not a new one`);
check(c.length === 1 && c[0].key && (await onHand("NG-COMMIT-C")) === before + 4,
  "stock rose by 4 exactly once after the retry");
await shot("13-commit-before-response-lost");

console.log("console errors:", consoleErrors.length ? consoleErrors : "none");
await browser.close();
console.log("RECOVERY CASES PASSED");
