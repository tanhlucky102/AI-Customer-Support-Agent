// UI smoke test with installed Chrome and its local DevTools protocol.
// Run while shop demo (8001) and widget Agent Server (8000) are available.
import { spawn } from "node:child_process";
import { mkdtemp, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

const chromePath = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const port = 9223;
const testUrl = process.env.ATI_TEST_URL || "http://127.0.0.1:8001/";
const testGuest = process.env.ATI_TEST_GUEST === "1";
const testLogin = process.env.ATI_TEST_LOGIN !== "0" && !testGuest;
const testHistory = process.env.ATI_TEST_HISTORY === "1";
const customQuestion = process.env.ATI_QUESTION || "";
const expectedText = process.env.ATI_EXPECT || (testLogin ? "Các đơn gần đây" : "Miễn phí vận chuyển");
const profile = await mkdtemp(path.join(os.tmpdir(), "ati-widget-chrome-"));
const screenshotPath = process.env.ATI_SCREENSHOT || path.join(os.tmpdir(), "ati-widget-smoke.png");
const chrome = spawn(chromePath, [
  "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
  `--remote-debugging-port=${port}`, "--remote-debugging-address=127.0.0.1",
  `--user-data-dir=${profile}`, "about:blank"
], { windowsHide: true, stdio: "ignore" });

let socket;
let nextId = 0;
const waiting = new Map();
const contexts = new Map();

async function pause(ms) { return new Promise(resolve => setTimeout(resolve, ms)); }
async function until(check, label, maxMs = 10000) {
  const start = Date.now();
  while (Date.now() - start < maxMs) {
    const value = await check();
    if (value) return value;
    await pause(150);
  }
  throw new Error(`Timed out: ${label}`);
}
function cdp(method, params = {}, sessionId) {
  return new Promise((resolve, reject) => {
    const id = ++nextId;
    waiting.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
  });
}
async function evaluate(expression, context) {
  const params = { expression, returnByValue: true, awaitPromise: true };
  if (context) params.contextId = context.id;
  const result = await cdp("Runtime.evaluate", params, context?.sessionId);
  if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
  return result.result?.value;
}

try {
  await until(async () => {
    try { return (await fetch(`http://127.0.0.1:${port}/json/version`)).ok; }
    catch { return false; }
  }, "Chrome debugging endpoint");
  const tabResponse = await fetch(`http://127.0.0.1:${port}/json/new?about:blank`,
                                  { method: "PUT" });
  if (!tabResponse.ok) throw new Error(`Could not create tab: ${tabResponse.status}`);
  const tab = await tabResponse.json();
  socket = new WebSocket(tab.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  socket.addEventListener("message", event => {
    const packet = JSON.parse(event.data);
    if (packet.id) {
      const request = waiting.get(packet.id);
      if (!request) return;
      waiting.delete(packet.id);
      packet.error ? request.reject(new Error(packet.error.message)) : request.resolve(packet.result);
    } else if (packet.method === "Runtime.executionContextCreated") {
      const context = packet.params.context;
      contexts.set(`${packet.sessionId || "main"}:${context.id}`,
                   { ...context, sessionId: packet.sessionId });
    } else if (packet.method === "Runtime.executionContextDestroyed") {
      contexts.delete(`${packet.sessionId || "main"}:${packet.params.executionContextId}`);
    } else if (packet.method === "Target.attachedToTarget") {
      cdp("Runtime.enable", {}, packet.params.sessionId).catch(() => {});
    }
  });
  await cdp("Page.enable");
  await cdp("Runtime.enable");
  await cdp("Target.setAutoAttach", { autoAttach: true, waitForDebuggerOnStart: false,
                                      flatten: true });
  await cdp("Page.navigate", { url: testUrl });
  await until(async () => evaluate("Boolean(window.ATI && window.ATI.__mounted)"),
              "widget loader on shop demo");
  await evaluate("window.ATI.open()");
  const frameContext = await until(async () => {
    for (const context of contexts.values()) {
      if (context.origin === "http://localhost:8000" && context.auxData?.isDefault) return context;
    }
    return null;
  }, "widget iframe context");
  await until(async () => evaluate("document.querySelectorAll('#chat-suggestions button').length", frameContext),
              "FAQ suggestions");
  if (testGuest) {
    const locked = await evaluate("document.querySelector('#chat-input').disabled && " +
      "document.querySelector('#chat-send').disabled && " +
      "!document.querySelector('#chat-login').hidden", frameContext);
    if (!locked) throw new Error("Guest chat is not locked");
    console.log("PASS: guest sees login prompt and cannot send a question");
  } else {
  if (testLogin) {
    await evaluate(`(async () => {
      await fetch('/login', { method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({ email: 'an@demo.vn', password: 'demo1234', next: '/' }) });
      await window.ATI.refreshAuth();
    })()`);
    await until(async () => evaluate("document.querySelector('#chat-auth').innerText.includes('Đang hỗ trợ')", frameContext),
                "authenticated widget state");
  }
  if (customQuestion) {
    await evaluate(`document.querySelector('#chat-input').value = ${JSON.stringify(customQuestion)};
                    document.querySelector('#chat-form').requestSubmit()`, frameContext);
  } else if (testLogin) {
    await evaluate("document.querySelector('#chat-suggestions button:nth-child(3)').click()", frameContext);
  } else {
    await evaluate("document.querySelector('#chat-suggestions button:nth-child(2)').click()", frameContext);
  }
  const answer = await until(async () => {
    const value = await evaluate("document.querySelector('#chat-messages').innerText", frameContext);
    return value.includes(expectedText) ? value : null;
  }, "chat answer in iframe", 45000);
  await until(async () => evaluate("document.querySelectorAll('.message__feedback button').length === 2", frameContext),
              "feedback controls after completed stream");
  if (testHistory) {
    await evaluate("document.querySelector('#chat-new').click(); " +
                   "document.querySelector('#chat-history-toggle').click()", frameContext);
    await until(async () => evaluate("document.querySelectorAll('#chat-history button').length", frameContext),
                "saved conversation in history");
    await evaluate("document.querySelector('#chat-history button').click()", frameContext);
    await until(async () => {
      const value = await evaluate("document.querySelector('#chat-messages').innerText", frameContext);
      return value.includes(expectedText);
    }, "restored answer from history");
    console.log("PASS: conversation restored from history");
  }
  console.log(`PASS: loader, iframe, open action and ${testLogin ? "order" : "FAQ"} answer`);
  console.log(`Answer: ${answer.slice(-180).replaceAll("\n", " ")}`);
  }
  const screenshot = await cdp("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
  await writeFile(screenshotPath, Buffer.from(screenshot.data, "base64"));
  console.log(`Screenshot: ${screenshotPath}`);
} finally {
  socket?.close();
  chrome.kill();
}
