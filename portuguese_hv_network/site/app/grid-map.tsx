'use client';
import Link from 'next/link';

import { useEffect, useMemo, useRef, useState } from 'react';
import * as maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import DataTools from './data-tools';
import { decodeOverlay, validateResultIdentity, readCatalog, stepSnapshot, type SnapshotCatalog, type SnapshotOverlay } from './snapshot-data';
import {
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  ChevronUp,
  CircleGauge,
  Factory,
  Layers3,
  MapPin,
  PanelLeftClose,
  PanelLeftOpen,
  Search,
  UtilityPole,
  X,
  Zap,
} from 'lucide-react';

type ViewMode = 'voltage' | 'loading' | 'voltage-result';
type Language = 'pt' | 'en' | 'zh';
type GeoFeature = { type: 'Feature'; geometry: { type: string; coordinates: unknown }; properties?: Record<string, unknown> };
type GeoCollection = { type: 'FeatureCollection'; features: GeoFeature[] };
type Summary = {
  case_id?: string; variant?: string; snapshot_kind?: 'SYNCHRONIZED_PUBLIC_RECORDS' | 'SEASON_MATCHED_PROXY' | 'CUSTOM_INPUT';
  new_interconnector_in_service?: boolean; unmapped_generation_residual_mw?: number;
  scenario_scaling: number; converged: boolean; buses: number; lines: number;
  transformers: number; vm_pu_min: number; vm_pu_max: number;
  facilities: number; substations: number; switching_stations: number; generation_assets: number;
  osm_substation_areas: number; osm_power_equipment: number; osm_hv_line_supports: number;
  line_loading_percent_max: number; trafo_loading_percent_max: number; total_load_p_mw: number; calibration_timestamp_utc?: string;
  total_generation_p_mw: number; losses_p_mw: number; validation_status: string;
  overloaded_line_rows?: number; overloaded_transformer_rows?: number;
  risk_items?: RiskItem[];
  scenario_sweep: Array<{ scaling: number; vm_pu_min: number; vm_pu_max: number; line_loading_percent_max: number; transformer_loading_percent_max: number; within_declared_screening_limits: boolean }>;
};
type RiskItem = {
  id: string; kind: 'LINE' | 'TRANSFORMER'; object_id: string; title: string;
  center: [number, number]; radius_km: number; zoom: number;
  loading_percent: number; p_mw: number; voltage_label: string;
  severity: 'OVER_LIMIT' | 'HOTSPOT';
};

