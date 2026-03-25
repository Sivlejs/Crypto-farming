'use strict';
/* ═══════════════════════════════════════════════════════════════
   Nexus AI Dashboard  –  Full-featured real-time controller
═══════════════════════════════════════════════════════════════ */

// ── State ────────────────────────────────────────────────────
let _status       = {};
let _opps         = [];
let _trades       = [];
let _payoutStatus = {};
let _payoutHist   = [];
let _yields       = [];
let _profitHistory= [];   // [{label, value}]
let _priceHistory = {};   // {symbol: [prices]}

// ── Charts ───────────────────────────────────────────────────
let profitChart, stratPieChart, priceChart;

// ── Socket.IO ────────────────────────────────────────────────
const socket = io({ transports: ['websocket', 'polling'] });

socket.on('connect',              ()    => { setStatus('connecting'); socket.emit('request_update'); });
socket.on('disconnect',           ()    => setStatus('offline'));
socket.on('status_update',        data  => { _status = data; renderAll(); });
socket.on('opportunities_update', data  => { _opps   = data; renderOpportunities(); });
socket.on('trades_update',        data  => { _trades = data; renderTrades(); renderTradesMini(); });
socket.on('payout_update',        data  => { _payoutStatus = data; renderPayout(); });

// ── Helpers ───────────────────────────────────────────────────
const $  = id  => document.getElementById(id);
const qs = sel => document.querySelector(sel);

function fmt(n, d=2){ return n == null ? '–' : Number(n).toFixed(d); }
function fmtUSD(n){
  if (n == null) return '–';
  return '$' + Number(n).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:4});
}
function fmtBig(n){
  if (!n) return '$0';
  if (n>=1e9) return '$'+(n/1e9).toFixed(2)+'B';
  if (n>=1e6) return '$'+(n/1e6).toFixed(2)+'M';
  if (n>=1e3) return '$'+(n/1e3).toFixed(1)+'K';
  return '$'+Number(n).toFixed(0);
}
function timeAgo(ts){
  if (!ts) return '';
  const d = Date.now()/1000 - ts;
  if (d<60)    return Math.round(d)+'s ago';
  if (d<3600)  return Math.round(d/60)+'m ago';
  if (d<86400) return Math.round(d/3600)+'h ago';
  return new Date(ts*1000).toLocaleDateString();
}
function shortHash(h){
  if (!h || h.includes('sim') || h==='failed') return h || '–';
  return h.slice(0,10)+'…'+h.slice(-6);
}
function explorerUrl(chain, hash){
  const base = {ethereum:'https://etherscan.io/tx/',bsc:'https://bscscan.com/tx/',polygon:'https://polygonscan.com/tx/'};
  return (base[chain] || '#') + hash;
}
function typeBadge(type){
  return `<span class="opp-type-badge ${type}">${type.replace('_',' ')}</span>`;
}
function toast(msg, kind='info'){
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.textContent = msg;
  $('toast-container').appendChild(el);
  setTimeout(()=>el.remove(), 4000);
}

// ── Tab navigation ────────────────────────────────────────────
function switchTab(name){
  document.querySelectorAll('.tab-content').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n=>n.classList.remove('active'));
  const tab = $(`tab-${name}`);
  if (tab) tab.classList.add('active');
  document.querySelectorAll(`.nav-item[data-tab="${name}"]`).forEach(n=>n.classList.add('active'));
  if (name==='prices')   loadYields();
  if (name==='settings') renderSettings();
}
document.querySelectorAll('.nav-item').forEach(n=>{
  n.addEventListener('click', e=>{ e.preventDefault(); switchTab(n.dataset.tab); });
});

function toggleSidebar(){
  document.getElementById('sidebar').classList.toggle('open');
}

// ── Status badge ──────────────────────────────────────────────
function setStatus(state){
  const el = $('sb-status');
  el.className = `sb-status ${state}`;
  el.textContent = state==='online'?'● Online': state==='connecting'?'● Connecting…':'● Offline';
}

// ── Master render ─────────────────────────────────────────────
function renderAll(){
  if (!_status) return;
  const r  = _status.rewards  || {};
  const m  = _status.monitor  || {};
  const p  = _status.payout   || {};
  const cfg= _status.config   || {};

  // Sidebar
  const uptime = _status.uptime_seconds || 0;
  $('sb-uptime').textContent = `Up ${Math.floor(uptime/3600)}h ${Math.floor((uptime%3600)/60)}m`;
  const dryBadge = $('sb-dry-run');
  dryBadge.style.display = _status.dry_run ? 'inline-block' : 'none';
  setStatus(_status.running ? 'online' : 'offline');

  // Start/stop buttons
  const startBtn = $('start-btn');
  const stopBtn  = $('stop-btn');
  if (_status.running) { startBtn.style.display='none'; stopBtn.style.display='inline-flex'; }
  else                 { startBtn.style.display='inline-flex'; stopBtn.style.display='none'; }

  // KPIs
  setKpi('kpi-profit',    fmtUSD(r.estimated_total_profit_usd));
  setKpi('kpi-trades',    r.successful_trades || 0);
  setKpi('kpi-opps',      m.total_found || 0);
  setKpi('kpi-paid',      fmtUSD(p.total_paid_usd));
  setKpi('kpi-flashbots', _status.flashbots_ready ? '✓ Ready' : '✗ Off');
  setKpi('kpi-scan',      m.scan_count || 0);
  if ($('kpi-flashbots')) {
    $('kpi-flashbots').querySelector('.kpi-value').style.color =
      _status.flashbots_ready ? 'var(--green)' : 'var(--text-dim)';
  }

  // Chains
  renderChains(_status.blockchain || {});

  // Payout
  _payoutStatus = p;
  renderPayout();

  // Strategies
  renderStrategyCards(_status);

  // Profit chart update
  if (r.estimated_total_profit_usd) {
    _profitHistory.push({
      label: new Date().toLocaleTimeString(),
      value: r.estimated_total_profit_usd || 0
    });
    if (_profitHistory.length > 30) _profitHistory.shift();
    updateProfitChart();
  }
}

function setKpi(id, val){
  const el = $(id);
  if (el) el.querySelector('.kpi-value').textContent = val;
}

// ── Blockchain connections ─────────────────────────────────────
function renderChains(chains){
  const el = $('chain-list');
  if (!el) return;
  el.innerHTML = '';
  for (const [key, info] of Object.entries(chains)){
    if (!info.enabled) continue;
    const on = info.connected;
    el.innerHTML += `
      <div class="chain-item">
        <div class="chain-left">
          <div class="chain-dot ${on?'on':'off'}"></div>
          <span class="chain-name">${info.name||key}</span>
        </div>
        <div class="chain-meta">
          <div>Block ${(info.block||0).toLocaleString()}</div>
          <div>${fmt(info.gas_gwei)} Gwei</div>
        </div>
      </div>`;
  }
}

