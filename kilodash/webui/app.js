/* Scottina Web Mirror — client.
 *
 * Renders the model the box emits and adds NOTHING (WEB-UI-DESIGN §0):
 *   - computes no number the box did not send (no ETA, no throughput rate);
 *   - draws no control needing data the model lacks (no bounded gauges);
 *   - never optimistically applies its own command — the display changes only
 *     when a frame says so. A tap gives input feedback, never state feedback.
 *
 * Data contract: To-DoLists/WEB-PROTOCOL.md. Presentation: WEB-UI-DESIGN.md.
 * If the two disagree, the protocol wins and this file is the bug.
 *
 * No dependencies, no build step, no network beyond the box's own origin.
 */
'use strict';

// ---------------------------------------------------------------- state ----
const S = {
  theme: null,
  tile: null,
  nav: ['home'],
  model: null,
  tiles: [],
  alerts: {},
  seq: null,
  rev: null,
  live: false,
  kind: null,          // model.kind currently mounted, so we know when to remount
};

const $ = (id) => document.getElementById(id);
const panel = $('panel');

// ---------------------------------------------------------------- theme ----
const THEME_KEYS = ['bg', 'card', 'card_hi', 'fg', 'muted', 'accent',
                    'ok', 'warn', 'bad'];

/* §2: the palette is normative and arrives in Hello. Never hardcode a
 * phosphor colour — a mid-stream Hello re-themes the whole UI live. */
function applyTheme(theme) {
  if (!theme) return;
  const root = document.documentElement.style;
  for (const k of THEME_KEYS) {
    const v = theme[k];
    if (Array.isArray(v) && v.length === 3) {
      root.setProperty('--' + k.replace('_', '-'), `rgb(${v.join(',')})`);
    }
  }
  $('thm').textContent = theme.name ? 'SKIN·' + theme.name.toUpperCase() : '';
  S.theme = theme;
}

// --------------------------------------------------------------- glyphs ----
/* §8: one system, not 22 unrelated icons. Common 64u grid, uniform bold
 * stroke, geometric primitives only — must read at tile size across a room.
 * Keyed by the `glyph` name the protocol sends; unknown key falls back to
 * `std` rather than leaving a gap. */
