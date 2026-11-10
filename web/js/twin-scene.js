/** 厂区空间示意：投影上传底图和真实点位，不推测建筑、尺寸或摄像头朝向。 */
import { esc } from "./util.js";

export function twinScene({ points, image = "", ratio = .5, selected = "", flat = false }) {
  const h = 1000 * Math.max(.2, Math.min(2, ratio));
  const scale = Math.min(1, 490 / (160 + .6 * h), 1040 / (760 + .55 * h));
  const a = flat ? .9 : .76 * scale, b = flat ? 0 : .16 * scale;
  const c = flat ? 0 : -.55 * scale, d = flat ? Math.min(.8, 490 / h) : .6 * scale;
  const ox = 600 - (a * 1000 + c * h) / 2, oy = 354 - (b * 1000 + d * h) / 2;
  const p = (x, y) => [ox + a * x * 1000 + c * y * h, oy + b * x * 1000 + d * y * h];
  const corners = [p(0,0),p(1,0),p(1,1),p(0,1)];
  const floor = corners.map((v) => v.join(",")).join(" ");
  const lines = Array.from({length:11}, (_,i) => i / 10).map((v) => {
    const l = [p(v,0),p(v,1)], r = [p(0,v),p(1,v)];
    return `<path d="M${l[0]} L${l[1]} M${r[0]} L${r[1]}"/>`;
  }).join("");
  const devices = [...points].sort((x,y) => p(x.x,x.y)[1] - p(y.x,y.y)[1]).map((node) => {
    const [x,y] = p(node.x,node.y), active = selected === node.id;
    const tone = node.risk ? "risk" : node.online ? "online" : "unknown";
    const height = flat ? 17 : node.kind === "camera" ? 58 : 42;
    return `<g class="twin-device ${tone} ${active ? "selected" : ""}" data-pt="${esc(node.id)}" tabindex="0" role="button" aria-label="${esc(node.name)}，${esc(node.status)}">
      <title>${esc(node.name)} · ${esc(node.status)}${node.risk ? ` · ${node.risk} 条待处理告警` : ""}</title>
      <ellipse class="twin-device-shadow" cx="${x + 11}" cy="${y + 3}" rx="25" ry="10"/>
      ${node.risk ? `<ellipse class="twin-risk-wave" cx="${x}" cy="${y}" rx="35" ry="16"/>` : ""}
      ${active ? `<ellipse class="twin-focus-ring" cx="${x}" cy="${y}" rx="45" ry="21"/><path class="twin-focus-cross" d="M${x-55},${y}h20 M${x+35},${y}h20 M${x},${y-29}v12 M${x},${y+17}v12"/>` : ""}
      <rect class="twin-beam" x="${x-13}" y="${y-140}" width="26" height="140" fill="url(#twin-beam-${tone})"/>
      <path class="twin-device-stem" d="M${x},${y}V${y-height}"/>
      <path class="twin-device-side" d="M${x-11},${y-height}l11,6v21l-11,-6Z"/>
      <path class="twin-device-front" d="M${x},${y-height+6}l13,-6v21l-13,6Z"/>
      <path class="twin-device-top" d="M${x-11},${y-height}l13,-6l11,6l-13,6Z"/>
      ${node.kind === "camera" ? `<path class="twin-camera-lens" d="M${x+4},${y-height+13}l6,-3v7l-6,3Z"/>` : ""}
      <circle class="twin-node-light" cx="${x}" cy="${y}" r="4"/>
      <g class="twin-billboard" transform="translate(${x-75},${y-height-55})"><rect width="150" height="40" rx="4"/>
        <text class="twin-point-name" x="11" y="17">${esc(node.name.length > 16 ? node.name.slice(0,15) + "…" : node.name)}</text>
        <circle class="twin-label-dot" cx="14" cy="29" r="2.5"/><text class="twin-point-status" x="22" y="32">${esc(node.status)}</text></g>
    </g>`;
  }).join("");
  return `<svg viewBox="0 0 1200 680" class="twin-scene-svg" aria-label="厂区设备空间示意，不是 BIM 模型">
    <defs>${["risk","online","unknown"].map((tone) => `<linearGradient id="twin-beam-${tone}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" class="beam-${tone}" stop-opacity="0"/><stop offset="1" class="beam-${tone}" stop-opacity=".3"/></linearGradient>`).join("")}
      <radialGradient id="twin-floor-glow"><stop class="twin-floor-glow" stop-opacity=".3"/><stop offset="1" class="twin-floor-glow" stop-opacity="0"/></radialGradient></defs>
    <ellipse cx="600" cy="390" rx="540" ry="260" fill="url(#twin-floor-glow)"/>
    <polygon class="twin-slab-shadow" points="${corners.map(([x,y]) => `${x},${y+32}`).join(" ")}"/>
    <polygon class="twin-slab-side" points="${[corners[1],corners[2],corners[3],...corners.slice(1).reverse().map(([x,y]) => [x,y+16])].map((v) => v.join(",")).join(" ")}"/>
    <polygon class="twin-floor" points="${floor}"/>
    ${image ? `<image href="${esc(image)}" width="1000" height="${h}" preserveAspectRatio="none" transform="matrix(${a} ${b} ${c} ${d} ${ox} ${oy})" class="twin-floor-image"/>` : ""}
    <g class="twin-floor-grid">${lines}</g><polygon class="twin-floor-outline" points="${floor}"/>
    <g class="twin-axis"><path d="M${p(0,1)}L${p(.22,1)} M${p(0,1)}L${p(0,.78)}"/><text x="${p(.24,1)[0]}" y="${p(.24,1)[1] + 3}">X</text><text x="${p(0,.75)[0]-12}" y="${p(0,.75)[1]}">Y</text></g>
    ${devices}
  </svg>`;
}
