import { readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { renderMermaidSVG, THEMES } from "beautiful-mermaid";

const here = dirname(fileURLToPath(import.meta.url));
const sourcePath = process.argv[2]
  ? resolve(process.cwd(), process.argv[2])
  : resolve(here, "../../../docs/figures/pt60-database-architecture.mmd");
const outputPath = process.argv[3]
  ? resolve(process.cwd(), process.argv[3])
  : resolve(here, "../../../docs/figures/pt60-database-architecture.svg");
const source = await readFile(sourcePath, "utf8");
const compact = sourcePath.includes("static-grid-schema");

const svg = renderMermaidSVG(source, {
  ...THEMES["github-light"],
  font: "Noto Sans SC",
  padding: compact ? 20 : 36,
  nodeSpacing: compact ? 16 : 32,
  layerSpacing: compact ? 28 : 58,
  componentSpacing: compact ? 16 : 28,
  thoroughness: 7,
});

await writeFile(outputPath, svg, "utf8");
console.log(`Rendered ${outputPath}`);
