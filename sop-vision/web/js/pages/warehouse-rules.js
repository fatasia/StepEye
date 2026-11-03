import { esc } from '../util.js';

const kinds = {intrusion:'区域入侵', fire_smoke:'烟火持续观察', aisle_occupied:'通道占用', object_count:'可见物数量'};
const states = {unknown:'未知', pending:'观察中', clear:'正常', alert:'告警'};

// Uses the station's existing regions/detector and atomic configuration reload.
// Counts describe one camera's visible objects, not cross-camera inventory.
export async function mountWarehouseRules(host, stationId, signal) {
  host.innerHTML = '<details><summary>区域与仓储规则</summary><div data-wh-body>正在读取…</div></details>';
  const body = host.querySelector('[data-wh-body]');
  const url = `/api/stations/${encodeURIComponent(stationId)}/ehs-config`;
  async function request(path, options = {}) {
    const response = await fetch(path, {...options, signal});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '规则配置无效');
    return data;
  }
  try {
    const [data, me] = await Promise.all([request(url), request('/api/auth/me')]);
    if (signal.aborted) return;
    const writable = ['admin', 'operator'].includes(me.role);
    let rules = structuredClone(data.config.rules);
    body.innerHTML = `<form class="safety-config-form"><p>复用已画区域和当前检测器。数量仅指单机位当前可见物，不累计库存。</p>
      <fieldset ${writable ? '' : 'disabled'}><label class="safety-switch"><input data-wh-enabled type="checkbox" ${data.config.enabled ? 'checked' : ''}>启用区域规则</label>
      <div data-wh-list></div><button type="button" class="btn" data-wh-add>添加规则</button></fieldset>
      <p data-wh-message role="status"></p><button class="btn btn-primary" type="submit" ${writable ? '' : 'hidden'}>保存区域规则</button></form>`;
    const form = body.querySelector('form');
    const list = body.querySelector('[data-wh-list]');
    const message = body.querySelector('[data-wh-message]');
    const input = (name, label, value, min, max) => `<label>${label}<input data-field="${name}" type="number" min="${min}" max="${max}" step="any" value="${value ?? ''}"></label>`;
    function read() {
      rules = [...list.querySelectorAll('[data-wh-rule]')].map((row, index) => {
        const value = key => row.querySelector(`[data-field="${key}"]`).value;
        const rule = {...rules[index], kind:value('kind'), zone:value('zone'),
          labels:value('labels').split(/[,，]/).map(v => v.trim()).filter(Boolean), min_s:Number(value('min_s'))};
        rule.min_count = rule.kind === 'object_count' && value('min_count') !== '' ? Number(value('min_count')) : null;
        rule.max_count = rule.kind === 'object_count' && value('max_count') !== '' ? Number(value('max_count')) : null;
        return rule;
      });
    }
    function paint() {
      list.innerHTML = rules.map((rule, index) => {
        const state = data.state.rules?.[rule.id];
        return `<section data-wh-rule><header><strong>${esc(rule.id)}</strong><button type="button" class="btn" data-wh-remove="${index}">移除</button></header>
          <div class="safety-grid"><label>检查项<select data-field="kind">${Object.entries(kinds).map(([key,name]) => `<option value="${key}" ${rule.kind === key ? 'selected' : ''}>${name}</option>`).join('')}</select></label>
          <label>区域<select data-field="zone">${data.zones.map(zone => `<option ${rule.zone === zone ? 'selected' : ''}>${esc(zone)}</option>`).join('')}</select></label>
          <label>检测类别<input data-field="labels" value="${esc(rule.labels.join(','))}" required placeholder="${esc(data.labels.join(','))}"></label>
          ${input('min_s','连续确认（秒）',rule.min_s,0,3600)}
          <span data-wh-limits ${rule.kind === 'object_count' ? '' : 'hidden'}>${input('min_count','数量下限',rule.min_count,0,256)}${input('max_count','数量上限',rule.max_count,0,256)}</span></div>
          <p>当前：${states[state?.status] || '未知'}${state?.count != null ? ` · 可见 ${Number(state.count)} 个` : ''}</p></section>`;
      }).join('');
      list.querySelectorAll('[data-field="kind"]').forEach(select => {
        select.onchange = () => { read(); paint(); };
      });
      list.querySelectorAll('[data-wh-remove]').forEach(button => {
        button.onclick = () => { read(); rules.splice(Number(button.dataset.whRemove), 1); paint(); };
      });
    }
    paint();
    body.querySelector('[data-wh-add]').onclick = () => {
      read();
      if (!data.zones.length) { message.textContent = '先在工位画出检查区域。'; return; }
      if (rules.length >= 64) { message.textContent = '最多配置64条规则。'; return; }
      let suffix = 1;
      while (rules.some(rule => rule.id === `warehouse-${suffix}`)) suffix++;
      rules.push({id:`warehouse-${suffix}`,kind:'aisle_occupied',zone:data.zones[0],
        labels:[data.labels[0] || 'person'],min_s:1,min_frames:3,min_confidence:.5,severity:'warn'});
      paint();
    };
    form.onsubmit = async event => {
      event.preventDefault();
      if (!writable) return;
      read();
      const button = form.querySelector('[type="submit"]');
      button.disabled = true;
      message.textContent = '正在保存并重载…';
      try {
        const saved = await request(url, {method:'PUT',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({...data.config,enabled:body.querySelector('[data-wh-enabled]').checked,rules})});
        if (signal.aborted) return;
        Object.assign(data, saved); rules = structuredClone(saved.config.rules); paint();
        message.textContent = '已保存并生效。';
      } catch (error) {
        if (!signal.aborted) message.textContent = error.message;
      } finally { if (!signal.aborted) button.disabled = false; }
    };
  } catch (error) { if (!signal.aborted) body.textContent = error.message; }
}
