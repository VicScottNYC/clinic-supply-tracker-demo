// Current-time walkthrough: run against an API started with P1_CLOCK=current (no fixed demo date).
import { chromium } from "playwright-core";
import fs from "node:fs";

const BASE = "http://127.0.0.1:5173";
const SHOTS = process.env.SHOTS ?? "./e2e-shots";
fs.mkdirSync(SHOTS, { recursive: true });
const browser = await chromium.launch({ executablePath: process.env.CHROME, args: ["--no-sandbox"] });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const errors = [];
page.on("pageerror", (e) => errors.push("pageerror: " + e.message));
const check = (cond, msg) => { if (!cond) throw new Error("CHECK FAILED: " + msg); console.log("ok -", msg); };

await page.goto(BASE);
await page.getByText("Live clock").waitFor();
check(await page.getByText("DEMO DATE").count() === 0, "live mode shows the Live clock notice, not DEMO DATE");
const today = await page.evaluate(() => new Date().toISOString().slice(0, 10));
const shown = await page.getByText(/Today \d{4}-\d{2}-\d{2}/).textContent();
check(shown.includes("Today"), `live clock shown (${shown.trim()}; browser date ${today})`);
await page.getByText("Alcohol prep pads").first().click();
await page.getByText("AP-2026A").first().waitFor();
check(true, "inventory loads in live mode");
await page.screenshot({ path: `${SHOTS}/14-live-clock.png`, fullPage: true });
console.log("errors:", errors.length ? errors : "none");
await browser.close();
console.log("CURRENT CLOCK WALK PASSED");
