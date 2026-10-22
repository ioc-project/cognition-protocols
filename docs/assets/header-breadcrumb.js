// Replaces the site-name text in the MkDocs Material header so the first
// segment ("Internet of Cognition") becomes a link to the IoC top-level site.
(function () {
  "use strict";

  function patch() {
    // Material renders the site name inside .md-header__topic > .md-ellipsis
    var els = document.querySelectorAll(".md-header__topic .md-ellipsis");
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      // Only process text nodes containing the full site name
      if (el.querySelector("a.ioc-parent-link")) continue;
      var text = el.textContent || "";
      var sep = text.indexOf(" / ");
      if (sep === -1) continue;

      var parent = text.slice(0, sep);
      var child  = text.slice(sep); // " / Cognition Protocols"

      var a = document.createElement("a");
      a.className = "ioc-parent-link";
      a.href = "https://ioc-project.github.io";
      a.textContent = parent;

      el.textContent = child;
      el.insertBefore(a, el.firstChild);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", patch);
  } else {
    patch();
  }

  // MkDocs Material with instant navigation swaps page content without a full
  // reload, so re-patch after each navigation.
  document.addEventListener("DOMContentSwapped", patch);
})();