// ── Opportunity ticker ────────────────────────────────────────
function renderOpportunities(){
  const el = $('opp-ticker');
  if (!el) return;
  $('opp-scan-label').textContent = (_status.monitor||{}).scan_count
    ? `Scan #${_status.monitor.scan_count}` : '';

  if (!_opps || !_opps.length){
    el.innerHTML = '<div class="empty-state">Scanning for opportunities…</div>';
    return;
  }
  el.innerHTML = _opps.slice(0,15).map(o => {
    const isFlash = (o.details||{}).strategy === 'flash_arbitrage';
    return `
      <div class="opp-item ${o.executed?'executed':''}">
        <div class="opp-row1">
          <div style="display:flex;gap:.35rem;align-items:center;flex-wrap:wrap">
            ${typeBadge(o.type)}
            <span class="opp-chain-badge">${o.chain}</span>
            ${isFlash?'<span class="opp-flash-badge">⚡FLASH</span>':''}
          </div>
          <span class="opp-profit">${fmtUSD(o.estimated_profit_usd)}</span>
        </div>
        <div class="opp-desc">${o.description}</div>
        <div class="opp-conf">Confidence ${fmt(o.confidence*100)}% · Score ${fmt(o.score)} · ${timeAgo(o.timestamp)}</div>
      </div>`;
  }).join('');

  // Also update top-opps on strategies tab
  const topEl = $('top-opps');
  if (topEl) topEl.innerHTML = el.innerHTML;
}

// ── Trades mini table ─────────────────────────────────────────
function renderTradesMini(){
  const tbody = $('recent-trades-mini-body');
  if (!tbody) return;
  tbody.innerHTML = _trades.slice(0,8).map(t => tradeRow(t)).join('');
}

function renderTrades(){
  const tbody = $('trades-body');
  if (!tbody) return;
  const cf = ($('trade-filter-chain')||{}).value || '';
  const tf = ($('trade-filter-type')||{}).value  || '';
  const filtered = _trades.filter(t =>
    (!cf || t.chain === cf) && (!tf || t.opp_type === tf)
  );
  tbody.innerHTML = filtered.map(t => tradeRow(t, true)).join('');
}

function filterTrades(){ renderTrades(); }

function tradeRow(t, full=false){
  const profit  = t.success ? fmtUSD(t.estimated_profit_usd) : '–';
  const cls     = t.success ? '' : ' fail';
  const modeBadge = t.dry_run ? '<span class="badge-sim">SIM</span>' : '<span class="badge-live">LIVE</span>';
  const statusBadge = t.success ? '<span class="badge-ok">✓</span>' : '<span class="badge-fail">✗</span>';
  const hash = t.tx_hash && !t.tx_hash.includes('sim') && t.tx_hash !== 'failed'
    ? `<a href="${explorerUrl(t.chain, t.tx_hash)}" target="_blank">${shortHash(t.tx_hash)}</a>`
    : `<span>${shortHash(t.tx_hash)}</span>`;

  if (full) return `
    <tr>
      <td>${timeAgo(t.timestamp)}</td>
      <td>${typeBadge(t.opp_type)}</td>
      <td style="text-transform:capitalize">${t.chain}</td>
      <td style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${t.description}">${t.description}</td>
      <td class="td-profit${cls}">${profit}</td>
      <td>${modeBadge}</td>
      <td>${statusBadge}</td>
      <td class="td-hash">${hash}</td>
    </tr>`;

  return `
    <tr>
      <td>${typeBadge(t.opp_type)}</td>
      <td style="max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${t.description}">${t.description}</td>
      <td style="text-transform:capitalize">${t.chain}</td>
      <td class="td-profit${cls}">${profit}</td>
      <td>${statusBadge}</td>
      <td class="td-hash">${hash}</td>
    </tr>`;
}

// ── Payout panel ──────────────────────────────────────────────
function renderPayout(){
  const p = _payoutStatus;
  if (!p) return;

  // KPIs
  setKpi('pkpi-pending',   fmtUSD(p.pending_usd));
  setKpi('pkpi-total',     fmtUSD(p.total_paid_usd));
  setKpi('pkpi-count',     p.payout_count || 0);
  setKpi('pkpi-threshold', fmtUSD(p.threshold_usd));

  // Progress bar
  const pct = p.threshold_usd > 0
    ? Math.min(100, (p.pending_usd / p.threshold_usd) * 100) : 0;
  const bar   = $('progress-bar');
  const pctEl = $('progress-pct');
  const hint  = $('progress-hint');
  if (bar)   bar.style.width   = pct + '%';
  if (pctEl) pctEl.textContent = fmt(pct) + '%';
  if (hint)  hint.textContent  = pct>=100
    ? '🚀 Sweep triggered!' : `$${fmt(p.pending_usd)} of $${fmt(p.threshold_usd)} threshold`;

  // Destination panel
  const destEl = $('payout-destination-panel');
  if (!destEl) return;
  const mode = p.payout_mode || 'unconfigured';
  const modeLabels = {
    coinbase_api: '🔵 Coinbase API',
    onchain:      '⛓ On-chain transfer',
    lightning:    '⚡ Lightning / Cash App',
    dry_run:      '🧪 Dry run (simulated)',
    unconfigured: '⚠️ Not configured',
  };
  destEl.innerHTML = `
    <div class="dest-row"><span class="dest-label">Mode</span>
      <span class="dest-val ${mode==='unconfigured'?'warn':'on'}">${modeLabels[mode]||mode}</span></div>
    <div class="dest-row"><span class="dest-label">Address / LN</span>
      <span class="dest-val">${p.payout_address||'not set'}</span></div>
    <div class="dest-row"><span class="dest-label">Chain</span>
      <span class="dest-val">${p.payout_chain||'–'}</span></div>
    <div class="dest-row"><span class="dest-label">Token</span>
      <span class="dest-val">${(p.payout_mode||'').includes('coinbase')?'via Coinbase':p.payout_chain+' USDC'}</span></div>
    <div class="dest-row"><span class="dest-label">Coinbase API</span>
      <span class="dest-val ${p.coinbase_configured?'on':'warn'}">${p.coinbase_configured?'Configured':'Not set'}</span></div>
    <div class="dest-row"><span class="dest-label">Lightning / Cash App</span>
      <span class="dest-val ${p.lightning_configured?'on':'warn'}">${p.lightning_configured?p.payout_address:'Not set'}</span></div>
  `;
}

