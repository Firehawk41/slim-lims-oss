// A form marked data-submit-once is sent once: a second click while the first is under way would only
// queue more work (a demo lab is seeded per persona click; Render, 2026-11-17). The button says why it waits.
document.addEventListener("submit", function (event) {
  var form = event.target;
  if (!form.matches || !form.matches("form[data-submit-once]")) return;
  if (form.dataset.sent) {
    event.preventDefault();
    return;
  }
  form.dataset.sent = "1";
  document.querySelectorAll("form[data-submit-once] button").forEach(function (b) { b.disabled = true; });
  var button = form.querySelector("button[type=submit]");
  if (button) button.textContent = form.dataset.submitOnce;
});