const G = {
  can:      '<line x1="8" y1="32" x2="56" y2="32"/><circle cx="18" cy="32" r="6"/><circle cx="46" cy="32" r="6"/><line x1="18" y1="20" x2="18" y2="10"/><line x1="46" y1="44" x2="46" y2="54"/>',
  n2k:      '<line x1="8" y1="32" x2="56" y2="32"/><circle cx="32" cy="32" r="9"/><line x1="32" y1="10" x2="32" y2="23"/><line x1="32" y1="41" x2="32" y2="54"/>',
  gps:      '<circle cx="32" cy="32" r="6"/><line x1="32" y1="8" x2="32" y2="19"/><line x1="32" y1="45" x2="32" y2="56"/><line x1="8" y1="32" x2="19" y2="32"/><line x1="45" y1="32" x2="56" y2="32"/><path d="M46 16 A22 22 0 0 1 46 48"/>',
  lightdock:'<path d="M14 18 L26 32 L14 46"/><path d="M50 18 L38 32 L50 46"/><line x1="30" y1="32" x2="34" y2="32"/>',
  lan:      '<rect x="10" y="38" width="44" height="16"/><line x1="32" y1="12" x2="32" y2="38"/><circle cx="32" cy="12" r="5"/><line x1="20" y1="46" x2="26" y2="46"/>',
  wifi:     '<path d="M12 26 A28 28 0 0 1 52 26"/><path d="M20 36 A18 18 0 0 1 44 36"/><circle cx="32" cy="48" r="4"/>',
  wifisniff:'<path d="M12 24 A28 28 0 0 1 52 24"/><circle cx="32" cy="46" r="4"/><line x1="44" y1="40" x2="56" y2="52"/><line x1="56" y1="40" x2="44" y2="52"/>',
  sdr:      '<circle cx="32" cy="44" r="5"/><path d="M18 38 A18 18 0 0 1 46 38"/><path d="M8 32 A32 32 0 0 1 56 32"/><line x1="32" y1="49" x2="32" y2="58"/>',
  ais:      '<path d="M12 44 L32 12 L52 44"/><line x1="20" y1="44" x2="44" y2="44"/><line x1="32" y1="44" x2="32" y2="56"/>',
  i2c:      '<line x1="10" y1="24" x2="54" y2="24"/><line x1="10" y1="42" x2="54" y2="42"/><circle cx="22" cy="24" r="5"/><circle cx="42" cy="42" r="5"/>',
  serial:   '<rect x="10" y="22" width="44" height="20"/><line x1="20" y1="42" x2="20" y2="52"/><line x1="32" y1="42" x2="32" y2="52"/><line x1="44" y1="42" x2="44" y2="52"/>',
  logic:    '<path d="M8 44 L20 44 L20 20 L34 20 L34 44 L48 44 L48 20 L56 20"/>',
  files:    '<path d="M14 12 L38 12 L50 24 L50 52 L14 52 Z"/><line x1="38" y1="12" x2="38" y2="24"/><line x1="50" y1="24" x2="38" y2="24"/>',
  tables:   '<rect x="10" y="14" width="44" height="36"/><line x1="10" y1="26" x2="54" y2="26"/><line x1="26" y1="14" x2="26" y2="50"/>',
  kismet:   '<circle cx="32" cy="32" r="20"/><circle cx="32" cy="32" r="8"/><line x1="32" y1="4" x2="32" y2="12"/><line x1="32" y1="52" x2="32" y2="60"/>',
  nodered:  '<circle cx="16" cy="32" r="6"/><circle cx="48" cy="18" r="6"/><circle cx="48" cy="46" r="6"/><line x1="22" y1="32" x2="42" y2="20"/><line x1="22" y1="32" x2="42" y2="44"/>',
  signalk:  '<path d="M12 46 Q32 10 52 46"/><circle cx="32" cy="46" r="5"/><line x1="12" y1="54" x2="52" y2="54"/>',
  pomodoro: '<circle cx="32" cy="36" r="18"/><line x1="32" y1="36" x2="32" y2="24"/><line x1="32" y1="36" x2="42" y2="40"/><line x1="26" y1="12" x2="38" y2="12"/>',
  health:   '<rect x="12" y="14" width="40" height="36"/><line x1="12" y1="26" x2="52" y2="26"/><line x1="24" y1="14" x2="24" y2="50"/>',
  microkvm: '<rect x="10" y="16" width="44" height="28"/><line x1="24" y1="52" x2="40" y2="52"/><line x1="32" y1="44" x2="32" y2="52"/>',
  settings: '<circle cx="32" cy="32" r="10"/><line x1="32" y1="6" x2="32" y2="16"/><line x1="32" y1="48" x2="32" y2="58"/><line x1="6" y1="32" x2="16" y2="32"/><line x1="48" y1="32" x2="58" y2="32"/>',
  std:      '<rect x="12" y="12" width="40" height="40"/><line x1="12" y1="12" x2="52" y2="52"/>',
};
const glyphSvg = (name) =>
  `<svg viewBox="0 0 64 64" aria-hidden="true">${G[name] || G.std}</svg>`;

// -------------------------------------------------------------- commands ----
/* §9: no ack, no correlation id. A POST is fire-and-observe; the screen
 * changes when the resulting frame arrives, never on the tap itself. */
async function send(action, extra) {
  try {
    const r = await fetch('/api/input', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(Object.assign({action}, extra || {})),
    });
    if (!r.ok) showReject();          // 400/503 — a local, honest failure
  } catch (e) {
    showReject();
  }
}

function showReject() {
  const r = $('reject');
  r.classList.remove('show');
  void r.offsetWidth;                 // restart the animation
  r.classList.add('show');
}

