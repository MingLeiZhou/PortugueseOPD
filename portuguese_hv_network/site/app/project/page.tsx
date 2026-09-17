import Link from 'next/link';
import release from '../release-info.json';

const panel = { padding: '24px', border: '1px solid #cddbd7', borderRadius: '16px', background: '#fff' };
export default function Project() {
  return <main style={{ maxWidth: 1060, margin: '0 auto', padding: '48px 24px 80px', color: '#18352e', lineHeight: 1.8 }}>
    <nav style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}><b>SimPT60</b><Link href="/">探索电网地图 ↗</Link><Link href="/results">研究结果</Link><a href="#dataset">数据包</a><a href="#python">Python</a><a href="#paper">论文</a></nav>
    <header style={{ padding: '56px 0 32px' }}>
      <p style={{ letterSpacing: '0.12em', fontSize: 13 }}>PORTUGUESE PUBLIC-DATA POWER-FLOW CASES</p>
      <h1 style={{ fontSize: 'clamp(28px, 4vw, 46px)', lineHeight: 1.35, fontWeight: 650, margin: '16px 0' }}>从公共观测，<br />到可复用的电网案例。</h1>
      <p style={{ maxWidth: 780 }}>PT60 将葡萄牙公开的网络、设备与运行记录连接起来，形成可追溯、可复算的交流潮流案例。数据包保存输入与证据，Python 工具完成读取和复算，网页展示网络与结果，论文解释方法与验证。</p>
      <p><strong>公共观测 → 网络与时间对齐 → 节点注入与交流潮流 → 数据包与验证</strong></p>
    </header>
    <div style={{ display: 'flex', gap: 36, flexWrap: 'wrap', paddingBottom: 32 }}>
      {['60–400 kV 网络', '3,783 个母线', '4,943 条线路', '228 台变压器', '31,492 个 15 分钟案例'].map(t=><span key={t}>{t}</span>)}
    </div>
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 360px), 1fr))', gap: 20 }}>
      <section id="dataset" style={panel}><h2 style={{ fontSize: 24 }}>01 · 一个数据包</h2>
        <p>{release.dataset.version} · {(release.dataset.bytes/1024/1024).toFixed(1)} MiB</p>
        <p>网络模型、资产与来源记录、31,492 个连续 15 分钟案例、潮流结果、验证实验和复算代码。</p>
        <p><a href="/download" download>下载完整数据包 ↓</a> · <a download href="/downloads/ATTRIBUTION.md">来源与许可</a> · <a download href="/downloads/release.json">版本清单</a></p>
        <p style={{ fontSize: 13 }}>候选数据版本；永久存储 DOI 待登记。SHA-256：</p><code style={{ fontSize: 11, overflowWrap: 'anywhere' }}>{release.dataset.sha256}</code>
      </section>
      <section id="python" style={panel}><h2 style={{ fontSize: 24 }}>02 · 一个 Python 包</h2>
        <p>pt60-tools {release.python.version} · Python 3.13</p>
        <p>下载 → 校验 → 读取 → 构建输入 → 求解 → 复算。基础安装仅依赖 NumPy，计算时按需安装求解依赖。</p>
        <p>已发布到 <a href={`https://pypi.org/project/pt60-tools/${release.python.version}/`}>PyPI ↗</a>，可直接使用 pip 安装。</p>
        <p><a href={release.python.wheel}>下载 wheel ↓</a> · <a download href={`/downloads/pt60_tools-${release.python.version}.tar.gz`}>软件源码 ↓</a> · <a download href="/downloads/usage-cn.md">使用说明</a></p>
      </section>
      <section style={panel}><h2 style={{ fontSize: 24 }}>03 · 一个网页</h2>
        <p>按时点和空间分配方法浏览母线电压与线路负载率，查看设备信息，下载所选案例输入和结果。</p>
        <p>地图与 Python 读取同一版本的数据及设备标识。时间统一为 UTC 区间起点，并保留公开记录与求解结果之间的追踪关系。</p>
        <p><Link href="/">打开交互地图 →</Link> · <Link href="/results">查看研究结果 →</Link></p>
      </section>
      <section id="paper" style={panel}><h2 style={{ fontSize: 24 }}>04 · 一篇论文</h2>
        <p>基于葡萄牙公共电网记录构建的高压拓扑与交流潮流基准数据集</p>
        <p>围绕输入、处理方法和输出展开，说明网络重建、观测映射、案例构建与技术验证。配套包包含当前中文正文、六张主图及正文链接的补充说明。</p>
        <p><a href={release.paper.url}>下载中文稿与配套材料 ↓</a></p><p style={{ fontSize: 13 }}>当前为论文稿件，尚未作为已发表文章发布。</p>
      </section>
    </div>
    <section style={{ marginTop: 40 }}><h2 style={{ fontSize: 26 }}>从一个案例开始</h2><p>在 Python 3.13 虚拟环境运行：</p>
      <pre style={{ background: '#102f29', color: '#e7f6ee', padding: 24, borderRadius: 14, overflowX: 'auto', fontSize: 13 }}>{`python -m pip install "pt60-tools[solve]==${release.python.version}"
pt60 fetch https://grid.jczw.xyz/download --output work/PT60-v2.1.0-rc2 --sha256 ${release.dataset.sha256}
export PT60_DATA="$PWD/work/PT60-v2.1.0-rc2"
pt60 verify
pt60 replay --case PT60_2025_SUMMER_WEEK_JUL07_13_H000 --output work/replay
pt60 view --result-dir work/replay`}</pre>
      <p>数据校验核对文件完整性；案例验证检查守恒、残差和收敛。公共记录缺失的参数和控制设置保留证据标签，结果不等同于运营商状态估计。</p>
    </section>
  </main>;
}