function renderPayoutHistory(){
  const tbody = $('payout-history-body');
  if (!tbody) return;
  if (!_payoutHist.length){
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:1rem;color:var(--text-dim)">No payouts yet…</td></tr>';
    return;
  }
  tbody.innerHTML = _payoutHist.map(p => `
    <tr>
      <td>${timeAgo(p.timestamp)}</td>
      <td class="td-profit ${p.success?'':'fail'}">${fmtUSD(p.amount_usd)}</td>
      <td>${p.method||'–'}</td>
      <td class="td-hash">${(p.destination||'').slice(0,20)}…</td>
      <td class="td-hash">${(p.tx_ref||'').slice(0,20)}${p.tx_ref&&p.tx_ref.length>20?'…':''}</td>
      <td>${p.success?'<span class="badge-ok">✓</span>':'<span class="badge-fail">✗</span>'}</td>
    </tr>`).join('');
}

// ── Strategy cards ────────────────────────────────────────────
const STRAT_META = {
  arbitrage:       {icon:'🔀', name:'DEX Arbitrage',       desc:'Cross-DEX price gaps'},
  flash_arbitrage: {icon:'⚡', name:'Flash Arbitrage',     desc:'Capital-free flash loans'},
  yield_farming:   {icon:'🌾', name:'Yield Farming',       desc:'Highest APY protocols'},
  liquidity_mining:{icon:'💧', name:'Liquidity Mining',    desc:'LP fee + reward pools'},
  liquidation:     {icon:'🏦', name:'Liquidation Bot',     desc:'Aave undercollateral'},
};

function renderStrategyCards(data){
  const el = $('strategy-cards');
  if (!el) return;
  const cfg        = data.config     || {};
  const strategies = cfg.strategies  || {};
  const byType     = (data.rewards||{}).by_type || {};
  const allOpps    = _opps;

  const stratList = [
    {key:'arbitrage',       enabled: strategies.arbitrage},
    {key:'flash_arbitrage', enabled: strategies.arbitrage},
    {key:'yield_farming',   enabled: strategies.yield_farming},
    {key:'liquidity_mining',enabled: strategies.liquidity_mining},
    {key:'liquidation',     enabled: true},
  ];

  el.innerHTML = stratList.map(({key, enabled}) => {
    const meta   = STRAT_META[key] || {icon:'⚙️', name:key, desc:''};
    const trades = byType[key] || byType['arbitrage'] || 0;
    const pending = allOpps.filter(o => o.type === key || (key.includes('arb') && o.type==='arbitrage')).length;
    return `
      <div class="strat-card">
        <div class="strat-header">
          <span class="strat-icon">${meta.icon}</span>
          <div>
            <div class="strat-name">${meta.name}</div>
            <div style="font-size:.72rem;color:var(--text-dim)">${meta.desc}</div>
          </div>
          <span class="strat-badge ${enabled?'on':'off'}">${enabled?'ON':'OFF'}</span>
        </div>
        <div class="strat-stats">
          <div class="strat-stat">
            <div class="strat-stat-label">Opportunities</div>
            <div class="strat-stat-val">${pending}</div>
          </div>
          <div class="strat-stat">
            <div class="strat-stat-label">Executions</div>
            <div class="strat-stat-val">${trades}</div>
          </div>
        </div>
      </div>`;
  }).join('');

  // Strategy pie chart
  if (stratPieChart && Object.keys(byType).length){
    stratPieChart.data.labels  = Object.keys(byType).map(k=>k.replace('_',' '));
    stratPieChart.data.datasets[0].data = Object.values(byType);
    stratPieChart.update('none');
  }
}

// ── Prices tab ────────────────────────────────────────────────
function renderPrices(prices){
  const el = $('price-grid');
  if (!el || !prices) return;
  const KEY_TOKENS = ['ETH','BTC','BNB','MATIC','USDC','USDT','LINK','UNI','AAVE','SUSHI','CAKE'];
  el.innerHTML = KEY_TOKENS.filter(s=>prices[s]).map(s => {
    const v = prices[s];
    return `<div class="price-tile">
      <div class="price-sym">${s}</div>
      <div class="price-val">${v>=1000?fmtBig(v):'$'+fmt(v,v<1?6:2)}</div>
    </div>`;
  }).join('');

  // Track price history for chart
  const tracked = ['ETH','BTC'];
  for (const sym of tracked){
    if (prices[sym]){
      if (!_priceHistory[sym]) _priceHistory[sym]=[];
      _priceHistory[sym].push(prices[sym]);
      if (_priceHistory[sym].length>30) _priceHistory[sym].shift();
    }
  }
  updatePriceChart();
}

async function loadYields(){
  const tbody = $('yield-body');
  if (!tbody) return;
  tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:1rem;color:var(--text-dim)">Loading…</td></tr>';
  try {
    const res  = await fetch('/api/yields');
    const data = await res.json();
    if (!data.length){ tbody.innerHTML='<tr><td colspan="7" style="text-align:center;padding:1rem;color:var(--text-dim)">No data</td></tr>'; return; }
    tbody.innerHTML = data.slice(0,30).map(p=>`
      <tr>
        <td style="font-weight:600;color:var(--accent2)">${p.protocol}</td>
        <td>${p.symbol}</td>
        <td style="text-transform:capitalize">${p.chain}</td>
        <td>${fmt(p.apy_base)}%</td>
        <td style="color:var(--orange)">${fmt(p.apy_reward)}%</td>
        <td style="color:var(--green);font-weight:700">${fmt(p.apy)}%</td>
        <td>${fmtBig(p.tvl_usd)}</td>
      </tr>`).join('');
  } catch(e){
    tbody.innerHTML='<tr><td colspan="7" style="text-align:center;padding:1rem;color:var(--text-dim)">Failed to load yield data</td></tr>';
  }
}