const VOLTAGE_COLORS: Record<number, string> = { 60: '#159a76', 130: '#ad7a23', 150: '#dc6b2f', 220: '#ce4b55', 400: '#7246a9' };
const GENERATION_COLORS: Record<string, string> = { hydro: '#2878b5', wind: '#55a8b5', solar: '#e3ad20', gas: '#d06b31', oil: '#7c6658', diesel: '#8b5d43', biomass: '#4c8a55', biogas: '#70a65f', waste: '#8668a5', geothermal: '#b34f4f', other: '#69757b' };
const VOLTAGES = [400, 220, 150, 130, 60];
const TIME_UI = {
  en: { group: 'Snapshot series', date: 'Date (UTC)', hour: 'Time (UTC)', failed: 'Snapshot could not be loaded', retry: 'Retry', catalogFailed: 'Snapshot list could not be loaded', modelScreen: 'Model rating screen' },
  pt: { group: 'Série de cenários', date: 'Data (UTC)', hour: 'Hora (UTC)', failed: 'Não foi possível carregar o cenário', retry: 'Tentar novamente', catalogFailed: 'Não foi possível carregar a lista', modelScreen: 'Limite do modelo' },
  zh: { group: '快照分组', date: '日期（UTC）', hour: '时间（UTC）', failed: '快照载入失败', retry: '重试', catalogFailed: '快照列表载入失败', modelScreen: '模型额定值筛查' },
};
const UI = {
  en: {
    title: 'PT60 · Portuguese ≥60 kV benchmark',
    subtitle: 'Public-record high-voltage topology · AC power-flow research case',
    pass: 'PASS', controls: 'Layers & results', search: 'Search facility, plant, source or ID', mapView: 'Map view', topology: 'Topology', lineLoading: 'Line loading', busVoltage: 'Bus voltage', voltageLayers: 'Voltage layers', buses: 'Electrical buses', transformers: 'Transformers', facilities: 'Substations & switching stations', generation: 'Generation assets', details: 'Detailed OSM shapes & symbols', labels: 'Names at high zoom', generationSource: 'Generation source', scenario: 'Current scenario', calibratedAt: 'Interval start', generationPlan: 'Modelled generation', voltageRange: 'Voltage range', overloadedLines: 'Over-limit lines', overloadedTransformers: 'Over-limit transformers', scale: 'scale', solved: 'Solved', acpf: 'AC power flow', load: 'MW load', losses: 'MW losses', legend: 'Legend', scope: 'Cases combine synchronized public observations with spatial allocation for unobserved node loads. Map results come from AC power-flow calculations.', proxyScope: 'Season-matched proxy: 2026 national totals use the 2025 same-season E-REDES spatial profile. This is not a synchronized node snapshot.', synchronized: 'Public-observation reconstruction', seasonalProxy: 'Season-matched load proxy', previousSnapshot: 'Previous snapshot', nextSnapshot: 'Next snapshot', loadingSnapshot: 'Loading snapshot', lineRisk: 'Line loading hotspot', transformerRisk: 'Transformer loading hotspot', overLimit: 'Over limit', watchHotspot: 'Highest-loading asset', linesStat: 'lines', facilitiesStat: 'facilities', generationStat: 'generation assets', transformersStat: 'transformers', publicGeneration: 'Public generation record', networkFacility: 'Network facility', networkObject: 'Selected network object', fallbackObject: 'Network object', mapLabel: 'Interactive map of the Portuguese high-voltage candidate network', riskTitle: 'Risk screening', riskSubtitle: 'Click a current snapshot hotspot to locate it on the map', projectionTitle: 'Grid-stress sensitivity', inputScale: 'Input scale', minVoltage: 'Minimum voltage', peakLine: 'Peak line loading', peakTrafo: 'Peak transformer loading', projectionNote: 'Interpolated from five grid stress simulations; this is not a demand forecast.', pressureLow: 'Lower pressure', pressureWatch: 'Watch', pressureHigh: 'High pressure', pressureSevere: 'Severe pressure', closeRisk: 'Collapse risk panel', openRisk: 'Open risk panel',
  },
  pt: {
    title: 'PT60 · Referência portuguesa ≥60 kV',
    subtitle: 'Topologia de alta tensão baseada em dados públicos · Caso de fluxo de potência CA',
    pass: 'VALIDADO', controls: 'Camadas e resultados', search: 'Pesquisar instalação, central, fonte ou ID', mapView: 'Vista do mapa', topology: 'Topologia', lineLoading: 'Carga das linhas', busVoltage: 'Tensão dos barramentos', voltageLayers: 'Níveis de tensão', buses: 'Barramentos elétricos', transformers: 'Transformadores', facilities: 'Subestações e postos de corte', generation: 'Ativos de geração', details: 'Geometrias e símbolos OSM detalhados', labels: 'Nomes em grande ampliação', generationSource: 'Fonte de energia', scenario: 'Cenário atual', calibratedAt: 'Calibrado em', generationPlan: 'Plano de geração', voltageRange: 'Intervalo de tensão', overloadedLines: 'Linhas sobre limite', overloadedTransformers: 'Transformadores sobre limite', scale: 'escala', solved: 'Convergente', acpf: 'Fluxo de potência CA', load: 'MW de carga', losses: 'MW de perdas', legend: 'Legenda', scope: 'Instantâneo sincronizado de registos públicos. Os fluxos estimados não são telemetria do operador.', proxyScope: 'Cenário proxy sazonal: os totais nacionais de 2026 usam o perfil espacial E-REDES da mesma época de 2025. Não é um instantâneo nodal sincronizado.', synchronized: 'Registos públicos sincronizados', seasonalProxy: 'Proxy espacial sazonal', previousSnapshot: 'Instantâneo anterior', nextSnapshot: 'Instantâneo seguinte', loadingSnapshot: 'A carregar instantâneo', lineRisk: 'Ponto crítico de linha', transformerRisk: 'Ponto crítico de transformador', overLimit: 'Acima do limite', watchHotspot: 'Ativo com maior carga', linesStat: 'linhas', facilitiesStat: 'instalações', generationStat: 'ativos de geração', transformersStat: 'transformadores', publicGeneration: 'Registo público de geração', networkFacility: 'Instalação da rede', networkObject: 'Objeto de rede selecionado', fallbackObject: 'Objeto de rede', mapLabel: 'Mapa interativo da rede candidata portuguesa de alta tensão', riskTitle: 'Triagem de risco', riskSubtitle: 'Selecione um ponto crítico atual para o localizar no mapa', projectionTitle: 'Sensibilidade da pressão da rede', inputScale: 'Escala de entrada', minVoltage: 'Tensão mínima', peakLine: 'Carga máxima da linha', peakTrafo: 'Carga máxima do transformador', projectionNote: 'Interpolação de cinco simulações de pressão da rede; não é uma previsão de procura.', pressureLow: 'Pressão reduzida', pressureWatch: 'Atenção', pressureHigh: 'Pressão elevada', pressureSevere: 'Pressão severa', closeRisk: 'Recolher painel de risco', openRisk: 'Abrir painel de risco',
  },
  zh: {
    title: 'PT60 · 葡萄牙 ≥60 kV 高压电网基准',
    subtitle: '基于公共记录的高压拓扑 · 交流潮流研究算例',
    pass: '通过', controls: '图层与结果', search: '搜索设施、发电站、能源类型或编号', mapView: '地图视图', topology: '拓扑', lineLoading: '线路负载率', busVoltage: '母线电压', voltageLayers: '电压等级', buses: '电气母线', transformers: '变压器', facilities: '变电站与开关站', generation: '发电设施', details: 'OSM 场区形状与设备符号', labels: '高缩放名称标签', generationSource: '能源类型', scenario: '当前情景', calibratedAt: '区间起点', generationPlan: '建模发电总量', voltageRange: '电压范围', overloadedLines: '越限线路', overloadedTransformers: '越限变压器', scale: '负荷比例', solved: '已收敛', acpf: '交流潮流', load: 'MW 负荷', losses: 'MW 损耗', legend: '图例', scope: '以同步公共观测构建案例，未覆盖的节点负荷通过空间分配补全。地图结果为交流潮流计算值。', proxyScope: '季节匹配代理：采用 2026 年全国总量和 2025 年同季 E-REDES 空间分布，不是同步节点实测断面。', synchronized: '公共观测重建案例', seasonalProxy: '季节负荷空间代理', previousSnapshot: '上一个快照', nextSnapshot: '下一个快照', loadingSnapshot: '正在载入快照', lineRisk: '线路负载热点', transformerRisk: '变压器负载热点', overLimit: '超过限值', watchHotspot: '当前最高负载设备', linesStat: '条线路', facilitiesStat: '个设施', generationStat: '个发电资产', transformersStat: '台变压器', publicGeneration: '公共发电记录', networkFacility: '电网设施', networkObject: '所选电网对象', fallbackObject: '电网对象', mapLabel: '葡萄牙高压候选电网交互式地图', riskTitle: '风险筛查提示', riskSubtitle: '点击当前风险即可在地图中定位', projectionTitle: '高压电网压力敏感性', inputScale: '输入比例', minVoltage: '最低电压', peakLine: '最高线路负载率', peakTrafo: '最高变压器负载率', projectionNote: '根据5组电网压力测试仿真插值得到，并非真实负荷预测。', pressureLow: '较低压力', pressureWatch: '需要关注', pressureHigh: '高压力', pressureSevere: '严重压力', closeRisk: '收起风险面板', openRisk: '打开风险面板',
  },
} as const;
const FIELD_LABELS: Record<Language, Record<string, string>> = {
  en: { id: 'Identifier', name: 'Name', power: 'OSM object', facility_type: 'Facility type', substation: 'Substation class', generation_source: 'Energy source', operator: 'Operator', plant_method: 'Generation method', plant_type: 'Plant type', nameplate_mw: 'OSM nameplate', voltage_kv: 'Voltage', bus_voltage_kv: 'Connected voltage', hv_kv: 'HV voltage', lv_kv: 'LV voltage', rating: 'OSM rating', vm_pu: 'Voltage (p.u.)', loading_percent: 'Loading', scenario_flow_status: 'Scenario flow status', p_mw: 'Scenario P', q_mvar: 'Scenario Q', p_from_mw: 'P from', q_from_mvar: 'Q from', p_to_mw: 'P to', q_to_mvar: 'Q to', pl_mw: 'Active loss', ql_mvar: 'Reactive loss', p_hv_mw: 'P HV', p_lv_mw: 'P LV', match_distance_m: 'Bus-match distance', length_km: 'Length', sn_mva: 'Rating', bus_id: 'Associated bus', source: 'Record source', source_status: 'Source evidence', dispatch_status: 'Dispatch basis', parameter_status: 'Parameter evidence' },
  pt: { id: 'Identificador', name: 'Nome', power: 'Objeto OSM', facility_type: 'Tipo de instalação', substation: 'Classe da subestação', generation_source: 'Fonte de energia', operator: 'Operador', plant_method: 'Método de geração', plant_type: 'Tipo de central', nameplate_mw: 'Potência nominal OSM', voltage_kv: 'Tensão', bus_voltage_kv: 'Tensão de ligação', hv_kv: 'Tensão AT', lv_kv: 'Tensão BT', rating: 'Potência OSM', vm_pu: 'Tensão (p.u.)', loading_percent: 'Carregamento', scenario_flow_status: 'Estado do fluxo no cenário', p_mw: 'P do cenário', q_mvar: 'Q do cenário', p_from_mw: 'P de origem', q_from_mvar: 'Q de origem', p_to_mw: 'P de destino', q_to_mvar: 'Q de destino', pl_mw: 'Perda ativa', ql_mvar: 'Perda reativa', p_hv_mw: 'P AT', p_lv_mw: 'P BT', match_distance_m: 'Distância ao barramento', length_km: 'Comprimento', sn_mva: 'Potência nominal', bus_id: 'Barramento associado', source: 'Fonte do registo', source_status: 'Evidência da fonte', dispatch_status: 'Base de despacho', parameter_status: 'Evidência dos parâmetros' },
  zh: { id: '编号', name: '名称', power: 'OSM 对象', facility_type: '设施类型', substation: '变电站类别', generation_source: '能源类型', operator: '运营方', plant_method: '发电方式', plant_type: '电厂类型', nameplate_mw: 'OSM 标称容量', voltage_kv: '电压', bus_voltage_kv: '接入电压', hv_kv: '高压侧电压', lv_kv: '低压侧电压', rating: 'OSM 额定值', vm_pu: '电压（标幺值）', loading_percent: '负载率', scenario_flow_status: '情景潮流状态', p_mw: '情景有功', q_mvar: '情景无功', p_from_mw: '首端有功', q_from_mvar: '首端无功', p_to_mw: '末端有功', q_to_mvar: '末端无功', pl_mw: '有功损耗', ql_mvar: '无功损耗', p_hv_mw: '高压侧有功', p_lv_mw: '低压侧有功', match_distance_m: '母线匹配距离', length_km: '长度', sn_mva: '额定容量', bus_id: '关联母线', source: '记录来源', source_status: '来源证据', dispatch_status: '出力依据', parameter_status: '参数证据' },
};
const ENERGY_LABELS: Record<Language, Record<string, string>> = {
  en: { hydro: 'hydro', wind: 'wind', solar: 'solar', gas: 'gas', oil: 'oil', diesel: 'diesel', biomass: 'biomass', biogas: 'biogas', waste: 'waste', geothermal: 'geothermal', other: 'other' },
  pt: { hydro: 'hídrica', wind: 'eólica', solar: 'solar', gas: 'gás', oil: 'petróleo', diesel: 'diesel', biomass: 'biomassa', biogas: 'biogás', waste: 'resíduos', geothermal: 'geotérmica', other: 'outra' },
  zh: { hydro: '水电', wind: '风电', solar: '光伏', gas: '燃气', oil: '燃油', diesel: '柴油', biomass: '生物质', biogas: '沼气', waste: '垃圾发电', geothermal: '地热', other: '其他' },
};
const FLOW_STATUS_LABELS: Record<Language, Record<string, string>> = {
  en: { ACTIVE_FLOW: 'Active modeled flow', BELOW_0_01_MW: 'Modeled flow below 0.01 MW', ZERO_FLOW_UNDER_CURRENT_SCENARIO: 'No modeled flow in the current scenario', NOT_ENERGIZED_IN_ACTIVE_COMPONENT: 'Outside the energized scenario component' },
  pt: { ACTIVE_FLOW: 'Fluxo modelado ativo', BELOW_0_01_MW: 'Fluxo modelado inferior a 0,01 MW', ZERO_FLOW_UNDER_CURRENT_SCENARIO: 'Sem fluxo modelado no cenário atual', NOT_ENERGIZED_IN_ACTIVE_COMPONENT: 'Fora da componente energizada do cenário' },
  zh: { ACTIVE_FLOW: '当前情景存在潮流', BELOW_0_01_MW: '模型潮流小于0.01 MW', ZERO_FLOW_UNDER_CURRENT_SCENARIO: '当前情景无模型潮流', NOT_ENERGIZED_IN_ACTIVE_COMPONENT: '未进入当前供电分量' },
};
const VIEW_DESCRIPTIONS: Record<Language, Record<ViewMode, string>> = {
  en: {
    voltage: 'Topology view: line colours identify nominal voltage classes; buses and facilities show network structure.',
    loading: 'Line-loading view: colours show the solved AC loading percentage for each modelled line.',
    'voltage-result': 'Bus-voltage view: colours show solved bus voltage in per-unit (p.u.) under the current scenario.',
  },
  pt: {
    voltage: 'Vista de topologia: as cores identificam as classes de tensão nominal; barramentos e instalações mostram a estrutura da rede.',
    loading: 'Vista de carga: as cores mostram a percentagem de carga CA calculada para cada linha modelada.',
    'voltage-result': 'Vista de tensão: as cores mostram a tensão calculada dos barramentos em p.u. no cenário atual.',
  },
  zh: {
    voltage: '拓扑视图：线路颜色表示额定电压等级，母线和设施用于查看网络结构。',
    loading: '线路负载视图：颜色表示当前情景下模型线路的交流负载率。',
    'voltage-result': '母线电压视图：颜色表示当前情景下模型母线的标幺电压。',
  },
};
const LEGEND_NOTES: Record<Language, Record<ViewMode, string[]>> = {
  en: {
    voltage: ['60 kV distribution layer', '130 kV class', '150 kV class', '220 kV transmission class', '400 kV transmission class'],
    loading: ['Low loading', 'Moderate loading', 'Elevated loading', 'Near thermal limit', 'At or above 100%'],
    'voltage-result': ['Below 0.94 p.u.', 'Around 0.97 p.u.', 'Near 0.99 p.u.', 'Around 1.01 p.u.', 'At or above 1.04 p.u.'],
  },
  pt: {
    voltage: ['Camada de distribuição 60 kV', 'Classe 130 kV', 'Classe 150 kV', 'Classe de transporte 220 kV', 'Classe de transporte 400 kV'],
    loading: ['Carga reduzida', 'Carga moderada', 'Carga elevada', 'Próxima do limite térmico', 'Igual ou superior a 100%'],
    'voltage-result': ['Inferior a 0,94 p.u.', 'Cerca de 0,97 p.u.', 'Perto de 0,99 p.u.', 'Cerca de 1,01 p.u.', 'Igual ou superior a 1,04 p.u.'],
  },
  zh: {
    voltage: ['60 kV 配电层', '130 kV 电压等级', '150 kV 电压等级', '220 kV 输电等级', '400 kV 输电等级'],
    loading: ['低负载', '中等负载', '较高负载', '接近热稳定限值', '达到或超过 100%'],
    'voltage-result': ['低于 0.94 p.u.', '约 0.97 p.u.', '接近 0.99 p.u.', '约 1.01 p.u.', '达到或超过 1.04 p.u.'],
  },
};
function circlePolygon(center: [number, number], radiusKm: number) {
  const coordinates: [number, number][] = [];
  for (let index = 0; index <= 64; index += 1) {
    const angle = (index / 64) * Math.PI * 2;
    const lat = center[1] + (radiusKm / 111.32) * Math.sin(angle);
    const lon = center[0] + (radiusKm / (111.32 * Math.cos(center[1] * Math.PI / 180))) * Math.cos(angle);
    coordinates.push([lon, lat]);
  }
  return coordinates;
}
const EMPTY_FEATURE_COLLECTION: GeoCollection = { type: 'FeatureCollection', features: [] };
const fmt = (value: unknown, digits = 1) => {
  if (value === null || value === undefined || value === '') return '—';
  const number = Number(value);
  return Number.isFinite(number) ? number.toLocaleString('en-GB', { maximumFractionDigits: digits }) : '—';
};
const textValue = (value: unknown) => {
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean' || typeof value === 'bigint') return String(value);
  if (value === null || value === undefined) return '';
  return JSON.stringify(value);
};
const timestampLabel = (value?: string) => value ? value.replace('T', ' ').replace(':00+00:00', ' UTC') : '—';
const mergeOverlay = (base: GeoCollection, overlays: Array<Record<string, unknown>>): GeoCollection => {
  const byId = new Map(overlays.map((row) => [String(row.id), row]));
  return {
    type: 'FeatureCollection',
    features: base.features.map((item) => ({
      ...item,
      properties: { ...item.properties, ...byId.get(String(item.properties?.id)) },
    })),
  };
};

