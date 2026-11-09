import { api } from "../api.js";
import { store } from "../store.js";
import { emptyBlock, errorBlock, toast } from "../ui.js";
import { $, esc, setHTML } from "../util.js";

export function render(root, params) {
  const day = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
  root.innerHTML = `<header class="command-head"><div><span class="command-kicker">STEPEYE / EVIDENCE REPLAY</span><h2>时间里的现场</h2><p>按工位与时段检索，回到事件发生的那一刻。</p></div><a class="btn" href="#/trace">进入事件追溯</a></header><div class="filters"><label class="field"><span>工位</span><select id="rec-station">${store.metas.map((s) => `<option value="${esc(s.id)}" ${s.id === params.id ? "selected" : ""}>${esc(s.name)}</option>`).join("")}</select></label>
    <label class="field"><span>开始 / 北京时间</span><input id="rec-start" type="datetime-local" value="${day}T00:00"></label>
    <label class="field"><span>结束 / 北京时间</span><input id="rec-end" type="datetime-local" value="${day}T23:59"></label><button class="btn btn-primary" id="rec-query">检索录像</button></div>
    <section class="panel" id="rec-panel" hidden><header class="panel-head"><h2>录像回放</h2><span class="muted" id="rec-note">选择已完成的录像片段播放</span></header>
    <div class="panel-body"><video id="rec-player" controls playsinline preload="metadata" style="width:100%;max-height:480px;background:var(--bg-0)"></video><div id="rec-current" class="muted"></div></div></section>
    <section class="panel table-panel"><div class="table-wrap" id="rec-list"></div></section>`;
  let dead = false, version = 0;
  const player = $("#rec-player", root), list = $("#rec-list", root), panel = $("#rec-panel", root);
  const resetPlayer = () => {
    player.pause();
    player.removeAttribute("src");
    player.load();
    panel.hidden = true;
    $("#rec-current", root).textContent = "";
  };
  const search = async () => {
    const turn = ++version, id = $("#rec-station", root).value;
    resetPlayer();
    if (!id) { setHTML(list, emptyBlock("没有可调阅的工位", "先接入相机并配置预览流及录像目录。")); return; }
    const start = $("#rec-start", root).value, end = $("#rec-end", root).value;
    if (!start || !end || end <= start) { toast("请选择有效的起止时间", "error"); return; }
    try {
      const data = await api.recordings(id, start + ":00+08:00", end + ":00+08:00");
      if (dead || turn !== version) return;
      $("#rec-note", root).textContent = `${data.rows.length} 段 · ${data.invalid} 个未完成或无效文件${data.truncated ? " · 结果截断，请缩小时段" : ""}`;
      setHTML(list, data.rows.length ? `<table class="table"><thead><tr><th>开始时间</th><th>时长</th><th>大小</th><th>操作</th></tr></thead><tbody>${data.rows.map((r) => `<tr><td>${esc(r.start.slice(0, 19).replace("T", " "))}</td><td class="num">${r.duration_s} s</td><td class="num">${(r.bytes / 1048576).toFixed(1)} MB</td><td><button class="btn btn-sm" data-record="${esc(r.name)}" data-station="${esc(id)}">播放</button></td></tr>`).join("")}</tbody></table>` : emptyBlock("该时段没有已完成录像", "确认 Monibuca 正在录制、目录已共享且检索时间正确。"));
    } catch (e) {
      if (dead || turn !== version) return;
      setHTML(list, errorBlock(e.message));
      list.querySelector("[data-retry]").onclick = search;
    }
  };
  list.onclick = (e) => {
    const b = e.target.closest("[data-record]");
    if (!b) return;
    panel.hidden = false;
    player.src = api.recordingVideo(b.dataset.station, b.dataset.record);
    $("#rec-current", root).textContent = b.dataset.record;
    player.play().catch(() => toast("请在播放器中点击播放，或检查浏览器是否支持该录像编码", "error"));
  };
  player.onerror = () => { if (!dead) toast("录像读取或解码失败，请检查文件和浏览器编码支持", "error"); };
  $("#rec-query", root).onclick = search;
  $("#rec-station", root).onchange = () => { resetPlayer(); search(); };
  search();
  return () => { dead = true; version++; player.onerror = null; resetPlayer(); };
}