// ── Settings tab ──────────────────────────────────────────────
function renderSettings(){
  if (!_status.config) return;
  const cfg  = _status.config;
  const pout = cfg.payout || {};

  function rows(items){
    return items.map(([k,v,cls])=>
      `<div class="setting-row"><span class="setting-key">${k}</span><span class="setting-val ${cls||''}">${v}</span></div>`
    ).join('');
  }

  $('settings-bot').innerHTML = rows([
    ['Min Profit',    fmtUSD(cfg.min_profit_usd),    ''],
    ['Max Gas',       fmt(cfg.max_gas_gwei)+' Gwei',  ''],
    ['Slippage',      fmt(cfg.slippage_percent)+'%',  ''],
    ['Max Trade',     fmtUSD(cfg.max_trade_usd),      ''],
    ['Scan Interval', cfg.scan_interval_seconds+'s',  ''],
    ['Dry Run',       cfg.dry_run?'YES':'NO',          cfg.dry_run?'warn':'on'],
    ['Wallet',        cfg.wallet_configured?'Configured':'⚠ Not Set', cfg.wallet_configured?'on':'warn'],
  ]);

  $('settings-payout').innerHTML = rows([
    ['Threshold',   fmtUSD(pout.threshold_usd),   ''],
    ['Chain',       pout.chain || '–',             ''],
    ['Token',       pout.token || '–',             ''],
    ['Address',     (pout.address||'not set').slice(0,20)+(pout.address&&pout.address.length>20?'…':''), pout.address&&pout.address!=='not set'?'on':'warn'],
    ['Coinbase API',pout.coinbase_api?'✓ Set':'Not set', pout.coinbase_api?'on':''],
    ['Lightning',   pout.lightning_address!=='not set'?'✓ Set':'Not set', pout.lightning_address!=='not set'?'on':''],
  ]);

  const st = cfg.strategies || {};
  $('settings-strategies').innerHTML = rows([
    ['Arbitrage',        st.arbitrage?'ON':'OFF',        st.arbitrage?'on':'off'],
    ['Flash Arbitrage',  st.arbitrage?'ON':'OFF',        st.arbitrage?'on':'off'],
    ['Yield Farming',    st.yield_farming?'ON':'OFF',    st.yield_farming?'on':'off'],
    ['Liquidity Mining', st.liquidity_mining?'ON':'OFF', st.liquidity_mining?'on':'off'],
    ['Liquidation Bot',  'ON',                           'on'],
    ['Flashbots',        _status.flashbots_ready?'Ready':'Not configured', _status.flashbots_ready?'on':''],
  ]);

  const ch = cfg.chains || {};
  $('settings-chains').innerHTML = rows([
    ['Ethereum', ch.ethereum?'Enabled':'Disabled', ch.ethereum?'on':'off'],
    ['BSC',      ch.bsc     ?'Enabled':'Disabled', ch.bsc?'on':'off'],
    ['Polygon',  ch.polygon ?'Enabled':'Disabled', ch.polygon?'on':'off'],
  ]);
}

// ── Charts ────────────────────────────────────────────────────
function initCharts(){
  const defaults = {
    responsive:true, maintainAspectRatio:true,
    plugins:{ legend:{ labels:{ color:'#8aa5c0', font:{size:11} } } },
    scales:{
      x:{ ticks:{color:'#5a7a99',maxTicksLimit:8}, grid:{color:'rgba(30,53,85,.6)'} },
      y:{ ticks:{color:'#5a7a99'},                 grid:{color:'rgba(30,53,85,.6)'} },
    },
  };

  // Profit chart
  const pc = $('profit-chart');
  if (pc) {
    profitChart = new Chart(pc, {
      type:'line',
      data:{
        labels:[],
        datasets:[{
          label:'Est. Profit (USD)', data:[],
          borderColor:'#00d4ff', backgroundColor:'rgba(0,212,255,.08)',
          tension:.4, fill:true, pointRadius:3,
        }]
      },
      options:{...defaults, plugins:{...defaults.plugins, title:{display:false}}},
    });
  }

  // Strategy pie chart
  const sp = $('strategy-pie');
  if (sp) {
    stratPieChart = new Chart(sp, {
      type:'doughnut',
      data:{
        labels:['Arbitrage','Yield','Liquidity','Liquidation'],
        datasets:[{
          data:[0,0,0,0],
          backgroundColor:['rgba(123,47,255,.7)','rgba(0,212,255,.7)','rgba(0,230,118,.7)','rgba(255,145,0,.7)'],
          borderColor:'#0d1526', borderWidth:2,
        }]
      },
      options:{
        responsive:true, maintainAspectRatio:true,
        plugins:{ legend:{ position:'bottom', labels:{color:'#8aa5c0',font:{size:11}} } },
      },
    });
  }

  // Price chart
  const prc = $('price-chart');
  if (prc) {
    priceChart = new Chart(prc, {
      type:'line',
      data:{
        labels:[],
        datasets:[
          {label:'ETH', data:[], borderColor:'#00d4ff', tension:.4, fill:false, pointRadius:2},
          {label:'BTC', data:[], borderColor:'#ff9100',  tension:.4, fill:false, pointRadius:2},
        ]
      },
      options:{...defaults, plugins:{...defaults.plugins}},
    });
  }
}

function updateProfitChart(){
  if (!profitChart) return;
  profitChart.data.labels   = _profitHistory.map(p=>p.label);
  profitChart.data.datasets[0].data = _profitHistory.map(p=>p.value);
  profitChart.update('none');
}

function updatePriceChart(){
  if (!priceChart) return;
  const len = Math.max((_priceHistory['ETH']||[]).length, (_priceHistory['BTC']||[]).length);
  priceChart.data.labels               = Array.from({length:len},(_,i)=>i+1+'');
  priceChart.data.datasets[0].data     = _priceHistory['ETH'] || [];
  priceChart.data.datasets[1].data     = (_priceHistory['BTC']||[]).map(v=>v/30); // scale BTC
  priceChart.data.datasets[1].label    = 'BTC÷30';
  priceChart.update('none');
}

// ── Agent controls ────────────────────────────────────────────
async function agentControl(action){
  try {
    const r = await fetch(`/api/agent/${action}`, {method:'POST'});
    const d = await r.json();
    toast(d.message || action+' sent', d.success?'success':'error');
    setTimeout(refreshAll, 1500);
  } catch(e){ toast('Request failed','error'); }
}

async function sweepNow(){
  try {
    const r = await fetch('/api/payout/sweep', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({})});
    const d = await r.json();
    toast(d.success?`Sweep: $${fmt(d.amount_usd||0)} sent!`:'Sweep failed: '+(d.reason||''), d.success?'success':'error');
    loadPayoutHistory();
  } catch(e){ toast('Sweep failed','error'); }
}

// ── Data loaders ──────────────────────────────────────────────
async function loadPayoutHistory(){
  try {
    const r = await fetch('/api/payout/history');
    _payoutHist = await r.json();
    renderPayoutHistory();
  } catch(e){}
}

async function refreshAll(){
  try {
    const [statusR, oppsR, tradesR, payoutR] = await Promise.all([
      fetch('/api/status'), fetch('/api/opportunities?limit=20'),
      fetch('/api/trades?limit=50'), fetch('/api/payout'),
    ]);
    _status       = await statusR.json();
    _opps         = await oppsR.json();
    _trades       = await tradesR.json();
    _payoutStatus = await payoutR.json();
    renderAll();
    renderOpportunities();
    renderTrades();
    renderTradesMini();
    renderPayout();
    if (_status.prices) renderPrices(_status.prices);
    toast('Refreshed','info');
  } catch(e){ toast('Refresh failed','error'); }
}

