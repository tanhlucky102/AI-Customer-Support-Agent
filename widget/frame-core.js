// Pure logic of the chat frame: no DOM, no network. frame.js builds the page from these results;
// the Node tests in tests/js/ check them (node --test "tests/js/*.test.js").
(function (root) {
  "use strict";

  // Minimal Markdown of model output: paragraphs, "- "/"1. " lists, **bold** and [n] citation
  // markers. The result is plain data; frame.js turns it into DOM nodes with textContent only,
  // so model text can never become HTML.
  function parseInline(text) {
    var pattern = /\*\*([^*]+)\*\*|\[(\d{1,2})\]/g;
    var parts = [];
    var last = 0;
    var match;
    while ((match = pattern.exec(text)) !== null) {
      if (match.index > last) parts.push({ type: "text", text: text.slice(last, match.index) });
      if (match[1] !== undefined) parts.push({ type: "bold", text: match[1] });
      else parts.push({ type: "cite", ref: match[2] });
      last = pattern.lastIndex;
    }
    if (last < text.length) parts.push({ type: "text", text: text.slice(last) });
    return parts;
  }

  function parseMarkdown(text) {
    var blocks = [];
    var list = null;
    String(text || "").split("\n").forEach(function (line) {
      var item = /^\s*(?:[-*•]|\d+[.)])\s+(.*)$/.exec(line);
      if (item) {
        var number = /^\s*(\d+)/.exec(line);
        var ordered = Boolean(number);
        if (!list || list.ordered !== ordered) {
          // "3." after a break stays 3.
          list = { type: "list", ordered: ordered, start: ordered ? Number(number[1]) : 1, items: [] };
          blocks.push(list);
        }
        list.items.push(item[1]);
        return;
      }
      list = null;
      if (line.trim()) blocks.push({ type: "p", text: line });
    });
    return blocks;
  }

  // Server-Sent Events (contract C4): "event: name\ndata: {json}" blocks separated by a blank line.
  function parseEvent(block) {
    var eventName = "message";
    var data = "";
    block.split("\n").forEach(function (line) {
      if (line.startsWith("event:")) eventName = line.slice(6).trim();
      if (line.startsWith("data:")) data += line.slice(5).trimStart();
    });
    if (!data) return null;
    try { return { name: eventName, payload: JSON.parse(data) }; }
    catch (_) { return null; }
  }

  // The complete events in what has arrived so far, and the unfinished rest to keep for the next chunk.
  function takeEvents(buffer) {
    var events = [];
    var separator;
    buffer = buffer.replace(/\r\n/g, "\n");
    while ((separator = buffer.indexOf("\n\n")) !== -1) {
      var event = parseEvent(buffer.slice(0, separator));
      buffer = buffer.slice(separator + 2);
      if (event) events.push(event);
    }
    return { events: events, rest: buffer };
  }

  // The customer a chat token names (only to notice a different login; the server verifies tokens).
  function tokenSubject(value) {
    try {
      var body = value.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
      return JSON.parse(atob(body)).sub || null;
    } catch (_) { return null; }
  }

  function isGuestId(value) {
    return /^[0-9a-f]{32}$/.test(value || "");
  }

  // White text on a light shop colour is unreadable: pick dark text for light colours.
  function textColourFor(hex) {
    var value = parseInt(hex.slice(1), 16);
    var channel = function (c) { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
    var luminance = 0.2126 * channel(value >> 16 & 255) + 0.7152 * channel(value >> 8 & 255) +
                    0.0722 * channel(value & 255);
    return luminance > 0.45 ? "#0f172a" : "#ffffff";
  }

  var DAILY_LIMITS = {
    daily_quota_exceeded: "Trợ lý đã hết lượt trả lời trong hôm nay. Vui lòng liên hệ trực tiếp với shop.",
    customer_daily_limit: "Bạn đã hỏi nhiều câu trong hôm nay. Vui lòng quay lại vào ngày mai hoặc liên hệ shop.",
    ip_daily_limit: "Mạng của bạn đã gửi quá nhiều câu hỏi trong hôm nay. Vui lòng thử lại vào ngày mai."
  };

  // What to tell the customer for a 429 answer: its `detail` and Retry-After header.
  function limitMessage(detail, retryAfter) {
    return DAILY_LIMITS[detail] ||
      "Bạn gửi câu hỏi hơi nhanh. Vui lòng thử lại sau khoảng " + (Number(retryAfter) || 60) + " giây.";
  }

  var api = { parseInline: parseInline, parseMarkdown: parseMarkdown, parseEvent: parseEvent,
              takeEvents: takeEvents, tokenSubject: tokenSubject, isGuestId: isGuestId,
              textColourFor: textColourFor, limitMessage: limitMessage };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ATICore = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