// ----------------------------------------------------------------- utils ----
const esc = (s) => String(s == null ? '' : s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const up = (s) => esc(s).toUpperCase();
const stateClass = (st) => st ? ' val-' + esc(st) : '';

function fmtClock(t) {
  if (!t) return '--:--:--';
  const d = new Date(t * 1000);
  return [d.getHours(), d.getMinutes(), d.getSeconds()]
    .map((n) => String(n).padStart(2, '0')).join(':');
}

/* Flash a cell to `accent` and let it decay — the phosphor-write (§5). This
 * is the delta made visible, and it is the whole reward of the event design:
 * you see exactly what changed. */
function flash(el) {
  if (!el) return;
  el.classList.add('hot');
  setTimeout(() => el.classList.remove('hot'), 60);
}

// ------------------------------------------------------------- renderers ----
/* Each renderer owns its subtree. `mount` builds it; `patch` updates in place
 * so scroll position survives and changed cells can flash individually. A
 * full rebuild every delta would both lose the scroll and make the flash
 * meaningless (everything would look changed). */
const R = {};

// --- §7.1 home -------------------------------------------------------------
R.home = {
  mount(m) {
    panel.innerHTML = '<div class="grid" id="grid"></div>';
    this.patch(m, true);
  },
  patch(m) {
    const tiles = (m.tiles || []);
    const grid = $('grid');
    if (!grid) return;
    grid.innerHTML = tiles.map((t) => {
      const absent = t.available === false;
      return `<button class="tile${absent ? ' absent' : ''}"
        ${absent ? 'disabled aria-disabled="true"' : ''}
        data-tile="${esc(t.id)}">
        ${glyphSvg(t.glyph)}
        <span class="tname${absent ? '' : ' bloom'}">${up(t.title)}</span>
        ${t.badge === 'lit' ? '<span class="badge"></span>' : ''}
      </button>`;
    }).join('');
    if (!tiles.length) {
      grid.innerHTML = '<div class="empty">no tiles reported</div>';
    }
  },
};

// --- §7.2 canbus -----------------------------------------------------------
const SEGN = 24;
const METER_CEIL = 1000;      // f/s at full scale — presentation only

R.canbus = {
  mount(m) {
    panel.innerHTML = `
      <div class="phead">CAN BUS <span class="sub" id="cb-sub"></span></div>
      <div class="meter" id="cb-meter"></div>
      <div class="statline" id="cb-stat"></div>
      <div class="tablewrap"><table>
        <thead><tr>
          <th>ID</th><th class="r">CNT</th><th class="r">HZ</th>
          <th class="r">DLC</th><th>DATA</th><th>NAME</th>
        </tr></thead>
        <tbody id="cb-rows"></tbody>
      </table></div>
      <div id="cb-trunc"></div>`;
    const meter = $('cb-meter');
    for (let i = 0; i < SEGN; i++) {
      const d = document.createElement('span');
      d.className = 'seg';
      meter.appendChild(d);
    }
    const lab = document.createElement('span');
    lab.className = 'mlabel';
    lab.id = 'cb-mlabel';
    meter.appendChild(lab);
    this.prev = {};
    this.patch(m, true);
  },
  patch(m, first) {
    $('cb-sub').textContent = `${(m.iface || '—').toUpperCase()} · ` +
      `${m.bitrate ? (m.bitrate / 1000) + 'K' : '—'}`;

    // segmented frame-rate meter — legitimate here: frame_rate is a single
    // emitted scalar with a sensible ceiling, no missing bounds (§7.2).
    const hz = Number(m.frame_rate || 0);
    const lit = Math.max(0, Math.min(SEGN, Math.round(hz / METER_CEIL * SEGN)));
    const segs = $('cb-meter').querySelectorAll('.seg');
    segs.forEach((s, i) => {
      s.className = 'seg' + (i < lit ? (i > SEGN * 0.8 ? ' lit hi' : ' lit') : '');
    });
    $('cb-mlabel').textContent = hz + ' F/S';

    const st = String(m.state || '—');
    // The box emits presentation states (none|standby|listen|fault) —
    // canbus.py deliberately performs no I/O for presentation, so bus-off is
    // not observable from here (§4.3 amendment). up/down are tolerated as
    // aliases in case the model ever gains a real controller read.
    const stCls = (st === 'listen' || st === 'up') ? 'up'
                : (st === 'fault' || st === 'bus-off' || st === 'error') ? 'bad' : '';
    $('cb-stat').innerHTML =
      `<span>IFACE <b>${up(m.iface || '—')}</b></span>` +
      `<span>STATE <b class="${stCls}">${up(st)}</b></span>` +
      `<span>TOTAL <b>${esc(m.total ?? 0)}</b></span>`;

    const tb = $('cb-rows');
    const rows = m.rows || [];
    if (!rows.length) {
      tb.innerHTML = '<tr><td colspan="6" class="empty">no traffic</td></tr>';
      this.prev = {};
    } else {
      // Rows render in the ORDER THE MODEL SENDS THEM — never re-sorted
      // client-side, so the web and the panel agree on what row 1 is (§7.2).
      const seen = new Set();
      rows.forEach((r, idx) => {
        seen.add(r.id);
        let tr = tb.querySelector(`tr[data-id="${CSS.escape(r.id)}"]`);
        const bytes = String(r.data || '').split(/\s+/).filter(Boolean);
        if (!tr) {
          tr = document.createElement('tr');
          tr.dataset.id = r.id;
          tr.innerHTML =
            `<td class="hex">${esc(r.id)}</td>` +
            `<td class="r cell" data-f="count"></td>` +
            `<td class="r cell" data-f="hz"></td>` +
            `<td class="r cell" data-f="dlc"></td>` +
            `<td class="hex" data-f="data"></td>` +
            `<td data-f="name"></td>`;
          tb.appendChild(tr);
        }
        if (tb.children[idx] !== tr) tb.insertBefore(tr, tb.children[idx]);

        const p = this.prev[r.id] || {};
        const setCell = (f, v) => {
          const el = tr.querySelector(`[data-f="${f}"]`);
          if (el.textContent !== String(v)) {
            el.textContent = v;
            if (!first && p[f] !== undefined) flash(el);
          }
        };
        setCell('count', r.count ?? 0);
        setCell('hz', r.hz ?? 0);
        setCell('dlc', r.dlc ?? 0);

        // Per-BYTE flash, not per-row: the byte-level change is the RE
        // signal, and flashing the whole row would drown it (§5).
        const dataCell = tr.querySelector('[data-f="data"]');
        const pb = p.bytes || [];
        if (dataCell.childElementCount !== bytes.length) {
          dataCell.innerHTML = bytes
            .map((b) => `<span class="byte">${esc(b)}</span>`).join(' ');
        } else {
          bytes.forEach((b, i) => {
            const sp = dataCell.children[i];
            if (sp && sp.textContent !== b) {
              sp.textContent = b;
              if (!first && pb[i] !== undefined) flash(sp);
            }
          });
        }

        const nameCell = tr.querySelector('[data-f="name"]');
        nameCell.innerHTML = r.name ? esc(r.name) : '<span class="dim">—</span>';
        tr.className = r.alert ? 'alert' : '';
        this.prev[r.id] = {count: r.count, hz: r.hz, dlc: r.dlc, bytes};
      });
      [...tb.querySelectorAll('tr[data-id]')].forEach((tr) => {
        if (!seen.has(tr.dataset.id)) { tr.remove(); delete this.prev[tr.dataset.id]; }
      });
    }

    // A partial table must never read as a whole one (§7.2).
    $('cb-trunc').innerHTML = m.truncated
      ? `<div class="trunc">TABLE TRUNCATED · ${rows.length} SHOWN</div>` : '';
  },
};

// --- §7.3 n2k --------------------------------------------------------------
R.n2k = {
  mount(m) {
    panel.innerHTML = `
      <div class="phead">NMEA2K <span class="sub" id="n2-sub"></span></div>
      <div class="statline" id="n2-stat"></div>
      <div class="tablewrap"><table>
        <thead><tr>
          <th>PGN</th><th>FIELD</th><th class="r">VALUE</th><th class="r">AGE</th>
        </tr></thead>
        <tbody id="n2-rows"></tbody>
      </table></div>
      <div id="n2-trunc"></div>`;
    this.prev = {};
    this.patch(m, true);
  },
  patch(m, first) {
    $('n2-sub').textContent = (m.iface || '—').toUpperCase();
    const srcs = (m.sources || [])
      .map((s) => `<span>SRC <b>${esc(s.src)}</b> ${up(s.label || '')}</span>`)
      .join('');
    $('n2-stat').innerHTML =
      `<span>STATE <b class="${m.state === 'listen' ? 'up' : ''}">${up(m.state || '—')}</b></span>` +
      `<span>PGNS <b>${esc(m.pgns_loaded ?? 0)}</b></span>` +
      `<span>UNKNOWN <b>${esc(m.unknown ?? 0)}</b></span>` + srcs;

    const tb = $('n2-rows');
    const fields = m.fields || [];
    if (!fields.length) {
      tb.innerHTML = '<tr><td colspan="4" class="empty">no decoded fields</td></tr>';
      this.prev = {};
    } else {
      const seen = new Set();
      fields.forEach((f, idx) => {
        const key = `${f.pgn}:${f.src}:${f.name}`;
        seen.add(key);
        let tr = tb.querySelector(`tr[data-k="${CSS.escape(key)}"]`);
        if (!tr) {
          tr = document.createElement('tr');
          tr.dataset.k = key;
          tr.innerHTML =
            `<td>${esc(f.pgn)}<span class="dim">·${esc(f.src)}</span></td>` +
            `<td data-f="name"></td>` +
            `<td class="r cell" data-f="val"></td>` +
            `<td class="r" data-f="age"></td>`;
          tb.appendChild(tr);
        }
        if (tb.children[idx] !== tr) tb.insertBefore(tr, tb.children[idx]);

        // `disp` is the box's own formatted string (units folded in). Prefer
        // it over re-formatting `value` here — one formatter, both surfaces.
        const shown = f.disp != null && f.disp !== ''
          ? f.disp
          : (f.value == null ? '—'
             : f.value + (f.unit ? ' ' + f.unit : ''));
        tr.querySelector('[data-f="name"]').textContent = String(f.name || '').toUpperCase();
        const vc = tr.querySelector('[data-f="val"]');
        if (vc.textContent !== String(shown)) {
          vc.textContent = shown;
          if (!first && this.prev[key] !== undefined) flash(vc);
        }
        this.prev[key] = shown;
        tr.querySelector('[data-f="age"]').textContent =
          f.last_seen == null ? '—' : f.last_seen + 'S';

        // armed = a watch is configured (amber). alerting = actually firing
        // (red). An armed-but-unfired watch is NEVER red (§2).
        tr.className = f.alerting ? 'alert' : '';
        vc.classList.toggle('val-warn', !!f.armed && !f.alerting);
        // Stale rows grey toward muted — the box sends the age, so the web
        // needs no clock of its own (§7.3).
        tr.classList.toggle('dim', Number(f.last_seen) > 5);
      });
      [...tb.querySelectorAll('tr[data-k]')].forEach((tr) => {
        if (!seen.has(tr.dataset.k)) { tr.remove(); delete this.prev[tr.dataset.k]; }
      });
    }
    $('n2-trunc').innerHTML = m.truncated
      ? `<div class="trunc">FIELD LIST TRUNCATED · ${fields.length} SHOWN</div>` : '';
  },
};

// --- §7.4 lightdock --------------------------------------------------------
const PHASES = ['hello', 'clock', 'push', 'pull', 'done'];

R.lightdock = {
  mount(m) {
    panel.innerHTML = `
      <div class="phead">LIGHT DOCK <span class="sub" id="ld-sub"></span></div>
      <div class="stages" id="ld-stages"></div>
      <div class="prog"><div class="fill" id="ld-fill"></div></div>
      <div class="proglabel" id="ld-plabel"></div>
      <div class="log" id="ld-log"></div>`;
    this.patch(m, true);
  },
  patch(m) {
    const dev = m.device || {};
    const link = String(m.link || 'absent');
    $('ld-sub').innerHTML = link === 'error'
      ? `<span class="val-bad">${up(link)}</span>`
      : `${up(link)}${dev.product ? ' · ' + up(dev.fw || '') : ''}`;

    const sess = m.session || {};
    const phase = String(sess.phase || 'idle');
    const at = PHASES.indexOf(phase);
    $('ld-stages').innerHTML = PHASES.map((p, i) => {
      let cls = 'stage';
      if (phase === 'error') cls += i === 0 ? ' err' : '';
      else if (at >= 0 && i < at) cls += ' done';
      else if (i === at) cls += ' active';
      return `<div class="${cls}">${up(p)}</div>`;
    }).join('');

    // Progress renders from the box's own figures ONLY — no rate, no ETA.
    // Both surfaces must agree on the number a user reads aloud (§0).
    const b = Number(sess.bytes || 0), bt = Number(sess.bytes_total || 0);
    const pct = bt > 0 ? Math.max(0, Math.min(100, b / bt * 100)) : 0;
    $('ld-fill').style.width = pct + '%';
    const parts = [up(phase)];
    if (bt > 0) parts.push(`${b} / ${bt} B`);
    const c = sess.counts || {};
    const tallies = Object.keys(c).filter((k) => c[k])
      .map((k) => `${k.replace(/_/g, ' ').toUpperCase()} ${c[k]}`);
    $('ld-plabel').textContent = parts.concat(tallies).join(' · ');

    const log = m.log || [];
    $('ld-log').innerHTML = log.length
      ? log.map((l) => `<div class="ln"><span class="t">${fmtClock(l.t)}</span>` +
          `<span class="${esc(l.level || 'info')}">${up(l.text)}</span></div>`).join('')
      : '<div class="empty">no session</div>';
    const lg = $('ld-log');
    lg.scrollTop = lg.scrollHeight;      // ship-log: newest at the bottom
  },
};

// --- §7.5 generic (the compatibility floor) --------------------------------
R.generic = {
  mount(m) {
    panel.innerHTML = `
      <div class="phead"><span id="gn-title"></span>
        <span class="sub">GENERIC MODEL</span></div>
      <div id="gn-rows"></div>
      <div class="btns" id="gn-btns"></div>
      <div class="note" id="gn-note"></div>`;
    this.prev = {};
    this.patch(m, true);
  },
  patch(m, first) {
    $('gn-title').textContent = String(m.title || '').toUpperCase();
    const rows = m.rows || [];
    const host = $('gn-rows');
    if (!rows.length) {
      host.innerHTML = '<div class="empty">no rows reported</div>';
    } else {
      rows.forEach((r, idx) => {
        const key = String(r.label);
        let band = host.querySelector(`[data-k="${CSS.escape(key)}"]`);
        if (!band) {
          band = document.createElement('div');
          band.className = 'band';
          band.dataset.k = key;
          band.innerHTML = `<span class="lbl">${up(r.label)}</span>` +
                           `<span class="v cell"></span>`;
          host.appendChild(band);
        }
        if (host.children[idx] !== band) host.insertBefore(band, host.children[idx]);
        const v = band.querySelector('.v');
        const txt = String(r.value ?? '—');
        if (v.textContent !== txt) {
          v.textContent = txt;
          if (!first && this.prev[key] !== undefined) flash(v);
        }
        this.prev[key] = txt;
        v.className = 'v cell' + stateClass(r.state);
      });
      const keys = new Set(rows.map((r) => String(r.label)));
      [...host.querySelectorAll('[data-k]')].forEach((b) => {
        if (!keys.has(b.dataset.k)) b.remove();
      });
    }

    const btns = m.buttons || [];
    $('gn-btns').innerHTML = btns.map((b) =>
      `<button class="btn" data-btn="${esc(b.id)}"
        ${b.enabled === false ? 'disabled' : ''}>${up(b.label || b.id)}</button>`
    ).join('');
    $('gn-note').textContent = m.note ? String(m.note).toUpperCase() : '';
  },
};

/* §9 of the protocol: an unknown kind renders as generic when it carries
 * `rows`, else a placeholder that NAMES the kind — never blank, never an
 * error. This is what lets a screen be promoted to a rich model without a
 * version bump, and what tells an operator their bundle is stale. */
function rendererFor(model) {
  if (!model || !model.kind) return null;
  if (R[model.kind]) return R[model.kind];
  if (Array.isArray(model.rows)) return R.generic;
  return {
    mount(m) {
      panel.innerHTML =
        `<div class="phead">${up(m.kind)}<span class="sub">NO RENDERER</span></div>` +
        `<div class="empty">this bundle has no renderer for model kind ` +
        `"${up(m.kind)}" — the box is newer than the web UI</div>`;
    },
    patch() {},
  };
}

// ----------------------------------------------------------------- chrome ----
function renderChrome() {
  const nav = S.nav || ['home'];
  const onHome = nav.length < 2;
  $('back').disabled = onHome;
  // nav is at most two deep — a two-level star, not a stack (§4).
  $('crumb').innerHTML = onHome
    ? 'HOME'
    : `HOME<span class="sep">&#9656;</span>${up(nav[1])}`;
  $('f-seq').textContent = S.seq ?? '—';
  $('f-rev').textContent = S.rev ?? '—';
}

function setLive(live) {
  S.live = live;
  $('instrument').classList.toggle('stale', !live);
  $('loz-link').classList.toggle('on', live);
  $('loz-hold').classList.toggle('on', !live);
}

function render(remount) {
  const r = rendererFor(S.model);
  if (!r) {
    panel.innerHTML = '<div class="empty">awaiting snapshot…</div>';
    return;
  }
  if (remount || S.kind !== S.model.kind) {
    S.kind = S.model.kind;
    r.mount(S.model);
    panel.classList.remove('bootin');
    void panel.offsetWidth;
    panel.classList.add('bootin');
  } else {
    r.patch(S.model, false);
  }
  renderChrome();
}

// ------------------------------------------------------------------ frames ----
function onFrame(f) {
  if (f.v !== undefined && f.v !== 1) return;   // §9: never guess a shape
  if (typeof f.seq === 'number' && f.seq) S.seq = f.seq;

  switch (f.type) {
    case 'Hello':
      applyTheme(f.theme);
      $('dev').textContent = String(f.device || 'scottina').toUpperCase();
      $('ver').textContent = f.kilodash_version
        ? 'KDASH ' + f.kilodash_version : '';
      // A mid-stream Hello re-themes and NOTHING else: it is not a new
      // connection, so the model is kept and no resync is issued (§3).
      break;

    case 'ScreenSnapshot':
      S.tile = f.tile; S.nav = f.nav || ['home'];
      S.tiles = f.tiles || S.tiles;
      S.model = f.model || null;
      S.rev = f.rev ?? 0;
      S.alerts = {};
      (f.alerts || []).forEach((a) => { if (a && a.id) S.alerts[a.id] = a; });
      setLive(true);
      render(true);
      break;

    case 'TileChanged':
      S.tile = f.tile; S.nav = f.nav || ['home'];
      S.model = f.model || null;
      S.rev = f.rev ?? 0;
      setLive(true);
      render(true);
      break;

    case 'DataUpdated':
      // Shallow merge at the top level, arrays whole — exactly as §4 says.
      // No deep merge, no array patching.
      if (!S.model) break;
      if (f.tile && S.tile && f.tile !== S.tile) break;   // stale in-flight
      S.rev = f.rev;
      Object.assign(S.model, f.changed || {});
      render(false);
      break;

    case 'AlertFired':
      if (f.alert && f.alert.id) S.alerts[f.alert.id] = f.alert;
      break;
    case 'AlertCleared':
      if (f.alert && f.alert.id) delete S.alerts[f.alert.id];
      break;

    case 'Error':
      // A bounced command cannot be tied to a specific POST — there is no
      // correlation id — so the honest treatment is a transient notice that
      // says "the box didn't move", not a per-action failure claim (§9).
      if (f.code === 'bad_command') showReject();
      if (f.code === 'resync') setLive(false);
      break;
  }
  renderChrome();
}

// -------------------------------------------------------------------- SSE ----
/* EventSource reconnects on its own with backoff — that is a large part of
 * why §1 chose SSE. On reconnect the backend sends Hello + a fresh snapshot,
 * so the client resyncs from the box and never patches a stale base (§5). */
function connect() {
  const es = new EventSource('/api/stream');
  es.onopen = () => { /* frames decide liveness, not the socket alone */ };
  es.onmessage = (e) => {
    let f;
    try { f = JSON.parse(e.data); } catch (_) { return; }
    onFrame(f);
  };
  es.onerror = () => setLive(false);
}

// ------------------------------------------------------------------ input ----
/* One delegated listener: tiles, buttons and BACK. Press feedback is CSS
 * (:active); nothing here mutates S — the display changes only when a frame
 * arrives (§0, §9). */
document.addEventListener('click', (ev) => {
  const tile = ev.target.closest('[data-tile]');
  if (tile && !tile.disabled) { send('tap_tile', {tile: tile.dataset.tile}); return; }
  const btn = ev.target.closest('[data-btn]');
  if (btn && !btn.disabled) { send('button_press', {button: btn.dataset.btn}); return; }
  if (ev.target.closest('#back')) send('back');
});

document.addEventListener('keydown', (ev) => {
  if (ev.key === 'Escape') send('back');
});

setLive(false);
renderChrome();
panel.innerHTML = '<div class="empty">connecting…</div>';
connect();