// ── Polling fallback ──────────────────────────────────────────
async function poll(){
  try {
    const r = await fetch('/api/status');
    const d = await r.json();
    _status = d;
    renderAll();
    if (d.prices) renderPrices(d.prices);
  } catch(e){}
}

// ── Init ─────────────────────────────────────────────────────
initCharts();
refreshAll();
loadPayoutHistory();
loadYields();
setInterval(poll,        15_000);
setInterval(loadPayoutHistory, 30_000);
setInterval(()=>{ if(document.querySelector('#tab-prices.active')) loadYields(); }, 60_000);

/* ══════════════════════════════════════════════════════════════
   AI Brain tab
══════════════════════════════════════════════════════════════ */
let _brain = {};

async function loadBrain(){
  try {
    const r = await fetch('/api/learning');
    _brain = await r.json();
    renderBrain();
  } catch(e){}
}

function renderBrain(){
  const b = _brain;
  if (!b || !b.model_info) return;

  // KPIs
  const mlActive  = b.ml_active;
  const mlEl      = $('bkpi-ml-val');
  const mlSub     = $('bkpi-ml-sub');
  if (mlEl) {
    mlEl.textContent = mlActive ? '✓ Active' : 'Warming Up';
    mlEl.style.color = mlActive ? 'var(--green)' : 'var(--yellow)';
  }
  if (mlSub) mlSub.textContent = mlActive
    ? `Trained on ${b.model_info.trained_on} trades`
    : `Need ${b.trades_until_ml} more trades`;

  const bWin = document.querySelector('#bkpi-winrate .kpi-value');
  if (bWin) bWin.textContent = (b.win_rate || 0) + '%';

  const regime = (b.market_regime || {}).regime || '–';
  const bRegime = document.querySelector('#bkpi-regime .kpi-value');
  if (bRegime) {
    bRegime.textContent = regime.charAt(0).toUpperCase() + regime.slice(1);
    bRegime.style.color = {volatile:'var(--red)', trending:'var(--orange)', calm:'var(--green)'}[regime] || 'var(--accent2)';
  }

  const bTotal = document.querySelector('#bkpi-total .kpi-value');
  if (bTotal) bTotal.textContent = fmtUSD(b.total_profit_usd);

  // Learning progress bar
  const trained  = b.total_executed || 0;
  const needed   = 30;
  const pct      = Math.min(100, (trained / needed) * 100);
  const bar      = $('brain-progress-bar');
  const pctLabel = $('brain-progress-pct');
  const hint     = $('brain-progress-hint');
  if (bar)      bar.style.width   = pct + '%';
  if (pctLabel) pctLabel.textContent = `${trained} / ${needed}`;
  if (hint)     hint.textContent  = mlActive
    ? `🧠 ML Model ACTIVE — accuracy improves each trade`
    : `Heuristic mode — ${Math.max(0, needed - trained)} more trades to activate ML`;

  // Model info
  const mi = $('brain-model-info');
  if (mi) {
    const info = b.model_info || {};
    mi.innerHTML = `
      <div class="setting-row"><span class="setting-key">Status</span><span class="setting-val ${mlActive?'on':'warn'}">${mlActive?'ML Active (RandomForest)':'Heuristic Fallback'}</span></div>
      <div class="setting-row"><span class="setting-key">Trained On</span><span class="setting-val">${info.trained_on || 0} trades</span></div>
      <div class="setting-row"><span class="setting-key">Min Samples</span><span class="setting-val">${info.min_samples || 30}</span></div>
      <div class="setting-row"><span class="setting-key">Total Evaluated</span><span class="setting-val">${b.total_evaluated || 0}</span></div>
      <div class="setting-row"><span class="setting-key">Total Executed</span><span class="setting-val">${b.total_executed || 0}</span></div>
      <div class="setting-row"><span class="setting-key">Best Trade</span><span class="setting-val green">${fmtUSD(b.best_trade_usd)}</span></div>`;
  }

  // Market regime panel
  const mr = $('brain-regime-panel');
  if (mr && b.market_regime) {
    const weights = b.market_regime.strategy_weights || {};
    const mstat   = b.market_regime;
    mr.innerHTML = `
      <div class="setting-row"><span class="setting-key">Regime</span><span class="setting-val">${regime}</span></div>
      <div class="setting-row"><span class="setting-key">ETH Volatility</span><span class="setting-val">${fmt(mstat.eth_volatility_pct)}%</span></div>
      <div class="setting-row"><span class="setting-key">BTC Volatility</span><span class="setting-val">${fmt(mstat.btc_volatility_pct)}%</span></div>
      ` + Object.entries(weights).map(([k,v])=>`
      <div class="setting-row">
        <span class="setting-key">${k.replace('_',' ')}</span>
        <span class="setting-val ${v>1.2?'on':v<0.8?'warn':''}">${v}× weight</span>
      </div>`).join('');
  }

  // Adaptive params
  const bp = $('brain-params');
  if (bp && b.optimizer) {
    const opt = b.optimizer;
    const params = opt.params || {};
    bp.innerHTML = `
      <div class="setting-row"><span class="setting-key">Min Profit (USD)</span><span class="setting-val">${fmtUSD(params.min_profit_usd)}</span></div>
      <div class="setting-row"><span class="setting-key">Gas Multiplier</span><span class="setting-val">${fmt(params.gas_multiplier, 3)}×</span></div>
      <div class="setting-row"><span class="setting-key">Slippage Tolerance</span><span class="setting-val">${fmt(params.slippage_tolerance, 2)}%</span></div>
      <div class="setting-row"><span class="setting-key">Confidence Threshold</span><span class="setting-val">${fmt(params.confidence_threshold, 3)}</span></div>
      <div class="setting-row"><span class="setting-key">Win Rate (50 trades)</span><span class="setting-val ${opt.win_rate>60?'on':'warn'}">${opt.win_rate || 0}%</span></div>
      <div class="setting-row"><span class="setting-key">Profit Accuracy</span><span class="setting-val">${fmt((opt.profit_accuracy||0)*100, 1)}%</span></div>`;
  }

  // Model version history
  const mh = $('brain-model-history');
  if (mh) {
    const history = b.model_history || [];
    if (!history.length) {
      mh.innerHTML = '<div class="empty-state">No model versions yet — training begins after 30 trades</div>';
    } else {
      mh.innerHTML = history.map(v=>`
        <div class="opp-item">
          <div class="opp-row1">
            <span class="opp-type-badge arbitrage">${v.model_type}</span>
            <span class="opp-profit">${fmt(v.accuracy*100, 1)}% acc</span>
          </div>
          <div class="opp-desc">${v.n_samples} samples · ${v.notes || ''}</div>
          <div class="opp-conf">${timeAgo(v.timestamp)}</div>
        </div>`).join('');
    }
  }

  // Parameter change log
  const pc = $('brain-param-changes');
  if (pc) {
    const changes = (b.optimizer || {}).adjustments || b.param_changes || [];
    if (!changes.length) {
      pc.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:1rem;color:var(--text-dim)">No adjustments yet…</td></tr>';
    } else {
      pc.innerHTML = changes.slice(0,20).map(c=>`
        <tr>
          <td>${timeAgo(c.time||c.timestamp)}</td>
          <td style="font-family:monospace;color:var(--accent2)">${c.param||c.param_name}</td>
          <td style="color:var(--text-dim)">${fmt(c.old||c.old_value, 4)}</td>
          <td style="color:var(--green);font-weight:700">${fmt(c.new||c.new_value, 4)}</td>
          <td style="color:var(--text-dim);font-size:.78rem">${c.reason||''}</td>
        </tr>`).join('');
    }
  }
}

