#!/usr/bin/env node
/*
 * EngineAI local browser worker.
 *
 * This deliberately runs with a fresh Chromium profile. It cannot read the
 * owner's normal browser profile, cookies, password store, or downloads. Every
 * navigation and interaction is explicitly confirmed by the caller. Login and
 * password actions are intentionally not automated.
 */
import http from "node:http";
import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { chromium } from "playwright";

const HOST = process.env.ENGINEAI_BROWSER_HOST || "127.0.0.1";
const PORT = Number(process.env.ENGINEAI_BROWSER_PORT || 8105);
const ROOT = path.resolve(process.env.ENGINEAI_ROOT || "/home/fuad_ashraf/engineai");
const ARTIFACTS = path.join(ROOT, "out", "browser_artifacts");
const MAX_SESSIONS = 1;
const MAX_BODY_BYTES = 64 * 1024;
const sessions = new Map();
const events = [];

function id(prefix) {
  return `${prefix}_${crypto.randomUUID().replaceAll("-", "").slice(0, 16)}`;
}
function event(kind, detail = {}) {
  const row = { id: id("evt"), at: new Date().toISOString(), kind, ...detail };
  events.unshift(row);
  if (events.length > 100) events.length = 100;
  return row;
}
function json(res, status, body) {
  res.writeHead(status, { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" });
  res.end(JSON.stringify(body));
}
function text(res, status, body, type = "text/plain; charset=utf-8") {
  res.writeHead(status, { "content-type": type, "cache-control": "no-store" });
  res.end(body);
}
async function readJson(req) {
  let raw = "";
  for await (const chunk of req) {
    raw += chunk;
    if (Buffer.byteLength(raw) > MAX_BODY_BYTES) throw new Error("request body exceeds 64 KiB");
  }
  try { return raw ? JSON.parse(raw) : {}; }
  catch { throw new Error("request body must be JSON"); }
}
function requireApproval(body) {
  if (body?.confirm_external_send !== true) {
    throw new Error("set confirm_external_send=true for each browser navigation or action");
  }
}
function safeUrl(value) {
  let url;
  try { url = new URL(String(value || "")); }
  catch { throw new Error("url must be a valid absolute http(s) URL"); }
  if (!new Set(["http:", "https:"]).has(url.protocol)) throw new Error("only http(s) navigation is allowed");
  return url.toString();
}
function selector(value) {
  const out = String(value || "").trim();
  if (!out || out.length > 300) throw new Error("selector must be 1–300 characters");
  return out;
}
function blockedSensitive(value) {
  return /password|passcode|sign[ -]?in|log[ -]?in|auth/i.test(String(value || ""));
}
async function pageSummary(page) {
  const title = await page.title();
  const url = page.url();
  const body = await page.locator("body").innerText({ timeout: 5000 }).catch(() => "");
  return { title: title.slice(0, 300), url, text_excerpt: body.replace(/\s+/g, " ").slice(0, 1200) };
}
async function startSession(body) {
  requireApproval(body);
  if (sessions.size >= MAX_SESSIONS) throw new Error("one browser session is already active; close it before starting another");
  const target = safeUrl(body.url);
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ acceptDownloads: false });
  const page = await context.newPage();
  page.setDefaultTimeout(12_000);
  const sessionId = id("browser");
  try {
    event("browser.navigate.started", { session_id: sessionId, url: target });
    await page.goto(target, { waitUntil: "domcontentloaded", timeout: 30_000 });
    sessions.set(sessionId, { browser, context, page, created_at: new Date().toISOString(), last_url: page.url() });
    const summary = await pageSummary(page);
    event("browser.navigate.completed", { session_id: sessionId, url: summary.url, title: summary.title });
    return { session_id: sessionId, ...summary, policy: policy() };
  } catch (err) {
    await browser.close().catch(() => {});
    event("browser.navigate.failed", { session_id: sessionId, url: target, error: String(err.message || err) });
    throw err;
  }
}
function getSession(sessionId) {
  const session = sessions.get(sessionId);
  if (!session) throw new Error("unknown or closed browser session");
  return session;
}
async function act(sessionId, body) {
  requireApproval(body);
  const session = getSession(sessionId);
  const action = String(body.action || "");
  event("browser.action.started", { session_id: sessionId, action });
  if (action === "navigate") {
    const target = safeUrl(body.url);
    await session.page.goto(target, { waitUntil: "domcontentloaded", timeout: 30_000 });
  } else if (action === "click") {
    const value = selector(body.selector);
    if (blockedSensitive(value)) throw new Error("login/authentication actions are not automated; sign in yourself in a separately approved browser");
    await session.page.locator(value).click();
    await session.page.waitForLoadState("domcontentloaded", { timeout: 8_000 }).catch(() => {});
  } else if (action === "type") {
    const value = selector(body.selector);
    const input = String(body.text || "");
    if (!input || input.length > 4_000) throw new Error("text must be 1–4000 characters");
    if (blockedSensitive(value)) throw new Error("password/authentication fields are not automated");
    await session.page.locator(value).fill(input);
  } else if (action === "wait") {
    const milliseconds = Math.max(0, Math.min(Number(body.milliseconds || 500), 5_000));
    await session.page.waitForTimeout(milliseconds);
  } else if (action === "screenshot") {
    await fs.mkdir(ARTIFACTS, { recursive: true });
    const name = `${sessionId}_${Date.now()}.png`;
    const file = path.join(ARTIFACTS, name);
    await session.page.screenshot({ path: file, fullPage: true });
    const summary = await pageSummary(session.page);
    event("browser.screenshot.completed", { session_id: sessionId, artifact: name, url: summary.url });
    return { action, artifact: `/api/artifacts/${name}`, ...summary };
  } else {
    throw new Error("action must be navigate, click, type, wait, or screenshot");
  }
  const summary = await pageSummary(session.page);
  session.last_url = summary.url;
  event("browser.action.completed", { session_id: sessionId, action, url: summary.url });
  return { action, ...summary };
}
async function closeSession(sessionId) {
  const session = getSession(sessionId);
  sessions.delete(sessionId);
  await session.context.close().catch(() => {});
  await session.browser.close().catch(() => {});
  event("browser.session.closed", { session_id: sessionId });
}
function policy() {
  return {
    bind: "localhost only",
    profile: "fresh temporary browser context; no personal browser data",
    approval: "confirm_external_send=true required for every navigation and action",
    blocked: ["password and login automation", "downloads", "multiple concurrent sessions"],
    not_a_claim: "Browser output is unverified web content, not engineering evidence by itself."
  };
}
function html() {
  return `<!doctype html><meta charset="utf-8"><title>EngineAI Browser Worker</title><style>body{font:15px system-ui;max-width:900px;margin:2rem auto;color:#15202b}input,button{padding:.6rem;margin:.25rem}pre{background:#f3f5f7;padding:1rem;white-space:pre-wrap}</style><h1>EngineAI Browser Worker</h1><p>Fresh local browser session. Every action needs explicit approval. Login/password automation and downloads are blocked.</p><input id="url" size="58" value="http://127.0.0.1:8103"><button id="open">Open approved page</button><pre id="out">No session active.</pre><script>let sid='';const out=document.querySelector('#out');async function call(url,body){const r=await fetch(url,{method:body?'POST':'GET',headers:{'content-type':'application/json'},body:body?JSON.stringify(body):undefined});const x=await r.json();if(!r.ok)throw Error(x.error||JSON.stringify(x));return x}document.querySelector('#open').onclick=async()=>{try{const x=await call('/api/sessions',{url:document.querySelector('#url').value,confirm_external_send:true});sid=x.session_id;out.textContent=JSON.stringify(x,null,2)}catch(e){out.textContent=e.message}};</script>`;
}
async function selftest() {
  let browser;
  try {
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage();
    await page.setContent("<title>EngineAI browser self-test</title><main>isolated browser works</main>");
    if (await page.title() !== "EngineAI browser self-test") throw new Error("unexpected page title");
    console.log("browser worker selftest: PASSED");
  } catch (err) {
    console.error(`browser worker selftest: FAILED: ${err.name}: ${err.message}`);
    process.exitCode = 1;
  } finally { await browser?.close().catch(() => {}); }
}
if (process.argv.includes("--selftest")) {
  await selftest();
} else {
  const server = http.createServer(async (req, res) => {
    try {
      const url = new URL(req.url, `http://${HOST}:${PORT}`);
      if (req.method === "GET" && url.pathname === "/") return text(res, 200, html(), "text/html; charset=utf-8");
      if (req.method === "GET" && url.pathname === "/api/health") return json(res, 200, { ok: true, service: "EngineAI Browser Worker", sessions: sessions.size, policy: policy() });
      if (req.method === "GET" && url.pathname === "/api/events") return json(res, 200, { events });
      if (req.method === "GET" && url.pathname === "/api/sessions") return json(res, 200, { sessions: [...sessions.entries()].map(([id, s]) => ({ session_id: id, created_at: s.created_at, url: s.last_url })) });
      if (req.method === "GET" && url.pathname.startsWith("/api/artifacts/")) {
        const name = path.basename(url.pathname.slice("/api/artifacts/".length));
        if (!/^[a-z0-9_]+\.png$/i.test(name)) throw new Error("invalid artifact name");
        return text(res, 200, await fs.readFile(path.join(ARTIFACTS, name)), "image/png");
      }
      if (req.method === "POST" && url.pathname === "/api/sessions") return json(res, 201, await startSession(await readJson(req)));
      const match = url.pathname.match(/^\/api\/sessions\/([a-z0-9_]+)\/(action|close)$/i);
      if (req.method === "POST" && match?.[2] === "action") return json(res, 200, await act(match[1], await readJson(req)));
      if (req.method === "POST" && match?.[2] === "close") { await closeSession(match[1]); return json(res, 200, { ok: true }); }
      return json(res, 404, { error: "not found" });
    } catch (err) { return json(res, 400, { error: String(err.message || err) }); }
  });
  server.listen(PORT, HOST, () => console.log(`EngineAI browser worker on http://${HOST}:${PORT}`));
  for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, async () => { for (const id of [...sessions.keys()]) await closeSession(id); server.close(() => process.exit(0)); });
}
