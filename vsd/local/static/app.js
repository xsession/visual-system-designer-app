'use strict';

const state = {
  token: localStorage.getItem('vsdStudioToken') || '',
  health: null,
  components: [],
  assets: [],
  projects: [],
  sessions: [],
  graph: { nodes: [], connections: [] },
  selectedNode: null,
  connecting: false,
  connectionSource: null,
  activeProjectId: null,
  firmwareReport: null,
  reportTab: 'summary',
  plotPaused: false,
  plotTimer: null,
  logs: [],
};

const colors = ['#55d6be', '#63a8ff', '#ffca69', '#ff7c91', '#b394ff', '#8dde72', '#ff9f5a', '#66e0ff'];
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

function toast(message, error = false) {
  const element = $('#toast');
  element.textContent = message;
  element.style.borderColor = error ? '#ff6f7d' : '#55d6be';
  element.classList.add('show');
  clearTimeout(element._timer);
  element._timer = setTimeout(() => element.classList.remove('show'), 2800);
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (state.token) headers.set('Authorization', `Bearer ${state.token}`);
  if (options.body && !(options.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try { detail = (await response.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  const type = response.headers.get('content-type') || '';
  return type.includes('application/json') ? response.json() : response.text();
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  })[character]);
}

function formatTime(timestamp) {
  return timestamp ? new Date(timestamp * 1000).toLocaleString() : '—';
}

function formatBytes(value) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / 1024 ** 2).toFixed(1)} MiB`;
}

async function bootstrap() {
  installTabNavigation();
  installDesignerEvents();
  installMonitorEvents();
  installFirmwareEvents();
  installEmulationEvents();
  installLogEvents();
  installSettingsEvents();
  window.addEventListener('resize', () => {
    renderConnections();
    if (state.lastPlot) drawPlot(state.lastPlot);
  });

  try {
    state.health = await api('/api/health');
    renderHealth();
    await Promise.all([loadComponents(), loadAssets(), loadProjects(), loadSessions()]);
    startPlotLoop();
  } catch (error) {
    toast(`Startup failed: ${error.message}`, true);
  }
}

function installTabNavigation() {
  $$('.tab').forEach(button => button.addEventListener('click', () => {
    $$('.tab').forEach(item => item.classList.toggle('active', item === button));
    $$('.panel-view').forEach(panel => panel.classList.toggle('active', panel.id === button.dataset.tab));
    if (button.dataset.tab === 'monitor') refreshPlot();
    if (button.dataset.tab === 'logs') refreshLogs();
    if (button.dataset.tab === 'emulation') loadSessions();
  }));
}

function renderHealth() {
  const health = state.health;
  $('#localMode').textContent = health.local_only ? 'LOCAL' : 'REMOTE TOKEN';
  $('#localMode').className = `pill ${health.local_only ? 'ok' : 'warning'}`;
  $('#catalogCount').textContent = `${health.catalog.total} components`;
  $('#dataPath').textContent = health.data_dir;
  $('#healthDetails').innerHTML = `
    <table>
      <tr><th>Data directory</th><td>${escapeHtml(health.data_dir)}</td></tr>
      <tr><th>Workspace</th><td>${escapeHtml(health.workspace)}</td></tr>
      <tr><th>Catalog</th><td>${health.catalog.total} local entries</td></tr>
      <tr><th>Mode</th><td>${health.local_only ? 'loopback-only' : 'remote/token-protected'}</td></tr>
    </table>`;
  renderToolchain(health.toolchain);
}

function renderToolchain(toolchain) {
  $('#toolchainCards').innerHTML = Object.entries(toolchain).map(([name, path]) => `
    <div class="stat"><span>${escapeHtml(name)}</span><strong>${path ? 'Ready' : 'Missing'}</strong><small class="muted">${escapeHtml(path || 'configure environment variable')}</small></div>
  `).join('');
}

async function loadComponents() {
  const query = encodeURIComponent($('#componentSearch').value.trim());
  const kind = encodeURIComponent($('#componentKind').value);
  const bus = encodeURIComponent($('#componentBus').value);
  state.components = await api(`/api/components?query=${query}&kind=${kind}&bus=${bus}&limit=250`);
  renderComponents();
}

function renderComponents() {
  const list = $('#componentList');
  list.innerHTML = state.components.map(component => `
    <article class="component-card" draggable="true" data-component="${escapeHtml(component.id)}">
      <strong>${escapeHtml(component.model)}</strong>
      <small>${escapeHtml(component.vendor)}</small>
      <div class="component-meta"><span>${escapeHtml(component.kind)}</span><span>${escapeHtml(component.bus)}</span><span>${escapeHtml(component.tier)}</span></div>
    </article>`).join('') || '<div class="empty-state">No matching components.</div>';
  list.querySelectorAll('.component-card').forEach(card => {
    card.addEventListener('dragstart', event => {
      event.dataTransfer.setData('application/x-vsd-component', card.dataset.component);
    });
    card.addEventListener('dblclick', () => addComponentNode(card.dataset.component, 80, 80));
  });
}

function installDesignerEvents() {
  let searchTimer;
  ['#componentSearch', '#componentKind', '#componentBus'].forEach(selector => {
    $(selector).addEventListener('input', () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(loadComponents, 180);
    });
  });
  const surface = $('#designSurface');
  surface.addEventListener('dragover', event => event.preventDefault());
  surface.addEventListener('drop', event => {
    event.preventDefault();
    const componentId = event.dataTransfer.getData('application/x-vsd-component');
    if (!componentId) return;
    const rect = surface.getBoundingClientRect();
    addComponentNode(componentId, event.clientX - rect.left + surface.scrollLeft, event.clientY - rect.top + surface.scrollTop);
  });
  $('#newProject').addEventListener('click', newProject);
  $('#saveProject').addEventListener('click', saveProject);
  $('#loadProject').addEventListener('click', loadSelectedProject);
  $('#connectNodes').addEventListener('click', () => {
    state.connecting = !state.connecting;
    state.connectionSource = null;
    $('#connectNodes').classList.toggle('primary', state.connecting);
    $('#designerHint').textContent = state.connecting ? 'Select two nodes to create a connection.' : 'Drag components from the catalog.';
  });
  $('#deleteNode').addEventListener('click', deleteSelectedNode);
  $('#fitCanvas').addEventListener('click', fitCanvas);
  surface.addEventListener('click', event => {
    if (event.target === surface || event.target.classList.contains('node-layer')) selectNode(null);
  });
}

function newProject() {
  state.graph = { nodes: [], connections: [] };
  state.activeProjectId = null;
  $('#projectName').value = 'Untitled system';
  selectNode(null);
  renderGraph();
}

function addComponentNode(componentId, x, y) {
  const component = state.components.find(item => item.id === componentId);
  if (!component) return;
  const node = {
    id: crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`,
    componentId: component.id,
    label: component.model,
    kind: component.kind,
    bus: component.bus,
    vendor: component.vendor,
    className: component.class_name,
    x: Math.max(10, Math.round(x / 12) * 12),
    y: Math.max(10, Math.round(y / 12) * 12),
    properties: {},
  };
  state.graph.nodes.push(node);
  renderGraph();
  selectNode(node.id);
}

