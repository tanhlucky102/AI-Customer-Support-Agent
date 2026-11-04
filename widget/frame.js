(function () {
  "use strict";
  var core = window.ATICore;  // frame-core.js: parsing and other logic without DOM (tested in Node)
  var params = new URLSearchParams(window.location.search);
  var shopKey = params.get("shop_key") || "";
  var parentOrigin = params.get("parent_origin") || "";
  var loginUrl = "";  // Set by the shop's embed (data-login-url); only a page of the embedding site.
  try {
    var requestedLogin = params.get("login_url");
    if (requestedLogin && new URL(requestedLogin).origin === parentOrigin) loginUrl = requestedLogin;
  } catch (_) {}
  var title = document.getElementById("chat-title");
  var mode = document.getElementById("chat-mode");
  var auth = document.getElementById("chat-auth");
  var login = document.getElementById("chat-login");
  var newChat = document.getElementById("chat-new");
  var historyToggle = document.getElementById("chat-history-toggle");
  var historyPanel = document.getElementById("chat-history");
  var messages = document.getElementById("chat-messages");
  var suggestions = document.getElementById("chat-suggestions");
  var status = document.getElementById("chat-status");
  var form = document.getElementById("chat-form");
  var input = document.getElementById("chat-input");
  var sendButton = document.getElementById("chat-send");
  var config = null;
  var token = null;
  var guestId = null;
  var customerKey = null;
  var authReceived = false;
  var authWaiters = [];
  var conversationId = null;
  var sending = false;
  var abortController = null;
  var opening = false;  // a saved conversation is being loaded

  function notifyParent(type) {
    if (parentOrigin) window.parent.postMessage({ type: type }, parentOrigin);
  }

  function scrollToBottom() {
    messages.scrollTop = messages.scrollHeight;
  }

  // Model output as DOM nodes with textContent only (core.parseMarkdown gives plain data), so
  // model text can never become HTML.
  function appendInline(parent, text) {
    core.parseInline(text).forEach(function (part) {
      if (part.type === "text") {
        parent.appendChild(document.createTextNode(part.text));
      } else if (part.type === "bold") {
        var strong = document.createElement("strong");
        strong.textContent = part.text;
        parent.appendChild(strong);
      } else {
        var cite = document.createElement("sup");
        cite.className = "cite";
        cite.dataset.ref = part.ref;  // clicking it shows the matching source below the answer
        cite.textContent = "[" + part.ref + "]";
        parent.appendChild(cite);
      }
    });
  }

  function renderMarkdown(container, text) {
    container.replaceChildren();
    core.parseMarkdown(text).forEach(function (block) {
      if (block.type === "p") {
        var paragraph = document.createElement("p");
        appendInline(paragraph, block.text);
        container.appendChild(paragraph);
        return;
      }
      var list = document.createElement(block.ordered ? "ol" : "ul");
      if (block.ordered) list.start = block.start;
      block.items.forEach(function (text) {
        var li = document.createElement("li");
        appendInline(li, text);
        list.appendChild(li);
      });
      container.appendChild(list);
    });
  }

  function addMessage(text, kind) {
    var item = document.createElement("div");
    item.className = "message message--" + kind;
    var body = document.createElement("div");
    body.className = "message__body";
    var message = { item: item, body: body, raw: text || "" };
    if (kind === "bot") renderMarkdown(body, message.raw);
    else body.textContent = message.raw;
    item.appendChild(body);
    messages.appendChild(item);
    scrollToBottom();
    return message;
  }

  // Tokens arrive faster than the screen refreshes: render at most once per frame, not per token.
  function appendToken(message, text) {
    message.raw += text;
    if (message.frame) return;
    message.frame = window.requestAnimationFrame(function () {
      message.frame = 0;
      renderMarkdown(message.body, message.raw);
      scrollToBottom();
    });
  }

  function flush(message) {
    if (message.frame) window.cancelAnimationFrame(message.frame);
    message.frame = 0;
    renderMarkdown(message.body, message.raw);
  }

  function resetConversation() {
    if (abortController) abortController.abort();
    conversationId = null;
    messages.replaceChildren();
    if (config) addMessage(config.greeting, "bot");
    status.hidden = true;
  }

  var tokenSubject = core.tokenSubject;

  function canChat() { return Boolean(token || guestId); }

  function authHeaders(headers) {
    headers = headers || {};
    if (token) headers.Authorization = "Bearer " + token;
    else if (guestId) headers["X-ATI-Guest"] = guestId;
    return headers;
  }

  // Guest mode (level-0 shops without customer accounts): a random ID kept in this browser
  // groups the visitor's own conversations. It is not a login and reaches no order data.
  function loadGuestId() {
    var key = "ati-guest:" + shopKey;
    try {
      var saved = window.localStorage.getItem(key);
      if (core.isGuestId(saved)) return saved;
    } catch (_) {}
    var bytes = new Uint8Array(16);
    window.crypto.getRandomValues(bytes);
    var value = Array.prototype.map.call(bytes, function (byte) {
      return ("0" + byte.toString(16)).slice(-2);
    }).join("");
    try { window.localStorage.setItem(key, value); } catch (_) {}
    return value;
  }

  function setAuthUI() {
    var signedIn = canChat();
    if (!token) auth.textContent = guestId ? "Bạn đang chat với tư cách khách, không cần đăng nhập."
                                           : "Vui lòng đăng nhập để hỏi và xem lịch sử chat.";
    input.disabled = !signedIn;
    sendButton.disabled = !signedIn || sending;
    newChat.disabled = !signedIn;  // also stops an answer that is still streaming
    historyToggle.disabled = !signedIn || sending;
    suggestions.querySelectorAll("button").forEach(function (button) {
      button.disabled = !signedIn;
    });
    login.hidden = signedIn || !loginUrl;
    if (!login.hidden) login.href = loginUrl;
    if (!signedIn) {
      historyPanel.hidden = true;
      historyToggle.setAttribute("aria-expanded", "false");
    }
  }

  async function loadHistory() {
    if (!canChat()) return;
    var requestToken = token;
    try {
      var response = await apiFetch("/v1/conversations?shop_key=" + encodeURIComponent(shopKey),
                                    { cache: "no-store" });
      if (!response.ok) throw new Error("history_failed");
      var data = await response.json();
      if (token !== requestToken) return;
      historyPanel.replaceChildren();
      if (!data.conversations.length) {
        var empty = document.createElement("p");
        empty.textContent = "Chưa có hội thoại nào.";
        historyPanel.appendChild(empty);
      }
      data.conversations.forEach(function (row) {
        var button = document.createElement("button");
        button.type = "button";
        button.textContent = row.title || "Hội thoại";
        button.title = row.title || "Hội thoại";
        button.addEventListener("click", function () { openConversation(row.id); });
        historyPanel.appendChild(button);
      });
    } catch (_) {
      if (token !== requestToken) return;
      historyPanel.replaceChildren();
      var error = document.createElement("p");
      error.textContent = "Chưa tải được lịch sử. Vui lòng thử lại.";
      historyPanel.appendChild(error);
    }
  }

  async function openConversation(id) {
    if (!canChat() || sending || opening) return;
    var requestToken = token;
    opening = true;
    try {
      var response = await apiFetch("/v1/conversations/" + encodeURIComponent(id) +
                                    "?shop_key=" + encodeURIComponent(shopKey), { cache: "no-store" });
      if (!response.ok) throw new Error("history_failed");
      var data = await response.json();
      if (token !== requestToken || sending) return;
      conversationId = id;
      messages.replaceChildren();
      data.messages.forEach(function (row) {
        var item = addMessage(row.content, row.role === "user" ? "user" : "bot");
        if (row.role === "assistant") addSources(item.item, row.sources);
      });
      historyPanel.hidden = true;
      historyToggle.setAttribute("aria-expanded", "false");
      input.focus();
    } catch (_) {
      if (token !== requestToken) return;
      status.textContent = "Chưa mở được hội thoại. Vui lòng thử lại.";
      status.hidden = false;
    } finally {
      opening = false;
    }
  }

  function waitForAuth(timeoutMs) {
    if (authReceived) return Promise.resolve();
    return new Promise(function (resolve) {
      var timer = setTimeout(resolve, timeoutMs);
      authWaiters.push(function () { clearTimeout(timer); resolve(); });
    });
  }

  // One request to the Agent Server. If the shop's token expired or was revoked (401), ask the page
  // for a fresh one and retry once; if another account signed in meanwhile, drop the request.
  async function apiFetch(path, options) {
    options = options || {};
    var extra = options.headers || {};
    var customerAtStart = customerKey;
    function request() {
      return fetch(path, Object.assign({}, options, { headers: authHeaders(Object.assign({}, extra)) }));
    }
    var response = await request();
    if (response.status !== 401 || !token) return response;
    authReceived = false;
    notifyParent("ati:token-request");
    await waitForAuth(5000);
    if (customerKey !== customerAtStart) {
      var switched = new Error("account_changed");
      switched.name = "AbortError";
      throw switched;
    }
    if (!token) throw new Error("Phiên đăng nhập đã hết hạn. Hãy đăng nhập lại trên website.");
    return request();
  }

  function addSources(item, sources) {
    if (!config || !Array.isArray(sources)) return;
    var expectedOrigin = new URL(config.shop_url).origin;
    var links = document.createElement("div");
    links.className = "message__sources";
    sources.forEach(function (source) {
      try {
        var url = new URL(source.href);
        if (url.origin !== expectedOrigin || !/^https?:$/.test(url.protocol)) return;
        var link = document.createElement("a");
        link.href = url.href;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = (source.ref ? "[" + source.ref + "] " : "") + (source.label || "Xem nguồn");
        if (source.ref) link.dataset.ref = String(source.ref);
        links.appendChild(link);
      } catch (_) { /* Ignore malformed source links. */ }
    });
    if (links.childNodes.length) {
      item.appendChild(links);
      scrollToBottom();
    }
  }

  function addFeedback(item, targetConversationId) {
    var row = document.createElement("div");
    row.className = "message__feedback";
    row.appendChild(document.createTextNode("Câu trả lời hữu ích?"));
    ["yes", "no"].forEach(function (rating) {
      var button = document.createElement("button");
      button.type = "button";
      button.textContent = rating === "yes" ? "👍" : "👎";
      button.setAttribute("aria-label", rating === "yes" ? "Hữu ích" : "Chưa hữu ích");
      button.addEventListener("click", async function () {
        row.querySelectorAll("button").forEach(function (other) { other.disabled = true; });
        try {
          var response = await apiFetch("/v1/feedback", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ shop_key: shopKey, conversation_id: targetConversationId,
                                   rating: rating })
          });
          if (!response.ok) throw new Error("feedback_failed");
          row.replaceChildren(document.createTextNode("Cảm ơn bạn đã phản hồi."));
        } catch (_) {
          row.replaceChildren(document.createTextNode("Chưa gửi được phản hồi."));
        }
      });
      row.appendChild(button);
    });
    item.appendChild(row);
    scrollToBottom();
  }

  async function consumeStream(response, pending) {
    if (!response.body) throw new Error("Trình duyệt không hỗ trợ đọc luồng phản hồi.");
    var reader = response.body.getReader();
    var decoder = new TextDecoder();
    var buffer = "";
    var doneEvent = false;
    while (true) {
      var result = await reader.read();
      var taken = core.takeEvents(buffer + decoder.decode(result.value || new Uint8Array(),
                                                          { stream: !result.done }));
      buffer = taken.rest;
      for (var i = 0; i < taken.events.length; i++) {
        var event = taken.events[i];
        if (event.name === "status") {
          status.textContent = event.payload.message || "Đang xử lý...";
        } else if (event.name === "token") {
          appendToken(pending, event.payload.text || "");
          scrollToBottom();
        } else if (event.name === "replace") {
          pending.raw = "";
          appendToken(pending, event.payload.text || "");
        } else if (event.name === "citations") {
          addSources(pending.item, event.payload.sources);
        } else if (event.name === "done") {
          conversationId = event.payload.conversation_id || conversationId;
          doneEvent = true;
        } else if (event.name === "error") {
          throw new Error(event.payload.message || "Lỗi từ chatbot server.");
        }
      }
      if (result.done) break;
    }
    if (!doneEvent) throw new Error("Luồng trả lời đã ngắt trước khi hoàn thành.");
  }

  async function callChat(question, pending) {
    abortController = new AbortController();
    var response = await apiFetch("/v1/chat", {
      method: "POST", headers: { "Content-Type": "application/json" }, signal: abortController.signal,
      body: JSON.stringify({ shop_key: shopKey, message: question,
                             conversation_id: conversationId })
    });
    if (response.status === 429) {
      var limit = await response.json().catch(function () { return {}; });
      throw new Error(core.limitMessage(limit.detail, response.headers.get("Retry-After")));
    }
    if (response.status === 401) throw new Error("Phiên đăng nhập đã hết hạn. Hãy đăng nhập lại trên website.");
    if (!response.ok) throw new Error("Chatbot server trả lỗi HTTP " + response.status + ".");
    await consumeStream(response, pending);
  }

  async function send(question) {
    question = question.trim();
    if (!question || sending || opening || !canChat()) return;
    sending = true;
    setAuthUI();
    addMessage(question, "user");
    input.value = "";
    var pending = addMessage("", "bot");
    messages.setAttribute("aria-busy", "true");  // screen readers announce the answer once, at the end
    status.textContent = "Đang kết nối chatbot...";
    status.hidden = false;
    try {
      await waitForAuth(5000);
      await callChat(question, pending);
      flush(pending);
      if (!pending.raw) appendToken(pending, "Chưa có câu trả lời.");
      addFeedback(pending.item, conversationId);
      if (!historyPanel.hidden) loadHistory();
    } catch (error) {
      if (error.name !== "AbortError") {
        var text = error.message || "Không kết nối được chatbot server.";
        if (pending.item.isConnected) {
          pending.raw = "";
          appendToken(pending, text);
        } else {
          addMessage(text, "bot");  // the conversation was reset meanwhile (logged out)
        }
      }
    } finally {
      flush(pending);
      messages.removeAttribute("aria-busy");
      status.hidden = true;
      sending = false;
      setAuthUI();
      abortController = null;
      input.focus();
    }
  }

  window.addEventListener("message", function (event) {
    if (event.source !== window.parent || event.origin !== parentOrigin) return;
    var data = event.data || {};
    if (data.type === "ati:auth") {
      var nextToken = typeof data.token === "string" ? data.token : null;
      var nextCustomer = nextToken ? tokenSubject(nextToken) : null;
      if (nextCustomer !== customerKey) {
        resetConversation();
        historyPanel.replaceChildren();
      }
      customerKey = nextCustomer;
      token = nextToken;
      authReceived = true;
      auth.textContent = token ? "Đang hỗ trợ: " + (data.customerName || "khách đã đăng nhập")
                               : "Vui lòng đăng nhập để hỏi và xem lịch sử chat.";
      setAuthUI();
      if (canChat() && !historyPanel.hidden) loadHistory();
      authWaiters.splice(0).forEach(function (resolve) { resolve(); });
    } else if (data.type === "ati:reset") {
      if (token) {  // The shop logged the customer out: their chat tokens stop working on the server too.
        fetch("/v1/logout", { method: "POST", keepalive: true,
                              headers: authHeaders({ "Content-Type": "application/json" }),
                              body: JSON.stringify({ shop_key: shopKey }) }).catch(function () {});
      }
      token = null;
      customerKey = null;
      authReceived = true;
      auth.textContent = "Vui lòng đăng nhập để hỏi và xem lịch sử chat.";
      resetConversation();
      historyPanel.replaceChildren();
      setAuthUI();
    } else if (data.type === "ati:focus") {
      input.focus();
    }
  });

  document.getElementById("chat-close").addEventListener("click", function () {
    notifyParent("ati:close");
  });
  newChat.addEventListener("click", function () {
    if (!canChat()) return;
    resetConversation();  // also aborts an answer that is still streaming
    historyPanel.hidden = true;
    historyToggle.setAttribute("aria-expanded", "false");
    input.focus();
  });
  historyToggle.addEventListener("click", function () {
    if (!canChat() || sending) return;
    historyPanel.hidden = !historyPanel.hidden;
    historyToggle.setAttribute("aria-expanded", String(!historyPanel.hidden));
    if (!historyPanel.hidden) loadHistory();
  });
  form.addEventListener("submit", function (event) {
    event.preventDefault();
    send(input.value);
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") notifyParent("ati:close");
  });
  messages.addEventListener("click", function (event) {
    var cite = event.target.closest ? event.target.closest(".cite") : null;
    if (!cite) return;
    var message = cite.closest(".message");
    var link = message && message.querySelector('.message__sources a[data-ref="' + cite.dataset.ref + '"]');
    if (link) {
      link.scrollIntoView({ block: "nearest" });
      link.focus();
    }
  });

  async function initialize() {
    try {
      var response = await fetch("/v1/widget-config?shop_key=" + encodeURIComponent(shopKey));
      if (!response.ok) throw new Error("Không tải được cấu hình widget.");
      config = await response.json();
      title.textContent = config.title;
      mode.textContent = config.mode === "agent" ? "AI HỖ TRỢ" : "CHẾ ĐỘ DỰ PHÒNG · KHÔNG DÙNG AI";
      if (/^#[0-9a-fA-F]{6}$/.test(config.primary_color)) {
        document.documentElement.style.setProperty("--accent", config.primary_color);
        document.documentElement.style.setProperty("--accent-text", core.textColourFor(config.primary_color));
      }
      addMessage(config.greeting, "bot");
      (config.suggestions || []).forEach(function (question) {
        var button = document.createElement("button");
        button.type = "button";
        button.textContent = question;
        button.addEventListener("click", function () { send(question); });
        suggestions.appendChild(button);
      });
      if (config.guest_chat) guestId = loadGuestId();
      setAuthUI();  // history is loaded when the history panel is opened
      notifyParent("ati:ready");
    } catch (error) {
      addMessage(error.message || "Không tải được widget.", "bot");
    }
  }
  initialize();
})();
