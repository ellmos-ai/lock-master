/* Lock-Watcher Frontend */

const API = '';

// ── State ────────────────────────────────────────
let roomsData = [];
let locksData = [];
let roomStatsData = {};
let currentView = localStorage.getItem('lockwatcher-view') || 'hierarchy';

// ── Init ─────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  initViewSwitcher();
  loadRoomMap();
  loadStats();
  setInterval(loadStats, 30000);
});

// ── Toast ────────────────────────────────────────
function toast(msg) {
  let el = document.getElementById('toast');
  if (!el) {
    el = document.createElement('div');
    el.id = 'toast';
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 2500);
}

// ── API Helpers ──────────────────────────────────
async function api(path, opts = {}) {
  const url = API + path;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 15000);
  try {
    const res = await fetch(url, {
      headers: { 'Content-Type': 'application/json' },
      signal: ctrl.signal,
      ...opts,
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
  } catch (err) {
    if (err.name === 'AbortError') {
      toast('Timeout — Server antwortet nicht');
    } else {
      toast('Fehler: ' + err.message);
    }
    return { error: err.message };
  } finally {
    clearTimeout(timer);
  }
}

async function apiPost(path, body) {
  return api(path, { method: 'POST', body: JSON.stringify(body) });
}

async function apiPut(path, body) {
  return api(path, { method: 'PUT', body: JSON.stringify(body) });
}

// ── Stats ────────────────────────────────────────
async function loadStats() {
  const s = await api('/api/stats');
  const el = document.getElementById('topbar-stats');
  el.innerHTML = `
    <span class="stat-badge active">${s.active} aktiv</span>
    <span class="stat-badge expired">${s.expired} abgelaufen</span>
    <span class="stat-badge deleted">${s.deleted} gelöscht</span>
    ${s.last_scan_at ? `<span class="stat-badge">Scan: ${formatTime(s.last_scan_at)}</span>` : ''}
  `;
}

// ── Room Map ─────────────────────────────────────
async function loadRoomMap() {
  const [rooms, locks] = await Promise.all([
    api('/api/rooms'),
    api('/api/locks'),
  ]);
  if (!rooms || rooms.error) return;
  roomsData = rooms;
  locksData = locks || [];
  await loadRoomStats();
  renderRoomMap(roomsData, locksData);
}

async function loadRoomStats() {
  const stats = await api('/api/room-stats');
  if (!stats || stats.error) return;
  roomStatsData = {};
  for (const s of stats) {
    roomStatsData[s.room_key] = s;
  }
}

function buildRoomCardInner(room, locks) {
  const roomLocks = locks.filter(l => l.room === room.key);
  const dots = roomLocks.map(l => {
    const name = l.display_name || l.filename;
    return `<div class="lock-dot ${l.lock_type}" onclick="event.stopPropagation(); showLockDetail(${l.id})" title="${esc(name)}">
      <span class="lock-dot-tooltip">${esc(name)}</span>
    </div>`;
  }).join('');

  const iconFile = room.icon || '';
  const iconHtml = iconFile
    ? `<div class="room-icon"><img src="/icons/${esc(iconFile)}" alt="" /></div>`
    : '';

  const stats = roomStatsData[room.key];

  return `
    <div class="room-card-accent" style="background:${room.color}"></div>
    <div class="room-header">
      <div class="room-header-left">
        ${iconHtml}
        <div>
          <div class="room-label">${esc(room.label)}</div>
          <div class="room-dirname">${esc(room.dir_name)}</div>
        </div>
      </div>
      <div class="room-lock-count ${roomLocks.length > 0 ? 'has-locks' : ''}">${roomLocks.length}</div>
    </div>
    ${room.goals ? `<div class="room-goals">${esc(room.goals)}</div>` : ''}
    ${dots ? `<div class="room-locks-preview">${dots}</div>` : ''}
    ${buildFileTypeBar(stats)}
  `;
}

function buildFileTypeBar(stats) {
  if (!stats) return '<div class="stats-pending">···</div>';
  const total = (stats.type_code || 0) + (stats.type_human || 0) + (stats.type_llm || 0) +
                (stats.type_media || 0) + (stats.type_other || 0);
  if (total === 0) return '';
  const segs = [
    { cls: 'ft-code',  n: stats.type_code || 0, label: 'Code' },
    { cls: 'ft-human', n: stats.type_human || 0, label: 'Dokumente' },
    { cls: 'ft-llm',   n: stats.type_llm || 0, label: 'Text' },
    { cls: 'ft-media', n: stats.type_media || 0, label: 'Medien' },
    { cls: 'ft-other', n: stats.type_other || 0, label: 'Sonstige' },
  ].filter(s => s.n > 0);
  return '<div class="file-type-bar">' +
    segs.map(s => `<div class="ft-seg ${s.cls}" style="flex:${s.n}" title="${s.label}: ${s.n.toLocaleString()}"></div>`).join('') +
    '</div>';
}

function squarify(items, x, y, w, h) {
  if (!items.length) return [];
  const total = items.reduce((s, it) => s + it.value, 0);
  if (total <= 0) return items.map(it => ({...it, x, y, w: 0, h: 0}));
  const sorted = [...items].sort((a, b) => b.value - a.value);
  const result = [];

  function doLayout(items, x, y, w, h, areaLeft) {
    if (!items.length) return;
    if (items.length === 1) {
      result.push({...items[0], x, y, w, h});
      return;
    }
    const isWide = w >= h;
    const side = isWide ? w : h;
    const otherSide = isWide ? h : w;
    let row = [items[0]];
    let rowSum = items[0].value;
    let i = 1;
    while (i < items.length) {
      const newRowSum = rowSum + items[i].value;
      const curStrip = (rowSum / areaLeft) * side;
      let curWorst = 0;
      for (const it of row) {
        const span = (it.value / rowSum) * otherSide;
        if (span > 0) curWorst = Math.max(curWorst, Math.max(curStrip / span, span / curStrip));
      }
      const newStrip = (newRowSum / areaLeft) * side;
      let newWorst = 0;
      const newRow = [...row, items[i]];
      for (const it of newRow) {
        const span = (it.value / newRowSum) * otherSide;
        if (span > 0) newWorst = Math.max(newWorst, Math.max(newStrip / span, span / newStrip));
      }
      if (newWorst <= curWorst) {
        row.push(items[i]);
        rowSum = newRowSum;
        i++;
      } else {
        break;
      }
    }
    const stripSize = (rowSum / areaLeft) * side;
    let offset = 0;
    for (const it of row) {
      const span = (it.value / rowSum) * otherSide;
      if (isWide) {
        result.push({...it, x, y: y + offset, w: stripSize, h: span});
      } else {
        result.push({...it, x: x + offset, y, w: span, h: stripSize});
      }
      offset += span;
    }
    const rest = items.slice(i);
    const restArea = areaLeft - rowSum;
    if (isWide) {
      doLayout(rest, x + stripSize, y, w - stripSize, h, restArea);
    } else {
      doLayout(rest, x, y + stripSize, w, h - stripSize, restArea);
    }
  }

  doLayout(sorted, x, y, w, h, total);
  return result;
}

function renderRoomMap(rooms, locks) {
  const container = document.getElementById('room-map');

  if (currentView === 'hierarchy') {
    container.classList.remove('treemap-mode');
    container.style.height = '';
    container.innerHTML = rooms.map(room =>
      `<div class="room-card" onclick="showRoomDetail('${room.key}')">${buildRoomCardInner(room, locks)}</div>`
    ).join('');
    return;
  }

  container.classList.add('treemap-mode');
  const W = container.offsetWidth || 800;
  const H = Math.max(window.innerHeight - 160, 400);
  container.style.height = H + 'px';

  const metricKey = currentView === 'files' ? 'file_count'
    : currentView === 'folders' ? 'folder_count'
    : 'total_bytes';

  const items = rooms.map(room => {
    const s = roomStatsData[room.key];
    return { room, value: s ? (s[metricKey] || 1) : 1 };
  });

  const tiles = squarify(items, 0, 0, W, H);
  const GAP = 2;

  container.innerHTML = tiles.map(t => {
    const tW = Math.max(t.w - GAP * 2, 0);
    const tH = Math.max(t.h - GAP * 2, 0);
    const sizeClass = tW < 100 || tH < 80 ? 'tile-small'
      : tW < 180 || tH < 120 ? 'tile-medium' : 'tile-large';
    return `<div class="room-card ${sizeClass}" onclick="showRoomDetail('${t.room.key}')"
      style="left:${t.x + GAP}px;top:${t.y + GAP}px;width:${tW}px;height:${tH}px"
      title="${esc(t.room.label)}: ${(roomStatsData[t.room.key] || {})[metricKey] || 0}">${buildRoomCardInner(t.room, locks)}</div>`;
  }).join('');
}

function initViewSwitcher() {
  const header = document.getElementById('topbar');
  if (!header) return;

  const wrapper = document.createElement('div');
  wrapper.style.cssText = 'display:flex;align-items:center;gap:8px;margin-left:16px';

  const switcher = document.createElement('div');
  switcher.className = 'view-switcher';
  const modes = [
    { key: 'hierarchy', label: 'Grid' },
    { key: 'files', label: 'Dateien' },
    { key: 'folders', label: 'Ordner' },
    { key: 'size', label: 'Größe' },
  ];
  switcher.innerHTML = modes.map(m =>
    `<button data-view="${m.key}" class="${m.key === currentView ? 'active' : ''}">${m.label}</button>`
  ).join('');
  switcher.addEventListener('click', e => {
    const btn = e.target.closest('button[data-view]');
    if (!btn) return;
    currentView = btn.dataset.view;
    localStorage.setItem('lockwatcher-view', currentView);
    switcher.querySelectorAll('button').forEach(b => b.classList.toggle('active', b.dataset.view === currentView));
    renderRoomMap(roomsData, locksData);
  });

  const refreshBtn = document.createElement('button');
  refreshBtn.className = 'view-refresh-btn';
  refreshBtn.textContent = '↻ Stats';
  refreshBtn.title = 'Verzeichnis-Statistiken neu berechnen';
  refreshBtn.onclick = async () => {
    refreshBtn.disabled = true;
    refreshBtn.textContent = '↻ …';
    try {
      await apiPost('/api/room-stats/refresh', {});
      await loadRoomStats();
      renderRoomMap(roomsData, locksData);
    } finally {
      refreshBtn.disabled = false;
      refreshBtn.textContent = '↻ Stats';
    }
  };

  const legend = document.createElement('div');
  legend.className = 'view-legend';
  legend.innerHTML = [
    { bg: '#10b981', label: 'Code' },
    { bg: '#3b82f6', label: 'Dokumente' },
    { bg: '#f59e0b', label: 'Text' },
    { bg: '#ec4899', label: 'Medien' },
    { bg: '#6b7280', label: 'Sonstige' },
  ].map(l => `<div class="view-legend-item"><div class="view-legend-dot" style="background:${l.bg}"></div>${l.label}</div>`).join('');

  wrapper.appendChild(switcher);
  wrapper.appendChild(refreshBtn);
  wrapper.appendChild(legend);
  const actions = document.getElementById('topbar-actions');
  if (actions) {
    header.insertBefore(wrapper, actions);
  } else {
    header.appendChild(wrapper);
  }
}

// ── Room Detail ──────────────────────────────────
async function showRoomDetail(key) {
  const room = await api(`/api/room/${key}`);
  const history = await api(`/api/room/${key}/history?limit=30`);

  let html = '';

  // Room settings
  html += `
    <div class="detail-section">
      <h3>Raum-Einstellungen</h3>
      <div class="form-group">
        <label>Name</label>
        <input type="text" id="room-label" value="${esc(room.label)}" />
      </div>
      <div class="form-group">
        <label>Raum-Icon</label>
        <div id="icon-picker-container">Lade Icons...</div>
        <input type="hidden" id="room-icon" value="${esc(room.icon || '')}" />
      </div>
      <div class="form-group" style="display:flex;gap:12px;align-items:end">
        <div style="flex:1">
          <label>Farbe</label>
          <input type="color" id="room-color" value="${room.color}" />
        </div>
        <div style="flex:1">
          <label>Notiz-Ziel</label>
          <select id="room-notes-target">
            <option value="own_file" ${room.notes_target === 'own_file' ? 'selected' : ''}>Eigene Datei</option>
            <option value="claude_md" ${room.notes_target === 'claude_md' ? 'selected' : ''}>An CLAUDE.md anheften</option>
          </select>
        </div>
        <div style="flex:1">
          <label>Dateiname</label>
          <input type="text" id="room-notes-filename" value="${esc(room.notes_filename || 'USER-NOTES.md')}" />
        </div>
      </div>
      <div class="form-group">
        <label>Ziele</label>
        <textarea id="room-goals" rows="2">${esc(room.goals || '')}</textarea>
      </div>
      <div class="form-group">
        <label>Notizen</label>
        <textarea id="room-notes" rows="3">${esc(room.notes || '')}</textarea>
      </div>
      <div class="btn-row">
        <button class="btn btn-primary" onclick="saveRoomSettings('${key}')">Speichern</button>
      </div>
    </div>
  `;

  // Locks
  html += `<div class="detail-section"><h3>Aktive Locks (${room.locks ? room.locks.length : 0})</h3>`;
  if (room.locks && room.locks.length > 0) {
    html += '<div class="lock-list">';
    for (const l of room.locks) {
      const name = l.display_name || l.filename;
      const rem = l.remaining || '';
      html += `
        <div class="lock-item" onclick="showLockDetail(${l.id})">
          <div class="lock-item-header">
            <span class="lock-item-name">${esc(name)}</span>
            <span class="lock-item-type ${l.lock_type}">${l.lock_type}</span>
          </div>
          <div class="lock-item-meta">
            ${l.owner ? `<span>Owner: ${esc(l.owner)}</span>` : ''}
            ${l.host ? `<span>Host: ${esc(l.host)}</span>` : ''}
            ${rem ? `<span>Restzeit: ${esc(rem)}</span>` : ''}
          </div>
        </div>
      `;
    }
    html += '</div>';
  } else {
    html += '<p style="color:var(--text-dim);font-size:13px">Keine aktiven Locks</p>';
  }
  html += '</div>';

  // Create lock
  html += `
    <div class="detail-section">
      <h3>Neuen Lock erstellen</h3>
      <div class="form-group">
        <label>Projektordner</label>
        <input type="text" id="new-lock-dir" value="${esc(room.abs_path || '')}" />
      </div>
      <div class="form-group" style="display:flex;gap:12px">
        <div style="flex:1">
          <label>Scope</label>
          <input type="text" id="new-lock-scope" value="project" placeholder="project oder Komponentenname" />
        </div>
        <div style="flex:1">
          <label>Gültigkeit</label>
          <input type="text" id="new-lock-expires" value="24h" />
        </div>
        <div style="flex:1">
          <label>Modus</label>
          <select id="new-lock-mode">
            <option value="hard">hard</option>
            <option value="soft">soft</option>
          </select>
        </div>
      </div>
      <div class="form-group">
        <label>Zweck</label>
        <input type="text" id="new-lock-purpose" placeholder="Warum wird gesperrt?" />
      </div>
      <div class="btn-row">
        <button class="btn btn-danger" onclick="createLock()">Lock erstellen</button>
      </div>
    </div>
  `;

  // Room files
  html += `
    <div class="detail-section">
      <h3>Projekt-Dateien (.md)</h3>
      <div id="room-files-list">Lade...</div>
    </div>
  `;

  // History
  html += `<div class="detail-section"><h3>Verlauf</h3>`;
  if (history.length > 0) {
    html += '<div class="event-list">';
    for (const e of history) {
      html += `
        <div class="event-row">
          <span class="event-type ${e.event_type}">${e.event_type}</span>
          <span class="event-time">${formatTime(e.timestamp)}</span>
          <span class="event-file">${esc(e.filename || e.display_name || '')}</span>
        </div>
      `;
    }
    html += '</div>';
  } else {
    html += '<p style="color:var(--text-dim);font-size:13px">Keine Events</p>';
  }
  html += '</div>';

  openModal(room.label, html);
  loadRoomFiles(key);
  loadIconPicker(room.icon || '');
}

async function loadIconPicker(currentIcon) {
  const icons = await api('/api/icons');
  const container = document.getElementById('icon-picker-container');
  if (!container || !Array.isArray(icons) || icons.length === 0) {
    if (container) container.innerHTML = '<span style="color:var(--text-muted);font-size:12px">Keine Icons verfügbar</span>';
    return;
  }
  const noneSelected = !currentIcon ? 'selected' : '';
  let html = `<div class="icon-picker">
    <div class="icon-option ${noneSelected}" onclick="selectIcon(this, '')" title="Kein Icon">
      <span style="color:var(--text-muted);font-size:16px">—</span>
    </div>`;
  for (const icon of icons) {
    const sel = icon === currentIcon ? 'selected' : '';
    html += `<div class="icon-option ${sel}" onclick="selectIcon(this, '${esc(icon)}')" title="${esc(icon)}">
      <img src="/icons/${esc(icon)}" alt="${esc(icon)}" />
    </div>`;
  }
  html += '</div>';
  container.innerHTML = html;
}

function selectIcon(el, icon) {
  document.querySelectorAll('.icon-option').forEach(o => o.classList.remove('selected'));
  el.classList.add('selected');
  document.getElementById('room-icon').value = icon;
}

async function loadRoomFiles(key) {
  const files = await api(`/api/room-files/${key}`);
  const el = document.getElementById('room-files-list');
  if (!el) return;
  if (files.length === 0) {
    el.innerHTML = '<p style="color:var(--text-dim);font-size:13px">Keine .md Dateien</p>';
    return;
  }
  el.innerHTML = '<div class="file-list">' + files.map(f => `
    <div class="file-item" onclick="editRoomFile('${key}', '${esc(f.name)}')">
      <span class="file-item-name">${esc(f.name)}</span>
      <span class="file-item-meta">${formatBytes(f.size)} &middot; ${formatTime(f.modified)}</span>
    </div>
  `).join('') + '</div>';
}

async function editRoomFile(roomKey, filename) {
  const data = await api(`/api/room-file/${roomKey}/${encodeURIComponent(filename)}`);
  const html = `
    <div class="form-group">
      <textarea id="edit-file-content" rows="20" style="font-family:'Cascadia Code','Fira Code',monospace;font-size:12px">${esc(data.content)}</textarea>
    </div>
    <div class="btn-row">
      <button class="btn btn-primary" onclick="saveRoomFile('${roomKey}', '${esc(filename)}')">Speichern</button>
      <button class="btn btn-ghost" onclick="showRoomDetail('${roomKey}')">Zurück</button>
    </div>
  `;
  openModal(filename, html);
}

async function saveRoomFile(roomKey, filename) {
  const content = document.getElementById('edit-file-content').value;
  await apiPost('/api/room-file', { room_key: roomKey, filename, content });
  toast('Gespeichert');
}

async function saveRoomSettings(key) {
  const updates = {
    label: document.getElementById('room-label').value,
    color: document.getElementById('room-color').value,
    icon: document.getElementById('room-icon').value,
    notes: document.getElementById('room-notes').value,
    goals: document.getElementById('room-goals').value,
    notes_target: document.getElementById('room-notes-target').value,
    notes_filename: document.getElementById('room-notes-filename').value,
  };
  const result = await apiPut(`/api/room/${key}`, updates);
  if (!result.error) {
    document.getElementById('modal-overlay').classList.add('hidden');
    toast('Raum gespeichert');
    loadRoomMap();
  }
}

async function createLock() {
  const data = {
    project_dir: document.getElementById('new-lock-dir').value,
    scope: document.getElementById('new-lock-scope').value,
    expires_after: document.getElementById('new-lock-expires').value,
    mode: document.getElementById('new-lock-mode').value,
    purpose: document.getElementById('new-lock-purpose').value,
  };
  const result = await apiPost('/api/lock', data);
  if (result.error) {
    toast('Fehler: ' + result.error);
  } else {
    toast('Lock erstellt: ' + result.filename);
    loadRoomMap();
  }
}

// ── Lock Detail ──────────────────────────────────
async function showLockDetail(lockId) {
  const l = await api(`/api/lock/${lockId}`);
  if (l.error) {
    toast(l.error);
    return;
  }

  const remClass = l.remaining === 'abgelaufen' ? 'expired' : (l.remaining && parseInt(l.remaining) < 2 ? 'warning' : 'ok');

  let html = `
    <div class="detail-section">
      <div class="form-group" style="margin-bottom:16px">
        <label>Anzeigename</label>
        <div style="display:flex;gap:8px">
          <input type="text" id="lock-display-name" value="${esc(l.display_name || '')}" placeholder="${esc(l.filename)}" style="flex:1" />
          <button class="btn btn-ghost" onclick="saveLockName(${l.id})">Speichern</button>
        </div>
      </div>
      <div class="lock-detail-grid">
        <span class="lock-detail-key">Datei</span><span class="lock-detail-value">${esc(l.filename)}</span>
        <span class="lock-detail-key">Pfad</span><span class="lock-detail-value">${esc(l.path)}</span>
        <span class="lock-detail-key">Typ</span><span class="lock-detail-value"><span class="lock-item-type ${l.lock_type}">${l.lock_type}</span></span>
        <span class="lock-detail-key">Scope</span><span class="lock-detail-value">${esc(l.scope || '-')}</span>
        <span class="lock-detail-key">Status</span><span class="lock-detail-value">${esc(l.status)}</span>
        <span class="lock-detail-key">Owner</span><span class="lock-detail-value">${esc(l.owner || '-')}</span>
        <span class="lock-detail-key">Host</span><span class="lock-detail-value">${esc(l.host || '-')}</span>
        <span class="lock-detail-key">Zweck</span><span class="lock-detail-value">${esc(l.purpose || '-')}</span>
        <span class="lock-detail-key">Modus</span><span class="lock-detail-value">${esc(l.mode || '-')}</span>
        <span class="lock-detail-key">Erstellt</span><span class="lock-detail-value">${formatTime(l.created_at)}</span>
        <span class="lock-detail-key">Gültigkeit</span><span class="lock-detail-value">${esc(l.expires_after || '24h')}</span>
        <span class="lock-detail-key">Läuft ab</span><span class="lock-detail-value">${formatTime(l.expires_at)}</span>
        <span class="lock-detail-key">Restzeit</span><span class="lock-detail-value"><span class="remaining-badge ${remClass}">${esc(l.remaining || '-')}</span></span>
        <span class="lock-detail-key">Quelle</span><span class="lock-detail-value">${esc(l.created_source || '-')}</span>
        <span class="lock-detail-key">Raum</span><span class="lock-detail-value">${esc(l.room)}</span>
        <span class="lock-detail-key">Zuerst gesehen</span><span class="lock-detail-value">${formatTime(l.first_seen)}</span>
        <span class="lock-detail-key">Zuletzt gesehen</span><span class="lock-detail-value">${formatTime(l.last_seen)}</span>
      </div>
    </div>
  `;

  // Team data
  if (l.team_data && typeof l.team_data === 'object') {
    html += '<div class="detail-section"><h3>Team-Lock Daten</h3>';
    for (const [section, entries] of Object.entries(l.team_data)) {
      if (entries && entries.length > 0) {
        html += `<h4 style="font-size:13px;color:var(--text-dim);margin:8px 0 4px">${esc(section)}</h4>`;
        html += '<ul style="font-size:12px;padding-left:16px">';
        for (const e of entries) {
          html += `<li>${esc(e)}</li>`;
        }
        html += '</ul>';
      }
    }
    html += '</div>';
  }

  // Events
  if (l.events && l.events.length > 0) {
    html += '<div class="detail-section"><h3>Events</h3><div class="event-list">';
    for (const e of l.events) {
      html += `
        <div class="event-row">
          <span class="event-type ${e.event_type}">${e.event_type}</span>
          <span class="event-time">${formatTime(e.timestamp)}</span>
        </div>
      `;
    }
    html += '</div></div>';
  }

  const title = l.display_name || l.filename;
  openModal(title, html);
}

async function saveLockName(lockId) {
  const name = document.getElementById('lock-display-name').value.trim();
  if (!name) { toast('Name darf nicht leer sein'); return; }
  await apiPut(`/api/lock/${lockId}/name`, { name });
  toast('Name gespeichert');
  loadRoomMap();
}

// ── User-Locks & Bulk-Lock ───────────────────────
function showPermissions() {
  openModal('Sperren & Rechte', `
    <div class="detail-section">
      <h3>User-Lock anlegen</h3>
      <p class="permission-hint">Ein User-Lock bleibt bestehen, bis du ihn
        ausdrücklich wieder entfernst. Agenten und Prune fassen ihn nicht an.</p>
      <div class="form-group">
        <label>Projektordner</label>
        <input id="user-lock-dir" type="text" placeholder="C:\\...\\Projekt" />
      </div>
      <div class="form-group">
        <label>Scope (optional)</label>
        <input id="user-lock-scope" type="text" placeholder="leer = gesamtes Projekt" />
      </div>
      <div class="form-group">
        <label>Zweck (optional)</label>
        <input id="user-lock-purpose" type="text" placeholder="Warum wird gesperrt?" />
      </div>
      <div class="btn-row">
        <button class="btn btn-danger" onclick="createUserLock()">User-Lock anlegen</button>
        <button class="btn btn-ghost" onclick="removeUserLock()">User-Lock entfernen</button>
      </div>
    </div>
    <div class="detail-section">
      <h3>Sofortsperrung aller Roots</h3>
      <p class="permission-hint">Die Vorschau schreibt nichts. User-Locks werden
        beim Sperren und Entsperren nie verändert.</p>
      <div class="form-group">
        <label>Grund</label>
        <input id="bulk-lock-reason" type="text" placeholder="z. B. Wartung oder Migration" />
      </div>
      <div class="btn-row">
        <button class="btn btn-ghost" onclick="runBulk('lock', false)">Vorschau sperren</button>
        <button class="btn btn-danger" onclick="runBulk('lock', true)">Alle sperren</button>
      </div>
      <div class="btn-row">
        <button class="btn btn-ghost" onclick="runBulk('unlock', false)">Vorschau entsperren</button>
        <button class="btn btn-primary" onclick="runBulk('unlock', true)">Alle entsperren</button>
      </div>
      <pre id="bulk-lock-result" class="bulk-result" aria-live="polite"></pre>
    </div>
  `);
}

async function createUserLock() {
  const result = await apiPost('/api/user-lock', {
    project_dir: document.getElementById('user-lock-dir').value,
    scope: document.getElementById('user-lock-scope').value,
    purpose: document.getElementById('user-lock-purpose').value,
  });
  if (result.error) {
    toast('Fehler: ' + result.error);
    return;
  }
  toast('User-Lock erstellt: ' + result.filename);
  loadRoomMap();
  loadStats();
}

async function removeUserLock() {
  const result = await apiPost('/api/user-lock/remove', {
    project_dir: document.getElementById('user-lock-dir').value,
    scope: document.getElementById('user-lock-scope').value,
  });
  if (result.error) {
    toast('Fehler: ' + result.error);
    return;
  }
  toast('User-Lock entfernt');
  loadRoomMap();
  loadStats();
}

async function runBulk(action, commit) {
  if (commit) {
    const prompt = action === 'lock'
      ? 'Wirklich alle angebundenen Roots sperren?'
      : 'Wirklich alle zentralen Bulk-Sperren entfernen?';
    if (!window.confirm(prompt)) return;
  }
  const path = action === 'lock' ? '/api/bulk-lock' : '/api/bulk-unlock';
  const result = await apiPost(path, {
    commit,
    reason: document.getElementById('bulk-lock-reason').value,
  });
  const output = document.getElementById('bulk-lock-result');
  if (!output) return;
  if (result.error) {
    output.textContent = 'Fehler: ' + result.error;
    return;
  }
  output.textContent = JSON.stringify(result, null, 2);
  if (commit) {
    toast(action === 'lock' ? 'Sofortsperrung gesetzt' : 'Bulk-Sperren entfernt');
    loadRoomMap();
    loadStats();
  }
}

// ── Central Files / Bibliothek ───────────────────
async function showCentralFiles() {
  const files = await api('/api/central-files');
  let html = `
    <div class="detail-section">
      <h3>Gespeicherte Dateien</h3>
      <div class="file-list">
        ${files.length > 0 ? files.map(f => `
          <div class="file-item" onclick="editCentralFile('${esc(f.filename)}')">
            <span class="file-item-name">${esc(f.name)}</span>
            <span class="file-item-meta">${formatBytes(f.size)} &middot; ${formatTime(f.modified)}</span>
          </div>
        `).join('') : '<p style="color:var(--text-dim);font-size:13px">Keine Dateien in der Bibliothek</p>'}
      </div>
    </div>
    <div class="detail-section">
      <h3>Neue Datei anlegen</h3>
      <div class="form-group">
        <label>Dateiname (ohne .md)</label>
        <input type="text" id="new-central-name" placeholder="z.B. CLAUDE_2" />
      </div>
      <div class="form-group">
        <label>Inhalt</label>
        <textarea id="new-central-content" rows="8"></textarea>
      </div>
      <div class="btn-row">
        <button class="btn btn-primary" onclick="saveCentralFile()">Anlegen</button>
      </div>
    </div>
    <div class="detail-section">
      <h3>Datei in Raum einsetzen</h3>
      <div class="form-group">
        <label>Raum</label>
        <select id="swap-room">${roomsData.map(r => `<option value="${r.key}">${esc(r.label)}</option>`).join('')}</select>
      </div>
      <div class="form-group">
        <label>Quelldatei (aus Bibliothek)</label>
        <select id="swap-source">${files.map(f => `<option value="${f.name}">${esc(f.name)}</option>`).join('')}</select>
      </div>
      <div class="form-group">
        <label>Zieldatei im Raum</label>
        <input type="text" id="swap-target" value="CLAUDE.md" />
      </div>
      <div class="btn-row">
        <button class="btn btn-danger" onclick="swapCentralFile()">Einsetzen (Backup wird erstellt)</button>
      </div>
    </div>
  `;
  openModal('Bibliothek', html);
}

async function editCentralFile(filename) {
  const data = await api(`/api/central-file/${encodeURIComponent(filename)}`);
  const html = `
    <div class="form-group">
      <textarea id="edit-central-content" rows="20" style="font-family:'Cascadia Code','Fira Code',monospace;font-size:12px">${esc(data.content)}</textarea>
    </div>
    <div class="btn-row">
      <button class="btn btn-primary" onclick="updateCentralFile('${esc(filename)}')">Speichern</button>
      <button class="btn btn-ghost" onclick="showCentralFiles()">Zurück</button>
    </div>
  `;
  openModal(data.name, html);
}

async function updateCentralFile(filename) {
  const content = document.getElementById('edit-central-content').value;
  const name = filename.endsWith('.md') ? filename : filename + '.md';
  await apiPost('/api/central-file', { name, content });
  toast('Gespeichert');
}

async function saveCentralFile() {
  const name = document.getElementById('new-central-name').value.trim();
  const content = document.getElementById('new-central-content').value;
  if (!name) { toast('Name erforderlich'); return; }
  await apiPost('/api/central-file', { name, content });
  toast('Datei angelegt');
  showCentralFiles();
}

async function swapCentralFile() {
  const data = {
    room_key: document.getElementById('swap-room').value,
    source_name: document.getElementById('swap-source').value,
    target_name: document.getElementById('swap-target').value,
  };
  const result = await apiPost('/api/swap-central-file', data);
  if (result.error) {
    toast('Fehler: ' + result.error);
  } else {
    toast('Datei eingesetzt (Backup in Bibliothek)');
  }
}

// ── Settings ─────────────────────────────────────
async function showSettings() {
  const [s, profile] = await Promise.all([api('/api/settings'), api('/api/profile')]);
  const daemonText = formatDaemonStatus(s.daemon);
  const html = `
    <div class="detail-section">
      <h3>Nutzerprofil</h3>
      <div class="form-group">
        <label>Name (wird bei Lock-Erstellung als Owner verwendet)</label>
        <div style="display:flex;gap:8px">
          <input type="text" id="profile-name" value="${esc(profile.name)}" style="flex:1" />
          <button class="btn btn-primary" onclick="saveProfile()">Speichern</button>
        </div>
      </div>
      <div class="settings-grid" style="margin-top:8px">
        <span class="settings-key">Host</span><span>${esc(profile.host)}</span>
      </div>
    </div>
    <div class="detail-section">
      <h3>Scan-Einstellungen</h3>
      <div class="settings-grid">
        <span class="settings-key">Full-Scan Intervall</span><span>${s.full_scan_interval}s</span>
        <span class="settings-key">Quick-Check Intervall</span><span>${s.check_interval}s</span>
        <span class="settings-key">Daemon</span><span>${esc(daemonText)}</span>
        <span class="settings-key">Datenbank</span><span style="font-family:monospace;font-size:12px">${esc(s.db_path)}</span>
      </div>
      <p style="margin-top:12px;font-size:12px;color:var(--text-dim)">
        Intervalle werden in config.py konfiguriert und gelten ab dem nächsten Daemon-Start.
      </p>
    </div>
  `;
  openModal('Einstellungen', html);
}

async function saveProfile() {
  const name = document.getElementById('profile-name').value.trim();
  if (!name) { toast('Name darf nicht leer sein'); return; }
  await apiPut('/api/profile', { name });
  toast('Profil gespeichert');
}

function formatDaemonStatus(daemon) {
  if (!daemon || daemon.state === 'unknown') return 'nicht bekannt';
  const age = daemon.age_seconds == null ? 'unbekannt' : `${daemon.age_seconds}s`;
  const cache = daemon.update_cache ? 'Cache an' : 'Cache aus';
  if (daemon.state === 'running') {
    return `läuft, PID ${daemon.pid || '?'}, Heartbeat vor ${age}, ${cache}`;
  }
  return `nicht aktuell, PID ${daemon.pid || '?'}, letzter Heartbeat vor ${age}, ${cache}`;
}

// ── Actions ──────────────────────────────────────
async function triggerScan() {
  const btn = document.querySelector('[onclick="triggerScan()"]');
  if (btn) { btn.disabled = true; btn.textContent = 'Scan...'; }
  toast('Scan läuft...');
  const result = await apiPost('/api/scan', {});
  if (!result.error) {
    toast(`Scan: ${result.locks_found} Locks, ${result.new || 0} neu`);
  }
  if (btn) { btn.disabled = false; btn.textContent = 'Scan'; }
  loadRoomMap();
  loadStats();
}

async function triggerPrune() {
  const btn = document.querySelector('[onclick="triggerPrune()"]');
  if (btn) { btn.disabled = true; btn.textContent = 'Prune...'; }
  toast('Bereinigung läuft...');
  const result = await apiPost('/api/prune', {});
  if (!result.error) {
    toast('Bereinigung abgeschlossen');
  }
  if (btn) { btn.disabled = false; btn.textContent = 'Prune'; }
  loadRoomMap();
  loadStats();
}

// ── Modal ────────────────────────────────────────
function openModal(title, bodyHtml) {
  document.getElementById('modal-title').textContent = title;
  document.getElementById('modal-body').innerHTML = bodyHtml;
  document.getElementById('modal-overlay').classList.remove('hidden');
}

function closeModal(event) {
  if (event && event.target !== document.getElementById('modal-overlay')) return;
  document.getElementById('modal-overlay').classList.add('hidden');
}

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    document.getElementById('modal-overlay').classList.add('hidden');
  }
});

// ── Util ─────────────────────────────────────────
function esc(str) {
  if (!str) return '';
  const div = document.createElement('div');
  div.textContent = String(str);
  return div.innerHTML;
}

function formatTime(iso) {
  if (!iso) return '-';
  try {
    const d = new Date(iso);
    return d.toLocaleString('de-DE', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
  } catch {
    return iso;
  }
}

function formatBytes(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}
