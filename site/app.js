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
  var base =
    "https://github.com/andropar/voice-annotate/releases/latest/download/";
  var files = {
    win: base + "voice-annotate-windows-setup.exe",
    mac: base + "voice-annotate-macos.zip",
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

// The hero is a small working copy of the app. The finished capture is there from the
// start; it then plays once (real word timings from the README demo) with the spoken words
// lighting up in capture.md. Pointing at a reference shows its mark and the other way
// round, and with a mouse you can add marks of your own. Coordinates are those of the
// 1568 px wide frame the app would save.
(function () {
  "use strict";
  var CANVAS = { w: 1512, h: 982 };
  var FRAME_W = 1568;
  var WORDS = [
    [" Okay.", 0.02],
    [" Quick", 0.58],
    [" review", 0.86],
    [" of", 1.22],
    [" the", 1.42],
    [" run", 1.52],
    [" dashboard.", 1.72],
    [" This", 2.52],
    [" best", 2.8],
    [" accuracy", 3.14],
    [" says", 3.58],
    [" 312%.", 3.92],
    [" That", 5.62],
    [" has", 5.86],
    [" to", 6.02],
    [" be", 6.22],
    [" a", 6.3],
    [" units", 6.44],
    [" bug.", 6.74],
    [" It's", 7.46],
    [" probably", 7.56],
    [" multiplying", 8],
    [" by", 8.7],
    [" 100", 8.92],
    [" twice.", 9.32],
    [" The", 10.12],
    [" legend", 10.32],
    [" here", 10.66],
    [" sits", 10.9],
    [" right", 11.16],
    [" on", 11.34],
    [" top", 11.56],
    [" of", 11.86],
    [" the", 11.98],
    [" curves.", 12.12],
    [" Can", 12.88],
    [" we", 12.98],
    [" move", 13.08],
    [" it", 13.32],
    [" below", 13.46],
    [" the", 13.66],
    [" chart?", 13.9],
    [" And", 14.4],
    [" this", 14.64],
    [" export", 14.78],
    [" button", 15.28],
    [" is", 15.52],
    [" floating", 15.72],
    [" down", 16.06],
    [" here.", 16.36],
    [" It", 16.92],
    [" should", 17.04],
    [" go", 17.2],
    [" up", 17.38],
    [" next", 17.66],
    [" to", 17.84],
    [" the", 18.08],
    [" filters.", 18.16],
  ];
  var SCRIPT = [
    {
      tool: "ellipse",
      a: [290, 280],
      b: [434, 328],
      color: "#ff3b30",
      start: 3.1,
      end: 3.8,
    },
    {
      tool: "ellipse",
      a: [1197, 612],
      b: [1409, 756],
      color: "#ff3b30",
      start: 10.4,
      end: 11.2,
    },
    {
      tool: "box",
      a: [280, 876],
      b: [410, 932],
      color: "#0a84ff",
      start: 14.8,
      end: 15.4,
    },
    {
      tool: "arrow",
      a: [430, 878],
      b: [780, 222],
      color: "#0a84ff",
      start: 17.3,
      end: 17.9,
    },
  ];
  var LENGTH = 18.8; // seconds of the capture
  var SPEED = 1.7;
  var REST = [760, 560];

  var stage = document.getElementById("stage");
  if (!stage) return;
  var canvas = document.getElementById("ink");
  var ctx = canvas.getContext("2d");
  var img = stage.querySelector("img");
  var thumb = document.getElementById("thumb");
  var tctx = thumb.getContext("2d");
  var transcript = document.getElementById("transcript");
  var md = document.getElementById("md");
  var timeLabel = document.getElementById("time");
  var countLabel = document.getElementById("count");
  var levels = document.querySelectorAll("#lv i");
  var hint = document.getElementById("your-turn");
  var tools = document.getElementById("tools");
  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  var marks = []; // {tool, a, b, points, color, number, progress, cite}
  var current = null; // the mark being drawn by the visitor
  var hot = null; // the mark pointed at, here or in capture.md
  var pointer = null; // where the scripted cursor is
  var tool = "ellipse",
    color = "#ff3b30";
  var numbered = 0;
  var words = []; // [element, time]
  var playing = false,
    raf = 0,
    startedAt = 0;
  var k = 1; // marks are sized for the page, not for the downscaled screenshot

  function fit() {
    var r = stage.getBoundingClientRect(),
      dpr = window.devicePixelRatio || 1;
    if (!r.width) return;
    canvas.width = Math.round(r.width * dpr);
    canvas.height = Math.round(r.height * dpr);
    k = Math.max(1, 0.86 / (r.width / CANVAS.w));
    draw();
  }

  // MARK: drawing (the same shapes the app burns into its frames)

  function stroke(path, c, width) {
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.strokeStyle = "rgba(255,255,255,.9)";
    ctx.lineWidth = (width + 3) * k;
    ctx.stroke(path);
    ctx.strokeStyle = c;
    ctx.lineWidth = width * k;
    ctx.stroke(path);
  }

  function badge(n, x, y, c) {
    ctx.beginPath();
    ctx.arc(x, y, 11.5 * k, 0, 7);
    ctx.fillStyle = "#fff";
    ctx.fill();
    ctx.beginPath();
    ctx.arc(x, y, 9.5 * k, 0, 7);
    ctx.fillStyle = c;
    ctx.fill();
    ctx.fillStyle = c === "#ffcc00" ? "#000" : "#fff";
    ctx.font = "700 " + 12.5 * k + "px Geist, system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(String(n), x, y + 0.6 * k);
  }

  // Where the pen is: marks grow from their first corner, as they do under a drag.
  function tip(m) {
    var p = m.progress === undefined ? 1 : m.progress;
    return [m.a[0] + (m.b[0] - m.a[0]) * p, m.a[1] + (m.b[1] - m.a[1]) * p];
  }

  function drawMark(m) {
    var p = m.progress === undefined ? 1 : m.progress;
    if (p <= 0) return;
    var path = new Path2D();
    ctx.globalAlpha = hot && hot !== m ? 0.22 : 1;
    if (m.tool === "pen") {
      var pts = m.points;
      if (pts.length < 2) return;
      path.moveTo(pts[0][0], pts[0][1]);
      for (var i = 1; i < pts.length - 1; i++) {
        path.quadraticCurveTo(
          pts[i][0],
          pts[i][1],
          (pts[i][0] + pts[i + 1][0]) / 2,
          (pts[i][1] + pts[i + 1][1]) / 2,
        );
      }
      path.lineTo(pts[pts.length - 1][0], pts[pts.length - 1][1]);
      stroke(path, m.color, 3.2);
      return;
    }
    var a = m.a,
      b = tip(m);
    var x = Math.min(a[0], b[0]),
      y = Math.min(a[1], b[1]),
      w = Math.abs(a[0] - b[0]),
      h = Math.abs(a[1] - b[1]);
    if (m.tool === "ellipse") {
      path.ellipse(
        x + w / 2,
        y + h / 2,
        Math.max(0.5, w / 2),
        Math.max(0.5, h / 2),
        0,
        0,
        Math.PI * 2,
      );
      stroke(path, m.color, 3);
    } else if (m.tool === "box") {
      path.roundRect(x, y, w, h, Math.min(4 * k, w / 2, h / 2));
      stroke(path, m.color, 3);
    } else if (m.tool === "arrow") {
      var dx = b[0] - a[0],
        dy = b[1] - a[1],
        len = Math.hypot(dx, dy);
      if (len < 1) return;
      var ux = dx / len,
        uy = dy / len,
        head = Math.min(15 * k, len * 0.6),
        half = head * 0.55;
      path.moveTo(a[0], a[1]);
      path.lineTo(b[0] - ux * head * 0.6, b[1] - uy * head * 0.6);
      stroke(path, m.color, 3.2);
      ctx.beginPath();
      ctx.moveTo(b[0], b[1]);
      ctx.lineTo(b[0] - ux * head - uy * half, b[1] - uy * head + ux * half);
      ctx.lineTo(b[0] - ux * head + uy * half, b[1] - uy * head - ux * half);
      ctx.closePath();
      ctx.fillStyle = m.color;
      ctx.fill();
    }
    if (m.number && p >= 1) {
      if (m.tool === "arrow") {
        var l = Math.max(Math.hypot(a[0] - m.b[0], a[1] - m.b[1]), 0.001);
        badge(
          m.number,
          a[0] + ((a[0] - m.b[0]) / l) * 13 * k,
          a[1] + ((a[1] - m.b[1]) / l) * 13 * k,
          m.color,
        );
      } else {
        badge(
          m.number,
          Math.max(12 * k, x - 7 * k),
          Math.max(12 * k, y - 7 * k),
          m.color,
        );
      }
    }
  }

  function drawPointer(at) {
    ctx.save();
    ctx.translate(at[0], at[1]);
    ctx.scale(k * 1.05, k * 1.05);
    ctx.beginPath();
    ctx.moveTo(0, 0);
    ctx.lineTo(0, 20);
    ctx.lineTo(5, 15.5);
    ctx.lineTo(8.5, 23.5);
    ctx.lineTo(12, 22);
    ctx.lineTo(8.6, 14.2);
    ctx.lineTo(15, 14.2);
    ctx.closePath();
    ctx.lineJoin = "round";
    ctx.lineWidth = 2.4;
    ctx.strokeStyle = "#fff";
    ctx.stroke();
    ctx.fillStyle = "#000";
    ctx.fill();
    ctx.restore();
  }

  function draw() {
    var s = canvas.width / CANVAS.w;
    ctx.setTransform(s, 0, 0, s, 0, 0);
    ctx.clearRect(0, 0, CANVAS.w, CANVAS.h);
    marks.forEach(drawMark);
    if (current) drawMark(current);
    ctx.globalAlpha = 1;
    // The attachment in capture.md is this screenshot with the marks on it.
    if (img.complete && img.naturalWidth) {
      tctx.drawImage(img, 0, 0, thumb.width, thumb.height);
      tctx.drawImage(canvas, 0, 0, thumb.width, thumb.height);
    }
    if (pointer) drawPointer(pointer);
  }

  // MARK: the reference text, as the app composes it

  var FRAME_H = Math.floor((CANVAS.h * FRAME_W) / CANVAS.w + 0.5);
  function px(v) {
    return Math.floor(v * (FRAME_W / CANVAS.w) + 0.5);
  }
  function py(v) {
    return Math.floor(v * (FRAME_H / CANVAS.h) + 0.5);
  }

  function describe(m) {
    var a = m.a,
      b = m.b;
    if (m.tool === "arrow")
      return (
        "#" +
        m.number +
        " arrow from (" +
        px(a[0]) +
        "," +
        py(a[1]) +
        ") pointing at (" +
        px(b[0]) +
        "," +
        py(b[1]) +
        ") in frame 1"
      );
    var xs, ys;
    if (m.tool === "pen") {
      xs = m.points.map(function (p) {
        return p[0];
      });
      ys = m.points.map(function (p) {
        return p[1];
      });
    } else {
      xs = [a[0], b[0]];
      ys = [a[1], b[1]];
    }
    var rect =
      "(" +
      px(Math.min.apply(0, xs)) +
      "," +
      py(Math.min.apply(0, ys)) +
      ")-(" +
      px(Math.max.apply(0, xs)) +
      "," +
      py(Math.max.apply(0, ys)) +
      ")";
    if (m.tool === "pen") return "drawing over " + rect + " in frame 1";
    return (
      "#" +
      m.number +
      (m.tool === "ellipse" ? " circled " : " box around ") +
      rect +
      " in frame 1"
    );
  }

  function cite(m) {
    var el = document.createElement("mark");
    el.className =
      "cite" +
      (m.color === "#0a84ff" ? " b" : m.color === "#ffcc00" ? " y" : "");
    el.textContent = "[" + describe(m) + "]";
    el.mark = m;
    m.cite = el;
    transcript.appendChild(document.createTextNode(" "));
    transcript.appendChild(el);
    return el;
  }

  function say(text) {
    var el = document.createElement("span");
    el.className = "w";
    el.textContent = text;
    transcript.appendChild(el);
    return el;
  }

  // The whole capture: every mark drawn, every word in place.
  function compose() {
    marks = [];
    numbered = 0;
    words = [];
    transcript.textContent = "";
    var time = document.createElement("span");
    time.className = "md-time";
    time.textContent = "[00:00 · frame 1]";
    transcript.appendChild(time);
    var next = 0;
    WORDS.forEach(function (w) {
      while (next < SCRIPT.length && SCRIPT[next].start <= w[1]) {
        var s = SCRIPT[next++];
        var m = {
          tool: s.tool,
          a: s.a,
          b: s.b,
          color: s.color,
          number: ++numbered,
          start: s.start,
          end: s.end,
        };
        marks.push(m);
        cite(m);
      }
      words.push([say(w[0]), w[1]]);
    });
  }

  function clock(t) {
    var s = Math.floor(t);
    return "0:" + (s < 10 ? "0" : "") + s;
  }

  function hud(t, speaking) {
    timeLabel.textContent = clock(t);
    var drawn = marks.filter(function (m) {
      return m.number && (m.progress === undefined || m.progress >= 1);
    }).length;
    countLabel.textContent = drawn ? " · #" + drawn : "";
    levels.forEach(function (bar, i) {
      var level = speaking
        ? 0.35 +
          0.65 * Math.abs(Math.sin(t * 13 + i * 1.7) * Math.sin(t * 7.3 + i))
        : 0.08;
      bar.classList.toggle("on", i / 5 < level);
    });
  }

  // MARK: playing the capture

  function ease(v) {
    v = Math.max(0, Math.min(1, v));
    return v < 0.5 ? 4 * v * v * v : 1 - Math.pow(-2 * v + 2, 3) / 2;
  }

  // The pointer glides to each mark before it is drawn and follows the stroke.
  function pointerAt(t) {
    var from = REST;
    for (var i = 0; i < SCRIPT.length; i++) {
      var s = SCRIPT[i],
        move = 0.65;
      if (t < s.start - move) return from;
      if (t < s.start) {
        var e = ease((t - (s.start - move)) / move);
        return [
          from[0] + (s.a[0] - from[0]) * e,
          from[1] + (s.a[1] - from[1]) * e,
        ];
      }
      var p = Math.min(1, (t - s.start) / (s.end - s.start));
      if (t <= s.end)
        return [s.a[0] + (s.b[0] - s.a[0]) * p, s.a[1] + (s.b[1] - s.a[1]) * p];
      from = s.b;
    }
    return from;
  }

  function finish() {
    cancelAnimationFrame(raf);
    playing = false;
    pointer = null;
    md.classList.remove("playing");
    marks.forEach(function (m) {
      m.progress = 1;
    });
    hud(LENGTH, false);
    // The invitation to draw leaves again after a while.
    hint.classList.add("show");
    setTimeout(function () {
      hint.classList.remove("show");
    }, 7000);
    draw();
  }

  function frame(now) {
    var t = ((now - startedAt) / 1000) * SPEED;
    var speaking = false;
    words.forEach(function (w) {
      if (t >= w[1]) w[0].classList.add("said");
      if (t >= w[1] && t < w[1] + 0.34) speaking = true;
    });
    marks.forEach(function (m) {
      if (m.start === undefined) return;
      m.progress = Math.max(0, Math.min(1, (t - m.start) / (m.end - m.start)));
      if (t >= m.start && !m.cite.classList.contains("lit")) {
        m.cite.classList.add("lit");
        // Keep the line being spoken in view where capture.md scrolls.
        if (md.scrollHeight > md.clientHeight)
          md.scrollTop = m.cite.offsetTop - md.clientHeight / 2;
      }
    });
    pointer = t < LENGTH - 0.3 ? pointerAt(t) : null;
    hud(Math.min(t, LENGTH), speaking);
    draw();
    if (t < LENGTH + 0.4) raf = requestAnimationFrame(frame);
    else finish();
  }

  function play() {
    cancelAnimationFrame(raf);
    compose();
    setHot(null);
    if (reduced) {
      finish();
      return;
    }
    hint.classList.remove("show");
    marks.forEach(function (m) {
      m.progress = 0;
    });
    md.classList.add("playing");
    md.scrollTop = 0;
    playing = true;
    startedAt = performance.now();
    raf = requestAnimationFrame(frame);
  }

  // MARK: pointing at a reference shows its mark, and the other way round

  function setHot(m) {
    if (hot === m) return;
    if (hot && hot.cite) hot.cite.classList.remove("hot");
    hot = m;
    if (hot && hot.cite) hot.cite.classList.add("hot");
    draw();
  }

  function hit(p) {
    var pad = 10 * k;
    for (var i = marks.length - 1; i >= 0; i--) {
      var m = marks[i];
      var xs = m.points
        ? m.points.map(function (q) {
            return q[0];
          })
        : [m.a[0], m.b[0]];
      var ys = m.points
        ? m.points.map(function (q) {
            return q[1];
          })
        : [m.a[1], m.b[1]];
      if (
        p[0] < Math.min.apply(0, xs) - pad ||
        p[0] > Math.max.apply(0, xs) + pad ||
        p[1] < Math.min.apply(0, ys) - pad ||
        p[1] > Math.max.apply(0, ys) + pad
      )
        continue;
      if (m.tool !== "arrow") return m;
      // An arrow's box is mostly empty: take the distance to its line.
      var dx = m.b[0] - m.a[0],
        dy = m.b[1] - m.a[1];
      var u = Math.max(
        0,
        Math.min(
          1,
          ((p[0] - m.a[0]) * dx + (p[1] - m.a[1]) * dy) / (dx * dx + dy * dy),
        ),
      );
      if (
        Math.hypot(p[0] - m.a[0] - u * dx, p[1] - m.a[1] - u * dy) <
        pad * 1.4
      )
        return m;
    }
    return null;
  }

  transcript.addEventListener("pointerover", function (e) {
    var el = e.target.closest(".cite");
    if (el && !playing && e.pointerType !== "touch") setHot(el.mark);
  });
  transcript.addEventListener("pointerout", function (e) {
    if (e.target.closest(".cite") && e.pointerType !== "touch") setHot(null);
  });
  transcript.addEventListener("click", function (e) {
    var el = e.target.closest(".cite");
    if (playing || !el) return;
    setHot(hot === el.mark ? null : el.mark);
  });

  // MARK: the visitor's own marks (mouse or pen; a finger scrolls the page)

  function point(e) {
    var r = canvas.getBoundingClientRect();
    return [
      Math.max(
        0,
        Math.min(CANVAS.w, ((e.clientX - r.left) / r.width) * CANVAS.w),
      ),
      Math.max(
        0,
        Math.min(CANVAS.h, ((e.clientY - r.top) / r.height) * CANVAS.h),
      ),
    ];
  }

  canvas.addEventListener("pointerdown", function (e) {
    if (e.pointerType === "touch" || e.button) return;
    if (playing) {
      words.forEach(function (w) {
        w[0].classList.add("said");
      });
      finish();
    }
    setHot(null);
    hint.classList.remove("show");
    var p = point(e);
    current = { tool: tool, a: p, b: p, points: [p], color: color };
    canvas.setPointerCapture(e.pointerId);
    e.preventDefault();
  });
  canvas.addEventListener("pointermove", function (e) {
    var p = point(e);
    if (!current) {
      if (!playing && e.pointerType !== "touch") setHot(hit(p));
      return;
    }
    current.b = p;
    current.points.push(p);
    draw();
  });
  canvas.addEventListener("pointerleave", function () {
    if (!current && !playing) setHot(null);
  });
  function release() {
    if (!current) return;
    var m = current;
    current = null;
    var size = Math.hypot(m.a[0] - m.b[0], m.a[1] - m.b[1]);
    if (m.tool === "pen" ? m.points.length > 3 : size > 12 * k) {
      if (m.tool !== "pen") {
        delete m.points;
        m.number = ++numbered;
      }
      marks.push(m);
      say(marks.length === SCRIPT.length + 1 ? " And this" : " and this");
      cite(m);
      md.scrollTop = md.scrollHeight;
      hud(LENGTH, false);
    }
    draw();
  }
  canvas.addEventListener("pointerup", release);
  canvas.addEventListener("pointercancel", release);

  tools.addEventListener("click", function (e) {
    var b = e.target.closest("button");
    if (!b) return;
    var group = b.dataset.tool ? "[data-tool]" : "[data-color]";
    tools.querySelectorAll(group).forEach(function (x) {
      x.classList.remove("on");
    });
    b.classList.add("on");
    if (b.dataset.tool) tool = b.dataset.tool;
    else color = b.dataset.color;
  });
  document.getElementById("replay").addEventListener("click", play);

  window.addEventListener("resize", fit);
  function begin() {
    compose();
    fit();
    hud(LENGTH, false);
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(draw);
    if (reduced || !("IntersectionObserver" in window)) {
      finish();
      return;
    }
    // Play once, when the demo is on screen.
    var io = new IntersectionObserver(
      function (entries) {
        if (!entries[0].isIntersecting) return;
        io.disconnect();
        setTimeout(play, 300);
      },
      { threshold: 0.5 },
    );
    io.observe(stage);
  }
  if (img.complete) begin();
  else img.addEventListener("load", begin);
})();

// How it works: one clip, three steps. The step that is playing is highlighted and its
// rail fills; choosing a step jumps there. The times are those in video/src/Steps.tsx.
(function () {
  "use strict";
  var STARTS = [0, 2.95, 17.4],
    END = 24.6;
  var film = document.getElementById("film");
  var player = document.getElementById("player");
  var items = document.querySelectorAll("#steps li");
  if (!film || !items.length) return;
  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var wanted = !reduced; // false once the visitor pauses
  var raf = 0;

  function update() {
    var t = film.currentTime;
    var active = t >= STARTS[2] ? 2 : t >= STARTS[1] ? 1 : 0;
    items.forEach(function (li, i) {
      var from = STARTS[i],
        to = i < 2 ? STARTS[i + 1] : film.duration || END;
      li.classList.toggle("on", i === active);
      li.style.setProperty(
        "--p",
        Math.max(0, Math.min(1, (t - from) / (to - from))).toFixed(3),
      );
    });
  }
  function loop() {
    update();
    raf = requestAnimationFrame(loop);
  }
  film.addEventListener("play", function () {
    player.classList.remove("paused");
    cancelAnimationFrame(raf);
    loop();
  });
  film.addEventListener("pause", function () {
    player.classList.add("paused");
    cancelAnimationFrame(raf);
    update();
  });
  film.addEventListener("seeked", update);

  function start() {
    var p = film.play();
    if (p && p.catch) p.catch(function () {});
  }
  function toggle() {
    wanted = film.paused;
    if (film.paused) start();
    else film.pause();
  }
  film.addEventListener("click", toggle);
  document.getElementById("pp").addEventListener("click", toggle);

  items.forEach(function (li, i) {
    li.querySelector(".step-head").addEventListener("click", function () {
      film.currentTime = STARTS[i] + 0.02;
      wanted = true;
      start();
      update();
    });
  });

  player.classList.add("paused");
  if (!("IntersectionObserver" in window)) return;
  // Plays while it is on screen.
  new IntersectionObserver(
    function (entries) {
      if (entries[0].isIntersecting) {
        if (wanted) start();
      } else if (!film.paused) {
        film.pause();
      }
    },
    { threshold: 0.35 },
  ).observe(player);
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
