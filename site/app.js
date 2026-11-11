// Shortcuts and downloads for the visitor's system.
(function () {
  "use strict";
  var ua = navigator.userAgent,
    platform =
      (navigator.userAgentData && navigator.userAgentData.platform) ||
      navigator.platform ||
      "";
  // Phones and tablets get no system: an iPad calls itself a Mac but has touch points.
  var os = /Win/i.test(platform)
    ? "win"
    : /Mac/i.test(platform) && !(navigator.maxTouchPoints > 1)
      ? "mac"
      : /Linux|X11/i.test(platform + ua) && !/Android/i.test(ua)
        ? "linux"
        : "";
  var names = { win: "Windows", mac: "macOS", linux: "Linux" };
  var base = "https://github.com/andropar/scribble/releases/latest/download/";
  var files = {
    win: base + "scribble-windows-setup.exe",
    mac: base + "scribble-macos.zip",
  };

  var cta = document.getElementById("cta");
  if (os && cta) {
    cta.textContent = "Download for " + names[os];
    if (files[os]) cta.href = files[os];
    document.getElementById("cta-note").textContent =
      "Free and open source. Also for " +
      Object.keys(names)
        .filter(function (k) {
          return k !== os;
        })
        .map(function (k) {
          return names[k];
        })
        .join(" and ") +
      ".";
  }

  // One cap per key, in the notation of the chosen system.
  var keys = document.querySelectorAll("kbd[data-mac]");
  function shortcuts(which) {
    keys.forEach(function (k) {
      var parts =
        which === "pc" ? k.dataset.pc.split("+") : Array.from(k.dataset.mac);
      k.textContent = "";
      parts.forEach(function (key) {
        var cap = document.createElement("span");
        cap.textContent = key;
        k.appendChild(cap);
      });
    });
    document.documentElement.classList.toggle("pc", which === "pc");
    document.querySelectorAll(".os-switch button").forEach(function (b) {
      b.classList.toggle("on", b.dataset.os === which);
      b.setAttribute("aria-pressed", b.dataset.os === which);
    });
  }
  document.querySelectorAll(".os-switch button").forEach(function (b) {
    b.addEventListener("click", function () {
      shortcuts(b.dataset.os);
    });
  });
  shortcuts(os === "win" || os === "linux" ? "pc" : "mac");

  // Download: one system at a time, the visitor's first.
  var tabs = document.getElementById("os-tabs");
  var panels = document.querySelectorAll(".dl");
  if (tabs && panels.length) {
    var show = function (which) {
      panels.forEach(function (p) {
        p.hidden = p.dataset.os !== which;
      });
      tabs.querySelectorAll("button").forEach(function (b) {
        b.setAttribute("aria-selected", b.dataset.os === which);
      });
    };
    tabs.hidden = false;
    document.querySelector(".download").classList.add("tabbed");
    tabs.addEventListener("click", function (e) {
      var b = e.target.closest("button");
      if (b) show(b.dataset.os);
    });
    show(os || "mac");
  }

  // The scroll hint at the bottom of the hero goes as soon as the page moves.
  var cue = document.getElementById("scroll-cue");
  if (cue) {
    var away = function () {
      cue.classList.toggle("gone", window.scrollY > 40);
    };
    window.addEventListener("scroll", away, { passive: true });
    away();
  }

  // The bar shows the name only once the one in the hero has scrolled away.
  var nav = document.getElementById("nav");
  var title = document.querySelector("h1");
  if (nav && title && "IntersectionObserver" in window) {
    nav.classList.add("at-top");
    new IntersectionObserver(
      function (entries) {
        nav.classList.toggle("at-top", entries[0].isIntersecting);
      },
      { rootMargin: "-56px 0px 0px 0px" },
    ).observe(title);
  }

  var copy = document.getElementById("copy");
  if (copy)
    copy.addEventListener("click", function () {
      navigator.clipboard
        .writeText(document.getElementById("linux-cmd").textContent)
        .then(function () {
          copy.textContent = "Copied";
          setTimeout(function () {
            copy.textContent = "Copy";
          }, 1600);
        });
    });
})();

// The hero video plays only when asked, then shows its controls.
(function () {
  "use strict";
  var film = document.getElementById("hero-film");
  var button = document.getElementById("big-play");
  if (!film || !button) return;
  button.addEventListener("click", function () {
    button.hidden = true;
    film.controls = true;
    var p = film.play();
    if (p && p.catch) p.catch(function () {});
  });
  film.addEventListener("ended", function () {
    film.controls = false;
    button.querySelector(".big-play-text").firstChild.textContent =
      "Watch again";
    button.hidden = false;
  });
})();

