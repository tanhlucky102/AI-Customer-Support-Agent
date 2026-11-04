// Tests of the widget's pure logic: node --test "tests/js/*.test.js"
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const core = require("../../widget/frame-core.js");

test("markdown: paragraphs, lists and numbering that survives a break", () => {
  const blocks = core.parseMarkdown("Chào bạn\n- Một\n- Hai\n\n3. Ba\n4. Bốn\nHết");
  assert.deepEqual(blocks, [
    { type: "p", text: "Chào bạn" },
    { type: "list", ordered: false, start: 1, items: ["Một", "Hai"] },
    { type: "list", ordered: true, start: 3, items: ["Ba", "Bốn"] },
    { type: "p", text: "Hết" },
  ]);
  // A price is not a list item; an empty answer has no blocks.
  assert.deepEqual(core.parseMarkdown("1.500.000₫"), [{ type: "p", text: "1.500.000₫" }]);
  assert.deepEqual(core.parseMarkdown(""), []);
});

test("markdown: bold and citations only; HTML stays text", () => {
  assert.deepEqual(core.parseInline("Phí **30.000₫** [1] và [123]"), [
    { type: "text", text: "Phí " },
    { type: "bold", text: "30.000₫" },
    { type: "text", text: " " },
    { type: "cite", ref: "1" },
    { type: "text", text: " và [123]" },
  ]);
  const html = '<img src=x onerror="alert(1)">';
  assert.deepEqual(core.parseInline(html), [{ type: "text", text: html }]);
});

test("server-sent events split across chunks, CRLF and broken blocks", () => {
  let taken = core.takeEvents('event: token\r\ndata: {"text":"Xin "}\r\n\r\nevent: tok');
  assert.deepEqual(taken.events, [{ name: "token", payload: { text: "Xin " } }]);
  taken = core.takeEvents(taken.rest + 'en\ndata: {"text":"chào"}\n\nevent: x\ndata: {bad json\n\n' +
                          'event: done\ndata: {"conversation_id":"c1"}\n\n');
  assert.deepEqual(taken.events.map((e) => e.name), ["token", "done"]);
  assert.equal(taken.events[1].payload.conversation_id, "c1");
  assert.equal(taken.rest, "");
  assert.equal(core.parseEvent("event: ping"), null);  // no data line
});

test("token subject, guest IDs, readable text colour and limit messages", () => {
  const payload = Buffer.from(JSON.stringify({ sub: "C017" })).toString("base64url");
  assert.equal(core.tokenSubject("x." + payload + ".y"), "C017");
  assert.equal(core.tokenSubject("not-a-token"), null);
  assert.ok(core.isGuestId("0123456789abcdef0123456789abcdef"));
  assert.ok(!core.isGuestId("0123456789ABCDEF0123456789ABCDEF"));
  assert.ok(!core.isGuestId(null));
  assert.equal(core.textColourFor("#ffd400"), "#0f172a");
  assert.equal(core.textColourFor("#1d4ed8"), "#ffffff");
  assert.match(core.limitMessage("daily_quota_exceeded", "100"), /hết lượt/);
  assert.match(core.limitMessage("customer_rate_limited", "17"), /17 giây/);
  assert.match(core.limitMessage(undefined, null), /60 giây/);
});
