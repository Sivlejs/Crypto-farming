/* ── Nexus AI Dashboard – JavaScript ──────────────────────── */
'use strict';

// ── Socket.IO ───────────────────────────────────────────────
const socket = io({ transports: ['websocket', 'polling'] });

socket.on('connect', () => {
  // Mark as connected to server; actual agent status updated via status_update
  setStatus('connecting');
  socket.emit('request_update');
});

socket.on('disconnect', () => setStatus('offline'));

socket.on('status_update',        data => renderStatus(data));
socket.on('opportunities_update', data => renderOpportunities(data));
socket.on('trades_update',        data => renderTrades(data));

// ── Helpers ─────────────────────────────────────────────────
const $ = id => document.getElementById(id);

function setStatus(state) {
  const badge = $('status-badge');
  if (state === 'online') {
    badge.textContent = '● Online';
    badge.className   = 'badge badge--online';
  } else if (state === 'connecting') {
    badge.textContent = '● Connecting…';
    badge.className   = 'badge badge--info';
  } else {
    badge.textContent = '● Offline';
    badge.className   = 'badge badge--offline';
  }
}

function fmt(n, decimals = 2) {
  if (n == null) return '–';
  return Number(n).toFixed(decimals);
}

function fmtUSD(n) {
  if (n == null) return '–';
  return '$' + Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 4 });
}

function fmtBig(n) {
  if (!n) return '$0';
  if (n >= 1e6) return '$' + (n / 1e6).toFixed(2) + 'M';
  if (n >= 1e3) return '$' + (n / 1e3).toFixed(1) + 'K';
  return '$' + Number(n).toFixed(0);
}

function typeLabel(type) {
  const labels = {
    arbitrage:       'Arbitrage',
    yield_farming:   'Yield',
    liquidity_mining:'Liquidity',
  };
  return labels[type] || type;
}

function timeAgo(ts) {
  const diff = (Date.now() / 1000) - ts;
  if (diff < 60) return Math.round(diff) + 's ago';
  if (diff < 3600) return Math.round(diff / 60) + 'm ago';
  return Math.round(diff / 3600) + 'h ago';
}

function shortHash(hash) {
  if (!hash || hash.startsWith('0xsim')) return 'DRY RUN';
  return hash.slice(0, 10) + '…';
}

// ── Status renderer ──────────────────────────────────────────
function renderStatus(data) {
  if (!data) return;

  // Uptime
  const uptime = data.uptime_seconds || 0;
  const h = Math.floor(uptime / 3600);
  const m = Math.floor((uptime % 3600) / 60);
  const s = uptime % 60;
  $('uptime').textContent = `Up ${h}h ${m}m ${s}s`;

  // Dry run badge
  const dryBadge = $('dry-run-badge');
  dryBadge.style.display = data.dry_run ? 'inline-flex' : 'none';

  // Stats row
  const rewards  = data.rewards  || {};
  const monitor  = data.monitor  || {};
  $('total-profit').textContent = fmtUSD(rewards.estimated_total_profit_usd);
  $('total-trades').textContent = rewards.successful_trades || 0;
  $('total-found').textContent  = monitor.total_found || 0;

  // Chain connections
  const chains = data.blockchain || {};
  let connectedCount = 0;
  const chainEl = $('chain-list');
  chainEl.innerHTML = '';
  for (const [key, info] of Object.entries(chains)) {
    if (!info.enabled) continue;
    if (info.connected) connectedCount++;
    const item = document.createElement('div');
    item.className = 'chain-item';
    item.innerHTML = `
      <div>
        <span class="chain-name">${info.name || key}</span>
        <span class="${info.connected ? 'chain-connected' : 'chain-disconnected'}">
          ${info.connected ? ' ●' : ' ○'}
        </span>
      </div>
      <div class="chain-meta">
        <span>Block ${(info.block || 0).toLocaleString()}</span>
        <span>${fmt(info.gas_gwei)} Gwei</span>
      </div>`;
    chainEl.appendChild(item);
  }
  $('active-chains').textContent = connectedCount;

  // Config panel
  const cfg = data.config || {};
  const strategies = cfg.strategies || {};
  const configItems = [
    ['Min Profit',   fmtUSD(cfg.min_profit_usd)],
    ['Max Gas',      fmt(cfg.max_gas_gwei) + ' Gwei'],
    ['Slippage',     fmt(cfg.slippage_percent) + '%'],
    ['Max Trade',    fmtUSD(cfg.max_trade_usd)],
    ['Arbitrage',    strategies.arbitrage    ? 'ON' : 'OFF'],
    ['Yield Farming',strategies.yield_farming? 'ON' : 'OFF'],
    ['LP Mining',    strategies.liquidity_mining? 'ON' : 'OFF'],
    ['Wallet',       cfg.wallet_configured ? 'Configured' : 'Not Set'],
  ];
  $('config-panel').innerHTML = configItems.map(([k, v]) => {
    const cls = v === 'ON' || v === 'Configured' ? 'config-val--on'
              : v === 'OFF' || v === 'Not Set'   ? 'config-val--off'
              : '';
    return `<div class="config-item"><span class="config-key">${k}:</span><span class="config-val ${cls}">${v}</span></div>`;
  }).join('');

  // Scan counter
  $('scan-counter').textContent = monitor.scan_count ? `Scan #${monitor.scan_count}` : '';
}

