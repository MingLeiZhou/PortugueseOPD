export type Snapshot = {
  case_id: string; slug: string; timestamp_utc: string; group_id: string;
  model_revision?: string;
  generator_geometry_url?: string;
  variants?: string[];
  input_available?: boolean;
};
export type SnapshotGroup = { id: string; label: { en: string; pt: string; zh: string } };
export type SnapshotCatalog = { snapshots: Snapshot[]; groups: SnapshotGroup[]; default_case_id: string };
export type SnapshotOverlay = {
  case_id: string; variant?: string; boundary_bus_ids: string[];
  lines: Array<Record<string, unknown>>; buses: Array<Record<string, unknown>>;
  transformers: Array<Record<string, unknown>>; generators: Array<Record<string, unknown>>;
};
type ColumnarTable = { columns: string[]; rows: unknown[][] };
type PackedOverlay = { case_id: string; variant?: string; boundary_bus_ids: string[]; schema_version: 2;
  tables: Record<'lines' | 'buses' | 'transformers' | 'generators', ColumnarTable> };

export function decodeOverlay(input: SnapshotOverlay | PackedOverlay): SnapshotOverlay {
  if (!('tables' in input)) return input;
  const unpack = (table: ColumnarTable) => table.rows.map((row) => {
    if (row.length !== table.columns.length) throw new Error('Malformed snapshot table');
    return Object.fromEntries(table.columns.map((column, index) => [column, row[index]]));
  });
  return { case_id: input.case_id, variant: input.variant, boundary_bus_ids: input.boundary_bus_ids,
    lines: unpack(input.tables.lines), buses: unpack(input.tables.buses),
    transformers: unpack(input.tables.transformers), generators: unpack(input.tables.generators) };
}

export function validateResultIdentity(overlay: SnapshotOverlay, summary: { case_id?: string; variant?: string }, caseId: string, variant: string) {
  if (overlay.case_id !== caseId || summary.case_id !== caseId || overlay.variant !== variant || summary.variant !== variant) {
    throw new Error('Mismatched case or allocation variant');
  }
}

export function readCatalog(value: unknown): SnapshotCatalog {
  if (!value || typeof value !== 'object') throw new Error('Invalid snapshot catalog');
  const input = value as Partial<SnapshotCatalog>;
  if (!input.snapshots?.length) throw new Error('No available snapshots');
  const ids = new Set<string>();
  const snapshots = input.snapshots.map((row) => {
    if (!row.case_id || !/^[a-z0-9_-]+$/.test(row.slug) || !Number.isFinite(Date.parse(row.timestamp_utc)) || ids.has(row.case_id)) {
      throw new Error('Invalid snapshot catalog');
    }
    ids.add(row.case_id);
    return { ...row, group_id: row.group_id || 'representative' };
  });
  const groups = input.groups ?? [{ id: 'representative', label: { en: 'Reference snapshots', pt: 'Cenários de referência', zh: '代表快照' } }];
  if (snapshots.some((row) => !groups.some((group) => group.id === row.group_id))) throw new Error('Unknown snapshot group');
  return { snapshots, groups, default_case_id: ids.has(input.default_case_id ?? '') ? input.default_case_id! : snapshots[0].case_id };
}

export function stepSnapshot(snapshots: Snapshot[], caseId: string, direction: -1 | 1): string {
  const current = snapshots.find((row) => row.case_id === caseId);
  const group = snapshots.filter((row) => row.group_id === current?.group_id);
  if (!group.length) return caseId;
  const index = group.findIndex((row) => row.case_id === caseId);
  // Do not imply continuity between the end and beginning of a week.
  return group[Math.max(0, Math.min(group.length - 1, index + direction))].case_id;
}
