import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { test } from 'node:test';
import { decodeOverlay, readCatalog, stepSnapshot } from '../app/snapshot-data.ts';

const root = new URL('../public/data/', import.meta.url);
const read = (path) => JSON.parse(readFileSync(new URL(path, root), 'utf8'));
const catalog = readCatalog(read('snapshots/index.json'));
const near = (actual, expected, epsilon = 1e-5) => assert.ok(Math.abs(actual - expected) < epsilon, `${actual} != ${expected}`);

test('339 snapshots: three references and two complete, monotonic 168-hour weeks', () => {
  assert.equal(catalog.snapshots.length, 339);
  assert.equal(catalog.snapshots.filter((r) => r.group_id === 'representative').length, 3);
  for (const groupId of ['summer-week', 'winter-week']) {
    const rows = catalog.snapshots.filter((r) => r.group_id === groupId);
    assert.equal(rows.length, 168);
    assert.equal(new Set(rows.map((r) => r.timestamp_utc.slice(0, 10))).size, 7);
    rows.slice(1).forEach((r, i) => assert.equal(Date.parse(r.timestamp_utc) - Date.parse(rows[i].timestamp_utc), 3600000));
    assert.equal(stepSnapshot(catalog.snapshots, rows[0].case_id, -1), rows[0].case_id);
    assert.equal(stepSnapshot(catalog.snapshots, rows[167].case_id, 1), rows[167].case_id);
    assert.equal(stepSnapshot(catalog.snapshots, rows[23].case_id, 1), rows[24].case_id);
  }
});

test('every overlay decodes, matches its summary, geometry, risks and physical bounds', () => {
  const ids = Object.fromEntries(['lines', 'buses', 'transformers', 'generators'].map((key) =>
    [key, new Set(read(`${key}.geojson`).features.map((f) => f.properties.id))]));
  const revised = read('snapshots/generators_revised.geojson');
  const revisedIds = new Set(revised.features.map((f) => f.properties.id));
  const names = new Map(revised.features.map((f) => [f.properties.id, f.properties.nameplate_mw]));
  const corrections = new Map([
    ['GEN:01017', 'BUS:JUNCTION:60:03371'], ['GEN:01018', 'BUS:JUNCTION:60:03371'],
    ['GEN:01099', 'BUS:OSM:way:542145772:400'], ['GEN:01100', 'BUS:OSM:way:542145772:400'],
  ]);
  for (const item of catalog.snapshots) {
    const summary = read(`snapshots/${item.slug}/summary.json`);
    const overlay = decodeOverlay(read(`snapshots/${item.slug}/results.json`));
    assert.equal(summary.case_id, item.case_id);
    assert.equal(overlay.case_id, item.case_id);
    assert.equal(summary.calibration_timestamp_utc, item.timestamp_utc);
    assert.equal(summary.converged, true);
    for (const key of ['lines', 'buses', 'transformers', 'generators']) {
      const overlayIds = new Set(overlay[key].map((r) => r.id));
      assert.equal(overlayIds.size, overlay[key].length);
      const geometryIds = key === 'generators' && item.model_revision === 'CONNECTION_REVISED' ? revisedIds : ids[key];
      for (const id of geometryIds) assert.ok(overlayIds.has(id), `${key}: missing ${id}`);
    }
    near(Math.max(...overlay.lines.map((r) => r.loading_percent ?? 0)), summary.line_loading_percent_max);
    near(Math.max(...overlay.transformers.map((r) => r.loading_percent ?? 0)), summary.trafo_loading_percent_max);
    near(Math.min(...overlay.buses.filter((r) => r.vm_pu != null).map((r) => r.vm_pu)), summary.vm_pu_min);
    assert.equal(overlay.boundary_bus_ids.length, item.new_interconnector_in_service ? 8 : 7);
    for (const risk of summary.risk_items) {
      const table = risk.kind === 'LINE' ? overlay.lines : overlay.transformers;
      near(table.find((r) => r.id === risk.object_id).loading_percent, risk.loading_percent);
      assert.ok(risk.center.every(Number.isFinite));
    }
    if (item.model_revision !== 'CONNECTION_REVISED') continue;
    assert.equal(item.generator_geometry_url, '/data/snapshots/generators_revised.geojson');
    assert.deepEqual(summary.scenario_sweep, []);
    assert.equal(summary.risk_notices, undefined);
    assert.equal(summary.generation_assets, overlay.generators.filter((r) => r.in_service).length);
    for (const row of overlay.generators) {
      if (row.in_service) assert.ok(revisedIds.has(row.id), `Active generator missing from map: ${String(row.id)}`);
      if (names.has(row.id)) assert.ok(row.p_mw >= 0 && row.p_mw <= names.get(row.id) + 1e-5);
      if (corrections.has(row.id)) assert.equal(row.bus_id, corrections.get(row.id));
    }
  }
});

test('export hashes and expected research sample counts remain intact', () => {
  const manifest = JSON.parse(readFileSync(new URL('../../outputs/temporal_validation/web_seasonal_export/manifest.json', import.meta.url)));
  assert.equal(manifest.verified_samples, 336);
  for (const row of manifest.snapshots) {
    for (const [name, expected] of Object.entries(row.files)) {
      const bytes = readFileSync(new URL(`snapshots/${row.slug}/${name}`, root));
      assert.equal(createHash('sha256').update(bytes).digest('hex'), expected);
    }
  }
});

test('bad catalog and malformed columnar rows fail explicitly', () => {
  assert.throws(() => readCatalog({ snapshots: [] }));
  assert.throws(() => readCatalog({ snapshots: [{ ...catalog.snapshots[0], slug: '../secret' }] }));
  assert.throws(() => readCatalog({ snapshots: [catalog.snapshots[0], catalog.snapshots[0]] }));
  assert.throws(() => decodeOverlay({ tables: { lines: { columns: ['id'], rows: [['a', 2]] } } }));
});
