import { esc, setHTML } from '../util.js';

const gearNames={helmet:'安全帽',vest:'反光衣',gloves:'手套',goggles:'护目镜',shoes:'安全鞋'};
const statuses={observed:['已观测','success'],missing:['缺失','danger'],pending:['确认中','warning'],unknown:['未知','muted'],
  alert:['触发','danger'],clear:['未触发','muted']};
const reasons={no_evidence:'无有效证据',insufficient_count:'数量证据不足',region_association:'按人员区域关联',
  ambiguous_assignment:'人员归属不明确',conflicting_evidence:'正负证据冲突'};
function status(value){
  const [label,tone]=statuses[value]||statuses.unknown;
  return `<span class="tone-${tone}-text">${label}</span>`;
}

/** Paint-only subscriber companion: no timer, model, or retained recognition state. */
export function mountSafetyRecognition(host){
  host.innerHTML=`<style>.live-safety[hidden]{display:none!important}.live-safety{margin-top:16px;border-top:1px solid var(--border,#34434b);padding-top:12px}.live-safety summary{cursor:pointer;font-size:13px}.live-safety .ls-person,.live-safety .ls-rule{padding:10px 0;border-bottom:1px solid var(--border,#34434b)}.live-safety .ls-items{display:grid;grid-template-columns:1fr 1fr;gap:7px 12px;margin-top:8px;font-size:12px}.live-safety .ls-items>span{display:flex;justify-content:space-between;gap:8px}.live-safety p{font-size:12px;line-height:1.6;margin:9px 0}.live-safety .ls-rule small{display:block;margin-top:5px}</style><details class="live-safety" hidden><summary>安全识别</summary><div data-ls-content></div></details>`;
  const panel=host.querySelector('details');
  const content=host.querySelector('[data-ls-content]');
  return (state, fresh=true)=>{
    const ppe=state?.ppe;
    const ehs=state?.ehs;
    panel.hidden=!ppe?.enabled&&!ehs?.enabled;
    if(panel.hidden){setHTML(content,'');return;}
    let html='<p class="muted">已观测只表示识别到穿戴证据，不等于合规结论；未知不按缺失处理。</p>';
    if(!fresh)html+='<p class="muted">实时连接中断或画面未更新，安全识别状态未知。</p>';
    if(ppe?.enabled){
      const persons=!fresh||ppe.status==='unknown'?[]:(ppe.persons||[]);
      if(!persons.length)html+=`<p class="muted">PPE：${fresh&&ppe.status==='no_person_observed'?'当前未观测到人员':'未知 · 等待有效人员证据'}</p>`;
      for(const person of persons){
        html+=`<div class="ls-person"><b>人员 #${esc(person.track_id)}</b><div class="ls-items">${Object.entries(gearNames).map(([key,name])=>{
          const gear=person.gear?.[key];
          const reason=gear?.reason?(reasons[gear.reason]||gear.reason):'无有效证据';
          return `<span title="${esc(reason)}"><span>${name}</span>${status(gear?.status)}</span>`;
        }).join('')}</div></div>`;
      }
    }
    if(ehs?.enabled){
      const rules=Object.entries(ehs.rules||{});
      html+='<p><b>仓储 / 通道规则</b></p>';
      if(!rules.length)html+='<p class="muted">暂无规则状态</p>';
      for(const [id,rule] of rules){
        const unknown=!fresh||!rule.status||rule.status==='unknown';
        // Unknown updates must discard preceding counts, even if a producer retains old fields.
        const count=unknown?null:rule.count;
        html+=`<div class="ls-rule"><div class="kv"><span>${esc(id)}</span>${status(unknown?'unknown':rule.status)}</div>${Number.isFinite(count)?`<small>当前可见数量：<b>${count}</b> · 单相机视野快照</small>`:Object.hasOwn(rule,'count')&&!unknown?'<small class="muted">数量尚未确认</small>':''}${!unknown&&rule.track_ids?.length?`<small class="muted">目标 #${rule.track_ids.map(esc).join('、#')}</small>`:''}</div>`;
      }
    }
    setHTML(content,html);
  };
}