const voltageExpression = (): maplibregl.ExpressionSpecification => ['match', ['get', 'voltage_kv'], 60, VOLTAGE_COLORS[60], 130, VOLTAGE_COLORS[130], 150, VOLTAGE_COLORS[150], 220, VOLTAGE_COLORS[220], 400, VOLTAGE_COLORS[400], '#7c8792'];
const loadingExpression = (): maplibregl.ExpressionSpecification => ['case', ['==', ['get', 'loading_percent'], null], '#9ca3af', ['interpolate', ['linear'], ['get', 'loading_percent'], 0, '#208f7b', 35, '#71ae55', 60, '#e6b84f', 80, '#e67e38', 100, '#c94152']];
const busVoltageExpression = (): maplibregl.ExpressionSpecification => ['case', ['==', ['get', 'vm_pu'], null], '#9ca3af', ['interpolate', ['linear'], ['get', 'vm_pu'], 0.94, '#3859a8', 0.97, '#4ca6c8', 0.99, '#70b66b', 1.01, '#f2c14e', 1.04, '#d95d39']];
const generationExpression = (): maplibregl.ExpressionSpecification => ['match', ['get', 'generation_source'], 'hydro', GENERATION_COLORS.hydro, 'wind', GENERATION_COLORS.wind, 'solar', GENERATION_COLORS.solar, 'gas', GENERATION_COLORS.gas, 'oil', GENERATION_COLORS.oil, 'diesel', GENERATION_COLORS.diesel, 'biomass', GENERATION_COLORS.biomass, 'biogas', GENERATION_COLORS.biogas, 'waste', GENERATION_COLORS.waste, 'geothermal', GENERATION_COLORS.geothermal, GENERATION_COLORS.other];