function renderGraph() {
  const layer = $('#nodeLayer');
  layer.innerHTML = state.graph.nodes.map(node => `
    <div class="graph-node ${node.id === state.selectedNode ? 'selected' : ''}" data-node="${node.id}" style="left:${node.x}px;top:${node.y}px">
      <div class="node-head"><strong>${escapeHtml(node.label)}</strong><small>${escapeHtml(node.vendor)}</small></div>
      <div class="node-body"><span>${escapeHtml(node.kind)} · ${escapeHtml(node.bus)}</span><span class="port"></span></div>
    </div>`).join('');
  layer.querySelectorAll('.graph-node').forEach(element => {
    element.addEventListener('mousedown', beginNodeDrag);
    element.addEventListener('click', event => {
      event.stopPropagation();
      handleNodeClick(element.dataset.node);
    });
  });
  renderConnections();
  renderGraphSummary();
}

function beginNodeDrag(event) {
  if (event.button !== 0 || state.connecting) return;
  event.preventDefault();
  const element = event.currentTarget;
  const node = state.graph.nodes.find(item => item.id === element.dataset.node);
  const startX = event.clientX;
  const startY = event.clientY;
  const originX = node.x;
  const originY = node.y;
  function move(moveEvent) {
    node.x = Math.max(0, Math.round((originX + moveEvent.clientX - startX) / 12) * 12);
    node.y = Math.max(0, Math.round((originY + moveEvent.clientY - startY) / 12) * 12);
    element.style.left = `${node.x}px`;
    element.style.top = `${node.y}px`;
    renderConnections();
  }
  function end() {
    document.removeEventListener('mousemove', move);
    document.removeEventListener('mouseup', end);
    renderGraphSummary();
  }
  document.addEventListener('mousemove', move);
  document.addEventListener('mouseup', end);
}

