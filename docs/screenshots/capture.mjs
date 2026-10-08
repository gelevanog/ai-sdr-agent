// Screenshots of the dashboard (and Mailpit) with headless Chrome (puppeteer-core).
//
//   PUPPETEER_FROM=<dir with node_modules/puppeteer-core> DRAFT_ID=12 ACCOUNT_ID=5 \
//     node docs/screenshots/capture.mjs http://localhost:3000 [shots...]
//
// Shots: hero, dossier, accounts, queue, sequences, replies, mailpit, evaluation, audit, settings.
// Every page shows data from the running API; see README > Screenshots for which model produced it.
import { createRequire } from "module";

const require = createRequire(process.env.PUPPETEER_FROM || import.meta.url);
const puppeteer = require("puppeteer-core");
const [base, ...shots] = process.argv.slice(2);
const out = process.env.OUT_DIR || new URL(".", import.meta.url).pathname;
const mailpit = process.env.MAILPIT_URL || "http://localhost:8025";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const browser = await puppeteer.launch({
  executablePath: process.env.CHROME || "/usr/bin/google-chrome",
  headless: "new",
  args: ["--no-sandbox"],
});

async function open(url, { width = 1440, height = 900 } = {}) {
  const page = await browser.newPage();
  await page.setViewport({ width, height, deviceScaleFactor: 1.5 });
  await page.emulateMediaFeatures([{ name: "prefers-color-scheme", value: "light" }]);
  page.on("console", (msg) => {
    if (msg.type() === "error") console.log("page error:", msg.text());
  });
  await page.goto(url, { waitUntil: "networkidle0" });
  await sleep(700);
  return page;
}

async function full(page, name, { maxHeight = 2600 } = {}) {
  const height = await page.evaluate(() => document.documentElement.scrollHeight);
  await page.setViewport({ width: page.viewport().width, height: Math.min(height, maxHeight), deviceScaleFactor: 1.5 });
  await sleep(400);
  await page.screenshot({ path: `${out}${name}.png` });
  console.log(`${name}.png`);
}

for (const shot of shots) {
  if (shot === "hero") {
    const page = await open(`${base}/review/${process.env.DRAFT_ID}`, { height: 1050 });
    await page.evaluate(() => document.querySelector("mark.claim")?.click());
    await sleep(500);
    await full(page, "hero", { maxHeight: 1500 });
  } else if (shot === "dossier") {
    const page = await open(`${base}/accounts/${process.env.ACCOUNT_ID}`);
    await full(page, "dossier", { maxHeight: 2300 });
  } else if (shot === "accounts") {
    const page = await open(`${base}/accounts`);
    await full(page, "accounts", { maxHeight: 1700 });
  } else if (shot === "queue") {
    const page = await open(`${base}/review`);
    await full(page, "queue", { maxHeight: 1500 });
  } else if (shot === "sequences") {
    const page = await open(`${base}/sequences`);
    await full(page, "sequences", { maxHeight: 1400 });
  } else if (shot === "replies") {
    const page = await open(`${base}/replies`);
    await full(page, "replies", { maxHeight: 1800 });
  } else if (shot === "mailpit") {
    const page = await open(mailpit, { height: 900 });
    await page.evaluate(() => {
      const links = [...document.querySelectorAll("a[href*='/view/']")];
      const first = links.find((a) => !a.innerText.includes("Re: ")) ?? links[0];
      first?.click();
    });
    await sleep(1500);
    await page.screenshot({ path: `${out}mailpit.png` });
    console.log("mailpit.png");
  } else if (shot === "evaluation") {
    const page = await open(`${base}/evaluation`);
    await full(page, "evaluation", { maxHeight: 2600 });
  } else if (shot === "audit") {
    const page = await open(`${base}/audit`, { height: 1000 });
    await page.screenshot({ path: `${out}audit-log.png` });
    console.log("audit-log.png");
  } else if (shot === "settings") {
    const page = await open(`${base}/settings`, { height: 1000 });
    await page.screenshot({ path: `${out}settings.png` });
    console.log("settings.png");
  } else if (shot === "overview") {
    const page = await open(`${base}/`, { height: 900 });
    await page.screenshot({ path: `${out}overview.png` });
    console.log("overview.png");
  } else {
    console.log(`unknown shot ${shot}`);
  }
}
await browser.close();
