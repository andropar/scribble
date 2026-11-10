// The hero is a small working copy of the app: a scripted capture plays (real word timings
// from the README demo), then visitors draw their own marks and see the references the
// real app would write, in the pixel coordinates of a 1568 px wide frame.
(function () {
  "use strict";

  var CANVAS = { w: 1512, h: 982 }; // canvas points of the screenshot
  var FRAME_W = 1568; // output image width the app would save
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
  var SPEED = 1.35;

  var stage = document.getElementById("stage");
  if (!stage) return;
  var canvas = document.getElementById("ink");
  var ctx = canvas.getContext("2d");
  var transcript = document.getElementById("transcript");
  var timeLabel = document.getElementById("time");
  var hint = document.getElementById("your-turn");
  var hud = document.getElementById("hud");
  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  var marks = []; // finished marks: {tool, a, b, points, color, number, progress}
  var current = null;
  var tool = "ellipse",
    color = "#ff3b30";
  var numbered = 0;
  var playing = false,
    raf = 0,
    startedAt = 0,
    wordIndex = 0,
    markIndex = 0;

  function fit() {
    var r = stage.getBoundingClientRect(),
      dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(r.width * dpr);
    canvas.height = Math.round(r.height * dpr);
    draw();
  }

  // MARK: drawing (the same shapes the app burns into its frames)

  function stroke(path, c, width) {
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.save();
    ctx.translate(0, 1);
    ctx.strokeStyle = "rgba(0,0,0,.22)";
    ctx.lineWidth = width + 3;
    ctx.stroke(path);
    ctx.restore();
    ctx.strokeStyle = c;
    ctx.lineWidth = width;
    ctx.stroke(path);
  }

  function badge(n, x, y, c) {
    ctx.beginPath();
    ctx.arc(x, y + 1, 12.5, 0, 7);
    ctx.fillStyle = "rgba(0,0,0,.25)";
    ctx.fill();
    ctx.beginPath();
    ctx.arc(x, y, 11, 0, 7);
    ctx.fillStyle = c;
    ctx.fill();
    ctx.beginPath();
    ctx.arc(x, y, 10, 0, 7);
    ctx.strokeStyle = "#fff";
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.fillStyle = c === "#ffcc00" ? "#000" : "#fff";
    ctx.font = "700 13px Geist, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(String(n), x, y + 0.5);
  }

  function drawMark(m) {
    var p = m.progress === undefined ? 1 : m.progress;
    var a = m.a,
      b = m.b,
      path = new Path2D();
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
      stroke(path, m.color, 4);
      return;
    }
    var x = Math.min(a[0], b[0]),
      y = Math.min(a[1], b[1]),
      w = Math.abs(a[0] - b[0]),
      h = Math.abs(a[1] - b[1]);
    if (m.tool === "ellipse") {
      path.ellipse(
        x + w / 2,
        y + h / 2,
        w / 2,
        h / 2,
        0,
        -Math.PI / 2,
        -Math.PI / 2 + Math.PI * 2 * p,
      );
      stroke(path, m.color, 3.5);
    } else if (m.tool === "box") {
      ctx.save();
      ctx.setLineDash([2 * (w + h) * p, 99999]);
      path.roundRect(x, y, w, h, 4);
      stroke(path, m.color, 3.5);
      ctx.restore();
    } else if (m.tool === "arrow") {
      var tx = a[0] + (b[0] - a[0]) * p,
        ty = a[1] + (b[1] - a[1]) * p;
      var dx = tx - a[0],
        dy = ty - a[1],
        len = Math.hypot(dx, dy);
      if (len < 1) return;
      var ux = dx / len,
        uy = dy / len,
        head = Math.min(16, len * 0.6),
        half = head * 0.55;
      path.moveTo(a[0], a[1]);
      path.lineTo(tx - ux * head * 0.6, ty - uy * head * 0.6);
      stroke(path, m.color, 4);
      ctx.beginPath();
      ctx.moveTo(tx, ty);
      ctx.lineTo(tx - ux * head - uy * half, ty - uy * head + ux * half);
      ctx.lineTo(tx - ux * head + uy * half, ty - uy * head - ux * half);
      ctx.closePath();
      ctx.fillStyle = m.color;
      ctx.fill();
    }
    if (m.number && p >= 1) {
      if (m.tool === "arrow") {
        var l = Math.max(Math.hypot(a[0] - b[0], a[1] - b[1]), 0.001);
        badge(
          m.number,
          a[0] + ((a[0] - b[0]) / l) * 15,
          a[1] + ((a[1] - b[1]) / l) * 15,
          m.color,
        );
      } else {
        badge(
          m.number,
          Math.max(13, x - 10.5),
          Math.max(13, y - 10.5),
          m.color,
        );
      }
    }
  }

  function draw() {
    var s = canvas.width / CANVAS.w;
    ctx.setTransform(s, 0, 0, s, 0, 0);
    ctx.clearRect(0, 0, CANVAS.w, CANVAS.h);
    marks.forEach(drawMark);
    if (current) drawMark(current);
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
    var box =
      "(" +
      px(Math.min.apply(0, xs)) +
      "," +
      py(Math.min.apply(0, ys)) +
      ")-(" +
      px(Math.max.apply(0, xs)) +
      "," +
      py(Math.max.apply(0, ys)) +
      ")";
    if (m.tool === "pen") return "drawing over " + box + " in frame 1";
    return (
      "#" +
      m.number +
      (m.tool === "ellipse" ? " circled " : " box around ") +
      box +
      " in frame 1"
    );
  }

  function say(text) {
    transcript.appendChild(document.createTextNode(text));
    scrollDown();
  }
  function cite(m) {
    var el = document.createElement("mark");
    el.textContent = " [" + describe(m) + "]";
    if (m.color === "#0a84ff") el.className = "b";
    if (m.color === "#ffcc00") el.className = "y";
    transcript.appendChild(el);
    scrollDown();
  }
  function scrollDown() {
    transcript.scrollTop = transcript.scrollHeight;
  }
  function clock(t) {
    var s = Math.floor(t);
    return "00:" + (s < 10 ? "0" : "") + s;
  }

  // MARK: the scripted capture

  function reset() {
    cancelAnimationFrame(raf);
    marks = [];
    current = null;
    numbered = 0;
    wordIndex = 0;
    markIndex = 0;
    transcript.innerHTML = "<b>[00:00 · frame 1]</b>";
    hint.classList.remove("show");
    draw();
  }

  function finishScript() {
    playing = false;
    timeLabel.textContent = clock(18.8);
    hint.classList.add("show");
  }

  function frame(now) {
    var t = ((now - startedAt) / 1000) * SPEED;
    timeLabel.textContent = clock(Math.min(t, 18.8));
    while (markIndex < SCRIPT.length && t >= SCRIPT[markIndex].start) {
      var s = SCRIPT[markIndex++];
      var m = {
        tool: s.tool,
        a: s.a,
        b: s.b,
        color: s.color,
        number: ++numbered,
        progress: 0,
        start: s.start,
        end: s.end,
      };
      marks.push(m);
      cite(m);
    }
    while (wordIndex < WORDS.length && t >= WORDS[wordIndex][1])
      say(WORDS[wordIndex++][0]);
    marks.forEach(function (m) {
      if (m.end) m.progress = Math.min(1, (t - m.start) / (m.end - m.start));
    });
    draw();
    if (t < 19.4) raf = requestAnimationFrame(frame);
    else finishScript();
  }

  function play() {
    reset();
    if (reduced) {
      // No motion: show the finished capture at once.
      var mi = 0;
      WORDS.forEach(function (w) {
        while (mi < SCRIPT.length && SCRIPT[mi].start <= w[1]) {
          var s = SCRIPT[mi++],
            m = {
              tool: s.tool,
              a: s.a,
              b: s.b,
              color: s.color,
              number: ++numbered,
            };
          marks.push(m);
          cite(m);
        }
        say(w[0]);
      });
      draw();
      finishScript();
      return;
    }
    playing = true;
    startedAt = performance.now();
    raf = requestAnimationFrame(frame);
  }

  // MARK: the visitor's own marks

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
    if (playing) {
      cancelAnimationFrame(raf);
      marks.forEach(function (m) {
        m.progress = 1;
      });
      while (wordIndex < WORDS.length) say(WORDS[wordIndex++][0]);
      finishScript();
    }
    hint.classList.remove("show");
    var p = point(e);
    current = { tool: tool, a: p, b: p, points: [p], color: color };
    canvas.setPointerCapture(e.pointerId);
  });
  canvas.addEventListener("pointermove", function (e) {
    if (!current) return;
    var p = point(e);
    current.b = p;
    current.points.push(p);
    draw();
  });
  function release() {
    if (!current) return;
    var m = current;
    current = null;
    var size = Math.hypot(m.a[0] - m.b[0], m.a[1] - m.b[1]);
    if (m.tool === "pen" ? m.points.length > 3 : size > 12) {
      if (m.tool !== "pen") m.number = ++numbered;
      marks.push(m);
      if (marks.length === SCRIPT.length + 1) say(" And");
      say(" this");
      cite(m);
    }
    draw();
  }
  canvas.addEventListener("pointerup", release);
  canvas.addEventListener("pointercancel", release);

  hud.addEventListener("pointerdown", function (e) {
    e.stopPropagation();
  });
  hud.addEventListener("click", function (e) {
    var b = e.target.closest("button");
    if (!b) return;
    if (b.id === "replay") {
      play();
      return;
    }
    var group = b.dataset.tool ? "[data-tool]" : "[data-color]";
    hud.querySelectorAll(group).forEach(function (x) {
      x.classList.remove("on");
    });
    b.classList.add("on");
    if (b.dataset.tool) tool = b.dataset.tool;
    else color = b.dataset.color;
  });

  window.addEventListener("resize", fit);
  var img = stage.querySelector("img");
  function begin() {
    fit();
    // Start when the stage is on screen.
    if (!("IntersectionObserver" in window)) {
      play();
      return;
    }
    var io = new IntersectionObserver(
      function (entries) {
        if (entries[0].isIntersecting) {
          io.disconnect();
          setTimeout(play, 500);
        }
      },
      { threshold: 0.4 },
    );
    io.observe(stage);
  }
  if (img.complete) begin();
  else img.addEventListener("load", begin);
})();

