(function () {
  "use strict";
  var params = new URLSearchParams(window.location.search);
  var shopKey = params.get("shop_key") || "";
  var parentOrigin = params.get("parent_origin") || "";
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
  var customerKey = null;
  var authReceived = false;
  var authWaiters = [];
  var conversationId = null;
  var sending = false;
  var abortController = null;

  function notifyParent(type) {
    if (parentOrigin) window.parent.postMessage({ type: type }, parentOrigin);
  }

  function scrollToBottom() {
    messages.scrollTop = messages.scrollHeight;
  }

  function addMessage(text, kind) {
    var item = document.createElement("div");
    item.className = "message message--" + kind;
    var body = document.createElement("p");
    body.textContent = text;
    item.appendChild(body);
    messages.appendChild(item);
    scrollToBottom();
    return { item: item, body: body };
  }

  function resetConversation() {
    if (abortController) abortController.abort();
    conversationId = null;
    messages.replaceChildren();
    if (config) addMessage(config.greeting, "bot");
    status.hidden = true;
  }

  function tokenSubject(value) {
    try {
      var body = value.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
      return JSON.parse(atob(body)).sub || null;
    } catch (_) { return null; }
  }

  function setAuthUI() {
    var signedIn = Boolean(token);
    auth.textContent = signedIn ? auth.textContent : "Vui lòng đăng nhập để hỏi và xem lịch sử chat.";
    input.disabled = !signedIn;
    sendButton.disabled = !signedIn || sending;
    newChat.disabled = !signedIn;
    historyToggle.disabled = !signedIn;
    suggestions.querySelectorAll("button").forEach(function (button) {
      button.disabled = !signedIn;
    });
    login.hidden = signedIn || !config || config.shop_id !== "demo_shop";
    if (!login.hidden) login.href = new URL("/login", config.shop_url).href;
    if (!signedIn) {
      historyPanel.hidden = true;
      historyToggle.setAttribute("aria-expanded", "false");
    }
  }

  async function loadHistory() {
    if (!token) return;
    var requestToken = token;
    try {
      var response = await fetch("/v1/conversations?shop_key=" + encodeURIComponent(shopKey), {
        headers: { Authorization: "Bearer " + requestToken }, cache: "no-store"
      });
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
    if (!token || sending) return;
    var requestToken = token;
    try {
      var response = await fetch("/v1/conversations/" + encodeURIComponent(id) +
                                 "?shop_key=" + encodeURIComponent(shopKey), {
        headers: { Authorization: "Bearer " + requestToken }, cache: "no-store"
      });
      if (!response.ok) throw new Error("history_failed");
      var data = await response.json();
      if (token !== requestToken) return;
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
    }
  }

  function waitForAuth(timeoutMs) {
    if (authReceived) return Promise.resolve();
    return new Promise(function (resolve) {
      var timer = setTimeout(resolve, timeoutMs);
      authWaiters.push(function () { clearTimeout(timer); resolve(); });
    });
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
        link.textContent = source.label || "Xem nguồn";
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
        try {
          var response = await fetch("/v1/feedback", {
            method: "POST",
            headers: { "Content-Type": "application/json", Authorization: "Bearer " + token },
            body: JSON.stringify({ shop_key: shopKey, conversation_id: targetConversationId,
                                   rating: rating })
          });
          if (!response.ok) throw new Error("feedback_failed");
          row.replaceChildren(document.createTextNode("Đã ghi nhận phản hồi trong server thử."));
        } catch (_) {
          row.replaceChildren(document.createTextNode("Chưa gửi được phản hồi."));
        }
      });
      row.appendChild(button);
    });
    item.appendChild(row);
    scrollToBottom();
  }

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

  async function consumeStream(response, pending) {
    if (!response.body) throw new Error("Trình duyệt không hỗ trợ đọc luồng phản hồi.");
    var reader = response.body.getReader();
    var decoder = new TextDecoder();
    var buffer = "";
    var doneEvent = false;
    while (true) {
      var result = await reader.read();
      buffer = (buffer + decoder.decode(result.value || new Uint8Array(),
                                       { stream: !result.done })).replace(/\r\n/g, "\n");
      var separator;
      while ((separator = buffer.indexOf("\n\n")) !== -1) {
        var event = parseEvent(buffer.slice(0, separator));
        buffer = buffer.slice(separator + 2);
        if (!event) continue;
        if (event.name === "status") {
          status.textContent = event.payload.message || "Đang xử lý...";
        } else if (event.name === "token") {
          pending.body.textContent += event.payload.text || "";
          scrollToBottom();
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

  async function callChat(question, pending, retry) {
    abortController = new AbortController();
    var headers = { "Content-Type": "application/json" };
    if (token) headers.Authorization = "Bearer " + token;
    var response = await fetch("/v1/chat", {
      method: "POST", headers: headers, signal: abortController.signal,
      body: JSON.stringify({ shop_key: shopKey, message: question,
                             conversation_id: conversationId })
    });
    if (response.status === 401 && retry) {
      authReceived = false;
      notifyParent("ati:token-request");
      await waitForAuth(5000);
      if (token) return callChat(question, pending, false);
      throw new Error("Phiên đăng nhập đã hết hạn. Hãy đăng nhập lại trên website.");
    }
    if (!response.ok) throw new Error("Chatbot server trả lỗi HTTP " + response.status + ".");
    await consumeStream(response, pending);
  }

  async function send(question) {
    question = question.trim();
    if (!question || sending || !token) return;
    sending = true;
    sendButton.disabled = true;
    addMessage(question, "user");
    input.value = "";
    var pending = addMessage("", "bot");
    status.textContent = "Đang kết nối chatbot...";
    status.hidden = false;
    try {
      await waitForAuth(5000);
      await callChat(question, pending, true);
      if (!pending.body.textContent) pending.body.textContent = "Chưa có câu trả lời.";
      addFeedback(pending.item, conversationId);
      await loadHistory();
    } catch (error) {
      if (error.name !== "AbortError") pending.body.textContent = error.message || "Không kết nối được chatbot server.";
    } finally {
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
      if (token) loadHistory();
      authWaiters.splice(0).forEach(function (resolve) { resolve(); });
    } else if (data.type === "ati:reset") {
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
    if (!token || sending) return;
    resetConversation();
    historyPanel.hidden = true;
    historyToggle.setAttribute("aria-expanded", "false");
    input.focus();
  });
  historyToggle.addEventListener("click", function () {
    if (!token || sending) return;
    historyPanel.hidden = !historyPanel.hidden;
    historyToggle.setAttribute("aria-expanded", String(!historyPanel.hidden));
    if (!historyPanel.hidden) loadHistory();
  });
  form.addEventListener("submit", function (event) {
    event.preventDefault();
    send(input.value);
  });

  async function initialize() {
    try {
      var response = await fetch("/v1/widget-config?shop_key=" + encodeURIComponent(shopKey));
      if (!response.ok) throw new Error("Không tải được cấu hình widget.");
      config = await response.json();
      title.textContent = config.title;
      mode.textContent = config.mode === "mock" ? "BẢN THỬ · CHƯA DÙNG AI" : "AI HỖ TRỢ";
      if (/^#[0-9a-fA-F]{6}$/.test(config.primary_color)) {
        document.documentElement.style.setProperty("--accent", config.primary_color);
      }
      addMessage(config.greeting, "bot");
      (config.suggestions || []).forEach(function (question) {
        var button = document.createElement("button");
        button.type = "button";
        button.textContent = question;
        button.addEventListener("click", function () { send(question); });
        suggestions.appendChild(button);
      });
      setAuthUI();
      notifyParent("ati:ready");
    } catch (error) {
      addMessage(error.message || "Không tải được widget.", "bot");
    }
  }
  initialize();
})();
