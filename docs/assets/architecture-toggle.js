// "Available today" toggle for the simplified architecture figure on the Overview.
// Greys the groups the generator tagged data-planned — the Cognition Fabric's
// API, management and sync planes, the whole Cognition Engines block, and the
// enforcement and interaction arrows. Loaded site-wide; returns on any page
// without the figure.
(function () {
  "use strict";
  var btn = document.getElementById("ioc-avail");
  var fig = document.querySelector(".ioc-figure");
  if (!btn || !fig) return;

  var note = document.getElementById("ioc-avail-note");

  function apply(on) {
    fig.classList.toggle("avail", on);
    btn.setAttribute("aria-pressed", String(on));
    if (note) note.hidden = !on;
  }

  btn.addEventListener("click", function () {
    apply(btn.getAttribute("aria-pressed") !== "true");
  });

  apply(false);
})();