// How it works: the section is pinned while you scroll through it, and the scroll position
// moves the three pictures: the shortcut, the capture, the paste. Nothing plays on its own.
(function () {
  "use strict";
  var how = document.getElementById("how");
  if (!how || !how.querySelector(".scene")) return;
  var pin = how.querySelector(".how-pin");
  var items = how.querySelectorAll("#steps li");
  var scenes = how.querySelectorAll(".scene");
  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var NAV = 57; // the bar the section is pinned under

  function id(name) {
    return document.getElementById(name);
  }
  function clamp(v) {
    return Math.max(0, Math.min(1, v));
  }
  // 0 before a, 1 after b
  function seg(q, a, b) {
    return clamp((q - a) / (b - a));
  }
  function ease(v) {
    return v < 0.5 ? 4 * v * v * v : 1 - Math.pow(-2 * v + 2, 3) / 2;
  }
  function mix(a, b, t) {
    return a + (b - a) * t;
  }
  function meter(bars, t, speaking) {
    bars.forEach(function (bar, i) {
      var level = speaking
        ? 0.35 +
          0.65 * Math.abs(Math.sin(t * 13 + i * 1.7) * Math.sin(t * 7.3 + i))
        : 0.08;
      bar.classList.toggle("on", i / 5 < level);
    });
  }

  // 1. The keys go down one by one, the screen flashes and freezes, the capture bar drops in.
  var keys1 = id("s1-keys"),
    veil = id("s1-veil"),
    hud1 = id("s1-hud"),
    note1 = id("s1-note"),
    cursor1 = id("s1-cursor");
  function shortcut(q) {
    var caps = keys1.children;
    for (var i = 0; i < caps.length; i++)
      caps[i].classList.toggle("down", q >= 0.12 + i * 0.07 && q < 0.5);
    keys1.style.opacity = seg(q, 0.02, 0.08) * (1 - seg(q, 0.5, 0.58));
    var flash = q < 0.5 ? seg(q, 0.44, 0.5) : 1 - seg(q, 0.5, 0.64);
    veil.style.opacity = Math.max(
      0.5 * seg(q, 0.02, 0.1) * (1 - seg(q, 0.42, 0.48)),
      0.92 * flash,
    );
    var bar = seg(q, 0.6, 0.7);
    hud1.style.opacity = bar;
    hud1.style.transform = "translate(-50%," + (bar - 1) * 12 + "px)";
    note1.style.opacity = seg(q, 0.68, 0.78);
    var move = ease(seg(q, 0, 0.36));
    cursor1.style.left = mix(76, 54, move) + "%";
    cursor1.style.top = mix(26, 60, move) + "%";
  }

  // 2. The view moves in on what is being talked about while its mark is drawn. The words
  //    under it light up as they are said.
  var stage2 = scenes[1],
    cam = id("s2-cam"),
    cursor2 = id("s2-cursor"),
    cap = id("s2-cap"),
    time2 = id("s2-time"),
    count2 = id("s2-count"),
    bars2 = how.querySelectorAll("#s2-lv i");
  var MARKS = [
    { a: [290, 280], b: [434, 328], from: 0.1, to: 0.3, el: id("s2-m1") },
    { a: [1197, 612], b: [1409, 756], from: 0.62, to: 0.82, el: id("s2-m2") },
  ];
  var SHOTS = [
    { x: 362, y: 304, z: 2.3 },
    { x: 1296, y: 690, z: 1.9 },
  ];
  var SAID = [
    {
      from: 0.03,
      to: 0.42,
      text: "This best | accuracy says 312%. That has to be a units bug.",
    },
    {
      from: 0.5,
      to: 0.94,
      text: "The legend | here sits right on top of the curves.",
    },
  ];
  SAID.forEach(function (line, n) {
    var el = document.createElement("span");
    el.className = "sent";
    line.words = [];
    line.text.split(" ").forEach(function (word) {
      var span = document.createElement("span");
      if (word === "|") {
        span.className = "cchip";
        span.textContent = "#" + (n + 1) + " circled";
        line.chip = span;
      } else {
        span.className = "cw";
        span.textContent = word;
        line.words.push(span);
      }
      el.appendChild(span);
      el.appendChild(document.createTextNode(" "));
    });
    line.el = el;
    cap.appendChild(el);
  });

  function capture(q) {
    var W = stage2.clientWidth,
      H = stage2.clientHeight;
    if (!W) return;
    var u = W / 1512; // screen pixels per screenshot point at zoom 1
    var move = ease(seg(q, 0.44, 0.6));
    var z = mix(SHOTS[0].z, SHOTS[1].z, move);
    var x = mix(SHOTS[0].x, SHOTS[1].x, move),
      y = mix(SHOTS[0].y, SHOTS[1].y, move);
    var tx = Math.min(0, Math.max(W - W * z, W / 2 - x * u * z));
    var ty = Math.min(
      0,
      Math.max(H - W * (982 / 1512) * z, H * 0.42 - y * u * z),
    );
    cam.style.transform =
      "translate(" + tx + "px," + ty + "px) scale(" + z + ")";
    var px = 1 / (u * z); // screenshot points per screen pixel: keeps the ink 3 px wide

    var tip = [760, 560],
      drawn = 0;
    MARKS.forEach(function (m, i) {
      var p = ease(seg(q, m.from, m.to));
      var c = [mix(m.a[0], m.b[0], p), mix(m.a[1], m.b[1], p)];
      var shapes = m.el.querySelectorAll("ellipse");
      m.el.style.display = p > 0 ? "" : "none";
      for (var k = 0; k < 2; k++) {
        shapes[k].setAttribute("cx", (m.a[0] + c[0]) / 2);
        shapes[k].setAttribute("cy", (m.a[1] + c[1]) / 2);
        shapes[k].setAttribute("rx", Math.max(0.1, (c[0] - m.a[0]) / 2));
        shapes[k].setAttribute("ry", Math.max(0.1, (c[1] - m.a[1]) / 2));
        shapes[k].setAttribute("stroke-width", (k ? 3 : 6) * px);
      }
      var pop = seg(q, m.to, m.to + 0.04);
      if (pop >= 1) drawn = i + 1;
      m.el
        .querySelector(".badge")
        .setAttribute(
          "transform",
          "translate(" +
            (m.a[0] - 6 * px) +
            " " +
            (m.a[1] - 6 * px) +
            ") scale(" +
            px * pop +
            ")",
        );
      // The pointer glides to the mark's first corner, then follows the drag.
      var glide = ease(seg(q, m.from - 0.1, m.from));
      if (glide > 0)
        tip =
          p > 0 ? c : [mix(tip[0], m.a[0], glide), mix(tip[1], m.a[1], glide)];
    });
    cursor2.setAttribute(
      "transform",
      "translate(" + tip[0] + " " + tip[1] + ") scale(" + px * 0.95 + ")",
    );

    var speaking = false;
    SAID.forEach(function (line, n) {
      var on = n === 0 ? q < 0.46 : q >= 0.46;
      line.el.classList.toggle("on", on);
      var at = seg(q, line.from, line.to) * line.words.length;
      line.words.forEach(function (word, i) {
        word.classList.toggle("said", i < at);
      });
      line.chip.classList.toggle("in", q >= MARKS[n].from);
      if (on && at > 0 && at < line.words.length) speaking = true;
    });
    time2.textContent = "0:0" + Math.min(9, 3 + Math.floor(q * 7));
    count2.textContent = drawn ? " · #" + drawn : "";
    meter(bars2, q * 9, speaking);
  }

  // 3. Paste: the files and the transcript arrive in the agent's message box.
  var keys3 = id("s3-keys"),
    text3 = id("s3-text"),
    send = id("s3-send");
  var tokens = [];
  (
    "This best [#1 circled (301,290)-(450,340) in frame 1] accuracy says 312%. " +
    "That has to be a units bug. The legend " +
    "[#2 circled (1241,634)-(1461,784) in frame 1] here sits right on top of the curves."
  )
    .split(/(\[[^\]]+\])|\s+/)
    .forEach(function (part) {
      if (!part) return;
      var span = document.createElement("span");
      span.className = part.charAt(0) === "[" ? "pw pcite" : "pw";
      span.textContent = part;
      tokens.push(span);
      text3.appendChild(span);
      text3.appendChild(document.createTextNode(" "));
    });
  function paste(q) {
    var caps = keys3.children;
    for (var i = 0; i < caps.length; i++)
      caps[i].classList.toggle("down", q >= 0.1 + i * 0.07 && q < 0.28);
    keys3.style.opacity = seg(q, 0.02, 0.08) * (1 - seg(q, 0.28, 0.36));
    id("s3-img").classList.toggle("in", q >= 0.26);
    id("s3-md").classList.toggle("in", q >= 0.3);
    var shown = seg(q, 0.32, 0.8) * tokens.length;
    tokens.forEach(function (token, i) {
      token.classList.toggle("in", i < shown);
    });
    send.classList.toggle("go", q >= 0.86);
    id("s3-hint").classList.toggle("in", q >= 0.86);
  }

  var render = [shortcut, capture, paste];
  var pinned = false;

  // Pinning needs room for the whole section under the bar.
  function decide() {
    var room = window.innerHeight >= (window.innerWidth > 980 ? 640 : 540);
    pinned = !reduced && room;
    how.classList.toggle("pinned", pinned);
  }

  function distance() {
    return how.offsetHeight - pin.offsetHeight;
  }

  function update() {
    if (!pinned) {
      // Stacked: every picture in its final state.
      items.forEach(function (li, i) {
        li.classList.add("on");
        li.style.setProperty("--p", 1);
        render[i](1);
      });
      return;
    }
    var p = clamp((NAV - how.getBoundingClientRect().top) / distance());
    var active = Math.min(2, Math.floor(p * 3));
    var q = p >= 1 ? 1 : p * 3 - active;
    items.forEach(function (li, i) {
      li.classList.toggle("on", i === active);
      li.style.setProperty("--p", i < active ? 1 : i === active ? q : 0);
      scenes[i].classList.toggle("on", i === active);
    });
    render[active](q);
  }

  var queued = false;
  function onScroll() {
    if (queued) return;
    queued = true;
    requestAnimationFrame(function () {
      queued = false;
      update();
    });
  }

  // Choosing a step scrolls to where it starts.
  items.forEach(function (li, i) {
    li.querySelector(".step-head").addEventListener("click", function () {
      if (!pinned) return;
      var top = window.scrollY + how.getBoundingClientRect().top - NAV;
      window.scrollTo({
        top: top + distance() * (i / 3 + 0.02),
        behavior: "smooth",
      });
    });
  });

  window.addEventListener("scroll", onScroll, { passive: true });
  window.addEventListener("resize", function () {
    decide();
    update();
  });
  // The key caps are rebuilt when the shortcut notation is switched.
  how.querySelectorAll(".os-switch button").forEach(function (b) {
    b.addEventListener("click", function () {
      requestAnimationFrame(update);
    });
  });
  decide();
  update();
})();