// Load brain when switching to brain tab
document.querySelectorAll('.nav-item[data-tab="brain"]').forEach(n=>{
  n.addEventListener('click', loadBrain);
});

// Auto-refresh brain if tab is visible
setInterval(()=>{
  if (document.querySelector('#tab-brain.active')) loadBrain();
}, 15_000);

// Load brain data on initial load and wire into status updates
(function(){
  setTimeout(() => loadBrain(), 2000);

  // Pull brain data from status update if included
  socket.on('status_update', data => {
    if (data && data.brain) { _brain = data.brain; renderBrain(); }
  });
})();

// ══════════════════════════════════════════════════════════════
//  NEXUS CHAT & VOICE
// ══════════════════════════════════════════════════════════════

let _chatOpen = false;
let _recognition = null;
let _isListening = false;
let _speechSynthesis = window.speechSynthesis || null;

// ── Toggle chat panel ─────────────────────────────────────────
function toggleChat() {
  _chatOpen = !_chatOpen;
  const panel = document.getElementById('nexus-chat-panel');
  const icon  = document.getElementById('chat-toggle-icon');
  panel.classList.toggle('open', _chatOpen);
  icon.textContent = _chatOpen ? '✕' : '💬';
  if (_chatOpen && document.getElementById('nexus-chat-messages').children.length === 0) {
    addNexusMsg("Hello! I'm Nexus, your AI crypto farming assistant. Ask me anything — status, profits, strategies, or just say 'help'. You can also click 🎤 to speak to me.", 'nexus');
  }
}

function clearChat() {
  document.getElementById('nexus-chat-messages').innerHTML = '';
  fetch('/api/chat', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({message:'__clear__'})}).catch(()=>{});
  addNexusMsg("Chat history cleared. How can I help you?", 'nexus');
}

// ── Send message ──────────────────────────────────────────────
async function sendChat() {
  const input = document.getElementById('nexus-chat-input');
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  addNexusMsg(text, 'user');
  const typingId = addNexusMsg('Thinking…', 'typing');
  try {
    const resp = await fetch('/api/chat', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({message: text})
    });
    const data = await resp.json();
    removeMsg(typingId);
    const reply = data.reply || data.error || 'Sorry, I had trouble understanding that.';
    addNexusMsg(reply, 'nexus');
    if (data.speak !== false) speakText(reply);
  } catch(e) {
    removeMsg(typingId);
    addNexusMsg('Connection error. Try again.', 'nexus');
  }
}

function addNexusMsg(text, role) {
  const msgs = document.getElementById('nexus-chat-messages');
  const id = 'msg-' + Date.now() + '-' + Math.random().toString(36).slice(2);
  const div = document.createElement('div');
  div.className = 'chat-msg ' + role;
  div.id = id;
  div.textContent = text;
  msgs.appendChild(div);
  msgs.scrollTop = msgs.scrollHeight;
  return id;
}

function removeMsg(id) {
  const el = document.getElementById(id);
  if (el) el.remove();
}

// ── Text-to-speech ────────────────────────────────────────────
async function speakText(text) {
  if (!text || text.length < 3) return;
  try {
    // Try server-side TTS first (ElevenLabs if configured)
    const resp = await fetch('/api/voice/tts', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({text})
    });
    if (resp.headers.get('content-type') === 'audio/mpeg') {
      const blob = await resp.blob();
      const url  = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audio.play().catch(()=>{});
      return;
    }
    // Fallback: browser speechSynthesis
    const json = await resp.json();
    if (json.use_browser_tts) browserSpeak(text);
  } catch(e) {
    browserSpeak(text);
  }
}

function browserSpeak(text) {
  if (!_speechSynthesis) return;
  _speechSynthesis.cancel();
  const utt = new SpeechSynthesisUtterance(text);
  utt.rate  = 1.05;
  utt.pitch = 1.0;
  // Prefer a deep/clear voice if available
  const voices = _speechSynthesis.getVoices();
  const preferred = voices.find(v =>
    /google|daniel|alex|samantha|en-us/i.test(v.name + v.lang)
  );
  if (preferred) utt.voice = preferred;
  _speechSynthesis.speak(utt);
}

// ── Voice recognition ─────────────────────────────────────────
function initVoiceRecognition() {
  const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRec) {
    document.getElementById('mic-btn').title = 'Speech recognition not supported in this browser';
    return;
  }
  _recognition = new SpeechRec();
  _recognition.continuous = false;
  _recognition.interimResults = false;
  _recognition.lang = 'en-US';

  _recognition.onresult = (event) => {
    const transcript = event.results[0][0].transcript.trim();
    document.getElementById('nexus-chat-input').value = transcript;
    stopVoice();
    sendChat();
  };
  _recognition.onerror = (e) => {
    console.warn('Speech error:', e.error);
    stopVoice();
    if (e.error !== 'no-speech' && e.error !== 'aborted') {
      addNexusMsg("Couldn't hear you clearly. Try again.", 'nexus');
    }
  };
  _recognition.onend = () => stopVoice();

  document.getElementById('nexus-voice-status').classList.add('active');
  document.getElementById('mic-btn').title = 'Click to speak';
}

function toggleVoice() {
  if (!_recognition) {
    addNexusMsg('Voice recognition is not supported in this browser. Try Chrome or Edge.', 'nexus');
    return;
  }
  if (!_chatOpen) toggleChat();
  _isListening ? stopVoice() : startVoice();
}