function propertyRows(properties: Record<string, unknown>, language: Language) {
  const locale = language === 'pt' ? 'pt-PT' : language === 'zh' ? 'zh-CN' : 'en-GB';
  return Object.entries(FIELD_LABELS.en).flatMap(([key]) => {
    if (key === 'source' && properties.object_type === 'line') return [];
    const label = FIELD_LABELS[language][key];
    const value = properties[key];
    if (value === null || value === undefined || value === '') return [];
    let rendered = textValue(value);
    if (key.endsWith('_kv')) rendered = `${fmt(value, 0)} kV`;
    if (key === 'vm_pu') rendered = `${fmt(value, 4)} p.u.`;
    if (key === 'loading_percent') rendered = `${fmt(value, 2)}%`;
    if (key.includes('_mw')) {
      const number = Number(value);
      if (Number.isFinite(number) && number !== 0 && Math.abs(number) < 0.01) {
        rendered = `${number < 0 ? '−' : '+'}<0.01 MW`;
      } else {
        rendered = Number.isFinite(number)
          ? `${number.toLocaleString(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} MW`
          : '—';
      }
    }
    if (key.includes('_mvar')) {
      const number = Number(value);
      if (Number.isFinite(number) && number !== 0 && Math.abs(number) < 0.01) {
        rendered = `${number < 0 ? '−' : '+'}<0.01 Mvar`;
      } else {
        rendered = Number.isFinite(number)
          ? `${number.toLocaleString(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} Mvar`
          : '—';
      }
    }
    if (key === 'match_distance_m') rendered = `${fmt(value, 1)} m`;
    if (key === 'length_km') rendered = `${fmt(value, 2)} km`;
    if (key === 'sn_mva') rendered = `${fmt(value, 1)} MVA`;
    if (key === 'generation_source') rendered = ENERGY_LABELS[language][textValue(value)] ?? rendered;
    if (key === 'scenario_flow_status') rendered = FLOW_STATUS_LABELS[language][textValue(value)] ?? rendered;
    return [[label, rendered] as [string, string]];
  });
}