// The page reads as one more capture. Each section gets a transcript line with the time
// you reached it; its heading comes in word by word, one phrase gets a mark of the kind the
// app draws, and the line then cites that mark with its position on the page.
(function () {
  "use strict";
  var NS = "http://www.w3.org/2000/svg";
  var PATHS = {
    circle:
      "M50 4 C 82 2, 99 22, 98 50 C 97 80, 74 97, 48 96 C 20 95, 2 78, 2 50 C 2 24, 22 6, 60 5",
    box: "M2 6 L98 6 L98 94 L2 94 Z",
    highlight: "M0 54 L100 52",
    pen: "M0 88 C 14 78, 22 98, 36 88 S 58 78, 72 88 S 90 96, 100 84",
  };
  var WORDS = {
    circle: "circled",
    box: "box around",
    highlight: "highlighted",
    pen: "drawing over",
  };
  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var opened = performance.now();
  var number = 0;
  var sized = [];

  // Paths are laid out in real pixels so the stroke keeps its width and the draw-in works.
  function layout() {
    sized.forEach(function (entry) {
      var svg = entry[0],
        w = svg.clientWidth || svg.getBoundingClientRect().width,
        h = svg.clientHeight || svg.getBoundingClientRect().height;
      if (!w || !h) return;
      svg.setAttribute("viewBox", "0 0 " + w + " " + h);
      if (entry[2] === "highlight")
        entry[1].style.strokeWidth = h * 0.62 + "px";
      entry[1].setAttribute(
        "d",
        PATHS[entry[2]].replace(/(-?[\d.]+) (-?[\d.]+)/g, function (_, x, y) {
          return ((x * w) / 100).toFixed(1) + " " + ((y * h) / 100).toFixed(1);
        }),
      );
    });
  }

  function ink(el) {
    var kind = el.dataset.mark,
      color = el.dataset.color || "#ff3b30";
    el.classList.add("mk", "mk-" + kind);
    var svg = document.createElementNS(NS, "svg");
    svg.setAttribute("class", "mk-ink");
    svg.setAttribute("aria-hidden", "true");
    var path = document.createElementNS(NS, "path");
    path.setAttribute("pathLength", "1");
    path.style.stroke = color;
    svg.appendChild(path);
    el.appendChild(svg);
    sized.push([svg, path, kind]);
    if (kind !== "pen") {
      var badge = document.createElement("span");
      badge.className = "mk-n";
      badge.setAttribute("aria-hidden", "true");
      badge.textContent = el.dataset.n = ++number;
      badge.style.background = color;
      el.appendChild(badge);
    }
  }

  function clock() {
    var s = Math.floor((performance.now() - opened) / 1000),
      m = Math.floor(s / 60);
    s %= 60;
    return (m < 10 ? "0" : "") + m + ":" + (s < 10 ? "0" : "") + s;
  }

  function reference(el) {
    var r = el.getBoundingClientRect(),
      x = window.scrollX,
      y = window.scrollY;
    return (
      "[" +
      (el.dataset.n ? "#" + el.dataset.n + " " : "") +
      WORDS[el.dataset.mark] +
      " (" +
      Math.round(r.left + x) +
      "," +
      Math.round(r.top + y) +
      ")-(" +
      Math.round(r.right + x) +
      "," +
      Math.round(r.bottom + y) +
      ")]"
    );
  }

  var frames = [];
  document
    .querySelectorAll("section[data-frame]")
    .forEach(function (section, i) {
      var h2 = section.querySelector("h2");
      if (!h2) return;
      var mark = section.querySelector("[data-mark]");
      if (mark) ink(mark);

      // One unit per word; a marked phrase is one unit.
      var units = [];
      h2.setAttribute("aria-label", h2.textContent);
      Array.from(h2.childNodes).forEach(function (node) {
        if (node.nodeType === 1) {
          node.classList.add("wd");
          units.push(node);
          return;
        }
        var piece = document.createDocumentFragment();
        node.textContent.split(/(\s+)/).forEach(function (part) {
          if (!part.trim()) {
            piece.appendChild(document.createTextNode(part));
            return;
          }
          var span = document.createElement("span");
          span.className = "wd";
          span.textContent = part;
          units.push(span);
          piece.appendChild(span);
        });
        h2.replaceChild(piece, node);
      });

      var line = document.createElement("p");
      line.className = "ts";
      line.setAttribute("aria-hidden", "true");
      var time = document.createElement("span");
      var cite = document.createElement("span");
      var color = mark && mark.dataset.color;
      cite.className =
        "ts-cite" +
        (color === "#0a84ff" ? " b" : color === "#ffcc00" ? " y" : "");
      line.appendChild(time);
      line.appendChild(document.createTextNode(" "));
      line.appendChild(cite);
      h2.parentNode.insertBefore(line, h2);

      frames.push({
        section: section,
        h2: h2,
        units: units,
        mark: mark,
        time: time,
        cite: cite,
        n: i + 2, // frame 1 is the screenshot in the hero
        done: false,
      });
      section.classList.add("wait");
    });

  // The marked words change size when the web font arrives or the window is resized.
  layout();
  if ("ResizeObserver" in window) {
    var watch = new ResizeObserver(layout);
    sized.forEach(function (entry) {
      watch.observe(entry[0]);
    });
  } else {
    window.addEventListener("resize", layout);
  }

  function type(el, text) {
    var i = 0;
    (function next() {
      el.textContent = text.slice(0, (i += 2));
      if (i < text.length) setTimeout(next, 16);
    })();
  }

  function run(f) {
    f.done = true;
    f.time.textContent = "[" + clock() + " · frame " + f.n + "]";
    f.section.classList.remove("wait");
    if (reduced) {
      if (f.mark) {
        f.mark.classList.add("in");
        f.cite.textContent = reference(f.mark);
      }
      return;
    }
    // Hide the words again, one by one this time, and let them come in like speech.
    var at = 120;
    var markAt = null;
    f.units.forEach(function (unit) {
      unit.style.opacity = 0;
      setTimeout(function () {
        unit.style.opacity = "";
      }, at);
      if (unit === f.mark) markAt = at;
      at += 95;
    });
    if (!f.mark) return;
    if (markAt === null) markAt = at + 150; // the mark is under the heading
    setTimeout(function () {
      f.mark.classList.add("in");
    }, markAt + 120);
    setTimeout(
      function () {
        type(f.cite, reference(f.mark));
      },
      markAt + 120 + 650,
    );
  }

  function check() {
    var limit = window.innerHeight * 0.86;
    frames.forEach(function (f) {
      if (!f.done && f.h2.getBoundingClientRect().top < limit) run(f);
    });
  }
  window.addEventListener("scroll", check, { passive: true });
  window.addEventListener("resize", check);
  check();
})();