function handleNodeClick(nodeId) {
  if (state.connecting) {
    if (!state.connectionSource) {
      state.connectionSource = nodeId;
      selectNode(nodeId);
      $('#designerHint').textContent = 'Select destination node.';
    } else if (state.connectionSource !== nodeId) {
      const exists = state.graph.connections.some(connection => connection.from === state.connectionSource && connection.to === nodeId);
      if (!exists) state.graph.connections.push({ id: crypto.randomUUID(), from: state.connectionSource, to: nodeId, type: 'bus' });
      state.connectionSource = null;
      $('#designerHint').textContent = 'Select another source or disable Connect.';
      renderGraph();
    }
  } else {
    selectNode(nodeId);
  }
}

function selectNode(nodeId) {
  state.selectedNode = nodeId;
  $$('.graph-node').forEach(node => node.classList.toggle('selected', node.dataset.node === nodeId));
  const node = state.graph.nodes.find(item => item.id === nodeId);
  if (!node) {
    $('#nodeInspector').className = 'empty-state';
    $('#nodeInspector').innerHTML = 'Select a node.';
    return;
  }
  $('#nodeInspector').className = 'inspector-grid';
  $('#nodeInspector').innerHTML = `
    <label>Label<input id="nodeLabel" value="${escapeHtml(node.label)}"></label>
    <label>Component<input value="${escapeHtml(node.componentId)}" disabled></label>
    <label>Bus<input value="${escapeHtml(node.bus)}" disabled></label>
    <label>Renode class<input value="${escapeHtml(node.className)}" disabled></label>
    <label>Properties JSON<textarea id="nodeProperties" rows="8">${escapeHtml(JSON.stringify(node.properties, null, 2))}</textarea></label>`;
  $('#nodeLabel').addEventListener('input', event => {
    node.label = event.target.value;
    renderGraph();
  });
  $('#nodeProperties').addEventListener('change', event => {
    try { node.properties = JSON.parse(event.target.value); toast('Node properties updated'); }
    catch (error) { toast(`Invalid JSON: ${error.message}`, true); }
  });
}

function renderConnections() {
  const svg = $('#connectionLayer');
  svg.setAttribute('viewBox', '0 0 2400 1600');
  svg.innerHTML = state.graph.connections.map(connection => {
    const from = state.graph.nodes.find(node => node.id === connection.from);
    const to = state.graph.nodes.find(node => node.id === connection.to);
    if (!from || !to) return '';
    const x1 = from.x + 190, y1 = from.y + 58;
    const x2 = to.x, y2 = to.y + 58;
    const bend = Math.max(50, Math.abs(x2 - x1) * .45);
    return `<path d="M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}"></path>`;
  }).join('');
}

function deleteSelectedNode() {
  if (!state.selectedNode) return;
  state.graph.nodes = state.graph.nodes.filter(node => node.id !== state.selectedNode);
  state.graph.connections = state.graph.connections.filter(connection => connection.from !== state.selectedNode && connection.to !== state.selectedNode);
  state.selectedNode = null;
  renderGraph();
  selectNode(null);
}

function fitCanvas() {
  const surface = $('#designSurface');
  if (!state.graph.nodes.length) { surface.scrollTo(0, 0); return; }
  const minX = Math.min(...state.graph.nodes.map(node => node.x));
  const minY = Math.min(...state.graph.nodes.map(node => node.y));
  surface.scrollTo({ left: Math.max(0, minX - 40), top: Math.max(0, minY - 40), behavior: 'smooth' });
}