// The rest of the page: download button for the visitor's system, shortcut switch, copy.
(function () {
  "use strict";
  var ua = navigator.userAgent,
    platform =
      (navigator.userAgentData && navigator.userAgentData.platform) ||
      navigator.platform ||
      "";
  var os = /Win/i.test(platform)
    ? "win"
    : /Mac|iPhone|iPad/i.test(platform)
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
    document.getElementById("cta-label").textContent =
      "Download for " + names[os];
    document.getElementById("cta-note").textContent =
      "free · also for " +
      Object.keys(names)
        .filter(function (k) {
          return k !== os;
        })
        .map(function (k) {
          return names[k];
        })
        .join(" and ");
    if (files[os]) cta.href = files[os];
    var card = document.querySelector('.dl-grid [data-os="' + os + '"]');
    if (card) card.classList.add("you");
  }

  var keys = document.querySelectorAll("kbd[data-mac]");
  function shortcuts(which) {
    keys.forEach(function (k) {
      var keys =
        which === "pc" ? k.dataset.pc.split("+") : Array.from(k.dataset.mac);
      k.textContent = "";
      keys.forEach(function (key) {
        var cap = document.createElement("span");
        cap.textContent = key;
        k.appendChild(cap);
      });
    });
    document.querySelectorAll(".os-switch button").forEach(function (b) {
      b.classList.toggle("on", b.dataset.os === which);
    });
  }
  document.querySelectorAll(".os-switch button").forEach(function (b) {
    b.addEventListener("click", function () {
      shortcuts(b.dataset.os);
    });
  });
  shortcuts(os === "win" || os === "linux" ? "pc" : "mac");

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

// The page marks itself up the way the app does: numbered circles and boxes that draw
// themselves when they scroll into view, and sections that rise in once.
(function () {
  "use strict";
  var NS = "http://www.w3.org/2000/svg";
  var PATHS = {
    circle:
      "M50 4 C 82 2, 99 22, 98 50 C 97 80, 74 97, 48 96 C 20 95, 2 78, 2 50 C 2 24, 22 6, 60 5",
    box: "M2 6 L98 6 L98 94 L2 94 Z",
    line: "M0 60 C 30 54, 65 66, 100 58",
  };
  var number = 0;
  var sized = [];
  // Paths are laid out in real pixels so the stroke keeps its width and the draw-in works.
  function layout() {
    sized.forEach(function (entry) {
      var w = entry[0].clientWidth || entry[0].getBoundingClientRect().width;
      var h = entry[0].clientHeight || entry[0].getBoundingClientRect().height;
      if (!w || !h) return;
      entry[0].setAttribute("viewBox", "0 0 " + w + " " + h);
      entry[1].setAttribute(
        "d",
        PATHS[entry[2]].replace(/(-?[\d.]+) (-?[\d.]+)/g, function (_, x, y) {
          return ((x * w) / 100).toFixed(1) + " " + ((y * h) / 100).toFixed(1);
        }),
      );
    });
  }
  var marks = document.querySelectorAll("[data-mark]");
  marks.forEach(function (el) {
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
    if (kind !== "line") {
      var badge = document.createElement("span");
      badge.className = "mk-n";
      badge.setAttribute("aria-hidden", "true");
      badge.textContent = ++number;
      badge.style.background = color;
      el.appendChild(badge);
    }
  });

  layout();
  window.addEventListener("resize", layout);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(layout);

  var rising = document.querySelectorAll(
    ".strip h2, .steps li, .strip .film, .modes-head, .mode-grid article, .facts li, .private, .download h2, .dl-grid article, .faq details",
  );
  if (!("IntersectionObserver" in window)) {
    marks.forEach(function (el) {
      el.classList.add("in");
    });
    return;
  }
  var io = new IntersectionObserver(
    function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        var el = entry.target;
        io.unobserve(el);
        el.classList.add("in");
        if (el.classList.contains("rv")) {
          // Hand the element back to its own hover transitions afterwards.
          setTimeout(function () {
            el.classList.remove("rv", "in");
            el.style.transitionDelay = "";
          }, 1100);
        }
      });
    },
    { threshold: 0.2, rootMargin: "0px 0px -6% 0px" },
  );
  rising.forEach(function (el) {
    var index = Array.prototype.indexOf.call(el.parentNode.children, el);
    el.classList.add("rv");
    el.style.transitionDelay = Math.min(index, 4) * 70 + "ms";
    io.observe(el);
  });
  marks.forEach(function (el) {
    io.observe(el);
  });
})();
