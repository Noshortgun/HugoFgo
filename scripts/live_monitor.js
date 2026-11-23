const PAGE_NAMES = {
  menu: '菜单', support: '助战选择', team: '队伍确认', battle: '战斗', card: '指令卡',
  result: '战斗结算', drops: '掉落奖励', ap_recovery: 'AP 恢复', story: '剧情',
  loading: '加载中', unknown: '未知画面',
};
const FIELD_NAMES = {
  wave: '面数', turn: '累计回合', np: '宝具充能', crit: '暴击率', turn_boundary: '回合边界',
  enemies: '敌人数量', ap: 'AP', support_class: '助战职阶', team_servants: '队伍从者',
  team_support_ce: '助战礼装', team_mystic: '御主礼装',
};
const STATUS_NAMES = {
  not_applicable: '不适用', not_read: '未读取', uncertain: '不确定', confirmed: '已确认',
};
const RUN_NAMES = { ready: '准备就绪', running: '实机运行中', completed: '已完成', stopped: '已停止', failed: '失败' };

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function fmtTime(timestamp) {
  if (!timestamp) return '—';
  return new Date(timestamp * 1000).toLocaleTimeString('zh-CN', { hour12: false });
}
function fmtValue(value) {
  if (value === null || value === undefined) return '—';
  if (Array.isArray(value)) return value.join(' / ');
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}
function fieldValue(observation, name) {
  const field = observation?.fields?.[name];
  return field?.value ?? '—';
}
function latestProgress(state) {
  const events = state.events || [];
  for (let i = events.length - 1; i >= 0; i--) {
    const data = events[i].data || {};
    if (data.wave !== undefined || data.turn !== undefined || data.action_index !== undefined) return data;
  }
  return {};
}
function setText(id, value) { document.getElementById(id).textContent = value; }

function renderFields(observation) {
  const host = document.getElementById('fields');
  host.replaceChildren();
  const fields = observation?.fields || {};
  const names = Object.keys(FIELD_NAMES).filter(name => fields[name]);
  if (!names.length) {
    host.append(el('div', 'empty', '等待字段识别'));
    return;
  }
  for (const name of names) {
    const field = fields[name];
    const row = el('div', `field-row ${field.status}`);
    row.append(el('span', 'name', FIELD_NAMES[name] || name));
    row.append(el('span', 'value', fmtValue(field.value)));
    const confidence = field.confidence === null || field.confidence === undefined ? '' : ` · ${Number(field.confidence).toFixed(3)}`;
    row.append(el('span', 'meta', `${STATUS_NAMES[field.status] || field.status}${confidence}`));
    host.append(row);
  }
}

function renderControls(observation) {
  const host = document.getElementById('controls');
  host.replaceChildren();
  const controls = observation?.controls || [];
  setText('control-count', `${controls.length} 个`);
  if (!controls.length) {
    host.append(el('div', 'empty', '当前画面没有可展示的控件'));
    return;
  }
  for (const control of controls) {
    const row = el('div', `control-row${control.actionable ? '' : ' disabled'}`);
    const title = el('div', 'control-title');
    title.append(el('strong', '', control.name));
    title.append(el('span', '', control.actionable ? '可执行' : '不可执行'));
    row.append(title);
    const details = [control.kind, control.reason];
    if (control.card) {
      details.push(`槽位 ${control.card.servant_slot}`, control.card.card_type);
      if (control.card.crit_rate !== null && control.card.crit_rate !== undefined) {
        details.push(`暴击 ${control.card.crit_rate}%`);
      }
      if (control.card.affinity) details.push(control.card.affinity);
    }
    if (control.support) details.push(control.support.name, control.support.ce || '礼装未知');
    row.append(el('div', 'control-meta', details.filter(Boolean).join(' · ')));
    host.append(row);
  }
}

function renderStats(state) {
  const host = document.getElementById('stats');
  host.replaceChildren();
  const perception = state.result?.perception;
  const evidence = state.observation?.evidence || {};
  const rows = [
    ['字段耗时', evidence.field_ms !== undefined ? `${Number(evidence.field_ms).toFixed(2)} ms` : '—'],
    ['字段缓存', evidence.field_cache_hit === 1 ? '命中' : evidence.field_cache_hit === 0 ? '未命中' : '—'],
    ['读帧次数', perception?.reads ?? '—'],
    ['读帧缓存', perception?.cache_hits ?? '—'],
    ['字段识别', perception?.field_reads ?? '—'],
    ['字段缓存命中', perception?.field_cache_hits ?? '—'],
    ['读帧均值', perception?.mean_ms !== null && perception?.mean_ms !== undefined ? `${perception.mean_ms} ms` : '—'],
    ['读帧 P95', perception?.p95_ms !== null && perception?.p95_ms !== undefined ? `${perception.p95_ms} ms` : '—'],
  ];
  for (const [name, value] of rows) {
    const card = el('div', 'stat');
    card.append(el('span', '', name), el('strong', '', String(value)));
    host.append(card);
  }
}

