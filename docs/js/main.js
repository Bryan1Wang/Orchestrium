// Orchestrium site — small, dependency-free interactions.
(function () {
  "use strict";

  // Mobile nav toggle
  var toggle = document.querySelector(".nav-toggle");
  var links = document.querySelector(".nav-links");
  if (toggle && links) {
    toggle.addEventListener("click", function () {
      var isOpen = links.classList.toggle("open");
      toggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
    });
    links.querySelectorAll("a").forEach(function (a) {
      a.addEventListener("click", function () {
        links.classList.remove("open");
        toggle.setAttribute("aria-expanded", "false");
      });
    });
  }

  // Light/dark theme toggle (dark by default)
  var themeBtn = document.getElementById("themeToggle");
  if (themeBtn) {
    var root = document.documentElement;
    themeBtn.addEventListener("click", function () {
      var current = root.getAttribute("data-theme") || "dark";
      var next = current === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem("theme", next); } catch (e) {}
      document.dispatchEvent(new Event("themechange"));
    });
  }

  // Scroll-reveal animation
  var revealEls = document.querySelectorAll(".reveal");
  if ("IntersectionObserver" in window && revealEls.length) {
    var observer = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-visible");
            observer.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.15, rootMargin: "0px 0px -40px 0px" }
    );
    revealEls.forEach(function (el) { observer.observe(el); });
  } else {
    revealEls.forEach(function (el) { el.classList.add("is-visible"); });
  }

  // Randomize the hero waveform bar heights a touch so it doesn't look mechanical
  var bars = document.querySelectorAll(".wave-visual .bar");
  bars.forEach(function (bar, i) {
    var delay = (i % 12) * 0.08;
    var duration = 2.2 + (i % 5) * 0.15;
    bar.style.animationDelay = delay + "s";
    bar.style.animationDuration = duration + "s";
  });

  // Click-to-enlarge screenshots (native <dialog>: focus trap, Esc, focus restore)
  var shotImgs = document.querySelectorAll(".shot img");
  if (shotImgs.length && typeof HTMLDialogElement === "function") {
    var dlg = document.createElement("dialog");
    dlg.className = "lightbox";
    dlg.setAttribute("aria-label", "Enlarged screenshot");
    dlg.innerHTML =
      '<button class="lightbox-close" type="button" aria-label="Close">&times;</button>' +
      '<img class="lightbox-img" alt="">' +
      '<p class="lightbox-cap"></p>';
    document.body.appendChild(dlg);
    var bigImg = dlg.querySelector(".lightbox-img");
    var cap = dlg.querySelector(".lightbox-cap");

    var closeLightbox = function () {
      dlg.classList.remove("open");
      document.documentElement.classList.remove("lightbox-open");
      dlg.close();
    };

    shotImgs.forEach(function (img) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "shot-btn";
      btn.setAttribute("aria-label", "Enlarge screenshot");
      img.parentNode.insertBefore(btn, img);
      btn.appendChild(img);
      btn.addEventListener("click", function () {
        bigImg.src = img.currentSrc || img.src;
        bigImg.alt = img.alt;
        cap.textContent = img.alt;
        document.documentElement.classList.add("lightbox-open");
        dlg.showModal();
        requestAnimationFrame(function () { dlg.classList.add("open"); });
      });
    });

    dlg.querySelector(".lightbox-close").addEventListener("click", closeLightbox);
    // Click on the backdrop (the dialog element itself, not its contents) closes.
    dlg.addEventListener("click", function (e) { if (e.target === dlg) closeLightbox(); });
    // Esc fires "cancel" then "close"; keep the page-scroll lock in sync.
    dlg.addEventListener("close", function () {
      dlg.classList.remove("open");
      document.documentElement.classList.remove("lightbox-open");
    });
  }

  // Footer year
  var yearEl = document.getElementById("year");
  if (yearEl) yearEl.textContent = new Date().getFullYear();
})();
