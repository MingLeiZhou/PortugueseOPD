import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { decodeOverlay, readCatalog, validateResultIdentity, stepSnapshot } from '../app/snapshot-data.ts';

const root = new URL('../public/data/pt60/', import.meta.url);
const read = (path) => JSON.parse(readFileSync(new URL(path, root), 'utf8'));
const catalog = readCatalog(read('snapshots/index.json'));
const ids = (kind) => read(`${kind}.geojson`).features.map((row) => row.properties.id).sort();

test('rc2 contains two complete chronological weeks, without the three arbitrary references', () => {
  for (const group of ['summer', 'winter']) {
    const cases = catalog.snapshots.filter((row) => row.group_id === group);
    assert.equal(cases.length, 168);
    assert.equal(stepSnapshot(catalog.snapshots, cases[0].case_id, -1), cases[0].case_id);
    assert.equal(stepSnapshot(catalog.snapshots, cases.at(-1).case_id, 1), cases.at(-1).case_id);
    for (let i = 1; i < cases.length; i++) assert.equal(Date.parse(cases[i].timestamp_utc) - Date.parse(cases[i - 1].timestamp_utc), 3600000);
  }
  assert.ok(catalog.snapshots.every((row) => ['summer', 'winter', 'custom'].includes(row.group_id)));
});

test('every variant joins the correct geometry and agrees with its summary', () => {
  const buses = ids('buses');
  const lines = ids('lines');
  let count = 0;
  for (const snapshot of catalog.snapshots) {
    for (const variant of snapshot.variants) {
      const path = `snapshots/${snapshot.slug}/${variant}`;
      const overlay = decodeOverlay(read(`${path}/results.json`));
      const summary = read(`${path}/summary.json`);
      validateResultIdentity(overlay, summary, snapshot.case_id, variant);
      assert.deepEqual(overlay.buses.map((row) => row.id).sort((a, b) => a.localeCompare(b)), buses);
      assert.deepEqual(overlay.lines.map((row) => row.id).sort((a, b) => a.localeCompare(b)), lines);
      const vm = overlay.buses.map((row) => row.vm_pu).filter((v) => v !== null);
      const loading = overlay.lines.map((row) => row.loading_percent).filter((v) => v !== null);
      assert.ok(Math.abs(Math.min(...vm) - summary.vm_pu_min) < 1e-10);
      assert.ok(Math.abs(Math.max(...vm) - summary.vm_pu_max) < 1e-10);
      assert.ok(Math.abs(Math.max(...loading) - summary.line_loading_percent_max) < 1e-10);
      assert.ok(overlay.buses.every((row) => !('p_mw' in row)));
      assert.equal(summary.risk_items[0].object_id, overlay.lines.reduce((a, b) => (a.loading_percent ?? -1) > (b.loading_percent ?? -1) ? a : b).id);
      count++;
    }
  }
  assert.equal(count, read('metadata.json').result_sets);
});

test('a same-hour result from a different method cannot be silently displayed', () => {
  const row = catalog.snapshots[0];
  const path = `snapshots/${row.slug}/AC_REVISED`;
  const overlay = decodeOverlay(read(`${path}/results.json`));
  const summary = read(`${path}/summary.json`);
  assert.throws(() => validateResultIdentity(overlay, summary, row.case_id, 'UNIFORM_PDE'));
  assert.throws(() => validateResultIdentity(overlay, summary, 'wrong-case', 'AC_REVISED'));
});

test('static geometry does not carry stale January operating values or seasonal ratings', () => {
  for (const kind of ['lines', 'buses', 'transformers', 'generators']) {
    for (const row of read(`${kind}.geojson`).features) {
      for (const key of ['vm_pu', 'loading_percent', 'p_mw', 'q_mvar', 'p_from_mw', 'max_i_ka']) assert.ok(!(key in row.properties));
    }
  }
});
