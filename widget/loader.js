/* Embeddable loader. It runs on the shop page; chat UI and API stay on Agent Server. */
(function () {
  "use strict";
  var script = document.currentScript;
  if (!script || !script.dataset.shopKey || window.ATI && window.ATI.__mounted) return;

  var serverOrigin = new URL(script.src).origin;
  var shopKey = script.dataset.shopKey;
  var tokenPath = script.dataset.tokenUrl || "";
  var frame;
  var panel;
  var launcher;
  var ready = false;
  var authEpoch = 0;

  function send(message) {
    if (ready && frame.contentWindow) frame.contentWindow.postMessage(message, serverOrigin);
  }

  async function refreshAuth() {
    var epoch = ++authEpoch;
    if (!tokenPath) {
      send({ type: "ati:auth", token: null, customerName: null });
      return;
    }
    try {
      var url = new URL(tokenPath, window.location.href);
      if (url.origin !== window.location.origin) throw new Error("token-url must be same-origin");
      var response = await fetch(url.href, { credentials: "same-origin", cache: "no-store" });
      if (!response.ok) throw new Error("HTTP " + response.status);
      var data = await response.json();
      if (epoch === authEpoch) send({ type: "ati:auth", token: data.token || null,
                                     customerName: data.customer_name || null });
    } catch (_) {
      if (epoch === authEpoch) send({ type: "ati:auth", token: null, customerName: null });
    }
  }

  function setOpen(open) {
    panel.hidden = !open;
    launcher.setAttribute("aria-expanded", String(open));
    if (open) {
      refreshAuth();
      send({ type: "ati:focus" });
    } else {
      launcher.focus();
    }
  }

  function mount() {
    var host = document.createElement("div");
    host.id = "ati-widget-host";
    var shadow = host.attachShadow({ mode: "open" });
    var stylesheet = document.createElement("link");
    stylesheet.rel = "stylesheet";
    stylesheet.href = serverOrigin + "/widget/loader.css";
    shadow.appendChild(stylesheet);

    panel = document.createElement("div");
    panel.className = "ati-panel";
    panel.hidden = true;
    frame = document.createElement("iframe");
    frame.title = "Trợ lý khách hàng";
    frame.referrerPolicy = "no-referrer";
    var url = new URL("/widget/frame", serverOrigin);
    url.searchParams.set("shop_key", shopKey);
    url.searchParams.set("parent_origin", window.location.origin);
    frame.src = url.href;
    panel.appendChild(frame);

    launcher = document.createElement("button");
    launcher.className = "ati-launcher";
    launcher.type = "button";
    launcher.textContent = "💬  Hỏi trợ lý";
    launcher.setAttribute("aria-label", "Mở trợ lý khách hàng");
    launcher.setAttribute("aria-expanded", "false");
    launcher.addEventListener("click", function () { setOpen(panel.hidden); });
    shadow.appendChild(panel);
    shadow.appendChild(launcher);
    document.body.appendChild(host);

    window.addEventListener("message", function (event) {
      if (event.origin !== serverOrigin || event.source !== frame.contentWindow) return;
      var data = event.data || {};
      if (data.type === "ati:ready") {
        ready = true;
        refreshAuth();
      } else if (data.type === "ati:close") {
        setOpen(false);
      } else if (data.type === "ati:token-request") {
        refreshAuth();
      }
    });

    window.ATI = {
      __mounted: true,
      open: function () { setOpen(true); },
      close: function () { setOpen(false); },
      refreshAuth: refreshAuth,
      reset: function () {
        authEpoch++;
        send({ type: "ati:reset" });
        send({ type: "ati:auth", token: null, customerName: null });
        setOpen(false);
      }
    };
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount, { once: true });
  } else {
    mount();
  }
})();
