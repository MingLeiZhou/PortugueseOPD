import Link from 'next/link';

const annualRoles = [
  { label: '最大净出口', strict: 1527, incremental: 1527, islands: 114 },
  { label: '最大净进口', strict: 1455, incremental: 1455, islands: 102 },
  { label: '最大光伏', strict: 1526, incremental: 1526, islands: 109 },
  { label: '最大风电', strict: 0, incremental: 1502, islands: 115 },
  { label: '最小负荷', strict: 1538, incremental: 1538, islands: 110 },
  { label: '最大负荷', strict: 2, incremental: 1516, islands: 110 },
];

const northernCases = [
  { outage: 'Deocriste–Lanheses', limit: '686 A', q: '4.0×', loading: '175.4%' },
  { outage: 'Lanheses–Feitosa', limit: '686 A', q: '3.0×', loading: '154.9%' },
  { outage: 'Cerveira–Valença', limit: '544 A', q: '1.5×', loading: '128.3%' },
];

function Metric({ value, label, note }: { value: string; label: string; note?: string }) {
  return <div className="results-metric"><strong>{value}</strong><span>{label}</span>{note && <small>{note}</small>}</div>;
}

export default function Results() {
  return <main className="results-page">
    <nav className="results-nav">
      <Link href="/" className="results-logo">SimPT60</Link>
      <div><a href="#validation">验证</a><a href="#nminus1">N−1</a><a href="#limits">边界</a><Link href="/project">数据与论文</Link><Link className="results-nav-primary" href="/">打开交互地图 ↗</Link></div>
    </nav>

    <header className="results-hero">
      <div className="results-hero-copy">
        <p className="results-kicker">PORTUGAL · 60–400 kV · PUBLIC-DATA RECONSTRUCTION</p>
        <h1>把公开记录变成<br /><em>可计算的电网状态。</em></h1>
        <p className="results-lead">SimPT60 将葡萄牙大陆的公开电网、设备与运行记录组织为静态网络和连续 15 分钟交流潮流案例，用于时序分析、风险筛查与可复现实验。</p>
        <div className="results-hero-actions"><Link href="/">探索电网地图</Link><a href="#nminus1">查看 N−1 结果 ↓</a></div>
      </div>
      <div className="results-hero-visual" aria-label="SimPT60 data flow">
        <div className="results-orbit results-orbit-one" /><div className="results-orbit results-orbit-two" />
        <div className="results-core"><span>SimPT60</span><b>AC</b><small>可追溯案例</small></div>
        <span className="results-node node-a">公开拓扑</span><span className="results-node node-b">15 min 状态</span><span className="results-node node-c">交流潮流</span><span className="results-node node-d">N−1 筛查</span>
      </div>
      <div className="results-hero-metrics">
        <Metric value="31,492" label="15 分钟案例" note="2025-05-01—2026-03-24" />
        <Metric value="3,783" label="母线" />
        <Metric value="4,943" label="线路" />
        <Metric value="228" label="变压器" />
      </div>
    </header>

    <section className="results-section results-evidence" id="validation">
      <div className="results-section-heading"><p>01 · EXTERNAL VALIDATION</p><h2>先回答：它是否接近现实？</h2><span>未参与模型构建的公开观测用于交叉验证；相关性衡量变化趋势，而不是宣称模型等同于运营商状态估计。</span></div>
      <div className="evidence-grid">
        <article className="correlation-card">
          <div><span>全国负荷趋势</span><strong>0.997</strong><small>Pearson r · 31,388 对时点</small></div>
          <svg viewBox="0 0 420 130" aria-label="PT60 and public load trend comparison">
            <title>PT60 and public load trend comparison</title>
            <path className="chart-grid" d="M0 25H420M0 65H420M0 105H420" />
            <path className="chart-public" d="M0 92 C28 88 35 43 62 48 S98 98 127 87 S167 31 198 45 S238 92 267 78 S302 30 334 41 S378 95 420 60" />
            <path className="chart-model" d="M0 96 C28 91 38 47 63 51 S99 94 128 84 S168 35 199 48 S237 88 268 75 S303 34 335 44 S380 91 420 63" />
          </svg>
          <div className="chart-legend"><span><i className="public" />独立公开观测</span><span><i className="model" />SimPT60</span></div>
        </article>
        <article className="correlation-card">
          <div><span>风电变化趋势</span><strong>0.9998</strong><small>Pearson r · 31,484 对时点</small></div>
          <svg viewBox="0 0 420 130" aria-label="PT60 and public wind trend comparison">
            <title>PT60 and public wind trend comparison</title>
            <path className="chart-grid" d="M0 25H420M0 65H420M0 105H420" />
            <path className="chart-public" d="M0 106 C30 105 34 85 60 86 S95 36 125 43 S156 96 190 83 S222 21 254 34 S288 91 320 72 S358 18 420 38" />
            <path className="chart-model" d="M0 108 C30 106 34 88 60 88 S96 38 126 45 S157 94 190 81 S223 24 255 37 S288 88 321 70 S359 21 420 41" />
          </svg>
          <div className="chart-legend"><span><i className="public" />独立公开观测</span><span><i className="model" />SimPT60</span></div>
        </article>
      </div>
      <div className="validation-strip"><span>时间变化</span><b>✓</b><span>空间排序</span><b>✓</b><span>发电平衡</span><b>✓</b><span>交流功率闭合</span><b>✓</b></div>
    </section>

    <section className="results-section results-n1" id="nminus1">
      <div className="results-section-heading"><p>02 · ANNUAL N−1 PANEL</p><h2>六个年度代表时点，9,894 个事故案例</h2><span>每个时点筛查 1,649 个可恢复物理元件。图中同时保留严格绝对判据与扣除 N−0 已有问题后的新增风险判据。</span></div>
      <div className="n1-summary">
        <div className="convergence-ring"><div><strong>99.94%</strong><span>主潮流收敛</span><small>9,888 / 9,894</small></div></div>
        <div className="n1-kpis"><Metric value="9,064" label="无新增违例且无孤岛" /><Metric value="131" label="引起新增热违例的元件" /><Metric value="6" label="引起新增电压违例的元件" /><Metric value="116" label="引起实质孤岛的元件" /></div>
      </div>
      <div className="role-chart">
        <div className="role-chart-head"><span>代表运行状态</span><span>通过案例 / 1,649</span></div>
        {annualRoles.map((role) => <div className="role-row" key={role.label}>
          <span>{role.label}</span>
          <div className="role-bars">
            <div className="role-track"><i className="incremental" style={{ width: `${role.incremental / 16.49}%` }} /><b>{role.incremental}</b></div>
            <div className="role-track strict-track"><i className="strict" style={{ width: `${Math.max(role.strict / 16.49, role.strict ? 1.5 : 0)}%` }} /><b>{role.strict}</b></div>
          </div>
          <small>{role.islands} 孤岛</small>
        </div>)}
        <div className="role-legend"><span><i className="incremental" />相对 N−0 无新增问题</span><span><i className="strict" />严格绝对通过</span></div>
      </div>
      <p className="results-callout"><b>为什么两种判据必须一起展示？</b> 最大风电和最大负荷的基础状态已经含有代理额定值违例；若只用绝对判据，会把同一个基础模型限制重复计算为数千次事故失败。新增风险判据用于研究诊断，不替代运营商合规判定。</p>
    </section>

    <section className="results-section results-north">
      <div className="results-section-heading"><p>03 · TARGETED DIAGNOSIS</p><h2>北部 60 kV：控制能恢复电压，但不能消除热过载</h2><span>公开长度和冬季电流限值已经复核。无功能力倍数仅用于敏感性分析，不被解释为真实机组能力。</span></div>
      <div className="north-table">
        <div className="north-row north-head"><span>退出元件</span><span>公开限值</span><span>最低 Q 倍数</span><span>恢复电压后最高负载率</span></div>
        {northernCases.map((item) => <div className="north-row" key={item.outage}><strong>{item.outage}</strong><span>{item.limit}</span><span>{item.q}</span><b>{item.loading}</b></div>)}
      </div>
      <div className="diagnosis-flow"><span>公开清册复核</span><i>→</i><span>拓扑与回路检查</span><i>→</i><span>有功/无功控制搜索</span><i>→</i><strong>仍需解释的模型不确定性</strong></div>
    </section>

    <section className="results-section results-boundary" id="limits">
      <div><p>04 · INTERPRETATION</p><h2>这是研究筛查数据，<br />不是运营商安全证书。</h2></div>
      <div className="boundary-grid">
        <article><b>可以支持</b><p>时序潮流、风险排序、情景分析、机器学习与可复现实验。</p></article>
        <article><b>仍需谨慎</b><p>工程代理额定值、缺失并联路径、配电网等值及逐台机组 P–Q 能力。</p></article>
        <article><b>完整追踪</b><p>原始来源、标准化表、模型修正、案例输入和计算结果分层保存。</p></article>
      </div>
    </section>

    <footer className="results-footer"><div><strong>SimPT60</strong><span>Public-data-informed Portuguese high-voltage grid dataset</span></div><div><Link href="/">交互地图</Link><Link href="/project">数据包与论文</Link><a href="#validation">返回顶部 ↑</a></div></footer>
  </main>;
}
