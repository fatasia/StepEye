/** 轻量 SVG / HTML 图表：只用令牌色，数字 tabular，单位显式。 */
import { esc } from "./util.js";

const NS = (inner, w, h, label) =>
  `<svg class="chart" viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(label)}" preserveAspectRatio="xMidYMid meet">${inner}</svg>`;

export function sparkline(values, { w = 120, h = 32 } = {}) {
  const v = values.filter((x) => x != null);
  if (v.length < 2) return ""; // 数据点不足时不画，避免一条无意义的虚线
  const lo = Math.min(...v), hi = Math.max(...v), span = hi - lo || 1;
  const pts = v.map((x, i) => [(i / (v.length - 1)) * w, h - 3 - ((x - lo) / span) * (h - 8)]);
  const line = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join("");
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true"><path d="${line}L${w} ${h}L0 ${h}Z" class="spark-area"/><path d="${line}" class="spark-line"/></svg>`;
}

/** 向上取整到 1/2/4/6/8/10 × 10^n，使中间刻度也是整数（如 0/30/60 而不是 0/27/53）。 */
function niceCeil(v) {
  const base = 10 ** Math.floor(Math.log10(Math.max(v, 1)));
  return [1, 2, 4, 6, 8, 10].map((m) => m * base).find((x) => x >= v) ?? 10 * base;
}

/** 堆叠柱：每个小时一根，合格 / 待复核 / 不合格。 */
export function stackedBars(buckets, { w = 360, h = 168 } = {}) {
  if (!buckets.length) return "";
  const m = { l: 30, r: 8, t: 10, b: 22 };
  const iw = w - m.l - m.r, ih = h - m.t - m.b;
  const max = Math.max(4, niceCeil(Math.max(...buckets.map((b) => b.total))));
  const ticks = [0, max / 2, max];
  const bw = Math.min(22, (iw / buckets.length) * 0.62);
  const step = iw / buckets.length;
  let s = ticks.map((t) => {
    const y = m.t + ih - (t / max) * ih;
    return `<line x1="${m.l}" x2="${w - m.r}" y1="${y}" y2="${y}" class="grid"/><text x="${m.l - 6}" y="${y + 4}" class="axis" text-anchor="end">${t}</text>`;
  }).join("");
  buckets.forEach((b, i) => {
    const x = m.l + i * step + (step - bw) / 2;
    let y = m.t + ih;
    for (const [key, cls] of [["pass", "bar-success"], ["uncertain", "bar-warning"], ["fail", "bar-danger"]]) {
      const hh = (b[key] / max) * ih;
      if (hh <= 0) continue;
      y -= hh;
      s += `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(1, hh - 1).toFixed(1)}" rx="1.5" class="${cls}"><title>${b.hour.slice(11)}:00  ${key === "pass" ? "合格" : key === "fail" ? "不合格" : "待复核"} ${b[key]}</title></rect>`;
    }
    if (buckets.length <= 12 || i % Math.ceil(buckets.length / 8) === 0) {
      s += `<text x="${(x + bw / 2).toFixed(1)}" y="${h - 6}" class="axis" text-anchor="middle">${b.hour.slice(11)}:00</text>`;
    }
  });
  return NS(s, w, h, "近 24 小时周期结果分布");
}

/** 步骤甘特：横轴为周期内时间（s），竖线为节拍目标。 */
export function gantt(steps, durationS, taktS) {
  const total = Math.max(durationS, taktS || 0, 1) * 1.04;
  const pctOf = (v) => `${((v / total) * 100).toFixed(2)}%`;
  const line = taktS ? `<i class="g-takt" style="left:${pctOf(taktS)}"></i>` : "";
  const rows = steps.map((s) => {
    const done = s.done_s != null;
    const start = s.armed_s ?? 0;
    const end = done ? s.done_s : (s.armed_s != null ? durationS : 0);
    const tone = !done ? "missing" : s.early ? "early" : "ok";
    const bar = s.armed_s == null && !done ? "" :
      `<i class="g-bar g-${tone}" style="left:${pctOf(start)};width:${pctOf(Math.max(end - start, total * 0.006))}"></i>`;
    const label = done ? `${(s.duration_s ?? 0).toFixed(1)} s` : "未完成";
    return `<div class="g-row"><span class="g-name ellipsis" title="${esc(s.name)}">${esc(s.name)}</span><span class="g-track">${line}${bar}</span><span class="g-val num ${done ? "" : "tone-danger-text"}">${label}</span></div>`;
  }).join("");
  const head = taktS
    ? `<div class="g-row g-head"><span></span><span class="g-track">${line}<b class="g-takt-label" style="left:${pctOf(taktS)}">节拍 ${taktS} s</b></span><span></span></div>`
    : "";
  return `<div class="gantt">${head}${rows}</div>`;
}
