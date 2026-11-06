// Runs the dashboard's JavaScript in Node with a small fake page, then
// hovers, taps and clicks everything, for several made-up datasets.
//
// Started by tests/test_dashboard_js.py, which builds the dashboards and passes
// their paths in DASHBOARD_SCENARIOS. Run on its own with:
//   DASHBOARD_SCENARIOS=manifest.json node --test tests/js/
'use strict';
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const manifest = JSON.parse(fs.readFileSync(process.env.DASHBOARD_SCENARIOS, 'utf8'));

// ---------------------------------------------------------------- fake DOM
function parseTags(html, tag) {
  const out = [];
  const re = new RegExp(`<${tag}\\b([^>]*)>`, 'g');
  let m;
  while ((m = re.exec(html))) {
    const dataset = {};
    const attrRe = /data-([\w-]+)="([^"]*)"/g;
    let a;
    while ((a = attrRe.exec(m[1]))) dataset[a[1]] = a[2];
    out.push({ tag, dataset });
  }
  return out;
}

class FakeElement {
  constructor(page, key, tag = 'div', width) {
    this.page = page; this.key = key; this.id = key; this.tag = tag;
    this._html = ''; this._kids = {}; this._all = {};
    this.listeners = {}; this.attrs = {}; this.style = {}; this.dataset = {};
    this.hidden = false; this.disabled = false; this.textContent = ''; this.title = '';
    this._width = width;
    page.elements.push(this);
  }
  get clientWidth() { return this._width ?? this.page.width; }
  get innerHTML() { return this._html; }
  set innerHTML(v) { this._html = String(v); this._kids = {}; this._all = {}; }
  querySelector(sel) {
    if (!this._kids[sel]) {
      const tag = sel.split(/[ .#]/)[0] || 'div';
      this._kids[sel] = new FakeElement(this.page, `${this.key} ${sel}`, tag, this.clientWidth);
    }
    return this._kids[sel];
  }
  querySelectorAll(sel) {
    if (!this._all[sel]) {
      const parts = sel.trim().split(/\s+/);
      const tag = parts.pop().replace(/[.#].*/, '') || 'div';
      let tags = parseTags(this._html, tag);
      // A descendant selector like '.zbar div' means the inner segments, which
      // are the only tags here that carry data-* attributes (not the container).
      if (parts.length) tags = tags.filter((t) => Object.keys(t.dataset).length);
      this._all[sel] = tags.map((t, i) => {
        const el = new FakeElement(this.page, `${this.key} ${sel}[${i}]`, t.tag, 60);
        el.dataset = t.dataset;
        return el;
      });
    }
    return this._all[sel];
  }
  closest(sel) {
    if (sel === 'button') return this.tag === 'button' ? this : null;
    return this.page.closestHit ? this : null;
  }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k]; }
  getBoundingClientRect() {
    const w = this.key === 'tip' ? 180 : this.clientWidth, h = this.key === 'tip' ? 90 : 200;
    return { left: 10, top: 20, width: w, height: h, right: 10 + w, bottom: 20 + h };
  }
}

// Server answers for /status during a scenario (n = how many times it was asked)
const STATUS = {
  // A refresh is already running when the page opens; it finishes with new data.
  A: (n) => (n === 1 ? { running: true, lastOk: 'a' } : { running: false, lastOk: 'b' }),
  // The last refresh failed.
  B: () => ({ running: false, lastOk: 'a', lastError: 'Garmin refused the key (HTTP 401)' }),
};

function makePage({ width, height = 800, protocol, fetchStatus }) {
  if (STATUS[fetchStatus]) fetchStatus = STATUS[fetchStatus];
  const page = { width, elements: [], byId: {}, windowListeners: {}, docListeners: {}, reloaded: 0,
    fetches: [], closestHit: true };
  const document = {
    getElementById(id) {
      if (!page.byId[id]) {
        const el = new FakeElement(page, id);
        if (id === 'wkMetric') el.innerHTML = '<button data-m="km"><button data-m="min"><button data-m="n">';
        if (id === 'range') el.innerHTML = '<button data-r="1"><button data-r="3"><button data-r="6"><button data-r="12">';
        page.byId[id] = el;
      }
      return page.byId[id];
    },
    addEventListener(type, fn) { (page.docListeners[type] ||= []).push(fn); },
  };
  let statusCalls = 0;
  const g = {
    document,
    innerWidth: width, innerHeight: height,
    location: { protocol, reload() { page.reloaded++; } },
    addEventListener(type, fn) { (page.windowListeners[type] ||= []).push(fn); },
    // Timers run instantly while the test is live and stop once it ends,
    // so background polling can't keep Node running.
    setTimeout: (fn) => { if (!page.dead) setImmediate(fn); return 0; },
    clearTimeout: () => {},
    fetch: async (url, opts = {}) => {
      page.fetches.push([url, opts.method || 'GET']);
      if (fetchStatus === 'down') throw new Error('offline');
      if (url === 'refresh') return { json: async () => ({ started: true }) };
      statusCalls++;
      const s = typeof fetchStatus === 'function' ? fetchStatus(statusCalls) : fetchStatus;
      return { json: async () => s };
    },
  };
  return { page, globals: g };
}

function loadDashboard(htmlFile, opts) {
  const html = fs.readFileSync(htmlFile, 'utf8');
  let script = html.split('<script>')[1].split('</script>')[0];
  // Same code file for every scenario (only the data differs), so coverage
  // adds up across scenarios.
  const dataLine = script.match(/^const D = (.*);$/m);
  assert.ok(dataLine, 'dashboard data line not found');
  const data = JSON.parse(dataLine[1]);
  script = script.replace(dataLine[0], 'const D = env.__DATA__;');
  const jsFile = path.join(path.dirname(htmlFile), 'dashboard.js');
  // The page's browser globals (document, fetch, timers...) come from `env`,
  // so nothing leaks between scenarios or into Node itself.
  fs.writeFileSync(jsFile, `module.exports = function (env) { with (env) {\n${script}\n} };\n`);
  const { page, globals } = makePage(opts);
  globals.__DATA__ = data;
  page.env = globals;
  page.restore = () => { page.dead = true; };
  delete require.cache[require.resolve(jsFile)];
  require(jsFile)(globals);
  return page;
}

const flush = () => new Promise((r) => setImmediate(r));
async function settle(n = 30) { for (let i = 0; i < n; i++) await flush(); }

// Fire every hover/tap/leave handler at several positions, including off-chart.
function exercise(page) {
  let fired = 0;
  for (const el of [...page.elements]) {
    const rect = el.getBoundingClientRect();
    for (const type of ['pointermove', 'pointerdown', 'pointerleave']) {
      for (const fn of el.listeners[type] || []) {
        for (const fx of [-0.2, 0, 0.03, 0.25, 0.5, 0.75, 0.97, 1, 1.3]) {
          for (const fy of [0.1, 0.5, 0.9, 1.5]) {
            for (const pointerType of ['mouse', 'touch']) {
              fn({ clientX: rect.left + rect.width * fx, clientY: rect.top + rect.height * fy, pointerType,
                target: el });
              fired++;
            }
          }
        }
      }
    }
  }
  return fired;
}

function text(page, id) { return page.byId[id] ? page.byId[id].innerHTML : ''; }

// ---------------------------------------------------------------- tests
for (const sc of manifest) {
  test(`dashboard renders and responds: ${sc.name}`, async () => {
    const page = loadDashboard(sc.html, { width: sc.width, height: sc.height, protocol: sc.protocol, fetchStatus: sc.status });
    try {
      // Every section rendered something
      for (const id of ['today', 'ring', 'tiles', 'wkChart', 'imChart', 'heat', 'rhrChart', 'hrvChart',
        'sleepChart', 'loadChart', 'zbar', 'bigTable', 'foot', 'meta', ...(sc.empty ? [] : ['recs'])]) {
        assert.ok(text(page, id).length > 0, `${id} is empty`);
      }
      for (const [id, want] of sc.expect || []) {
        assert.match(text(page, id), new RegExp(want), `${id} should contain ${want}`);
      }
      assert.ok(!/NaN|undefined/.test(text(page, 'tiles')), 'tiles show NaN/undefined');
      assert.ok(!/NaN|undefined/.test(text(page, 'today')), 'today shows NaN/undefined');

      // Hover/tap everything
      const fired = exercise(page);
      assert.ok(fired > 0);
      const tip = page.byId.tip;
      assert.ok(tip.innerHTML.length > 0 || sc.empty, 'tooltip never filled');

      // Tapping outside a chart hides the tooltip; scrolling hides it too
      for (const hit of [true, false]) {
        page.closestHit = hit;
        for (const fn of page.docListeners.pointerdown || []) fn({ target: page.byId.today });
      }
      for (const fn of page.windowListeners.scroll || []) fn();
      assert.strictEqual(tip.style.opacity, 0);

      // Switch every measure and time range, then hover again
      const segs = [['wkMetric', ['km', 'min', 'n']], ['range', ['1', '3', '6', '12']]];
      for (const [id] of segs) {
        const g = page.byId[id];
        for (const fn of g.listeners.click || []) {
          fn({ target: { closest: () => null } });   // click between buttons: ignored
          for (const b of g.querySelectorAll('button')) fn({ target: b });
        }
      }
      exercise(page);
      assert.match(text(page, 'wkTitle') || page.byId.wkTitle.textContent, /by sport/);

      // Resize: same width is ignored, a new width re-renders
      for (const fn of page.windowListeners.resize || []) fn();
      page.env.innerWidth = sc.width + 100;
      for (const fn of page.windowListeners.resize || []) fn();
      await settle();

      // Refresh button (only when served over http)
      const btn = page.byId.refreshBtn;
      if (sc.protocol.startsWith('http')) {
        assert.strictEqual(btn.hidden, false);
        await settle();
        for (const fn of btn.listeners.click || []) await fn();
        await settle(60);
        if (sc.expectReload) assert.ok(page.reloaded > 0, 'page should reload after refresh');
        if (sc.expectButton) assert.strictEqual(btn.textContent, sc.expectButton);
      } else {
        assert.ok(!btn || !(btn.listeners.click || []).length, 'no refresh button when opened as a file');
      }
    } finally {
      page.restore();
    }
  });
}
