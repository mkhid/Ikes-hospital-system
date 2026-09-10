/*
 * Portal front-end helpers.
 *
 * Depends on Bootstrap's bundle and Chart.js, both served from static/vendor,
 * so the portal makes no external requests and works with no internet.
 */
(function (global) {
  "use strict";

  var HospitalPortal = {};

  var PALETTE = {
    brand: "#1a5fb4",
    brandSoft: "rgba(26, 95, 180, 0.15)",
    green: "#21a049",
    amber: "#e08a17",
    red: "#d64545",
    blue: "#6355d8",
    grey: "#94a3b8",
    ink: "#16202b",
    muted: "#6b7c8f",
    line: "#e4ebf3",
  };

  HospitalPortal.palette = PALETTE;

  // ------------------------------------------------------------------
  // Theme
  // ------------------------------------------------------------------
  // The chart colours are read back out of the stylesheet rather than
  // duplicated here, so a palette change in theme.css reaches the charts
  // without this file being touched, and so the two themes cannot drift apart.
  function cssVar(name, fallback) {
    try {
      var value = global
        .getComputedStyle(document.documentElement)
        .getPropertyValue(name)
        .trim();
      return value || fallback;
    } catch (e) {
      return fallback;
    }
  }

  function refreshPalette() {
    PALETTE.brand = cssVar("--brand", PALETTE.brand);
    PALETTE.ink = cssVar("--ink", PALETTE.ink);
    PALETTE.muted = cssVar("--ink-muted", PALETTE.muted);
    PALETTE.line = cssVar("--line", PALETTE.line);
    PALETTE.grey = cssVar("--ink-faint", PALETTE.grey);
  }

  function currentTheme() {
    return document.documentElement.getAttribute("data-bs-theme") === "dark"
      ? "dark"
      : "light";
  }

  HospitalPortal.setTheme = function (theme) {
    document.documentElement.setAttribute("data-bs-theme", theme);
    try {
      localStorage.setItem("theme", theme);
    } catch (e) {
      /* private mode: the choice simply will not persist */
    }
    refreshPalette();
    // Chart.js bakes colours in at construction, so the charts have to be
    // rebuilt rather than merely re-rendered.
    if (global.Chart) {
      applyChartDefaults();
      HospitalPortal.renderCharts();
    }
  };

  HospitalPortal.toggleTheme = function () {
    HospitalPortal.setTheme(currentTheme() === "dark" ? "light" : "dark");
  };

  // ------------------------------------------------------------------
  // Chart.js defaults
  // ------------------------------------------------------------------
  function applyChartDefaults() {
    if (!global.Chart) { return false; }
    refreshPalette();
    var C = global.Chart;
    C.defaults.font.family =
      '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif';
    C.defaults.font.size = 11;
    C.defaults.color = PALETTE.muted;
    C.defaults.plugins.legend.labels.boxWidth = 10;
    C.defaults.plugins.legend.labels.boxHeight = 10;
    C.defaults.plugins.legend.labels.usePointStyle = true;
    C.defaults.plugins.tooltip.backgroundColor =
      currentTheme() === "dark" ? "rgba(6, 12, 20, 0.94)" : "rgba(22, 32, 43, 0.94)";
    C.defaults.plugins.tooltip.padding = 9;
    C.defaults.plugins.tooltip.cornerRadius = 5;
    C.defaults.plugins.tooltip.titleFont = { size: 11.5, weight: "600" };
    C.defaults.plugins.tooltip.bodyFont = { size: 11 };
    C.defaults.maintainAspectRatio = false;
    C.defaults.animation.duration = 500;
    return true;
  }

  function gridScale(extra) {
    return Object.assign(
      {
        grid: { color: PALETTE.line, drawTicks: false },
        border: { display: false },
        ticks: { padding: 6 },
      },
      extra || {}
    );
  }

  function canvas(id) {
    var node = document.getElementById(id);
    if (!node) { return null; }
    try {
      return JSON.parse(node.getAttribute("data-chart"));
    } catch (err) {
      return null;
    }
  }

  /**
   * Render every chart declared on the page.
   *
   * Each canvas carries its configuration in a data-chart attribute, so the
   * templates stay declarative and no chart data is inlined as script.
   */
  HospitalPortal.renderCharts = function () {
    if (!applyChartDefaults()) { return; }

    var nodes = document.querySelectorAll("canvas[data-chart]");
    for (var i = 0; i < nodes.length; i++) {
      var node = nodes[i];
      var spec = canvas(node.id);
      if (!spec) { continue; }

      // renderCharts may run twice: once from the page's own script block and
      // again on DOMContentLoaded. Chart.js refuses to reuse a canvas, so any
      // existing instance is torn down first, making the call idempotent.
      var existing = global.Chart.getChart(node);
      if (existing) { existing.destroy(); }

      buildChart(node, spec);
    }
  };

  function buildChart(node, spec) {
    var kind = spec.kind || "bar";

    if (kind === "coverage") { return coverageChart(node, spec); }
    if (kind === "shiftMix") { return shiftMixChart(node, spec); }
    if (kind === "workload") { return workloadChart(node, spec); }
    if (kind === "presence") { return presenceChart(node, spec); }
    if (kind === "delivery") { return deliveryChart(node, spec); }
    if (kind === "hours") { return hoursChart(node, spec); }
    return null;
  }

  /** Filled versus unfilled slots per day. */
  function coverageChart(node, spec) {
    return new global.Chart(node, {
      type: "bar",
      data: {
        labels: spec.labels,
        datasets: [
          {
            label: "Filled",
            data: spec.filled,
            backgroundColor: PALETTE.brand,
            borderRadius: 6,
            stack: "slots",
          },
          {
            label: "Coverage gap",
            data: spec.gaps,
            backgroundColor: PALETTE.red,
            borderRadius: 6,
            stack: "slots",
          },
        ],
      },
      options: {
        scales: {
          x: gridScale({ stacked: true, grid: { display: false } }),
          y: gridScale({ stacked: true, beginAtZero: true, ticks: { precision: 0 } }),
        },
        plugins: { legend: { position: "bottom" } },
      },
    });
  }

  /** How the week divides between morning, afternoon and night. */
  function shiftMixChart(node, spec) {
    return new global.Chart(node, {
      type: "doughnut",
      data: {
        labels: spec.labels,
        datasets: [
          {
            data: spec.values,
            backgroundColor: [PALETTE.brand, PALETTE.blue, PALETTE.ink],
            borderWidth: 2,
            borderColor: cssVar("--surface", "#fff"),
          },
        ],
      },
      options: {
        cutout: "62%",
        plugins: { legend: { position: "bottom" } },
      },
    });
  }

  /** Night and weekend duty per staff member: the fairness picture. */
  function workloadChart(node, spec) {
    return new global.Chart(node, {
      type: "bar",
      data: {
        labels: spec.labels,
        datasets: [
          {
            label: "Night shifts",
            data: spec.nights,
            backgroundColor: PALETTE.ink,
            borderRadius: 6,
          },
          {
            label: "Weekend shifts",
            data: spec.weekends,
            backgroundColor: PALETTE.amber,
            borderRadius: 6,
          },
        ],
      },
      options: {
        indexAxis: "y",
        scales: {
          x: gridScale({ beginAtZero: true, ticks: { precision: 0 } }),
          y: gridScale({ grid: { display: false } }),
        },
        plugins: { legend: { position: "bottom" } },
      },
    });
  }

  /** Rostered versus actual hours per staff member. */
  function hoursChart(node, spec) {
    return new global.Chart(node, {
      type: "bar",
      data: {
        labels: spec.labels,
        datasets: [
          {
            label: "Hours rostered",
            data: spec.scheduled,
            backgroundColor: PALETTE.brandSoft,
            borderColor: PALETTE.brand,
            borderWidth: 1,
            borderRadius: 6,
          },
          {
            label: "Hours worked",
            data: spec.worked,
            backgroundColor: PALETTE.brand,
            borderRadius: 6,
          },
        ],
      },
      options: {
        scales: {
          x: gridScale({ grid: { display: false } }),
          y: gridScale({ beginAtZero: true }),
        },
        plugins: { legend: { position: "bottom" } },
      },
    });
  }

  /** Live presence split. Kept as a module-level handle so polling can update it. */
  var presenceChartRef = null;

  function presenceChart(node, spec) {
    presenceChartRef = new global.Chart(node, {
      type: "doughnut",
      data: {
        labels: ["Available", "Temporarily out", "On leave", "Absent", "Off duty"],
        datasets: [
          {
            data: spec.values,
            backgroundColor: [
              PALETTE.green,
              PALETTE.amber,
              PALETTE.blue,
              PALETTE.red,
              PALETTE.grey,
            ],
            borderWidth: 2,
            borderColor: cssVar("--surface", "#fff"),
          },
        ],
      },
      options: {
        cutout: "62%",
        plugins: { legend: { position: "bottom" } },
      },
    });
    return presenceChartRef;
  }

  /** Delivery rate per notification channel. */
  function deliveryChart(node, spec) {
    return new global.Chart(node, {
      type: "bar",
      data: {
        labels: spec.labels,
        datasets: [
          {
            label: "Delivered %",
            data: spec.values,
            backgroundColor: spec.values.map(function (v) {
              return v >= 99 ? PALETTE.green : v >= 90 ? PALETTE.amber : PALETTE.red;
            }),
            borderRadius: 6,
          },
        ],
      },
      options: {
        indexAxis: "y",
        scales: {
          x: gridScale({ beginAtZero: true, max: 100 }),
          y: gridScale({ grid: { display: false } }),
        },
        plugins: { legend: { display: false } },
      },
    });
  }

  // ------------------------------------------------------------------
  // Live presence polling
  // ------------------------------------------------------------------
  /**
   * Keep the operations dashboard live.
   *
   * Polling pauses while the tab is hidden, so a dashboard left open on a ward
   * screen does not keep hitting the server all night.
   */
  HospitalPortal.startPresencePolling = function (endpoint, intervalMs) {
    var timer = null;

    function paint(data) {
      Object.keys(data).forEach(function (key) {
        var nodes = document.querySelectorAll('[data-live="' + key + '"]');
        for (var i = 0; i < nodes.length; i++) {
          if (nodes[i].textContent !== String(data[key])) {
            nodes[i].textContent = data[key];
            flash(nodes[i]);
          }
        }
      });

      var asOf = document.getElementById("as-of");
      if (asOf) { asOf.textContent = data.as_of; }

      var shift = document.getElementById("current-shift");
      if (shift) { shift.textContent = data.current_shift; }

      var board = document.getElementById("presence-board");
      if (board && data.staff) { renderBoard(board, data.staff); }

      if (presenceChartRef) {
        presenceChartRef.data.datasets[0].data = [
          data.on_duty,
          data.temporarily_out,
          data.on_leave,
          data.absent,
          data.off_duty,
        ];
        presenceChartRef.update("none");
      }
    }

    function renderBoard(board, staff) {
      board.innerHTML = staff
        .map(function (person) {
          return (
            '<div class="presence-card ' + person.colour + '">' +
              '<span class="avatar avatar-sm">' + initials(person.name) + "</span>" +
              '<div class="min-w-0">' +
                "<strong>" + escapeHtml(person.name) + "</strong>" +
                "<small>" + escapeHtml(person.department) + " &middot; " +
                  escapeHtml(person.label) + "</small>" +
              "</div>" +
            "</div>"
          );
        })
        .join("");
    }

    function initials(name) {
      var parts = String(name).trim().split(/\s+/);
      var first = parts[0] ? parts[0][0] : "";
      var last = parts.length > 1 ? parts[parts.length - 1][0] : "";
      return (first + last).toUpperCase();
    }

    function escapeHtml(value) {
      var div = document.createElement("div");
      div.textContent = value == null ? "" : String(value);
      return div.innerHTML;
    }

    function flash(node) {
      node.style.transition = "none";
      node.style.opacity = "0.35";
      global.requestAnimationFrame(function () {
        node.style.transition = "opacity 0.5s";
        node.style.opacity = "1";
      });
    }

    function poll() {
      if (document.hidden) { return; }
      fetch(endpoint, { headers: { "X-Requested-With": "fetch" } })
        .then(function (response) { return response.ok ? response.json() : null; })
        .then(function (data) { if (data) { paint(data); } })
        .catch(function () { /* transient network issue: retry next tick */ });
    }

    function start() { stop(); timer = global.setInterval(poll, intervalMs || 30000); }
    function stop() { if (timer) { global.clearInterval(timer); timer = null; } }

    document.addEventListener("visibilitychange", function () {
      if (document.hidden) { stop(); } else { poll(); start(); }
    });

    start();
  };

  // ------------------------------------------------------------------
  // Form helpers
  // ------------------------------------------------------------------
  /** Ask before an action that cannot be quietly undone. */
  HospitalPortal.confirmSubmit = function (selector, message) {
    var forms = document.querySelectorAll(selector);
    for (var i = 0; i < forms.length; i++) {
      forms[i].addEventListener("submit", function (event) {
        if (!global.confirm(message)) { event.preventDefault(); }
      });
    }
  };

  /**
   * Show the exact period a chosen date and week count resolve to.
   *
   * A roster always runs Monday to Sunday, so the date the manager picks is
   * snapped back to the Monday of its week on the server. Mirroring that here
   * means they can see the real period before submitting, rather than having
   * to infer it.
   */
  HospitalPortal.previewRosterPeriod = function (dateId, weeksId, outputId) {
    var dateEl = document.getElementById(dateId);
    var weeksEl = document.getElementById(weeksId);
    var out = document.getElementById(outputId);
    if (!dateEl || !out) { return; }

    var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    var DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

    function mondayOf(d) {
      var copy = new Date(d.getTime());
      // getDay() is 0 for Sunday, so Sunday steps back six days, not none.
      var back = (copy.getDay() + 6) % 7;
      copy.setDate(copy.getDate() - back);
      return copy;
    }

    function fmt(d, withYear) {
      return DAYS[d.getDay()] + " " + d.getDate() + " " + MONTHS[d.getMonth()] +
             (withYear ? " " + d.getFullYear() : "");
    }

    function update() {
      if (!dateEl.value) { out.textContent = "—"; return; }

      // Parse as local, not UTC: new Date("2026-09-14") is UTC midnight and
      // can land on the previous day west of Greenwich.
      var parts = dateEl.value.split("-");
      var picked = new Date(+parts[0], +parts[1] - 1, +parts[2]);
      if (isNaN(picked.getTime())) { out.textContent = "—"; return; }

      var weeks = weeksEl ? parseInt(weeksEl.value, 10) || 1 : 1;
      var start = mondayOf(picked);
      var end = new Date(start.getTime());
      end.setDate(end.getDate() + weeks * 7 - 1);

      var label = fmt(start, false) + " to " + fmt(end, true);
      if (weeks > 1) { label += "  (" + weeks + " rosters)"; }
      out.textContent = label;
    }

    dateEl.addEventListener("change", update);
    dateEl.addEventListener("input", update);
    if (weeksEl) { weeksEl.addEventListener("change", update); }
    update();
  };

  /** Keep a date range coherent: the end date can never precede the start. */
  HospitalPortal.linkDateRange = function (startId, endId) {
    var start = document.getElementById(startId);
    var end = document.getElementById(endId);
    if (!start || !end) { return; }
    function sync() {
      end.min = start.value;
      if (end.value && end.value < start.value) { end.value = start.value; }
    }
    start.addEventListener("change", sync);
    sync();
  };

  // ------------------------------------------------------------------
  document.addEventListener("DOMContentLoaded", function () {
    HospitalPortal.renderCharts();

    var themeButton = document.getElementById("theme-toggle");
    if (themeButton) {
      themeButton.addEventListener("click", HospitalPortal.toggleTheme);
    }

    // Follow the operating system if the user has never chosen explicitly.
    // Once they have, their choice wins and this stops applying.
    try {
      var media = global.matchMedia("(prefers-color-scheme: dark)");
      if (media && media.addEventListener) {
        media.addEventListener("change", function (event) {
          if (!localStorage.getItem("theme")) {
            HospitalPortal.setTheme(event.matches ? "dark" : "light");
          }
        });
      }
    } catch (e) {
      /* matchMedia or storage unavailable: the default theme stands */
    }

    // Enable Bootstrap tooltips wherever a title is opted in.
    if (global.bootstrap && global.bootstrap.Tooltip) {
      var nodes = document.querySelectorAll('[data-bs-toggle="tooltip"]');
      for (var i = 0; i < nodes.length; i++) {
        new global.bootstrap.Tooltip(nodes[i]);
      }
    }

    // Each form carries its own wording in the attribute.
    var guarded = document.querySelectorAll("form[data-confirm]");
    for (var g = 0; g < guarded.length; g++) {
      guarded[g].addEventListener("submit", function (event) {
        var message = event.currentTarget.getAttribute("data-confirm");
        if (message && !global.confirm(message)) { event.preventDefault(); }
      });
    }
  });

  global.HospitalPortal = HospitalPortal;
})(window);
