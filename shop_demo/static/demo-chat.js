// Widget tạm thời: chỉ hiển thị văn bản từ API cùng nguồn, không dựng HTML từ câu trả lời.
(function () {
  "use strict";
  var root = document.getElementById("demo-chat");
  if (!root) return;

  var launcher = root.querySelector(".demo-chat__launcher");
  var panel = root.querySelector(".demo-chat__panel");
  var closeButton = root.querySelector(".demo-chat__close");
  var messages = root.querySelector(".demo-chat__messages");
  var form = root.querySelector(".demo-chat__form");
  var input = form.querySelector("input");
  var submit = form.querySelector("button");

  function addMessage(text, kind, sources) {
    var item = document.createElement("div");
    item.className = "demo-chat__message demo-chat__message--" + kind;
    var body = document.createElement("p");
    body.textContent = text;
    item.appendChild(body);

    if (Array.isArray(sources) && sources.length) {
      var links = document.createElement("div");
      links.className = "demo-chat__sources";
      sources.forEach(function (source) {
        if (!source || typeof source.href !== "string" || !/^\/(?!\/)/.test(source.href)) return;
        var link = document.createElement("a");
        link.href = source.href;
        link.textContent = source.label || "Xem nguồn";
        links.appendChild(link);
      });
      item.appendChild(links);
    }

    messages.appendChild(item);
    messages.scrollTop = messages.scrollHeight;
    return item;
  }

  function greeting() {
    addMessage("Xin chào! Mình trả lời theo chính sách và đơn hàng giả lập của shop. Đây là chatbot demo, chưa dùng AI/RAG.", "bot");
  }

  function setOpen(open) {
    panel.hidden = !open;
    launcher.setAttribute("aria-expanded", String(open));
    if (open) input.focus();
    else launcher.focus();
  }

  async function send(question) {
    question = question.trim();
    if (!question || submit.disabled || input.disabled) return;
    setOpen(true);
    addMessage(question, "user");
    input.value = "";
    submit.disabled = true;
    var pending = addMessage("Đang tra cứu...", "bot");

    try {
      var response = await fetch("/api/demo-chat", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: question })
      });
      if (response.status === 401) throw new Error("login_required");
      if (!response.ok) throw new Error("HTTP " + response.status);
      var result = await response.json();
      pending.remove();
      addMessage(result.reply || "Chưa có câu trả lời.", "bot", result.sources);
    } catch (error) {
      pending.remove();
      addMessage(error.message === "login_required" ? "Vui lòng đăng nhập để dùng chatbot." :
                 "Không kết nối được chatbot demo. Hãy thử lại sau.", "bot");
    } finally {
      submit.disabled = false;
      input.focus();
    }
  }

  launcher.addEventListener("click", function () { setOpen(panel.hidden); });
  closeButton.addEventListener("click", function () { setOpen(false); });
  form.addEventListener("submit", function (event) {
    event.preventDefault();
    send(input.value);
  });
  root.querySelectorAll("[data-question]").forEach(function (button) {
    button.addEventListener("click", function () { send(button.dataset.question); });
  });
  root.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && !panel.hidden) setOpen(false);
  });

  // base.html gọi ATI.reset() khi đăng xuất; xoá nội dung đơn hàng đang hiện.
  window.ATI = window.ATI || {};
  window.ATI.reset = function () {
    messages.replaceChildren();
    greeting();
    setOpen(false);
  };
  greeting();
})();
