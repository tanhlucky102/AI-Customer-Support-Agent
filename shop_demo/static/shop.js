// Đếm ngược Hot Sale (đến 23:59:59 hôm nay) + tự ẩn thông báo flash
(function () {
  var cd = document.getElementById("cd");
  if (cd) {
    var pad = function (n) { return String(n).padStart(2, "0"); };
    var tick = function () {
      var now = new Date(), end = new Date(now);
      end.setHours(23, 59, 59, 999);
      var s = Math.max(0, Math.floor((end - now) / 1000));
      cd.innerHTML = "<b>" + pad(Math.floor(s / 3600)) + "</b>:<b>" + pad(Math.floor(s % 3600 / 60)) + "</b>:<b>" + pad(s % 60) + "</b>";
    };
    tick();
    setInterval(tick, 1000);
  }
  var f = document.querySelector("[data-flash]");
  if (f) setTimeout(function () { f.classList.add("gone"); }, 5000);
})();
