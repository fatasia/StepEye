import { esc } from '../util.js';
import { openDrawer } from '../ui.js';

/** 行业工步模板：选模板 → 预览骨架 → 区域映射 → 存为当前工位的新配方版本。
 *  占位区域「待映射:」必须全部映射为工位实际区域（后端配置校验强制），不直接改运行规则。 */
export function openIndustryTemplates({ station, canWrite, onSaved } = {}) {
  let closed = false;
  let busy = false;
  const controller = new AbortController();
  const drawer = openDrawer({ title: '行业工步模板', subtitle: '选行业 → 选模板 → 区域映射 → 存为配方版本',
    onClose: () => { closed = true; controller.abort(); }, html: `
    <style>.ind-tpl{font-size:13px;line-height:1.7}.ind-tpl label{display:block;margin:14px 0}.ind-tpl select,.ind-tpl input{display:block;box-sizing:border-box;width:100%;margin-top:6px;padding:9px;border:1px solid var(--border,#34434b);border-radius:6px;background:var(--panel,#172027);color:inherit;font:inherit}.ind-tpl [role=alert]{color:#ee958b}.ind-tpl .ind-actions{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0}.ind-tpl table{width:100%;border-collapse:collapse;text-align:left;margin:10px 0}.ind-tpl th,.ind-tpl td{padding:7px 8px 7px 0;border-bottom:1px solid var(--border,#34434b)}.ind-tpl .ind-note{color:var(--muted,#95a6ad)}.ind-tpl .ind-map{margin:12px 0;padding:10px;border:1px solid var(--border,#34434b);border-radius:6px}.ind-tpl .ind-map label{margin:8px 0}</style>
    <section class="ind-tpl">
      <p class="ind-note">模板提供标准步骤骨架；区域占位「待映射:」需映射为当前工位的实际区域。保存为新配方版本后，还需在产品配方里切换版本才会生效。</p>
      <label>行业<select data-ind-industry><option value="">全部行业</option></select></label>
      <label>模板<select data-ind-template><option value="">选择模板</option></select></label>
      <div class="ind-map" data-ind-map hidden><p class="ind-note" style="margin:4px 0">区域映射（占位 → 当前工位区域）</p><div data-ind-map-rows></div></div>
      <div data-ind-preview></div>
      <div class="ind-actions"><button class="btn" data-ind-refresh>刷新</button><button class="btn btn-primary" data-ind-apply ${canWrite ? '' : 'hidden'} disabled>存为当前工位的新配方</button></div>
      <p data-ind-error role="alert"></p><p data-ind-status role="status"></p>
    </section>` });
  const body = drawer.body;
  const find = s => body.querySelector(s);
  const error = t => { if (!closed) find('[data-ind-error]').textContent = t; };
  let templates = [];
  let currentSteps = [];
  let placeholders = [];

  async function request(url, options = {}) {
    const response = await fetch(url, { ...options, signal: controller.signal });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `请求失败（${response.status}）`);
    return data;
  }

  function collectPlaceholders(steps) {
    const found = new Set();
    for (const s of steps) {
      const mode = ['all', 'any', 'seq'].find(k => s.evidence[k]?.length);
      for (const cond of s.evidence[mode] || []) {
        const info = Object.values(cond)[0];
        if (info && typeof info.zone === 'string' && info.zone.startsWith('待映射:')) found.add(info.zone);
      }
    }
    return [...found];
  }

  function renderMap() {
    const box = find('[data-ind-map]');
    if (!placeholders.length) { box.hidden = true; find('[data-ind-apply]').disabled = !canWrite || !currentSteps.length; return; }
    box.hidden = false;
    find('[data-ind-map-rows]').innerHTML = placeholders.map(p =>
      `<label>${esc(p)}<input data-ind-zone-for="${esc(p)}" maxlength="100" placeholder="工位区域 id" required></label>`).join('');
    find('[data-ind-apply]').disabled = true;
    find('[data-ind-map-rows]').oninput = () => {
      const complete = placeholders.every(p => find(`[data-ind-zone-for="${CSS.escape(p)}"]`)?.value.trim());
      find('[data-ind-apply]').disabled = !canWrite || !complete;
    };
  }

  async function loadSteps() {
    const tid = find('[data-ind-template]').value;
    const box = find('[data-ind-preview]');
    if (!tid) {
      box.innerHTML = '<p class="ind-note">选择模板后显示步骤骨架。</p>';
      currentSteps = []; placeholders = []; renderMap();
      return;
    }
    const data = await request(`/api/industry-templates/${encodeURIComponent(tid)}/steps`);
    currentSteps = data.steps;
    placeholders = collectPlaceholders(currentSteps);
    box.innerHTML = `<table><thead><tr><th>#</th><th>步骤</th><th>名称</th><th>证据</th></tr></thead><tbody>${currentSteps.map((s, i) => {
      const mode = ['all', 'any', 'seq'].find(k => s.evidence[k]?.length);
      const first = (s.evidence[mode] || [])[0] || {};
      const kind = Object.keys(first)[0] || '—';
      const info = Object.values(first)[0] || {};
      return `<tr><td>${i + 1}</td><td><strong>${esc(s.id)}</strong></td><td>${esc(s.name || '')}</td><td>${esc(kind)}${info.zone ? ` · ${esc(info.zone)}` : ''}</td></tr>`;
    }).join('')}</tbody></table>`;
    renderMap();
  }

  async function fillTemplates() {
    const industry = find('[data-ind-industry]').value;
    templates = (await request(`/api/industry-templates${industry ? `?industry=${encodeURIComponent(industry)}` : ''}`)).items;
    const select = find('[data-ind-template]');
    select.innerHTML = templates.length
      ? templates.map(t => `<option value="${esc(t.id)}">${esc(t.name)} · ${esc(t.description)}</option>`).join('')
      : '<option value="">该行业暂无模板</option>';
    await loadSteps();
  }

  find('[data-ind-industry]').onchange = () => fillTemplates().catch(e => error(e.message));
  find('[data-ind-template]').onchange = () => loadSteps().catch(e => error(e.message));
  find('[data-ind-refresh]').onclick = () => fillTemplates().catch(e => error(e.message));

  find('[data-ind-apply]').onclick = async () => {
    if (busy || closed || !canWrite) return;
    const tid = find('[data-ind-template]').value;
    if (!tid) { error('先选择模板'); return; }
    const zoneMap = {};
    for (const p of placeholders) {
      const value = find(`[data-ind-zone-for="${CSS.escape(p)}"]`)?.value.trim();
      if (!value) { error(`「${p}」尚未映射区域`); return; }
      zoneMap[p.slice('待映射:'.length)] = value;
    }
    busy = true;
    find('[data-ind-apply]').disabled = true;
    error(''); find('[data-ind-status]').textContent = '正在映射区域并保存为新配方版本…';
    try {
      const data = await request(`/api/industry-templates/${encodeURIComponent(tid)}/steps`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ zone_map: zoneMap }) });
      const created = await request('/api/recipes', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: `行业模板 ${tid}`, model_key: tid, station_id: station, patch: { steps: data.steps } }),
      });
      find('[data-ind-status]').textContent = `已保存为配方「${created.name}」；在产品配方里切换版本后生效。`;
      onSaved?.(created);
    } catch (e) {
      if (!closed) error(e.message);
    } finally {
      busy = false;
      if (!closed) find('[data-ind-apply]').disabled = false;
    }
  };

  (async () => {
    const industries = (await request('/api/industry-templates/industries')).items;
    if (closed) return;
    find('[data-ind-industry]').innerHTML = '<option value="">全部行业</option>' +
      industries.map(i => `<option value="${esc(i)}">${esc(i)}</option>`).join('');
    await fillTemplates();
  })().catch(e => { if (!closed) error(e.message); });
  return drawer;
}
