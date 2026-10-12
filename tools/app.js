(function () {
  "use strict";
  var doc = document;
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)");
  var wide = window.matchMedia("(min-width: 900px)");
  var fine = window.matchMedia("(hover: hover) and (pointer: fine)");
  function each(list, fn) { Array.prototype.forEach.call(list, fn); }
  function kbdFocus(el) { try { return el.matches(":focus-visible"); } catch (e) { return true; } }

  /* ================================================================ map */
  var atlas = doc.getElementById("atlas");
  if (atlas) (function () {
    var map = atlas.querySelector(".map");
    var svg = map.querySelector("svg");
    var ring = svg.querySelector(".ring");
    var tip = map.querySelector(".tip");
    var say = doc.getElementById("map-say");
    var vb = svg.viewBox.baseVal;
    var liveWord = map.getAttribute("data-live") || "Live now";
    var states = Array.prototype.slice.call(svg.querySelectorAll(".st"));
    var pops = {}, pins = {}, byAbbr = {};
    each(atlas.querySelectorAll(".pop"), function (p) { pops[p.getAttribute("data-abbr")] = p; });
    each(map.querySelectorAll(".pin"), function (p) { pins[p.getAttribute("data-abbr")] = p; });
    var picks = doc.querySelectorAll(".pick[data-abbr]");
    // open: the state whose card is showing. sticky: opened by click, tap or keyboard (stays until closed);
    // otherwise it was opened by pointing at the state and closes when the pointer leaves.
    // opener: where focus goes back to when the card closes (never set by pointing).
    var open = null, sticky = false, opener = null, tipTimer = 0, leaveTimer = 0, focused = null;
    // tipFor: the state the label is about. muted: the state whose label Escape hid; it stays hidden until
    // the pointer or focus moves to another state.
    var tipFor = null, muted = null;

    states.forEach(function (el) {
      byAbbr[el.getAttribute("data-abbr")] = el;
      el._x = parseFloat(el.getAttribute("data-x"));
      el._y = parseFloat(el.getAttribute("data-y"));
      el._adj = (el.getAttribute("data-adj") || "").split(" ").filter(Boolean);
      el.setAttribute("tabindex", "-1");
    });
    var current = svg.querySelector(".st.live") || states[0];
    current.setAttribute("tabindex", "0");

    function isLive(el) { return el.classList.contains("live"); }
    function abbrOf(el) { return el.getAttribute("data-abbr"); }

    function setCurrent(el) {
      if (current === el) return;
      current.setAttribute("tabindex", "-1");
      el.setAttribute("tabindex", "0");
      current = el;
    }

    // Arrow keys follow the map: first the bordering state that lies most squarely in that direction,
    // otherwise the nearest state inside a 90-degree cone that way (Alaska, Hawaii, across a lake).
    function neighbour(from, dx, dy) {
      function pick(pool, cone, byAngle) {
        var best = null, score = Infinity;
        pool.forEach(function (el) {
          if (!el || el === from) return;
          var vx = el._x - from._x, vy = el._y - from._y;
          var along = vx * dx + vy * dy;
          if (along <= 1) return;
          var across = Math.abs(vx * dy - vy * dx);
          if (across > cone * along) return;
          var s = byAngle ? across / along : along + 2.2 * across;
          if (s < score) { score = s; best = el; }
        });
        return best;
      }
      return pick(from._adj.map(function (a) { return byAbbr[a]; }), 1.2, true) || pick(states, 1, false);
    }

    /* ---- the small label: "Texas · Coming soon" ---- */
    function label(el) {
      tipFor = el;
      tip.textContent = "";
      var b = doc.createElement("b"); b.textContent = el.getAttribute("data-name");
      var s = doc.createElement("span"); s.textContent = " · " + (isLive(el) ? liveWord : "Coming soon");
      tip.appendChild(b); tip.appendChild(s);
    }
    function showTip(el, x, y) {
      label(el);
      var w = map.clientWidth, half = tip.offsetWidth / 2 + 4;
      tip.style.left = Math.max(half, Math.min(w - half, x)) + "px";
      tip.style.top = Math.max(tip.offsetHeight + 14, y) + "px";
      tip.classList.add("show");
    }
    function tipAtState(el) {
      var w = map.clientWidth, h = map.querySelector("svg").clientHeight;
      var x = (el._x - vb.x) / vb.width * w, y = (el._y - vb.y) / vb.height * h;
      if (isLive(el) && pins[abbrOf(el)]) y -= pins[abbrOf(el)].offsetHeight / 2;
      showTip(el, x, y);
    }
    function tipAtPointer(el, e) {
      var r = map.getBoundingClientRect();
      showTip(el, e.clientX - r.left, e.clientY - r.top);
    }
    function hideTip() { tip.classList.remove("show"); }
    // After the pointer moves off a state: back to the keyboard-focused state's label, or none.
    function restoreTip() { if (focused && focused !== muted && kbdFocus(focused)) tipAtState(focused); else hideTip(); }

    function ringOn(el) {
      var p = el.tagName.toLowerCase() === "path" ? el : el.querySelector("path");
      ring.setAttribute("d", p.getAttribute("d"));
    }
    function ringOff() { ring.setAttribute("d", ""); }

    /* ---- the state card ---- */
    function place(abbr) {
      var pop = pops[abbr];
      if (!wide.matches) { pop.style.left = pop.style.top = ""; return; }
      var a = atlas.getBoundingClientRect();
      var b = byAbbr[abbr].getBoundingClientRect();
      var pin = pins[abbr];
      var cw = pop.offsetWidth, ch = pop.offsetHeight, gap = 22;
      // Sit over the open Plains to the state's west; use the east side only if the west is too tight.
      var x = b.left - a.left - gap - cw;
      if (x < 0) x = Math.min(a.width - cw, b.right - a.left + gap);
      var legend = map.querySelector(".legend");
      var floor = getComputedStyle(legend).position === "absolute"
        ? legend.getBoundingClientRect().top - a.top - 12 : map.offsetHeight;
      var py = pin ? pin.getBoundingClientRect().top + pin.offsetHeight / 2 - a.top : b.top - a.top;
      var y = Math.max(0, Math.min(floor - ch, py - ch * 0.35));
      pop.style.left = Math.round(x) + "px";
      pop.style.top = Math.round(y) + "px";
    }

    function setOpen(abbr, opts) {
      opts = opts || {};
      clearTimeout(leaveTimer);
      sticky = !!(abbr && opts.sticky);
      opener = null;   // callers that open by click, tap or key set it after this
      Object.keys(pops).forEach(function (k) { pops[k].hidden = k !== abbr; });
      states.forEach(function (el) {
        if (!isLive(el)) return;
        var on = abbrOf(el) === abbr;
        el.classList.toggle("on", on);
        el.setAttribute("aria-expanded", on ? "true" : "false");
      });
      Object.keys(pins).forEach(function (k) { pins[k].classList.toggle("on", k === abbr); });
      each(picks, function (p) { p.setAttribute("aria-expanded", p.getAttribute("data-abbr") === abbr ? "true" : "false"); });
      open = abbr;
      if (!abbr) return;
      place(abbr);
      if (opts.focus) pops[abbr].focus({ preventScroll: !opts.reveal || wide.matches });
      if (opts.reveal && !wide.matches) {
        pops[abbr].scrollIntoView({ block: "nearest", behavior: reduce.matches ? "auto" : "smooth" });
      }
    }
    function close(returnFocus) {
      if (!open) return;
      var was = open, inside = pops[was].contains(doc.activeElement);
      // Focus goes back only to what opened the card by click, tap or key; a card opened by pointing
      // leaves focus alone unless focus was inside it (then it goes to the state).
      var back = opener || (inside ? byAbbr[was] : null);
      setOpen(null);
      if ((returnFocus || inside) && back) back.focus({ preventScroll: true });
    }

    /* ---- keyboard ----
       Listeners sit on the wrapper, not the <svg>: Chrome makes an <svg> with focus listeners
       focusable itself, which would put an extra tab stop in front of the states. */
    map.addEventListener("focusin", function (e) {
      var el = e.target.closest && e.target.closest(".st");
      if (!el) return;
      focused = el;
      muted = null;
      setCurrent(el);
      if (kbdFocus(el)) {
        map.classList.add("kbd");
        ringOn(el);
        tipAtState(el);
      }
    });
    map.addEventListener("focusout", function () {
      focused = null;
      map.classList.remove("kbd");
      ringOff();
      hideTip();
    });
    map.addEventListener("keydown", function (e) {
      var el = e.target.closest && e.target.closest(".st");
      if (!el) return;
      var dir = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[e.key];
      if (dir) {
        e.preventDefault();
        var n = neighbour(el, dir[0], dir[1]);
        if (n) n.focus();
        return;
      }
      if (e.key === "Home" || e.key === "End") {
        e.preventDefault();
        var live = svg.querySelectorAll(".st.live");
        (e.key === "Home" ? live[0] : live[live.length - 1]).focus();
        return;
      }
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        if (isLive(el)) { setOpen(abbrOf(el), { sticky: true, focus: true, reveal: true }); opener = el; }
        else { say.textContent = ""; say.textContent = el.getAttribute("data-name") + " is coming soon."; tipAtState(el); }
      }
    });

    /* ---- pointer ---- */
    map.addEventListener("click", function (e) {
      var el = e.target.closest && e.target.closest(".st");
      if (!el) return;
      if (isLive(el)) {
        e.preventDefault();
        setOpen(abbrOf(el), { sticky: true, reveal: true });
        opener = el;
        hideTip();
      } else {
        tipAtPointer(el, e);
        clearTimeout(tipTimer);
        tipTimer = setTimeout(restoreTip, 1800);
      }
    });
    map.addEventListener("pointermove", function (e) {
      if (e.pointerType !== "mouse") return;
      var el = e.target.closest && e.target.closest(".st");
      if (el !== muted) muted = null;
      if (!el || isLive(el)) { restoreTip(); return; }
      if (!muted) tipAtPointer(el, e);
    });
    // leaving the map ends a mouse dismissal (a keyboard user's dismissed label stays hidden)
    map.addEventListener("pointerleave", function (e) {
      if (e.pointerType !== "mouse") return;
      if (muted !== focused) muted = null;
      restoreTip();
    });
    // Pointing at a live state (or its icon) on a wide screen shows its card while the pointer is on the
    // state, its icon or the card, and for a moment after, so the pointer can cross the gap to the card.
    function hoverOpen(e) {
      if (e.pointerType !== "mouse" || !wide.matches || !fine.matches) return;
      clearTimeout(leaveTimer);
      var k = e.currentTarget.getAttribute("data-abbr");
      if (open !== k) setOpen(k);
    }
    function hoverStay(e) { if (e.pointerType === "mouse") clearTimeout(leaveTimer); }
    function hoverLeave(e) {
      if (e.pointerType !== "mouse" || !open || sticky) return;
      clearTimeout(leaveTimer);
      leaveTimer = setTimeout(function () {
        if (open && !sticky && !pops[open].contains(doc.activeElement)) setOpen(null);
      }, 300);
    }
    states.forEach(function (el) {
      if (!isLive(el)) return;
      el.addEventListener("pointerenter", hoverOpen);
      el.addEventListener("pointerleave", hoverLeave);
    });
    Object.keys(pins).forEach(function (k) {
      pins[k].addEventListener("pointerenter", hoverOpen);
      pins[k].addEventListener("pointerleave", hoverLeave);
    });
    Object.keys(pops).forEach(function (k) {
      pops[k].addEventListener("pointerenter", hoverStay);
      pops[k].addEventListener("pointerleave", hoverLeave);
      // clicking inside a card that was opened by pointing keeps it open
      pops[k].addEventListener("click", function () { if (open === k) sticky = true; });
    });

    each(picks, function (p) {
      p.addEventListener("click", function (e) {
        e.preventDefault();
        setOpen(p.getAttribute("data-abbr"), { sticky: true, focus: e.detail === 0, reveal: true });
        opener = p;
      });
    });
    Object.keys(pops).forEach(function (k) {
      pops[k].querySelector(".x").addEventListener("click", function () { close(true); });
    });
    // Escape hides the label (it can cover neighbouring states) and closes an open card.
    doc.addEventListener("keydown", function (e) {
      if (e.key !== "Escape") return;
      if (tip.classList.contains("show")) muted = tipFor;
      hideTip();
      if (open) close(true);
    });
    doc.addEventListener("click", function (e) {
      if (!open || !wide.matches) return;
      if (e.target.closest(".pop, .pin, .st.live, .pick")) return;
      close(false);
    });
    window.addEventListener("resize", function () { if (open) place(open); restoreTip(); });
  })();

  /* ================================================================ chart */
  var plot = doc.getElementById("plot");
  var dataEl = doc.getElementById("chart-data");
  if (plot && dataEl) (function () {
    var D = JSON.parse(dataEl.textContent);
    var cross = plot.querySelector(".cross");
    var ctip = plot.querySelector(".ctip");
    var say = doc.getElementById("chart-say");
    // On phones the readout is a strip above the plot (CSS) with short series names.
    var narrow = window.matchMedia("(max-width: 640px)");
    var dots = {};
    each(plot.querySelectorAll(".hov"), function (d) { dots[d.classList[1]] = d; });
    var paths = {};
    each(plot.querySelectorAll("path.ln"), function (p) { paths[p.classList[1]] = p; });
    var n = D.m.length, cur = n - 1;

    // y position (0..1) of series k at month i, read back from the drawn line
    var ys = {};
    Object.keys(paths).forEach(function (k) {
      var arr = new Array(n).fill(null);
      var re = /[ML]([\d.]+),([\d.]+)/g, m;
      var d = paths[k].getAttribute("d");
      while ((m = re.exec(d))) arr[Math.round(parseFloat(m[1]) / 1000 * (n - 1))] = parseFloat(m[2]) / 1000;
      ys[k] = arr;
    });

    function show(i, announce) {
      cur = Math.max(0, Math.min(n - 1, i));
      var f = cur / (n - 1);
      cross.style.left = (f * 100) + "%";
      ctip.textContent = "";
      var head = doc.createElement("div"); head.className = "m"; head.textContent = D.m[cur];
      ctip.appendChild(head);
      var spoken = [D.m[cur] + ", change since " + D.since + ":"];
      if (D.s[0].v[cur] == null) {
        var none = doc.createElement("div"); none.className = "r none"; none.textContent = "Not published by BLS";
        ctip.appendChild(none);
        spoken = [D.m[cur] + ": not published by BLS."];
      } else {
        D.s.forEach(function (s) {
          var r = doc.createElement("div"); r.className = "r";
          var k = doc.createElement("i"); k.style.borderColor = "var(--s-" + s.k + ")";
          var nm = doc.createElement("span"); nm.textContent = narrow.matches ? s.sn : s.n;
          var b = doc.createElement("b"); b.textContent = s.v[cur];
          r.appendChild(k); r.appendChild(nm); r.appendChild(b);
          ctip.appendChild(r);
          spoken.push(s.n + " " + s.v[cur] + ".");
        });
      }
      Object.keys(dots).forEach(function (k) {
        var y = ys[k] && ys[k][cur];
        dots[k].classList.toggle("v", y != null);
        if (y != null) { dots[k].style.left = (f * 100) + "%"; dots[k].style.top = (y * 100) + "%"; }
      });
      plot.classList.add("on");
      if (narrow.matches) {
        ctip.style.left = "";
      } else {
        var w = plot.clientWidth, tw = ctip.offsetWidth, x = f * w;
        var left = x + 16;
        if (left + tw > w) left = x - 16 - tw;
        if (left < 0) left = Math.max(0, Math.min(w - tw, x - tw / 2));
        ctip.style.left = left + "px";
      }
      if (announce) say.textContent = spoken.join(" ");
    }
    function hide() { plot.classList.remove("on"); }
    function at(e) {
      var r = plot.getBoundingClientRect();
      return Math.round(((e.clientX - r.left) / r.width) * (n - 1));
    }
    plot.addEventListener("pointermove", function (e) { show(at(e)); });
    plot.addEventListener("pointerdown", function (e) { show(at(e)); });
    plot.addEventListener("pointerleave", function (e) { if (e.pointerType === "mouse" && doc.activeElement !== plot) hide(); });
    plot.addEventListener("focus", function () { show(cur, true); });
    plot.addEventListener("blur", hide);
    plot.addEventListener("keydown", function (e) {
      var step = { ArrowLeft: -1, ArrowRight: 1, PageUp: -12, PageDown: 12 }[e.key];
      if (e.key === "Home") step = -n;
      else if (e.key === "End") step = n;
      else if (e.key === "Escape") { hide(); return; }
      if (step) { e.preventDefault(); show(cur + step, true); }
    });
    doc.addEventListener("pointerdown", function (e) { if (!plot.contains(e.target)) hide(); });
  })();
})();