function renderGraphSummary() {
  const counts = state.graph.nodes.reduce((acc, node) => { acc[node.kind] = (acc[node.kind] || 0) + 1; return acc; }, {});
  $('#graphSummary').textContent = JSON.stringify({ nodes: state.graph.nodes.length, connections: state.graph.connections.length, byKind: counts }, null, 2);
}

async function saveProject() {
  try {
    const project = await api('/api/projects', {
      method: 'POST',
      body: JSON.stringify({ id: state.activeProjectId, name: $('#projectName').value, graph: state.graph }),
    });
    state.activeProjectId = project.id;
    await loadProjects();
    $('#projectSelect').value = project.id;
    toast('Project saved locally');
  } catch (error) { toast(error.message, true); }
}

async function loadProjects() {
  state.projects = await api('/api/projects');
  $('#projectSelect').innerHTML = '<option value="">Select…</option>' + state.projects.map(project => `<option value="${project.id}">${escapeHtml(project.name)}</option>`).join('');
}

function loadSelectedProject() {
  const project = state.projects.find(item => item.id === $('#projectSelect').value);
  if (!project) return;
  state.activeProjectId = project.id;
  state.graph = project.graph || { nodes: [], connections: [] };
  $('#projectName').value = project.name;
  selectNode(null);
  renderGraph();
}

async function loadAssets() {
  state.assets = await api('/api/assets?limit=1000');
  const options = '<option value="">None</option>' + state.assets.map(asset => `<option value="${asset.id}">${escapeHtml(asset.name)} (${formatBytes(asset.size)})</option>`).join('');
  ['#firmwareAsset', '#sessionFirmware', '#sessionPlatform', '#sessionScript'].forEach(selector => $(selector).innerHTML = options);
}

function installMonitorEvents() {
  $('#refreshPlot').addEventListener('click', refreshPlot);
  $('#pausePlot').addEventListener('click', event => {
    state.plotPaused = !state.plotPaused;
    event.target.textContent = state.plotPaused ? 'Resume' : 'Pause';
  });
  $('#injectPoint').addEventListener('click', injectPoint);
  $('#plotMode').addEventListener('change', refreshPlot);
}

function startPlotLoop() {
  clearInterval(state.plotTimer);
  state.plotTimer = setInterval(() => {
    if (!state.plotPaused && $('#monitor').classList.contains('active')) refreshPlot();
  }, 1000);
}

async function refreshPlot() {
  const sessionId = $('#monitorSession').value;
  if (!sessionId) {
    drawEmptyPlot('Select or start a session.');
    return;
  }
  const windowSeconds = Number($('#monitorWindow').value) || 30;
  const since = Date.now() / 1000 - windowSeconds;
  const channels = $('#monitorChannels').value.split(',').map(item => item.trim()).filter(Boolean);
  const params = new URLSearchParams({
    since: String(since),
    limit: '100000',
    expression: $('#filterExpression').value,
    pipeline: $('#filterPipeline').value || '[]',
    plot: $('#plotMode').value,
  });
  channels.forEach(channel => params.append('channel', channel));
  try {
    const response = await api(`/api/sessions/${sessionId}/telemetry?${params}`);
    state.lastPlot = response.plot;
    drawPlot(response.plot);
    renderPlotStats(response.points);
  } catch (error) { toast(`Plot: ${error.message}`, true); }
}

async function injectPoint() {
  const sessionId = $('#monitorSession').value;
  if (!sessionId) return toast('Select a session first', true);
  try {
    await api(`/api/sessions/${sessionId}/telemetry`, {
      method: 'POST',
      body: JSON.stringify({ points: [{ channel: $('#injectChannel').value, value: Number($('#injectValue').value), kind: $('#injectKind').value }] }),
    });
    refreshPlot();
  } catch (error) { toast(error.message, true); }
}

function canvasContext() {
  const canvas = $('#plotCanvas');
  const rect = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.floor(rect.width * ratio));
  canvas.height = Math.max(1, Math.floor(rect.height * ratio));
  const context = canvas.getContext('2d');
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  return { canvas, context, width: rect.width, height: rect.height };
}

function drawEmptyPlot(message) {
  const { context, width, height } = canvasContext();
  context.clearRect(0, 0, width, height);
  context.fillStyle = '#90a2b7';
  context.textAlign = 'center';
  context.fillText(message, width / 2, height / 2);
}