export default function GridMap({ dataRoot = '/data/pt60' }: { dataRoot?: string }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const baseDataRef = useRef<{ lines: GeoCollection; buses: GeoCollection; transformers: GeoCollection; facilities: GeoCollection; generators: GeoCollection } | null>(null);
  const dataRef = useRef<{ lines: GeoCollection; buses: GeoCollection; transformers: GeoCollection; facilities: GeoCollection; generators: GeoCollection } | null>(null);
  const [loadedSummary, setSummary] = useState<Summary | null>(null);
  const [mode, setMode] = useState<ViewMode>('loading');
  const [enabledVoltages, setEnabledVoltages] = useState(new Set(VOLTAGES));
  const [showBuses, setShowBuses] = useState(true);
  const [showTransformers, setShowTransformers] = useState(true);
  const [showFacilities, setShowFacilities] = useState(true);
  const [showGenerators, setShowGenerators] = useState(true);
  const [showLabels, setShowLabels] = useState(true);
  const showDetailedInfrastructure = false;
  const [selected, setSelected] = useState<Record<string, unknown> | null>(null);
  const [query, setQuery] = useState('');
  const [mobilePanel, setMobilePanel] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [language, setLanguage] = useState<Language>('en');
  const [riskPanelOpen, setRiskPanelOpen] = useState(true);
  const [selectedRisk, setSelectedRisk] = useState<string | null>(null);
  const [mapReady, setMapReady] = useState(false);
  const [mapError, setMapError] = useState(false);
  const [catalog, setCatalog] = useState<SnapshotCatalog | null>(null);
  const [selectedCaseId, setSelectedCaseId] = useState('');
  const [variant, setVariant] = useState('AC_REVISED');
  const [snapshotLoading, setSnapshotLoading] = useState(false);
  const [snapshotError, setSnapshotError] = useState(false);
  const [catalogError, setCatalogError] = useState(false);
  const [retry, setRetry] = useState(0);
  const copy = UI[language];
  const timeCopy = TIME_UI[language];
  const selectedSnapshot = catalog?.snapshots.find((row) => row.case_id === selectedCaseId);
  const groupSnapshots = catalog?.snapshots.filter((row) => row.group_id === selectedSnapshot?.group_id) ?? [];
  const snapshotIndex = groupSnapshots.findIndex((row) => row.case_id === selectedCaseId);
  const activeVariant = selectedSnapshot?.variants?.includes(variant)
    ? variant
    : selectedSnapshot?.variants?.[0] ?? variant;
  const snapshotPending = !mapReady || snapshotLoading || loadedSummary?.case_id !== selectedCaseId || loadedSummary?.variant !== activeVariant;
  const summary = !snapshotPending && !snapshotError ? loadedSummary : null;
  const dates = [...new Set(groupSnapshots.map((row) => row.timestamp_utc.slice(0, 10)))];
  const selectedDate = selectedSnapshot?.timestamp_utc.slice(0, 10) ?? '';

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${dataRoot}/snapshots/index.json`, { signal: controller.signal, cache: 'no-cache' })
      .then((response) => {
        if (!response.ok) throw new Error('Snapshot catalog unavailable');
        return response.json();
      }).then((input) => {
        const next = readCatalog(input);
        if (controller.signal.aborted) return;
        setCatalog(next);
        setSelectedCaseId((current) => next.snapshots.some((row) => row.case_id === current) ? current : next.default_case_id);
      }).catch(() => { if (!controller.signal.aborted) setCatalogError(true); });
    return () => controller.abort();
  }, [retry, dataRoot]);

  useEffect(() => {
    const timer = setTimeout(() => {
      mapRef.current?.resize();
    }, 240);
    return () => clearTimeout(timer);
  }, [sidebarOpen]);

  const chooseLanguage = (next: Language) => {
    setLanguage(next);
    window.localStorage.setItem('grid-language', next);
    document.documentElement.lang = next === 'zh' ? 'zh-CN' : next;
  };

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    setMapReady(false);
    setMapError(false);
    maplibregl.setWorkerUrl('/maplibre-gl-worker.mjs');
    const map = new maplibregl.Map({
      container: containerRef.current, center: [-8.05, 39.65], zoom: 6.25, minZoom: 5.6, maxZoom: 15,
      maxBounds: [[-11.7, 35.5], [-5.0, 43.2]], attributionControl: false,
      style: { version: 8, glyphs: 'https://fonts.openmaptiles.org/{fontstack}/{range}.pbf', sources: { osm: { type: 'raster', tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'], tileSize: 256, attribution: '© OpenStreetMap contributors', maxzoom: 19 } }, layers: [{ id: 'osm', type: 'raster', source: 'osm', paint: { 'raster-saturation': -0.72, 'raster-opacity': 0.72, 'raster-brightness-max': 0.94 } }] },
    });
    map.on('error', (event) => console.error('MapLibre error', event.error));
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'top-right');
    map.addControl(new maplibregl.ScaleControl({ maxWidth: 110, unit: 'metric' }), 'bottom-right');
    map.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-right');

    map.once('load', () => Promise.all([
      fetch(`${dataRoot}/lines.geojson`).then((r) => r.json() as Promise<GeoCollection>), fetch(`${dataRoot}/buses.geojson`).then((r) => r.json() as Promise<GeoCollection>),
      fetch(`${dataRoot}/transformers.geojson`).then((r) => r.json() as Promise<GeoCollection>), fetch(`${dataRoot}/boundaries.geojson`).then((r) => r.json() as Promise<GeoCollection>),
      fetch(`${dataRoot}/facilities.geojson`).then((r) => r.json() as Promise<GeoCollection>), fetch(`${dataRoot}/generators.geojson`).then((r) => r.json() as Promise<GeoCollection>),
      fetch(`${dataRoot}/substation_areas.geojson`).then((r) => r.json() as Promise<GeoCollection>), fetch(`${dataRoot}/power_equipment.geojson`).then((r) => r.json() as Promise<GeoCollection>),
      fetch(`${dataRoot}/line_supports.geojson`).then((r) => r.json() as Promise<GeoCollection>),
      fetch(`${dataRoot}/summary.json`).then((r) => r.json() as Promise<Summary>),
    ]).then(([lines, buses, transformers, boundaries, facilities, generators, substationAreas, powerEquipment, lineSupports, loadedSummary]) => {
      if (mapRef.current !== map) return;
      baseDataRef.current = { lines, buses, transformers, facilities, generators };
      dataRef.current = { lines, buses, transformers, facilities, generators }; setSummary(loadedSummary);
      map.addSource('grid-lines', { type: 'geojson', data: lines });
      map.addSource('grid-buses', { type: 'geojson', data: buses });
      map.addSource('grid-transformers', { type: 'geojson', data: transformers });
      map.addSource('grid-facilities', { type: 'geojson', data: facilities });
      map.addSource('grid-generators', { type: 'geojson', data: generators });
      map.addSource('osm-substation-areas', { type: 'geojson', data: substationAreas });
      map.addSource('osm-power-equipment', { type: 'geojson', data: powerEquipment });
      map.addSource('osm-line-supports', { type: 'geojson', data: lineSupports });
      map.addSource('grid-boundaries', { type: 'geojson', data: boundaries });
      map.addSource('risk-areas', { type: 'geojson', data: EMPTY_FEATURE_COLLECTION as never });
      map.addLayer({ id: 'substation-area-fill', type: 'fill', source: 'osm-substation-areas', minzoom: 8.5, paint: { 'fill-color': '#bb75bd', 'fill-opacity': ['interpolate', ['linear'], ['zoom'], 8.5, 0.12, 13, 0.32, 15, 0.4] } });
      map.addLayer({ id: 'substation-area-outline', type: 'line', source: 'osm-substation-areas', minzoom: 8.5, paint: { 'line-color': '#563b5d', 'line-width': ['interpolate', ['linear'], ['zoom'], 8.5, 0.7, 13, 2.1, 15, 2.6], 'line-opacity': 0.92 } });
      map.addLayer({ id: 'grid-lines-shadow', type: 'line', source: 'grid-lines', paint: { 'line-color': '#fff', 'line-width': ['interpolate', ['linear'], ['zoom'], 5, 1.5, 10, 4.5], 'line-opacity': 0.8 } });
      map.addLayer({ id: 'grid-lines', type: 'line', source: 'grid-lines', paint: { 'line-color': loadingExpression(), 'line-width': ['interpolate', ['linear'], ['zoom'], 5, 0.8, 8, 1.8, 12, 4], 'line-opacity': 0.9 } });
      map.addLayer({ id: 'risk-area-fill', type: 'fill', source: 'risk-areas', filter: ['==', ['get', 'id'], ''], paint: { 'fill-color': ['match', ['get', 'severity'], 'OVER_LIMIT', '#d95845', '#d59b2d'], 'fill-opacity': 0.18 } });
      map.addLayer({ id: 'risk-area-outline', type: 'line', source: 'risk-areas', filter: ['==', ['get', 'id'], ''], paint: { 'line-color': ['match', ['get', 'severity'], 'OVER_LIMIT', '#bd392f', '#a16207'], 'line-width': 2.5, 'line-dasharray': [2, 1.5], 'line-opacity': 0.95 } });
      map.addLayer({ id: 'grid-buses', type: 'circle', source: 'grid-buses', minzoom: 6.8, paint: { 'circle-radius': ['interpolate', ['linear'], ['zoom'], 7, 1.8, 11, 5], 'circle-color': busVoltageExpression(), 'circle-stroke-color': '#fff', 'circle-stroke-width': 0.8, 'circle-opacity': 0.88 } });
      map.addLayer({ id: 'grid-transformers', type: 'circle', source: 'grid-transformers', minzoom: 6.2, paint: { 'circle-radius': ['interpolate', ['linear'], ['zoom'], 6, 2.5, 11, 6.5], 'circle-color': '#102f45', 'circle-stroke-color': '#f7f3e9', 'circle-stroke-width': 1.3 } });
      map.addLayer({ id: 'grid-facilities', type: 'circle', source: 'grid-facilities', minzoom: 7.2, paint: { 'circle-radius': ['interpolate', ['linear'], ['zoom'], 7, 2.7, 12, 7], 'circle-color': ['match', ['get', 'facility_type'], 'switching_station', '#f2f0e9', '#102f45'], 'circle-stroke-color': '#102f45', 'circle-stroke-width': ['interpolate', ['linear'], ['zoom'], 7, 1, 12, 2] } });
      map.addLayer({ id: 'grid-generators', type: 'circle', source: 'grid-generators', minzoom: 7, paint: { 'circle-radius': ['step', ['coalesce', ['get', 'nameplate_mw'], 0], 3, 5, 4, 25, 5.5, 100, 7, 500, 9.5], 'circle-color': generationExpression(), 'circle-stroke-color': '#fff', 'circle-stroke-width': 1.1, 'circle-opacity': 0.92 } });
      map.addLayer({ id: 'osm-line-supports', type: 'symbol', source: 'osm-line-supports', minzoom: 12.2, layout: { 'text-field': ['match', ['get', 'power'], 'tower', '⊠', 'pole', '•', '·'], 'text-font': ['Noto Sans Regular'], 'text-size': ['interpolate', ['linear'], ['zoom'], 12.2, 8, 15, 13], 'text-allow-overlap': true }, paint: { 'text-color': '#111a20', 'text-halo-color': '#fff', 'text-halo-width': 0.7 } });
      map.addLayer({ id: 'osm-power-equipment', type: 'circle', source: 'osm-power-equipment', minzoom: 11.3, paint: { 'circle-radius': ['interpolate', ['linear'], ['zoom'], 11.3, 2.6, 15, 6.2], 'circle-color': '#fff', 'circle-stroke-color': '#29333a', 'circle-stroke-width': 1.4 } });
      map.addLayer({ id: 'osm-power-equipment-symbols', type: 'symbol', source: 'osm-power-equipment', minzoom: 12, layout: { 'text-field': ['match', ['get', 'power'], 'transformer', 'T', 'switch', 'S', 'converter', 'C', 'compensator', 'Q', 'portal', 'P', ''], 'text-font': ['Noto Sans Regular'], 'text-size': ['interpolate', ['linear'], ['zoom'], 12, 7, 15, 10], 'text-allow-overlap': true }, paint: { 'text-color': '#18252c' } });
      map.addLayer({ id: 'grid-boundaries', type: 'circle', source: 'grid-boundaries', paint: { 'circle-radius': 5, 'circle-color': '#fff', 'circle-opacity': 0.9, 'circle-stroke-color': '#d1495b', 'circle-stroke-width': 1.5, 'circle-stroke-opacity': 0.88 } });
      map.addLayer({ id: 'substation-area-labels', type: 'symbol', source: 'osm-substation-areas', minzoom: 10.5, filter: ['!=', ['coalesce', ['get', 'name'], ''], ''], layout: { 'text-field': ['get', 'name'], 'text-font': ['Noto Sans Regular'], 'text-size': 11, 'text-max-width': 16, 'text-allow-overlap': false }, paint: { 'text-color': '#3f2e43', 'text-halo-color': '#fff', 'text-halo-width': 1.5 } });
      map.addLayer({ id: 'facility-labels', type: 'symbol', source: 'grid-facilities', minzoom: 10, filter: ['all', ['!=', ['coalesce', ['get', 'name'], ''], ''], ['!=', ['get', 'source'], 'OpenStreetMap']], layout: { 'text-field': ['get', 'name'], 'text-font': ['Noto Sans Regular'], 'text-size': 11, 'text-offset': [0, 1.15], 'text-anchor': 'top', 'text-max-width': 14, 'text-allow-overlap': false }, paint: { 'text-color': '#142e3d', 'text-halo-color': '#fff', 'text-halo-width': 1.5 } });
      map.addLayer({ id: 'generator-labels', type: 'symbol', source: 'grid-generators', minzoom: 11, filter: ['!=', ['coalesce', ['get', 'name'], ''], ''], layout: { 'text-field': ['case', ['has', 'nameplate_mw'], ['concat', ['get', 'name'], ' · ', ['to-string', ['round', ['get', 'nameplate_mw']]], ' MW'], ['get', 'name']], 'text-font': ['Noto Sans Regular'], 'text-size': 10.5, 'text-offset': [0, 1.2], 'text-anchor': 'top', 'text-max-width': 16, 'text-allow-overlap': false }, paint: { 'text-color': '#24343c', 'text-halo-color': '#fff', 'text-halo-width': 1.5 } });
      ['grid-lines', 'grid-buses', 'grid-transformers', 'grid-facilities', 'grid-generators', 'grid-boundaries', 'substation-area-fill', 'osm-power-equipment', 'osm-line-supports'].forEach((layer) => {
        map.on('mouseenter', layer, () => { map.getCanvas().style.cursor = 'pointer'; });
        map.on('mouseleave', layer, () => { map.getCanvas().style.cursor = ''; });
        map.on('click', layer, (event) => { const hit = event.features?.[0]?.properties; if (hit) setSelected(hit as Record<string, unknown>); });
      });
      setMapReady(true);
    }).catch((error) => {
      if (mapRef.current !== map) return;
      console.error('Failed to load map data', error);
      setMapError(true);
    }));
    mapRef.current = map;
    return () => { map.remove(); mapRef.current = null; };
  }, [dataRoot, retry]);

  useEffect(() => {
    if (!mapReady || !baseDataRef.current || !mapRef.current?.getSource('grid-lines') || !selectedSnapshot) return;
    let cancelled = false;
    const controller = new AbortController();
    const snapshot = selectedSnapshot;
    setSnapshotLoading(true);
    setSnapshotError(false);
    setSelected(null);
    setSelectedRisk(null);
    const root = `${dataRoot}/snapshots/${snapshot.slug}/${activeVariant}`;
    Promise.all([
      fetch(`${root}/results.json`, { signal: controller.signal }).then((response) => {
        if (!response.ok) throw new Error(`Snapshot results request failed: ${response.status}`);
        return response.json() as Promise<SnapshotOverlay>;
      }),
      fetch(`${root}/summary.json`, { signal: controller.signal }).then((response) => {
        if (!response.ok) throw new Error(`Snapshot summary request failed: ${response.status}`);
        return response.json() as Promise<Summary>;
      }),
      snapshot.generator_geometry_url
        ? fetch(snapshot.generator_geometry_url, { signal: controller.signal }).then((response) => {
          if (!response.ok) throw new Error(`Snapshot asset geometry request failed: ${response.status}`);
          return response.json() as Promise<GeoCollection>;
        }) : Promise.resolve(baseDataRef.current.generators),
    ]).then(([payload, loadedSummary, generatorGeometry]) => {
      if (cancelled || !mapRef.current || !baseDataRef.current) return;
      const overlay = decodeOverlay(payload);
      validateResultIdentity(overlay, loadedSummary, snapshot.case_id, activeVariant);
      const base = baseDataRef.current;
      const lines = mergeOverlay(base.lines, overlay.lines);
      const buses = mergeOverlay(base.buses, overlay.buses);
      const transformers = mergeOverlay(base.transformers, overlay.transformers);
      const generators = mergeOverlay(generatorGeometry, overlay.generators);
      const boundaryIds = new Set(overlay.boundary_bus_ids);
      const boundaries: GeoCollection = {
        type: 'FeatureCollection',
        features: buses.features.filter((item) => boundaryIds.has(String(item.properties?.id))),
      };
      const riskAreas: GeoCollection = {
        type: 'FeatureCollection',
        features: (loadedSummary.risk_items ?? []).map((item) => ({
          type: 'Feature',
          geometry: { type: 'Polygon', coordinates: [circlePolygon(item.center, item.radius_km)] },
          properties: { id: item.id, severity: item.severity },
        })),
      };
      const map = mapRef.current;
      void (map.getSource('grid-lines') as maplibregl.GeoJSONSource).setData(lines as never);
      void (map.getSource('grid-buses') as maplibregl.GeoJSONSource).setData(buses as never);
      void (map.getSource('grid-transformers') as maplibregl.GeoJSONSource).setData(transformers as never);
      void (map.getSource('grid-generators') as maplibregl.GeoJSONSource).setData(generators as never);
      map.setFilter('grid-generators', ['!=', ['get', 'in_service'], false]);
      map.setFilter('generator-labels', ['all', ['!=', ['get', 'in_service'], false], ['!=', ['coalesce', ['get', 'name'], ''], '']]);
      void (map.getSource('grid-boundaries') as maplibregl.GeoJSONSource).setData(boundaries as never);
      void (map.getSource('risk-areas') as maplibregl.GeoJSONSource).setData(riskAreas as never);
      map.setFilter('risk-area-fill', ['==', ['get', 'id'], '']);
      map.setFilter('risk-area-outline', ['==', ['get', 'id'], '']);
      dataRef.current = { lines, buses, transformers, facilities: base.facilities, generators };
      setSummary(loadedSummary);
      setSnapshotLoading(false);
    }).catch((error) => {
      if (!cancelled) {
        console.error('Failed to load snapshot', error);
        setSnapshotError(true);
        setSnapshotLoading(false);
      }
    });
    return () => { cancelled = true; controller.abort(); };
  }, [mapReady, selectedSnapshot, retry, activeVariant, dataRoot]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map?.getLayer('grid-lines')) return;
    const filter: maplibregl.FilterSpecification = ['all', ['in', ['get', 'voltage_kv'], ['literal', [...enabledVoltages]]], ['!=', ['get', 'in_service'], false]];
    map.setFilter('grid-lines', filter); map.setFilter('grid-lines-shadow', filter); map.setFilter('grid-buses', filter);
    map.setPaintProperty('grid-lines', 'line-color', mode === 'loading' ? loadingExpression() : voltageExpression());
    map.setPaintProperty('grid-buses', 'circle-color', mode === 'voltage-result' ? busVoltageExpression() : voltageExpression());
    map.setLayoutProperty('grid-buses', 'visibility', showBuses ? 'visible' : 'none');
    map.setLayoutProperty('grid-transformers', 'visibility', showTransformers ? 'visible' : 'none');
    map.setLayoutProperty('grid-facilities', 'visibility', showFacilities ? 'visible' : 'none');
    map.setLayoutProperty('grid-generators', 'visibility', showGenerators ? 'visible' : 'none');
    map.setLayoutProperty('facility-labels', 'visibility', showFacilities && showLabels ? 'visible' : 'none');
    map.setLayoutProperty('generator-labels', 'visibility', showGenerators && showLabels ? 'visible' : 'none');
    ['substation-area-fill', 'substation-area-outline', 'osm-line-supports', 'osm-power-equipment', 'osm-power-equipment-symbols'].forEach((layer) => map.setLayoutProperty(layer, 'visibility', showDetailedInfrastructure ? 'visible' : 'none'));
    map.setLayoutProperty('substation-area-labels', 'visibility', showDetailedInfrastructure && showLabels ? 'visible' : 'none');
  }, [mode, enabledVoltages, showBuses, showTransformers, showFacilities, showGenerators, showLabels, showDetailedInfrastructure]);

  const legend = useMemo(() => mode === 'voltage'
    ? VOLTAGES.map((v) => [`${v} kV`, VOLTAGE_COLORS[v]])
    : mode === 'loading' ? [['0–35%', '#208f7b'], ['35–60%', '#71ae55'], ['60–80%', '#e6b84f'], ['80–100%', '#e67e38'], ['≥100%', '#c94152']]
      : [['≤0.94 p.u.', '#3859a8'], ['0.97', '#4ca6c8'], ['0.99', '#70b66b'], ['1.01', '#f2c14e'], ['≥1.04', '#d95d39']], [mode]);

  const runSearch = () => {
    if (!summary) return;
    const needle = query.trim().toLowerCase(); const data = dataRef.current; const map = mapRef.current;
    if (!needle || !data || !map) return;
    const hit = [...data.facilities.features, ...data.generators.features, ...data.buses.features, ...data.transformers.features, ...data.lines.features].find((item) => [item.properties?.id, item.properties?.name, item.properties?.operator, item.properties?.generation_source].some((value) => textValue(value).toLowerCase().includes(needle)));
    if (!hit) return;
    setSelected(hit.properties ?? {});
    if (hit.geometry.type === 'Point') map.flyTo({ center: hit.geometry.coordinates as [number, number], zoom: 11 });
    if (hit.geometry.type === 'LineString') { const bounds = new maplibregl.LngLatBounds(); (hit.geometry.coordinates as [number, number][]).forEach((point) => bounds.extend(point)); map.fitBounds(bounds, { padding: 110, maxZoom: 11 }); }
  };

  const focusRisk = (item: RiskItem) => {
    const next = selectedRisk === item.id ? null : item.id;
    setSelectedRisk(next);
    const map = mapRef.current;
    if (!map?.getLayer('risk-area-fill')) return;
    const filter: maplibregl.FilterSpecification = ['==', ['get', 'id'], next ?? ''];
    map.setFilter('risk-area-fill', filter);
    map.setFilter('risk-area-outline', filter);
    if (next) map.flyTo({ center: item.center, zoom: item.zoom, essential: true });
  };

  const changeSnapshot = (direction: -1 | 1) => {
    if (catalog) setSelectedCaseId((current) => stepSnapshot(catalog.snapshots, current, direction));
  };

  const snapshotKind = summary?.snapshot_kind === 'CUSTOM_INPUT' ? ({ en: 'Local input case', pt: 'Caso com entrada local', zh: '本地输入案例' }[language]) : summary?.snapshot_kind === 'SEASON_MATCHED_PROXY' ? copy.seasonalProxy : copy.synchronized;

  return <main className={`app-shell ${sidebarOpen ? '' : 'sidebar-collapsed'}`}>
    <header className="topbar">
      <button
        className="sidebar-toggle-btn"
        onClick={() => setSidebarOpen(!sidebarOpen)}
        title={sidebarOpen ? "收起左侧面板 / Collapse sidebar" : "展开左侧面板 / Expand sidebar"}
        aria-label="Toggle sidebar"
      >
        {sidebarOpen ? <PanelLeftClose size={18} /> : <PanelLeftOpen size={18} />}
      </button>
      <Link href="/project" className="brand-mark" aria-label="PT60 project and downloads" title="PT60 · 项目与下载"><UtilityPole size={20} /></Link>
      <div className={`snapshot-switcher ${snapshotLoading ? 'is-loading' : ''}`} aria-live="polite" aria-busy={snapshotLoading}>
        <button disabled={snapshotIndex <= 0} onClick={() => changeSnapshot(-1)} aria-label={copy.previousSnapshot} title={copy.previousSnapshot}><ChevronLeft size={18} /></button>
        <div className="snapshot-copy">
          <span>PT60 · {Math.max(0, snapshotIndex + 1)}/{groupSnapshots.length}</span>
          <h1>{timestampLabel(selectedSnapshot?.timestamp_utc)}</h1>
        </div>
        <button disabled={snapshotIndex < 0 || snapshotIndex >= groupSnapshots.length - 1} onClick={() => changeSnapshot(1)} aria-label={copy.nextSnapshot} title={copy.nextSnapshot}><ChevronRight size={18} /></button>
      </div>
      <nav className="language-switch" aria-label="Language">
        <button className={language === 'pt' ? 'active' : ''} onClick={() => chooseLanguage('pt')} aria-label="Português">PT</button>
        <button className={language === 'en' ? 'active' : ''} onClick={() => chooseLanguage('en')} aria-label="English">EN</button>
        <button className={language === 'zh' ? 'active' : ''} onClick={() => chooseLanguage('zh')} aria-label="中文">中文</button>
      </nav>
    </header>
    <aside className={`control-panel ${sidebarOpen ? '' : 'is-collapsed'} ${mobilePanel ? 'is-open' : ''}`}>
      <button className="mobile-handle" onClick={() => setMobilePanel(!mobilePanel)} aria-label={copy.controls}><span>{copy.controls}</span>{mobilePanel ? <ChevronDown size={18} /> : <ChevronUp size={18} />}</button>
      <div className="panel-scroll">
        <section className="snapshot-picker">
          <label>{timeCopy.group}<select value={selectedSnapshot?.group_id ?? ''} disabled={!catalog} onChange={(event) => {
            const first = catalog?.snapshots.find((row) => row.group_id === event.target.value);
            if (first) setSelectedCaseId(first.case_id);
          }}>{catalog?.groups.map((group) => <option key={group.id} value={group.id}>{group.label[language]}</option>)}</select></label>
          <div className="snapshot-picker-time">
            <label>{timeCopy.date}<select value={selectedDate} disabled={!selectedSnapshot} onChange={(event) => {
              const candidates = groupSnapshots.filter((row) => row.timestamp_utc.startsWith(event.target.value));
              const sameHour = candidates.find((row) => row.timestamp_utc.slice(11, 16) === selectedSnapshot?.timestamp_utc.slice(11, 16));
              if (candidates.length) setSelectedCaseId((sameHour ?? candidates[0]).case_id);
            }}>{dates.map((date) => <option key={date}>{date}</option>)}</select></label>
            <label>{timeCopy.hour}<select value={selectedCaseId} disabled={!selectedSnapshot} onChange={(event) => setSelectedCaseId(event.target.value)}>
              {groupSnapshots.filter((row) => row.timestamp_utc.startsWith(selectedDate)).map((row) => <option key={row.case_id} value={row.case_id}>{row.timestamp_utc.slice(11, 16)}</option>)}
            </select></label>
          </div>
        </section>
        <DataTools language={language} variant={activeVariant} variants={selectedSnapshot?.variants ?? []}
          onVariant={setVariant} root={`${dataRoot}/snapshots/${selectedSnapshot?.slug ?? ''}`}
          caseId={selectedCaseId} ready={!!summary} inputAvailable={selectedSnapshot?.input_available !== false} dataRoot={dataRoot} />
        <section className="search-box"><Search size={16} /><input aria-label={copy.search} value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && runSearch()} placeholder={copy.search} /></section>
        <section className="control-section"><div className="eyebrow"><Layers3 size={14} /> {copy.mapView}</div><div className="segmented"><button className={mode === 'voltage' ? 'active' : ''} onClick={() => setMode('voltage')}>{copy.topology}</button><button className={mode === 'loading' ? 'active' : ''} onClick={() => setMode('loading')}>{copy.lineLoading}</button><button className={mode === 'voltage-result' ? 'active' : ''} onClick={() => setMode('voltage-result')}>{copy.busVoltage}</button></div><p className="view-description">{VIEW_DESCRIPTIONS[language][mode]}</p></section>
        <section className="legend"><div className="eyebrow">{copy.legend}</div>{legend.map(([label, color], index) => <div key={label} className="legend-row"><span style={{ background: color }} /><div><b>{label}</b><small>{LEGEND_NOTES[language][mode][index]}</small></div></div>)}</section>
        <section className="control-section"><div className="eyebrow"><Zap size={14} /> {copy.voltageLayers}</div><div className="voltage-grid">{VOLTAGES.map((voltage) => <label key={voltage} className="voltage-toggle"><input type="checkbox" checked={enabledVoltages.has(voltage)} onChange={() => setEnabledVoltages((current) => { const next = new Set(current); if (next.has(voltage)) next.delete(voltage); else next.add(voltage); return next; })} /><span className="swatch" style={{ background: VOLTAGE_COLORS[voltage] }} />{voltage} kV</label>)}</div><label className="switch-row"><input type="checkbox" checked={showBuses} onChange={(e) => setShowBuses(e.target.checked)} /><span /> {copy.buses}</label><label className="switch-row"><input type="checkbox" checked={showTransformers} onChange={(e) => setShowTransformers(e.target.checked)} /><span /> {copy.transformers}</label><label className="switch-row"><input type="checkbox" checked={showFacilities} onChange={(e) => setShowFacilities(e.target.checked)} /><span /> {copy.facilities}</label><label className="switch-row"><input type="checkbox" checked={showGenerators} onChange={(e) => setShowGenerators(e.target.checked)} /><span /> {copy.generation}</label><label className="switch-row"><input type="checkbox" checked={showLabels} onChange={(e) => setShowLabels(e.target.checked)} /><span /> {copy.labels}</label></section>
        <section className="control-section"><div className="eyebrow"><Factory size={14} /> {copy.generationSource}</div><div className="generation-grid">{Object.entries(GENERATION_COLORS).filter(([key]) => key !== 'other').map(([label, color]) => <div key={label} className="generation-key"><span style={{ background: color }} />{ENERGY_LABELS[language][label]}</div>)}</div></section>
        <section className="control-section"><div className="eyebrow"><CircleGauge size={14} /> {copy.scenario}</div><div className={`snapshot-kind-tag ${summary?.snapshot_kind === 'SEASON_MATCHED_PROXY' ? 'is-proxy' : ''}`}>{snapshotKind}</div><div className="metric-grid"><div><strong>{timestampLabel(summary?.calibration_timestamp_utc)}</strong><span>{copy.calibratedAt}</span></div><div><strong>{summary ? `${fmt(summary.total_load_p_mw, 1)} MW` : '—'}</strong><span>{copy.load}</span></div><div><strong>{summary ? `${fmt(summary.total_generation_p_mw, 1)} MW` : '—'}</strong><span>{copy.generationPlan}</span></div><div><strong>{summary?.converged ? copy.solved : '—'}</strong><span>{copy.acpf}</span></div><div><strong>{summary ? `${fmt(summary.vm_pu_min, 3)}–${fmt(summary.vm_pu_max, 3)} p.u.` : '—'}</strong><span>{copy.voltageRange}</span></div><div><strong>{summary ? `${fmt(summary.line_loading_percent_max, 1)}%` : '—'}</strong><span>{copy.peakLine}</span></div><div><strong>{summary ? `${fmt(summary.trafo_loading_percent_max, 1)}%` : '—'}</strong><span>{copy.peakTrafo}</span></div><div><strong>{summary?.overloaded_line_rows ?? '—'}</strong><span>{copy.overloadedLines}</span></div><div><strong>{summary?.overloaded_transformer_rows ?? '—'}</strong><span>{copy.overloadedTransformers}</span></div></div></section>
        <div className="scope-note"><AlertTriangle size={15} /><p>{summary?.snapshot_kind === 'CUSTOM_INPUT' ? ({ en: 'AC power flow from local user inputs.', pt: 'Fluxo de potência CA com entradas locais.', zh: '使用本地输入求解的交流潮流案例。' }[language]) : summary?.snapshot_kind === 'SEASON_MATCHED_PROXY' ? copy.proxyScope : copy.scope}</p></div>
      </div>
    </aside>
    <div ref={containerRef} className={`map-canvas ${snapshotPending || snapshotError ? 'is-pending' : ''}`} aria-busy={snapshotPending} aria-label={copy.mapLabel} />
    {(catalogError || mapError || snapshotError || snapshotPending) && <output className="snapshot-feedback">
      <span>{catalogError ? timeCopy.catalogFailed : snapshotError || mapError ? timeCopy.failed : copy.loadingSnapshot}</span>
      {(catalogError || mapError || snapshotError) && <button onClick={() => { setCatalogError(false); setMapError(false); setSnapshotError(false); setRetry((value) => value + 1); }}>{timeCopy.retry}</button>}
    </output>}
    <div className="map-stats"><span><b>{summary ? fmt(summary.lines, 0) : '—'}</b> {copy.linesStat}</span><span><b>{summary ? fmt(summary.facilities, 0) : '—'}</b> {copy.facilitiesStat}</span><span><b>{summary ? fmt(summary.generation_assets, 0) : '—'}</b> {copy.generationStat}</span><span><b>{summary ? fmt(summary.transformers, 0) : '—'}</b> {copy.transformersStat}</span></div>
    {(summary?.risk_items?.length ?? 0) > 0 && <aside className={`risk-panel ${riskPanelOpen ? 'is-open' : 'is-closed'}`}>
      <button className="risk-panel-heading" onClick={() => setRiskPanelOpen(!riskPanelOpen)} aria-label={riskPanelOpen ? copy.closeRisk : copy.openRisk}><span><AlertTriangle size={15} />{copy.riskTitle}</span>{riskPanelOpen ? <ChevronUp size={17} /> : <ChevronDown size={17} />}</button>
      {riskPanelOpen && <div className="risk-content">
        <p className="risk-instruction">{copy.riskSubtitle}</p>
        <div className="risk-list">{summary?.risk_items?.map((item, index) => {
          const kind = item.kind === 'LINE' ? copy.lineRisk : copy.transformerRisk;
          const status = item.severity === 'OVER_LIMIT' ? timeCopy.modelScreen : copy.watchHotspot;
          return <button key={item.id} className={`risk-item ${item.severity === 'OVER_LIMIT' ? 'over-limit' : ''} ${selectedRisk === item.id ? 'active' : ''}`} onClick={() => focusRisk(item)}><span className="risk-index">{index + 1}</span><span><strong>{item.title}</strong><b>{kind} · {fmt(item.loading_percent, 2)}%</b><small>{status} · {item.object_id} · {fmt(item.p_mw, 1)} MW · {item.voltage_label}</small></span></button>;
        })}</div>
      </div>}
    </aside>}
    {selected && summary && <aside className="detail-card"><button className="close-button" onClick={() => setSelected(null)} aria-label="Close details"><X size={17} /></button><div className="detail-icon"><MapPin size={17} /></div><p className="detail-type">{selected.object_type === 'generation_asset' ? copy.publicGeneration : selected.object_type === 'facility' ? copy.networkFacility : copy.networkObject}</p><h2>{typeof (selected.name || selected.id) === 'string' ? String(selected.name || selected.id) : copy.fallbackObject}</h2><dl>{propertyRows(selected, language).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl></aside>}
  </main>;
}