function startVoice() {
  if (!_recognition || _isListening) return;
  _isListening = true;
  _recognition.start();
  document.getElementById('mic-btn').classList.add('recording');
  document.getElementById('nexus-voice-indicator').style.display = 'flex';
  document.getElementById('nexus-voice-status').classList.add('listening');
  document.getElementById('nexus-voice-status').classList.remove('active');
}

function stopVoice() {
  if (!_isListening) return;
  _isListening = false;
  try { _recognition.stop(); } catch(e) {}
  document.getElementById('mic-btn').classList.remove('recording');
  document.getElementById('nexus-voice-indicator').style.display = 'none';
  document.getElementById('nexus-voice-status').classList.remove('listening');
  document.getElementById('nexus-voice-status').classList.add('active');
}

// Init voice on page load
initVoiceRecognition();

// ══════════════════════════════════════════════════════════════
//  TIMING TAB
// ══════════════════════════════════════════════════════════════

const _STRATEGY_URGENCY = {
  flash_arbitrage:   {label:'Flash Arbitrage',   urgency:'⚡ Instant', color:'var(--red)'},
  arbitrage:         {label:'Arbitrage',         urgency:'⚡ Instant', color:'var(--red)'},
  liquidation:       {label:'Liquidation',       urgency:'⚡ Instant', color:'var(--red)'},
  triangular_arb:    {label:'Triangular Arb',    urgency:'⚡ Instant', color:'var(--red)'},
  stablecoin_arb:    {label:'Stablecoin Arb',    urgency:'⚡ Instant', color:'var(--red)'},
  cross_chain_arb:   {label:'Cross-Chain Arb',   urgency:'🔶 Normal',  color:'var(--orange)'},
  perp_funding:      {label:'Perp Funding',      urgency:'🔶 Normal',  color:'var(--orange)'},
  yield_farming:     {label:'Yield Farming',     urgency:'✅ Deferred', color:'var(--green)'},
  liquidity_mining:  {label:'Liquidity Mining',  urgency:'✅ Deferred', color:'var(--green)'},
  staking:           {label:'Staking',           urgency:'✅ Deferred', color:'var(--green)'},
  lending:           {label:'Lending',           urgency:'✅ Deferred', color:'var(--green)'},
  governance_farming:{label:'Governance',        urgency:'✅ Deferred', color:'var(--green)'},
  vault_optimizer:   {label:'Vault Optimizer',   urgency:'✅ Deferred', color:'var(--green)'},
};

async function loadTiming() {
  try {
    const data = await fetch('/api/timing').then(r=>r.json());
    const oracle = data.gas_oracle || {};
    const bestGas = oracle.best_gas || {};

    // KPIs
    setText('tkpi-gas-val', oracle.current_gwei != null ? oracle.current_gwei + ' Gwei' : '–');
    const isCheap = oracle.is_cheap;
    setText('tkpi-cheap-val', isCheap === true ? '🟢 CHEAP' : isCheap === false ? '🔴 EXPENSIVE' : '–');
    setText('tkpi-cheap-sub', oracle.should_wait ? 'Deferring non-urgent trades' : 'Executing normally');
    setText('tkpi-queue-val', data.queue_size ?? 0);
    const ch = oracle.cheapest_hour;
    setText('tkpi-cheapest-hour-val', ch != null ? `${ch}:00 UTC` : 'Collecting data…');

    // Gas oracle stats
    const gasEl = document.getElementById('gas-oracle-stats');
    if (gasEl && oracle.samples != null) {
      gasEl.innerHTML = [
        ['Samples collected', oracle.samples],
        ['Mean gas (Gwei)',    oracle.mean_gwei ?? '–'],
        ['25th pct (cheap)',  oracle.p25_gwei ?? '–'],
        ['75th pct (expensive)', oracle.p75_gwei ?? '–'],
        ['Recommended maxFee', bestGas.max_fee_gwei ? bestGas.max_fee_gwei + ' Gwei' : '–'],
        ['Priority fee',      bestGas.priority_fee_gwei ? bestGas.priority_fee_gwei + ' Gwei' : '–'],
      ].map(([k,v]) => `<div class="settings-row"><span>${k}</span><span>${v}</span></div>`).join('');
    }

    // Scheduler stats
    const schedEl = document.getElementById('scheduler-stats');
    if (schedEl) {
      schedEl.innerHTML = [
        ['Trades submitted', data.submitted ?? 0],
        ['Trades expired',   data.expired   ?? 0],
        ['Queue size',       data.queue_size ?? 0],
        ['Scheduler running', data.running ? '✅ Yes' : '⛔ No'],
      ].map(([k,v]) => `<div class="settings-row"><span>${k}</span><span>${v}</span></div>`).join('');
    }

    // Strategy urgency table
    const tbl = document.getElementById('timing-strategy-table');
    if (tbl) {
      tbl.innerHTML = '<div style="font-size:.82rem;color:var(--text-dim);margin-bottom:.6rem">Strategy execution scheduling mode:</div>' +
        Object.entries(_STRATEGY_URGENCY).map(([key, s]) =>
          `<div class="settings-row"><span>${s.label}</span><span style="color:${s.color};font-weight:600">${s.urgency}</span></div>`
        ).join('');
    }

  } catch(e) { console.warn('Timing load error', e); }
}

// Load timing when switching to tab
document.querySelectorAll('.nav-item[data-tab="timing"]').forEach(n => {
  n.addEventListener('click', loadTiming);
});

// Auto-refresh timing tab
setInterval(() => {
  if (document.querySelector('#tab-timing.active')) loadTiming();
}, 20_000);

setTimeout(() => loadTiming(), 3000);

// ══════════════════════════════════════════════════════════════
//  SETTINGS TAB — Coinbase, Payout, Bot Configuration
// ══════════════════════════════════════════════════════════════

let _settingsCache = {};

async function loadSettings() {
  try {
    const resp = await fetch('/api/settings');
    const data = await resp.json();
    _settingsCache = data;
    populateSettingsForm(data);
    toast('Settings loaded', 'info');
  } catch(e) {
    console.warn('Settings load error', e);
    toast('Failed to load settings', 'error');
  }
}