function drawPlot(plot) {
  if (!plot) return drawEmptyPlot('No data.');
  const frame = canvasContext();
  const { context: ctx, width, height } = frame;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = '#0a1119'; ctx.fillRect(0, 0, width, height);
  drawGrid(ctx, width, height);
  if (plot.mode === 'time' || plot.mode === 'scope') drawLineSeries(ctx, width, height, plot.series || [], plot.mode === 'scope');
  else if (plot.mode === 'bar') drawBars(ctx, width, height, plot.labels || [], plot.values || []);
  else if (plot.mode === 'histogram') drawHistograms(ctx, width, height, plot.series || []);
  else if (plot.mode === 'spectrum') drawSpectrum(ctx, width, height, plot.series || []);
  else if (plot.mode === 'logic') drawLogic(ctx, width, height, plot.series || []);
  renderLegend(plot);
}

function drawGrid(ctx, width, height) {
  ctx.strokeStyle = 'rgba(90,120,150,.18)'; ctx.lineWidth = 1;
  for (let x = 45; x < width - 10; x += Math.max(40, (width - 55) / 10)) { ctx.beginPath(); ctx.moveTo(x, 10); ctx.lineTo(x, height - 30); ctx.stroke(); }
  for (let y = 10; y < height - 30; y += Math.max(30, (height - 40) / 8)) { ctx.beginPath(); ctx.moveTo(45, y); ctx.lineTo(width - 10, y); ctx.stroke(); }
}

function drawLineSeries(ctx, width, height, series, scopeMode) {
  const numeric = series.flatMap(item => item.y.map((value, index) => ({ x: Number(item.x[index]), y: Number(value) })).filter(point => Number.isFinite(point.x) && Number.isFinite(point.y)));
  if (!numeric.length) return;
  const minX = Math.min(...numeric.map(point => point.x)), maxX = Math.max(...numeric.map(point => point.x));
  let minY = Math.min(...numeric.map(point => point.y)), maxY = Math.max(...numeric.map(point => point.y));
  if (scopeMode) { const maxAbs = Math.max(Math.abs(minY), Math.abs(maxY), 1e-9); minY = -maxAbs; maxY = maxAbs; }
  if (minY === maxY) { minY -= 1; maxY += 1; }
  series.forEach((item, colorIndex) => {
    ctx.strokeStyle = colors[colorIndex % colors.length]; ctx.lineWidth = scopeMode ? 2 : 1.7; ctx.beginPath();
    let started = false;
    item.y.forEach((raw, index) => {
      const xValue = Number(item.x[index]), yValue = Number(raw);
      if (!Number.isFinite(xValue) || !Number.isFinite(yValue)) return;
      const x = 45 + ((xValue - minX) / Math.max(maxX - minX, 1e-9)) * (width - 60);
      const y = 10 + (1 - (yValue - minY) / (maxY - minY)) * (height - 45);
      if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
    });
    ctx.stroke();
  });
  drawAxisLabels(ctx, width, height, minX, maxX, minY, maxY);
}

function drawBars(ctx, width, height, labels, values) {
  const numeric = values.map(Number); const max = Math.max(...numeric.map(value => Math.abs(value)), 1);
  const barWidth = (width - 65) / Math.max(labels.length, 1);
  numeric.forEach((value, index) => {
    const x = 50 + index * barWidth + 4; const h = Math.abs(value) / max * (height - 70); const y = height - 35 - h;
    ctx.fillStyle = colors[index % colors.length]; ctx.fillRect(x, y, Math.max(3, barWidth - 8), h);
    ctx.fillStyle = '#90a2b7'; ctx.font = '11px sans-serif'; ctx.fillText(labels[index].slice(0, 14), x, height - 15);
  });
}

function drawHistograms(ctx, width, height, series) {
  const maxCount = Math.max(1, ...series.flatMap(item => item.counts));
  series.forEach((item, colorIndex) => {
    const count = item.counts.length; const binWidth = (width - 60) / Math.max(count, 1);
    ctx.fillStyle = `${colors[colorIndex % colors.length]}88`;
    item.counts.forEach((value, index) => {
      const h = value / maxCount * (height - 55);
      ctx.fillRect(45 + index * binWidth, height - 35 - h, Math.max(1, binWidth - 1), h);
    });
  });
}

