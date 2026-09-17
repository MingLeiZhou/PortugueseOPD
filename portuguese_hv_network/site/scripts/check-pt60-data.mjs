import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

const root = 'public/data/pt60';
const fail = (message) => {
  console.error(`${message}\nFrom the repository root, generate the map data first:\n  pt60 export-web --output portuguese_hv_network/site/public/data/pt60\nThe exporter requires an extracted rc2 release and a new output directory.`);
  process.exit(1);
};
if (!existsSync(join(root, 'metadata.json'))) fail('PT60 map data is missing.');
const metadata = JSON.parse(readFileSync(join(root, 'metadata.json'), 'utf8'));
const catalog = JSON.parse(readFileSync(join(root, 'snapshots/index.json'), 'utf8'));
if (metadata.verification.status !== 'PASS' || metadata.cases !== catalog.snapshots.length) fail('PT60 map metadata is inconsistent.');
let count = 0;
for (const row of catalog.snapshots) {
  if (!/^[a-z0-9_-]+$/.test(row.slug)) fail('Invalid PT60 case slug.');
  for (const variant of row.variants) {
    if (!metadata.variants.includes(variant)) fail('Invalid PT60 variant.');
    for (const name of ['results.json', 'summary.json']) {
      if (!existsSync(join(root, 'snapshots', row.slug, variant, name))) fail(`Missing ${row.case_id}/${variant}/${name}`);
    }
    count++;
  }
  if (row.input_available && !existsSync(join(root, 'snapshots', row.slug, 'input.json'))) fail(`Missing replay input: ${row.case_id}`);
}
if (count !== metadata.result_sets) fail('PT60 result count does not match its catalog.');
for (const name of ['buses', 'lines', 'transformers', 'facilities', 'generators', 'boundaries', 'substation_areas', 'power_equipment', 'line_supports']) {
  if (!existsSync(join(root, `${name}.geojson`))) fail(`Missing ${name} geometry.`);
}
for (const name of ['seasonal_week_validation.csv', 'spatial_allocation_results.csv']) {
  if (!existsSync(join(root, 'downloads', name))) fail(`Missing download: ${name}`);
}
console.log(`PT60 data ready: ${catalog.snapshots.length} cases, ${count} result sets.`);