function renderDecision(state) {
  const decision = state.decision;
  if (!decision) {
    setText('decision-control', '尚未执行动作');
    setText('decision-reason', '监控已就绪，开始实机验证后显示真实执行动作。');
    setText('decision-time', '—');
    document.getElementById('decision-tags').replaceChildren();
    return;
  }
  const data = decision.data || {};
  setText('decision-control', data.control || decision.label);
  setText('decision-reason', data.reason || decision.label);
  setText('decision-time', fmtTime(decision.timestamp));
  const tags = document.getElementById('decision-tags');
  tags.replaceChildren();
  const values = [
    data.wave !== undefined ? `第 ${data.wave} 面` : null,
    data.turn !== undefined ? `回合 ${data.turn}` : null,
    data.action_index !== undefined ? `动作 ${data.action_index}` : null,
    data.card_position !== undefined ? `卡序 ${data.card_position}` : null,
    data.x !== undefined ? `坐标 ${data.x}, ${data.y}` : null,
    data.wait_ms !== undefined ? `等待 ${Number(data.wait_ms).toFixed(0)} ms` : null,
    data.channel_ms !== undefined ? `通道 ${Number(data.channel_ms).toFixed(1)} ms` : null,
    data.batch_id !== undefined ? `批次 ${data.batch_id}` : null,
  ].filter(Boolean);
  for (const value of values) tags.append(el('span', 'tag', value));
  if (state.monitor_error) tags.append(el('span', 'tag warn', '监控绘制异常，游戏执行继续'));
}

function renderEvents(state) {
  const host = document.getElementById('events');
  host.replaceChildren();
  const events = (state.events || []).slice(-12);
  if (!events.length) {
    host.append(el('div', 'empty', '等待真实执行事件'));
    return;
  }
  for (const event of events) {
    const row = el('div', 'event-row');
    row.append(el('span', 'time', fmtTime(event.timestamp)));
    row.append(el('span', 'event-type', event.label || event.type));
    const data = event.data || {};
    const summary = [data.control, data.reason, data.state,
      data.wave !== undefined ? `面 ${data.wave}` : null,
      data.turn !== undefined ? `回合 ${data.turn}` : null,
      data.action_index !== undefined ? `动作 ${data.action_index}` : null,
      data.card_position !== undefined ? `卡序 ${data.card_position}` : null,
      data.wait_ms !== undefined ? `${Number(data.wait_ms).toFixed(0)} ms` : null,
      data.message, data.actual,
    ].filter(value => value !== null && value !== undefined && value !== '').join(' · ');
    row.append(el('span', 'event-data', summary || '—'));
    host.append(row);
  }
}

function renderHistory(state) {
  const host = document.getElementById('history');
  host.replaceChildren();
  const items = (state.history || []).slice(-12);
  setText('history-count', `${(state.history || []).length} 帧`);
  if (!items.length) {
    const row = el('tr', 'empty');
    const cell = el('td', '', '等待关键帧数据');
    cell.colSpan = 6;
    row.append(cell);
    host.append(row);
    return;
  }
  for (const item of items) {
    const observation = item.observation || {};
    const row = el('tr');
    row.append(el('td', '', `#${item.id}`));
    row.append(el('td', '', item.page));
    row.append(el('td', '', String(fieldValue(observation, 'wave'))));
    row.append(el('td', '', String(fieldValue(observation, 'turn'))));
    row.append(el('td', '', item.decision?.data?.control || '等待决策'));
    row.append(el('td', '', fmtTime(item.timestamp)));
    host.append(row);
  }
}

function render(state) {
  const status = document.getElementById('status');
  status.className = `status ${state.status}`;
  status.textContent = RUN_NAMES[state.status] || state.status;
  setText('run-dir', state.run_dir || '尚未开始（当前为演示帧）');
  if (state.serial) setText('serial', state.serial);
  if (state.job_name) setText('job-name', state.job_name);
  document.getElementById('start').disabled = state.status === 'running';
  document.getElementById('stop').disabled = state.status !== 'running';
  const progress = latestProgress(state);
  const observation = state.observation || {};
  setText('page', PAGE_NAMES[observation.base_page] || observation.base_page || '—');
  setText('progress', `${fieldValue(observation, 'wave')} / ${fieldValue(observation, 'turn')}`);
  setText('action-index', progress.action_index ?? '—');
  const evidence = observation.evidence || {};
  setText('field-ms', evidence.field_ms !== undefined ? `${Number(evidence.field_ms).toFixed(2)} ms` : '—');
  setText('field-cache', evidence.field_cache_hit === 1 ? '命中' : evidence.field_cache_hit === 0 ? '未命中' : '—');
  renderFields(observation);
  renderControls(observation);
  renderStats(state);
  renderDecision(state);
  renderEvents(state);
  renderHistory(state);
  if (state.error) setText('decision-reason', state.error);
}

async function refresh() {
  try {
    const response = await fetch('/api/state', { cache: 'no-store' });
    if (response.ok) render(await response.json());
  } catch (error) {
    setText('status', '监控连接中断');
  }
}

document.getElementById('start').addEventListener('click', async () => {
  document.getElementById('start').disabled = true;
  await fetch('/api/start', { method: 'POST' });
  await refresh();
});
document.getElementById('stop').addEventListener('click', async () => {
  await fetch('/api/stop', { method: 'POST' });
  await refresh();
});
refresh();
setInterval(refresh, 800);