function drawSpectrum(ctx, width, height, series) {
  const lineSeries = series.map(item => ({ channel: item.channel, x: item.frequency, y: item.magnitude }));
  drawLineSeries(ctx, width, height, lineSeries, false);
  ctx.fillStyle = '#90a2b7'; ctx.fillText('Frequency (Hz)', width - 90, height - 12);
}

function drawLogic(ctx, width, height, series) {
  const transitions = series.flatMap(item => item.transitions);
  if (!transitions.length) return;
  const minX = Math.min(...transitions.map(item => item.timestamp)); const maxX = Math.max(...transitions.map(item => item.timestamp));
  const laneHeight = (height - 40) / Math.max(series.length, 1);
  series.forEach((item, index) => {
    const base = 15 + index * laneHeight + laneHeight * .7;
    ctx.strokeStyle = colors[index % colors.length]; ctx.lineWidth = 2; ctx.beginPath();
    let previousX = 45; let previousY = base;
    item.transitions.forEach(transition => {
      const x = 45 + ((transition.timestamp - minX) / Math.max(maxX - minX, 1e-9)) * (width - 60);
      const y = base - (transition.value ? laneHeight * .45 : 0);
      ctx.lineTo(x, previousY); ctx.lineTo(x, y); previousX = x; previousY = y;
    });
    ctx.lineTo(width - 10, previousY); ctx.stroke();
    ctx.fillStyle = '#90a2b7'; ctx.fillText(item.channel, 4, base - laneHeight * .18);
  });
}

function drawAxisLabels(ctx, width, height, minX, maxX, minY, maxY) {
  ctx.fillStyle = '#90a2b7'; ctx.font = '10px sans-serif';
  ctx.fillText(maxY.toPrecision(4), 4, 16); ctx.fillText(minY.toPrecision(4), 4, height - 32);
  ctx.fillText(new Date(minX * 1000).toLocaleTimeString(), 45, height - 12);
  ctx.textAlign = 'right'; ctx.fillText(new Date(maxX * 1000).toLocaleTimeString(), width - 10, height - 12); ctx.textAlign = 'left';
}

function renderLegend(plot) {
  const names = plot.series ? plot.series.map(item => item.channel) : plot.labels || [];
  $('#plotLegend').innerHTML = names.map((name, index) => `<span class="legend-item" style="--legend-color:${colors[index % colors.length]}">${escapeHtml(name)}</span>`).join('');
}

function renderPlotStats(points) {
  const channels = [...new Set(points.map(point => point.channel))];
  const numeric = points.map(point => Number(point.value)).filter(Number.isFinite);
  $('#plotStats').innerHTML = [
    ['Points', points.length], ['Channels', channels.length], ['Minimum', numeric.length ? Math.min(...numeric).toPrecision(6) : '—'],
    ['Maximum', numeric.length ? Math.max(...numeric).toPrecision(6) : '—'], ['Mean', numeric.length ? (numeric.reduce((a,b)=>a+b,0)/numeric.length).toPrecision(6) : '—']
  ].map(([label, value]) => `<div class="stat"><span>${label}</span><strong>${value}</strong></div>`).join('');
}

function installFirmwareEvents() {
  $('#uploadFirmware').addEventListener('click', uploadFirmware);
  $('#analyzeFirmware').addEventListener('click', analyzeFirmware);
  $$('.report-tabs button').forEach(button => button.addEventListener('click', () => {
    state.reportTab = button.dataset.report;
    $$('.report-tabs button').forEach(item => item.classList.toggle('active', item === button));
    renderFirmwareReport();
  }));
}

async function uploadFirmware() {
  const file = $('#firmwareUpload').files[0];
  if (!file) return toast('Choose a firmware file', true);
  const form = new FormData(); form.append('file', file);
  try {
    $('#firmwareProgress').textContent = 'Storing locally…';
    const asset = await api('/api/assets', { method: 'POST', body: form });
    await loadAssets(); $('#firmwareAsset').value = asset.id; $('#sessionFirmware').value = asset.id;
    $('#firmwareProgress').textContent = `Stored as ${asset.sha256.slice(0, 12)}…`;
    toast('Firmware stored locally');
  } catch (error) { toast(error.message, true); }
}