function populateSettingsForm(settings) {
  // Bot settings
  const minProfit = settings.min_profit_usd?.value ?? 2.0;
  const maxGas = settings.max_gas_gwei?.value ?? 80;
  const slippage = settings.slippage_percent?.value ?? 0.5;
  const maxTrade = settings.max_trade_usd?.value ?? 10000;
  const dryRun = settings.dry_run?.value ?? true;

  document.getElementById('set-min-profit').value = minProfit;
  document.getElementById('set-max-gas').value = maxGas;
  document.getElementById('set-slippage').value = slippage;
  document.getElementById('set-max-trade').value = maxTrade;
  
  // Update mode buttons
  document.getElementById('mode-sim').classList.toggle('active', dryRun);
  document.getElementById('mode-live').classList.toggle('active', !dryRun);

  // Payout settings
  const payoutAddr = settings.payout_address?.value ?? '';
  const payoutChain = settings.payout_chain?.value ?? 'ethereum';
  const payoutToken = settings.payout_token?.value ?? 'USDC';
  const payoutThreshold = settings.payout_threshold_usd?.value ?? 10;
  const lightningAddr = settings.lightning_address?.value ?? '';

  document.getElementById('set-payout-addr').value = payoutAddr;
  document.getElementById('set-payout-chain').value = payoutChain;
  document.getElementById('set-payout-token').value = payoutToken;
  document.getElementById('set-payout-threshold').value = payoutThreshold;
  document.getElementById('set-lightning').value = lightningAddr;

  // Coinbase - show configured status
  const cbConfigured = settings.coinbase_api_key?.actual_set;
  const cbStatus = document.getElementById('cb-status');
  if (cbStatus) {
    cbStatus.textContent = cbConfigured ? '✅ Configured' : '⚠️ Not configured';
    cbStatus.className = 'status-badge ' + (cbConfigured ? 'configured' : 'not-configured');
  }

  // Strategy checkboxes
  document.getElementById('set-strat-arb').checked = settings.strategy_arbitrage?.value ?? true;
  document.getElementById('set-strat-yield').checked = settings.strategy_yield_farming?.value ?? true;
  document.getElementById('set-strat-lp').checked = settings.strategy_liquidity_mining?.value ?? true;
  document.getElementById('set-strat-liq').checked = settings.strategy_liquidation?.value ?? true;

  // Chain checkboxes
  document.getElementById('set-chain-eth').checked = settings.chain_eth?.value ?? true;
  document.getElementById('set-chain-bsc').checked = settings.chain_bsc?.value ?? true;
  document.getElementById('set-chain-polygon').checked = settings.chain_polygon?.value ?? true;
  document.getElementById('set-chain-arbitrum').checked = settings.chain_arbitrum?.value ?? false;
  document.getElementById('set-chain-optimism').checked = settings.chain_optimism?.value ?? false;
  document.getElementById('set-chain-base').checked = settings.chain_base?.value ?? false;
}

async function updateSetting(key, value) {
  try {
    const resp = await fetch('/api/settings/update', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({key, value})
    });
    const result = await resp.json();
    if (result.success) {
      toast(`${key} updated`, 'info');
    } else {
      toast(`Failed: ${result.error}`, 'error');
    }
    return result.success;
  } catch(e) {
    toast('Update failed', 'error');
    return false;
  }
}

async function setDryRun(isDry) {
  const success = await updateSetting('dry_run', isDry);
  if (success) {
    document.getElementById('mode-sim').classList.toggle('active', isDry);
    document.getElementById('mode-live').classList.toggle('active', !isDry);
    toast(isDry ? 'Switched to SIMULATION mode' : '⚠️ Switched to LIVE TRADING', isDry ? 'info' : 'warn');
  }
}

async function saveCoinbaseCredentials() {
  const apiKey = document.getElementById('set-cb-key').value.trim();
  const apiSecret = document.getElementById('set-cb-secret').value.trim();
  const accountId = document.getElementById('set-cb-account').value.trim();

  if (!apiKey && !apiSecret) {
    toast('Please enter API credentials', 'error');
    return;
  }

  try {
    const resp = await fetch('/api/settings/coinbase', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        api_key: apiKey,
        api_secret: apiSecret,
        account_id: accountId
      })
    });
    const result = await resp.json();
    
    const cbStatus = document.getElementById('cb-status');
    if (result.success && result.configured) {
      toast('✅ Coinbase credentials saved!', 'info');
      cbStatus.textContent = '✅ Configured';
      cbStatus.className = 'status-badge configured';
      // Clear the input fields for security
      document.getElementById('set-cb-key').value = '';
      document.getElementById('set-cb-secret').value = '';
    } else {
      toast('Failed to save Coinbase credentials', 'error');
      cbStatus.textContent = '⚠️ Not configured';
      cbStatus.className = 'status-badge not-configured';
    }
  } catch(e) {
    toast('Failed to save credentials', 'error');
  }
}

async function savePayoutSettings() {
  const addr = document.getElementById('set-payout-addr').value.trim();
  const chain = document.getElementById('set-payout-chain').value;
  const token = document.getElementById('set-payout-token').value;
  const threshold = parseFloat(document.getElementById('set-payout-threshold').value) || 10;

  try {
    const resp = await fetch('/api/settings/payout', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        address: addr,
        chain: chain,
        token: token,
        threshold_usd: threshold
      })
    });
    const result = await resp.json();
    
    if (result.success) {
      toast('✅ Payout settings saved!', 'info');
    } else {
      toast('Some payout settings failed to save', 'warn');
    }
  } catch(e) {
    toast('Failed to save payout settings', 'error');
  }
}

async function saveLightningSettings() {
  const addr = document.getElementById('set-lightning').value.trim();
  const success = await updateSetting('lightning_address', addr);
  if (success) {
    toast('⚡ Lightning address saved!', 'info');
  }
}

async function saveAllSettings() {
  const settings = {
    min_profit_usd: parseFloat(document.getElementById('set-min-profit').value) || 2.0,
    max_gas_gwei: parseFloat(document.getElementById('set-max-gas').value) || 80,
    slippage_percent: parseFloat(document.getElementById('set-slippage').value) || 0.5,
    max_trade_usd: parseFloat(document.getElementById('set-max-trade').value) || 10000,
  };

  try {
    const resp = await fetch('/api/settings/update', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(settings)
    });
    const result = await resp.json();
    
    if (result.success) {
      toast('✅ All bot settings saved!', 'info');
    } else {
      toast(`Saved ${result.updated}/${result.total} settings`, 'warn');
    }
  } catch(e) {
    toast('Failed to save settings', 'error');
  }
}

async function updateStrategySetting(strategy, enabled) {
  await updateSetting(`strategy_${strategy}`, enabled);
}

async function updateChainSetting(chain, enabled) {
  await updateSetting(`chain_${chain}`, enabled);
}

// Load settings when switching to the tab
document.querySelectorAll('.nav-item[data-tab="settings"]').forEach(n => {
  n.addEventListener('click', loadSettings);
});

// Initial load
setTimeout(() => {
  if (document.querySelector('#tab-settings.active')) {
    loadSettings();
  }
}, 1000);
