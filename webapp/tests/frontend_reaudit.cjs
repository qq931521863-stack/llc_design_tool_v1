// Execute the shipped client, including its real boot/reset/report handlers.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const fixture = JSON.parse(fs.readFileSync(0, 'utf8'));
const nodes = new Map();
function element() {
  const classes = new Set();
  return { value: '', style: {}, dataset: {}, handlers: {}, children: [],
    classList: { add: c => classes.add(c), remove: c => classes.delete(c),
      toggle: () => {}, contains: c => classes.has(c) },
    addEventListener(name, callback) { this.handlers[name] = callback; },
    appendChild(child) { this.children.push(child); if (child.selected) this.value = child.value; },
    append() {}, setAttribute() {}, removeAttribute() {}, scrollIntoView() {}, click() {},
  };
}
const document = { getElementById(id) { if (!nodes.has(id)) nodes.set(id, element()); return nodes.get(id); },
  querySelectorAll: () => [], createElement: () => element() };
const requests = [];
const context = vm.createContext({ document, location: { hash: '#/llc' },
  window: { addEventListener() {} }, console, Plotly: { react() {}, newPlot() {} },
  URL: { createObjectURL: () => 'blob:test', revokeObjectURL() {} },
  fetch: async (path, options = {}) => {
    requests.push({ path, body: options.body ? JSON.parse(options.body) : null });
    const data = path === '/api/health' ? { status: 'ok' }
      : path === '/api/llc/defaults' ? fixture.defaults : fixture.analysis;
    return { ok: true, json: async () => data, blob: async () => ({}) };
  },
});
const source = fs.readFileSync(process.argv[2], 'utf8');
vm.runInContext(source.replace(/boot\(\);\s*$/, 'globalThis.bootCompletion = boot();'), context);
(async () => {
  await context.bootCompletion;
  const first = requests.find(r => r.path === '/api/llc/analyze');
  assert(first);
  assert.equal(first.body.spec.parameter_mode, 'AUTO_DESIGN');
  for (const key of ['user_lr_h', 'user_cr_f', 'user_lm_h']) assert(!(key in first.body.spec));
  document.getElementById('resetBtn').handlers.click();
  await new Promise(setImmediate);
  const analyses = requests.filter(r => r.path === '/api/llc/analyze');
  assert.equal(analyses.length, 2);
  assert.deepEqual(analyses[0].body, analyses[1].body);
  for (const [id, value] of Object.entries({ c_lr: 100, c_cr: 25, c_lm: 500, c_ratio: 7.6,
    c_vin: 480, c_vout: 48, c_pout: 1200, c_load: 1, c_fs: 50,
    c_type: 'pi', c_bw: 1000, c_pm: 60 })) document.getElementById(id).value = value;
  document.getElementById('cReportBtn').handlers.click();
  vm.runInContext('route()', context);
  assert.equal(document.getElementById('rXlsxBtn').disabled, true);
  await document.getElementById('rPdfBtn').handlers.click();
  const report = requests.at(-1);
  assert.equal(report.path, '/api/control/report');
  assert.equal(report.body.plant_context.plant.vbus_v, 480);
  assert.equal(report.body.plant_context.plant.vout_v, 48);
  assert.equal(report.body.plant_context.plant.pout_w, 1200);
  assert.equal(report.body.plant_context.plant.turns_ratio, 7.6);
  assert.equal(report.body.plant_context.sample_time_s, 1 / 50000);
  assert(!('spec' in report.body));
  context.testLoop = { margins: { stable: false }, margins_min_delay: { stable: null },
    margins_max_delay: { stable: null } };
  vm.runInContext('renderLoop(testLoop)', context);
  const table = document.getElementById('cLoopTable').innerHTML;
  assert.equal((table.match(/UNKNOWN/g) || []).length, 2);
  assert.equal((table.match(/FAIL/g) || []).length, 1);
  assert(!table.includes('PASS'));
  document.getElementById('railReport').handlers.click();
  await document.getElementById('rPdfBtn').handlers.click();
  assert.equal(requests.at(-1).path, '/api/llc/report');
  console.log('boot, reset and control/LLC report isolation verified');
})().catch(error => { console.error(error); process.exitCode = 1; });