async function analyzeFirmware() {
  const assetId = Number($('#firmwareAsset').value);
  if (!assetId) return toast('Select a firmware asset', true);
  try {
    $('#firmwareProgress').textContent = 'Analyzing…';
    const response = await api('/api/firmware/analyze', { method: 'POST', body: JSON.stringify({ asset_id: assetId, architecture: $('#firmwareArch').value || null }) });
    state.firmwareReport = response.report; state.reportTab = 'summary';
    $$('.report-tabs button').forEach(button => button.classList.toggle('active', button.dataset.report === 'summary'));
    renderFirmwareReport(); $('#firmwareProgress').textContent = `Analysis ${response.id}`;
  } catch (error) { $('#firmwareProgress').textContent = ''; toast(error.message, true); }
}

function renderFirmwareReport() {
  const report = state.firmwareReport; const output = $('#firmwareReport');
  if (!report) return;
  const tab = state.reportTab;
  if (tab === 'summary') {
    output.innerHTML = `<div class="stats-grid">
      ${[['Format', report.format], ['Size', formatBytes(report.size)], ['Entropy', Number(report.entropy).toFixed(4)], ['SHA-256', report.sha256.slice(0, 16)+'…']].map(([label,value])=>`<div class="stat"><span>${label}</span><strong>${escapeHtml(value)}</strong></div>`).join('')}
    </div><h3>Findings</h3>${report.findings.length ? `<table>${report.findings.map(item=>`<tr><td>${escapeHtml(item.severity)}</td><td>${escapeHtml(item.message)}</td><td>0x${Number(item.offset).toString(16)}</td></tr>`).join('')}</table>` : '<p class="muted">No heuristic findings.</p>'}
    <h3>Container metadata</h3><pre>${escapeHtml(JSON.stringify(report.elf || report.uf2 || report.intel_hex || report.raw, null, 2).slice(0, 40000))}</pre>`;
  } else if (tab === 'sections') output.innerHTML = tableFromObjects(report.elf?.sections || [], ['name','type','address','size','entropy']);
  else if (tab === 'symbols') output.innerHTML = tableFromObjects(report.elf?.symbols || [], ['name','address','size','bind','type']);
  else if (tab === 'strings') output.innerHTML = tableFromObjects(report.strings || [], ['offset','encoding','value']);
  else if (tab === 'disassembly') {
    const sections = report.elf?.executable_sections || [];
    const rows = sections.flatMap(section => (section.disassembly || []).map(item => ({ section: section.name, ...item })));
    output.innerHTML = tableFromObjects(rows, ['section','address','bytes','mnemonic','operands']);
  } else if (tab === 'hexdump') output.innerHTML = `<pre>${escapeHtml(report.hexdump_preview)}</pre>`;
}

