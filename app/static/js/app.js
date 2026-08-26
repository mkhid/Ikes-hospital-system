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
    brand: "#0f766e",
    brandSoft: "rgba(15, 118, 110, 0.15)",
    green: "#21a049",
    amber: "#e08a17",
    red: "#d64545",
    blue: "#3b6fe0",
    grey: "#94a3b8",
    ink: "#16202b",
    muted: "#6b7c8f",
    line: "#e4ebf3",
  };

  HospitalPortal.palette = PALETTE;

  // ------------------------------------------------------------------
  // Chart.js defaults
  // ------------------------------------------------------------------
  function applyChartDefaults() {
    if (!global.Chart) { return false; }
    var C = global.Chart;
    C.defaults.font.family =
      '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif';
    C.defaults.font.size = 11;
    C.defaults.color = PALETTE.muted;
    C.defaults.plugins.legend.labels.boxWidth = 10;
    C.defaults.plugins.legend.labels.boxHeight = 10;
    C.defaults.plugins.legend.labels.usePointStyle = true;
    C.defaults.plugins.tooltip.backgroundColor = "rgba(22, 32, 43, 0.94)";
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
            borderRadius: 3,
            stack: "slots",
          },
          {
            label: "Coverage gap",
            data: spec.gaps,
            backgroundColor: PALETTE.red,
            borderRadius: 3,
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
            borderColor: "#fff",
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
            borderRadius: 3,
          },
          {
            label: "Weekend shifts",
            data: spec.weekends,
            backgroundColor: PALETTE.amber,
            borderRadius: 3,
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
            borderRadius: 3,
          },
          {
            label: "Hours worked",
            data: spec.worked,
            backgroundColor: PALETTE.brand,
            borderRadius: 3,
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
            borderColor: "#fff",
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
            borderRadius: 3,
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
