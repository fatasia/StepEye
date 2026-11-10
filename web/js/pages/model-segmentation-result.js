import { $ } from "../util.js";

// 掩膜独立显示，不把像素区域转换成检测框或 OK/NG。
export function renderSegmentationResult(root, result, originalUrl) {
  const image = $("[data-model-img]", root);
  const maskUrl = `data:image/png;base64,${result.mask_png_base64}`;
  const panel = $("[data-model-result]", root);
  panel.innerHTML = `<b>针脚区域 · ${Number(result.foreground_pixels).toLocaleString()} 像素</b>
    <span>${Number(result.patch_count)} 个图块 · ${Number(result.elapsed_ms).toFixed(0)} ms · CPU</span>
    <div role="group" aria-label="分割结果视图">
      <button class="btn" data-segment-original aria-pressed="false">原图</button>
      <button class="btn btn-primary" data-segment-mask aria-pressed="true">掩膜</button>
      <a class="btn" data-segment-download download="pin-mask.png">下载掩膜</a>
    </div><small>白色为针脚区域；此模型不判定缺针、弯针或压接是否合格。</small>`;
  const original = $("[data-segment-original]", panel);
  const mask = $("[data-segment-mask]", panel);
  const select = (showMask) => {
    image.src = showMask ? maskUrl : originalUrl;
    image.alt = showMask ? "针脚二值分割掩膜，白色为针脚" : "试跑原图";
    for (const [button, on] of [[original, !showMask], [mask, showMask]]) {
      button.setAttribute("aria-pressed", String(on));
      button.classList.toggle("btn-primary", on);
    }
  };
  original.onclick = () => select(false);
  mask.onclick = () => select(true);
  $("[data-segment-download]", panel).href = maskUrl;
  select(true);
}