function tableFromObjects(rows, columns) {
  if (!rows.length) return '<div class="empty-state">No data available. Install optional analysis dependencies when applicable.</div>';
  return `<table><thead><tr>${columns.map(column=>`<th>${escapeHtml(column)}</th>`).join('')}</tr></thead><tbody>${rows.slice(0,5000).map(row=>`<tr>${columns.map(column=>`<td>${escapeHtml(typeof row[column] === 'number' && column.includes('address') ? '0x'+row[column].toString(16) : row[column])}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
}

function installEmulationEvents() {
  $('#startSession').addEventListener('click', startSession);
}

async function loadSessions() {
  state.sessions = await api('/api/sessions?limit=500');
  const sessionOptions = '<option value="">Select…</option>' + state.sessions.map(session => `<option value="${session.id}">${session.status} · ${session.target} · ${formatTime(session.started_at)}</option>`).join('');
  $('#monitorSession').innerHTML = sessionOptions;
  $('#logSession').innerHTML = '<option value="">All</option>' + sessionOptions.replace('<option value="">Select…</option>', '');
  $('#sessionRows').innerHTML = state.sessions.map(session => `
    <tr><td><span class="pill ${session.status === 'running' ? 'ok' : ''}">${escapeHtml(session.status)}</span></td><td>${escapeHtml(session.target)}</td><td>${formatTime(session.started_at)}</td><td><code>${escapeHtml((session.command || []).join(' '))}</code></td><td>${session.status === 'running' || session.status === 'starting' ? `<button data-stop="${session.id}" class="danger">Stop</button>` : ''}</td></tr>`).join('');
  $$('[data-stop]').forEach(button => button.addEventListener('click', () => stopSession(button.dataset.stop)));
}

async function startSession() {
  const payload = {
    target: $('#sessionTarget').value,
    firmware_asset_id: Number($('#sessionFirmware').value) || null,
    platform_asset_id: Number($('#sessionPlatform').value) || null,
    script_asset_id: Number($('#sessionScript').value) || null,
    extra_args: shellSplit($('#sessionArgs').value),
  };
  try {
    const session = await api('/api/sessions', { method: 'POST', body: JSON.stringify(payload) });
    await loadSessions(); $('#monitorSession').value = session.id; toast(`Session ${session.id.slice(0, 8)} started`);
  } catch (error) { toast(error.message, true); }
}

async function stopSession(sessionId) {
  try { await api(`/api/sessions/${sessionId}/stop`, { method: 'POST' }); await loadSessions(); toast('Session stopped'); }
  catch (error) { toast(error.message, true); }
}

function shellSplit(value) {
  const result = []; let current = ''; let quote = null;
  for (let index=0; index<value.length; index++) {
    const character = value[index];
    if (quote) { if (character === quote) quote = null; else current += character; }
    else if (character === '"' || character === "'") quote = character;
    else if (/\s/.test(character)) { if (current) { result.push(current); current=''; } }
    else current += character;
  }
  if (current) result.push(current); return result;
}

function installLogEvents() {
  $('#refreshLogs').addEventListener('click', refreshLogs);
  $('#exportLogs').addEventListener('click', exportLogs);
}

async function refreshLogs() {
  const params = new URLSearchParams();
  if ($('#logSession').value) params.set('session_id', $('#logSession').value);
  if ($('#logLevel').value) params.set('level', $('#logLevel').value);
  if ($('#logSource').value) params.set('source', $('#logSource').value);
  if ($('#logSearch').value) params.set('search', $('#logSearch').value);
  params.set('limit', '20000');
  try { state.logs = await api(`/api/logs?${params}`); renderLogs(); }
  catch (error) { toast(error.message, true); }
}

function renderLogs() {
  $('#logOutput').innerHTML = state.logs.map(log => `<div class="log-line ${escapeHtml(log.level)}"><span>${formatTime(log.timestamp)}</span><span>${escapeHtml(log.level)}</span><span>${escapeHtml(log.source)}</span><span>${escapeHtml(log.message)}</span></div>`).join('') || '<div class="empty-state">No matching log records.</div>';
}

function exportLogs() {
  const rows = [['timestamp','level','source','session_id','message'], ...state.logs.map(log => [log.timestamp, log.level, log.source, log.session_id || '', log.message])];
  const csv = rows.map(row => row.map(value => `"${String(value).replaceAll('"','""')}"`).join(',')).join('\n');
  const link = document.createElement('a'); link.href = URL.createObjectURL(new Blob([csv], {type:'text/csv'})); link.download = 'vsd-logs.csv'; link.click(); URL.revokeObjectURL(link.href);
}

async function downloadBundledAsset(name) {
  try {
    const response = await fetch(`/api/bundled-assets/${encodeURIComponent(name)}`);
    if (!response.ok) throw new Error(await response.text());
    const link = document.createElement('a');
    link.href = URL.createObjectURL(await response.blob());
    link.download = name;
    link.click();
    URL.revokeObjectURL(link.href);
  } catch (error) { toast(error.message, true); }
}

function installSettingsEvents() {
  $('#apiToken').value = state.token;
  $('#downloadComponentModels').addEventListener('click', () => downloadBundledAsset('renode-external-components-overlay.zip'));
  $('#saveToken').addEventListener('click', () => {
    state.token = $('#apiToken').value.trim();
    if (state.token) localStorage.setItem('vsdStudioToken', state.token); else localStorage.removeItem('vsdStudioToken');
    toast('Browser token updated');
  });
}

bootstrap();
