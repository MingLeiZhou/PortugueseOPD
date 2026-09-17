'use client';
import Link from 'next/link';

type Language = 'en' | 'pt' | 'zh';
const COPY = {
  zh: { title: '数据与复现', model: '空间分配方法', primary: 'PDIRT 负荷分配（主方法）', uniform: 'PDE 均匀分配', capacity: 'PDE 容量加权', results: '下载当前设备结果', input: '下载归档回放输入', table: '下载主方法实验表', alternatives: '下载对照实验表', provenance: '数据版本与校验', how: '如何使用', note: '数据库包含 31,492 个连续 15 分钟案例；地图提供紧凑的代表性时点，时间为 UTC 区间起点。灰色为缺失值，零负载也可能来自停运线路。', steps: '先在项目页下载并解压数据包，设置 PT60_DATA 为解压目录，再运行：', help: 'init → 修改输入 → solve → 查看结果。replay 用于复现所选归档案例。求解在本机 Python 环境执行。' },
  en: { title: 'Data & reproduction', model: 'Spatial allocation', primary: 'PDIRT allocation (primary)', uniform: 'Uniform PDE', capacity: 'Capacity-weighted PDE', results: 'Download device results', input: 'Download archived replay input', table: 'Download primary experiment table', alternatives: 'Download comparison table', provenance: 'Version & verification', how: 'How to use', note: 'The database contains 31,492 continuous 15-minute cases. This map exposes compact representative snapshots, labelled by UTC interval start. Grey denotes missing values and zero loading may include inactive lines.', steps: 'Download and extract the dataset from the project page; set PT60_DATA to its directory, then run:', help: 'init → edit inputs → solve → inspect results. replay reproduces the selected archived case. Solving runs in your local Python environment.' },
  pt: { title: 'Dados e reprodução', model: 'Distribuição espacial', primary: 'Distribuição PDIRT (principal)', uniform: 'PDE uniforme', capacity: 'PDE ponderado por capacidade', results: 'Descarregar resultados por equipamento', input: 'Descarregar entrada do caso', table: 'Descarregar tabela principal', alternatives: 'Descarregar tabela de comparação', provenance: 'Versão e verificação', how: 'Como utilizar', note: 'A base de dados contém 31 492 casos contínuos de 15 minutos. O mapa disponibiliza instantes representativos compactos, identificados pelo início do intervalo em UTC.', steps: 'Descarregar e extrair os dados; definir PT60_DATA para a pasta, e executar:', help: 'init → editar entradas → solve → consultar resultados. replay reproduz o caso arquivado selecionado. O cálculo é executado no ambiente Python local.' },
};

export default function DataTools({ language, variant, variants, onVariant, root, caseId, ready, inputAvailable, dataRoot }: {
  language: Language; variant: string; variants: string[]; onVariant: (value: string) => void;
  root: string; caseId: string; ready: boolean; inputAvailable: boolean; dataRoot: string;
}) {
  const copy = COPY[language];
  const labels: Record<string, string> = { AC_REVISED: copy.primary, UNIFORM_PDE: copy.uniform, CAPACITY_PDE: copy.capacity };
  return <section className="control-section data-tools">
    <div className="eyebrow">{copy.title} · rc2</div>
    <label>{copy.model}<select value={variant} disabled={!variants.length} onChange={(e) => onVariant(e.target.value)}>
      {variants.map((item) => <option key={item} value={item}>{labels[item] ?? item}</option>)}
    </select></label>
    <p>{copy.note}</p>
    <div className="data-downloads">
      <Link href="/project">{({ zh: "项目、数据包与论文", en: "Project, dataset & paper", pt: "Projeto, dados e artigo" })[language]} →</Link>
      <Link href="/results">{({ zh: "研究结果与验证", en: "Research results & validation", pt: "Resultados e validação" })[language]} →</Link>
      {ready && <><a href={`${root}/${variant}/results.json`} download={`${caseId}_${variant}.json`}>{copy.results} ↗</a>
      {inputAvailable && <a href={`${root}/input.json`} download={`${caseId}_input.json`}>{copy.input} ↗</a>}</>}
      <a href={`${dataRoot}/downloads/seasonal_week_validation.csv`} download>{copy.table} ↗</a>
      <a href={`${dataRoot}/downloads/spatial_allocation_results.csv`} download>{copy.alternatives} ↗</a>
      <a href={`${dataRoot}/metadata.json`} download>{copy.provenance} ↗</a>
    </div>
    <details><summary>{copy.how}</summary><p>{copy.steps}</p><pre>{`python -m pip install "pt60-tools[solve]==0.4.3"
pt60 verify
pt60 init --output work/my-case
pt60 solve --input work/my-case --output work/my-result
pt60 view --result-dir work/my-result

${inputAvailable ? `pt60 replay --case ${caseId || 'CASE_ID'} --output work/replay` : ''}`}</pre><p>{copy.help}</p></details>
  </section>;
}