// ── Opportunities renderer ───────────────────────────────────
function renderOpportunities(opps) {
  const el = $('opportunity-list');
  if (!opps || opps.length === 0) {
    el.innerHTML = '<div class="empty">No opportunities found yet…</div>';
    return;
  }
  el.innerHTML = opps.map(o => {
    const executed = o.executed ? ' opp-executed' : '';
    return `
      <div class="opp-item${executed}">
        <div class="opp-header">
          <span class="opp-type opp-type--${o.type}">${typeLabel(o.type)}</span>
          <span class="opp-chain">${o.chain}</span>
          <span class="opp-profit">${fmtUSD(o.estimated_profit_usd)}</span>
        </div>
        <div class="opp-desc">${o.description}</div>
        <div class="opp-confidence">Confidence: ${fmt(o.confidence * 100)}% · Score: ${fmt(o.score)} · ${timeAgo(o.timestamp)}</div>
      </div>`;
  }).join('');
}

// ── Trades renderer ──────────────────────────────────────────
function renderTrades(trades) {
  const el = $('trade-list');
  if (!trades || trades.length === 0) {
    el.innerHTML = '<div class="empty">No trades executed yet…</div>';
    return;
  }
  el.innerHTML = trades.map(t => {
    const profitClass = t.success ? 'trade-profit' : 'trade-profit trade-profit--fail';
    const profitText  = t.success ? fmtUSD(t.estimated_profit_usd) : 'Failed';
    return `
      <div class="trade-item">
        <div class="trade-desc" title="${t.description}">${t.description}</div>
        ${t.dry_run ? '<span class="trade-dry">SIM</span>' : ''}
        <span class="${profitClass}">${profitText}</span>
        <span class="trade-hash">${shortHash(t.tx_hash)}</span>
      </div>`;
  }).join('');
}

// ── Yield rates ──────────────────────────────────────────────
async function loadYields() {
  try {
    const res  = await fetch('/api/yields');
    const data = await res.json();
    const el = $('yield-list');
    if (!data || data.length === 0) {
      el.innerHTML = '<div class="empty">No yield data available</div>';
      return;
    }
    el.innerHTML = data.slice(0, 15).map(p => `
      <div class="yield-item">
        <div>
          <span class="yield-protocol">${p.protocol}</span>
          <span class="yield-symbol"> ${p.symbol}</span>
        </div>
        <div style="display:flex;gap:.8rem;align-items:center">
          <span class="yield-tvl">${fmtBig(p.tvl_usd)}</span>
          <span class="yield-apy">${fmt(p.apy)}%</span>
        </div>
      </div>`).join('');
  } catch (e) {
    $('yield-list').innerHTML = '<div class="empty">Could not load yield rates</div>';
  }
}

// ── Polling fallback (if WebSocket not available) ────────────
async function pollStatus() {
  try {
    const res  = await fetch('/api/status');
    const data = await res.json();
    renderStatus(data);
  } catch (_) {}

  try {
    const res  = await fetch('/api/opportunities?limit=10');
    const data = await res.json();
    renderOpportunities(data);
  } catch (_) {}

  try {
    const res  = await fetch('/api/trades?limit=10');
    const data = await res.json();
    renderTrades(data);
  } catch (_) {}
}

// ── Init ─────────────────────────────────────────────────────
loadYields();
setInterval(loadYields, 60_000);      // Refresh yield table every minute
setInterval(pollStatus, 15_000);      // Fallback polling every 15 s
pollStatus();                          // Immediate first fetch
