'use strict';
// Lelelilo Gamemaker: the board. Every few seconds it asks the server for the prompts and redraws what changed: the map
// of every branch and version, the line of the one in view, and the chosen one with its result (the game the Lelelilo
// published, played right here in a frame). The starting point, the game before the first prompt, is always there to
// play. On a phone the game isn't loaded in the page (it would catch every swipe): a button opens it full screen.
//
// Every prompt builds on a version: the newest of the branch in view, or one picked ("Build on this version"). Going on
// from a version that already goes on starts a new branch.
//
// While a prompt is on its way, Neri (the games Lelelilo of lelelilo.com) is shown at the workbench: sewing, hopping
// over to playtest on the arcade, wiping its brow. The words come from words.js, in English or Spanish, and simpler in
// Kids mode. A browser can also ask to be told when the Lelelilo finishes (sw.js shows the notification).
(() => {
  const POLL_MS = 4000;
  const ONLINE_FOR = 150;   // the watcher checks in every 30 s: after this many seconds without a word it's away
  const DRAFT = 'gamemaker_draft';
  const VIEW = 'gamemaker_view';
  const LANG = 'gamemaker_lang';
  const KIDS = 'gamemaker_kids';
  const KNOWS_MAP = 'gamemaker_knows_map';  // the map's key shows until its first button is tapped
  const START = 0;          // the starting point, in place of a prompt's number
  const PHONE = matchMedia('(max-width: 899px), (pointer: coarse) and (max-height: 500px)');  // upright or on its side
  const STILL = matchMedia('(prefers-reduced-motion: reduce)');
  const LIVE = new Set(['queued', 'running', 'done']);  // a version that is, or will be: what can be built on
  const BEATS = [['sew', 5200], ['hop', 800], ['play', 3600], ['wipe', 1400], ['back', 800]];  // Neri at work, in a loop
  const SEAM_MINUTES = 10;  // the working seam is about 60% sewn after this long, and never quite reaches the end

  const $ = id => document.getElementById(id);
  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  };
  const svgEl = (tag, attrs) => {
    const e = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
    return e;
  };
  const saved = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private window */ } },
  };

  // -- words: English or Spanish, simpler in Kids mode
  const WORDS = window.WORDS;
  function guessLang() {
    const first = ((navigator.languages && navigator.languages[0]) || navigator.language || 'en').toLowerCase();
    return first.startsWith('es') ? 'es' : 'en';
  }
  let lang = saved.get(LANG) === 'es' ? 'es' : saved.get(LANG) === 'en' ? 'en' : guessLang();
  let kids = saved.get(KIDS) === '1';
  function t(key, vars = {}) {
    const tables = kids ? [WORDS[`${lang}_kids`], WORDS[lang], WORDS.en] : [WORDS[lang], WORDS.en];
    for (const table of tables) {
      if (table && key in table) {
        const v = table[key];
        return typeof v === 'function' ? v(vars) : typeof v === 'string' ? v.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? vars[k] : m)) : v;
      }
    }
    return key;
  }
  const statusWord = s => t(`st_${s}`);

  // -- state
  let state = null;      // the last answer from /api/state
  let skew = 0;          // the server's clock minus ours, in seconds
  let selected = null;   // the prompt in the viewer
  let pinned = false;    // an older one was picked by hand: newer prompts don't take the viewer over
  let meanwhile = null;  // a prompt still on its way while an older version is played: when it's ready, it's shown
  let shown = null;      // what the viewer last drew (id, updated_at, stalled): details are fetched again only when it changes
  let detail = null;     // that prompt, with its notes
  let frameUrl = null;   // what the frame holds. It is only touched when this changes, so a game in play isn't reset
  let result = { id: null, url: null };  // whose result the viewer shows, and where it is
  let playing = false;   // the player is open: the game covers the screen
  let wantPlay = null;   // open the player as soon as this one's result is shown
  let viewSeq = 0;
  let polling = false;
  let tree = null;       // which version builds on which, from the last state: see lineage()
  let base = null;       // the version picked for the next prompt to build on (null: the newest of the branch in view)
  let candidate = null;  // in the picker: the version tapped, not yet confirmed
  let mapperMode = null; // the large map is open: 'browse' or 'pick'
  let view = saved.get(VIEW) === 'list' ? 'list' : 'line';
  const unfolded = new Set();  // lines whose earlier versions were unfolded by hand
  const drawn = new Map();     // what each part was last drawn from: it's drawn again only when that changes
  const cards = new Map();
  const lastStatus = new Map();  // each prompt's status at the last poll: a change to done while watched is celebrated
  const sewn = new Set();        // the versions the map has shown: one that appears later is sewn on with a pop
  let readyTitle = null;

  // -- time
  const serverNow = () => Date.now() / 1000 + skew;
  function ago(at) {
    const s = Math.max(0, Math.round(serverNow() - at));
    if (s < 45) return t('just_now');
    if (s < 90) return t('a_min');
    const m = Math.round(s / 60);
    if (m < 60) return t('mins', { n: m });
    const h = Math.round(m / 60);
    if (h < 24) return t('hours', { n: h });
    const d = Math.round(h / 24);
    return d === 1 ? t('yesterday') : t('days', { n: d });
  }
  const locale = () => (lang === 'es' ? 'es' : 'en');
  const full = at => new Date(at * 1000).toLocaleString(locale(), { dateStyle: 'medium', timeStyle: 'short' });
  const short = at => {
    const d = new Date(at * 1000);
    return d.toDateString() === new Date().toDateString()
      ? d.toLocaleTimeString(locale(), { hour: '2-digit', minute: '2-digit' })
      : d.toLocaleDateString(locale(), { day: 'numeric', month: 'short' });
  };

  const statusOf = p => (p.stalled ? 'stalled' : p.status);
  const isBrother = () => state && state.me.role === 'brother';
  const branching = () => !!(state && state.agent.branches && isBrother());

  // -- the tree: which version builds on which, and the branches
  function lineage(s) {
    const order = [...s.prompts].sort((a, b) => a.id - b.id);
    const branches = new Map(s.branches.map((b, i) => [b.id, { ...b, colour: i % 6, versions: [] }]));
    for (const p of order) (branches.get(p.branch) || branches.get(0)).versions.push(p);
    const kids = new Map();  // version → the live versions built on it
    for (const p of order) {
      if (!LIVE.has(p.status)) continue;
      const at = p.parent ?? START;
      if (!kids.has(at)) kids.set(at, []);
      kids.get(at).push(p);
    }
    return { byId: new Map(order.map(p => [p.id, p])), order, branches, kids };
  }
  const live = p => !!p && LIVE.has(p.status);
  const canBuildOn = v => v === START || live(tree.byId.get(v));
  const branchOf = v => (v === START ? 0 : (tree.byId.get(v) || { branch: 0 }).branch);
  const colourOf = br => (tree.branches.get(br) || { colour: 0 }).colour;
  const shortName = br => (tree.branches.get(br) || { name: '?' }).name;
  const tagName = br => (br === 0 ? t('main') : t('branch', { name: shortName(br) }));
  const longName = br => (br === 0 ? t('the_main') : t('the_branch', { name: shortName(br) }));
  // the name the next branch will get: the server names them in order, B, C, ... Z, AA
  function nextName() {
    let k = state.branches.length + 1, name = '';
    while (k) { const r = (k - 1) % 26; name = String.fromCharCode(65 + r) + name; k = Math.floor((k - 1) / 26); }
    return name;
  }
  const words = (text, n) => {
    const w = String(text || '').split(/\s+/).filter(Boolean);
    return w.slice(0, n).join(' ') + (w.length > n ? '…' : '');
  };
  const titleOf = v => {
    if (v === START) return t('start');
    const p = tree.byId.get(v);
    const s = p ? (p.title || words(p.text, 8)) : '';
    return s.charAt(0).toUpperCase() + s.slice(1);
  };
  const versionLabel = v => (v === START ? t('the_start') : t('version_label', { id: v, title: titleOf(v) }));
  // whether a branch already goes on from a version: then building there starts a new branch (the server's rule)
  const goesOn = (v, br) => (tree.kids.get(v) || []).some(q => q.branch === br);
  function tipOf(br) {
    const b = tree.branches.get(br);
    const vs = b ? b.versions.filter(live) : [];
    return vs.length ? vs[vs.length - 1].id : null;
  }
  const childOn = (v, br) => (tree.kids.get(v) || []).find(q => q.branch === br);
  // what the next prompt builds on: the one picked, or the newest of the branch in view
  function baseNow() {
    if (base != null && !canBuildOn(base)) base = null;  // it failed or was cancelled meanwhile
    if (base != null) return base;
    const p = selected != null && selected !== START ? tree.byId.get(selected) : null;
    const br = p ? p.branch : 0;
    const tip = tipOf(br);
    if (tip != null) return tip;
    const b = tree.branches.get(br);
    return br !== 0 && b && b.from != null && canBuildOn(b.from) ? b.from : (tipOf(0) ?? START);
  }
  // the nearest version at or above `v` that can be played
  function playableFrom(v) {
    while (v != null && v !== START) {
      const p = tree.byId.get(v);
      if (!p) break;
      if (p.status === 'done' && p.result_url) return v;
      v = p.parent;
    }
    return state.game ? START : null;
  }
  // the versions from the starting point down to `v` (both included)
  function ancestry(v) {
    const up = [];
    while (v != null && v !== START) {
      up.unshift(v);
      const p = tree.byId.get(v);
      v = p ? p.parent ?? START : START;
    }
    return [START, ...up];
  }
  function branchFacts(b) {
    const vs = b.versions.filter(live);
    const done = b.versions.filter(p => p.status === 'done');
    const coming = vs.length - done.length;
    const from = b.id === 0 || b.from == null || b.from === START ? t('from_start')
      : branchOf(b.from) !== b.id ? t('from_v_on', { id: b.from, branch: tagName(branchOf(b.from)) }) : t('from_v', { id: b.from });
    const count = t('versions', { n: done.length }) + (coming ? ` · ${t('to_come', { n: coming })}` : '');
    const summary = b.summary || done.map(p => titleOf(p.id)).join(' → ') || (vs.length ? t('nothing_made') : t('nothing_here'));
    return { from, count, summary, dead: !vs.length };
  }

  // -- the server
  async function api(path, body) {
    const opts = body === undefined
      ? { cache: 'no-store' }
      : { method: 'POST', cache: 'no-store', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
    const res = await fetch('/api/' + path, opts);
    if (res.status === 204) return null;
    let data = null;
    try { data = await res.json(); } catch (e) { /* not JSON */ }
    if (!res.ok) {
      const err = new Error((data && data.error) || `${res.status}`);
      err.status = res.status;
      throw err;
    }
    return data;
  }

  async function poll() {
    if (polling) return;
    polling = true;
    try {
      const s = await api('state');
      skew = s.now - Date.now() / 1000;
      state = s;
      $('offline').hidden = true;
      render();
      $('loader').classList.add('done');
    } catch (e) {
      $('offline').hidden = false;
    } finally {
      polling = false;
    }
  }

  // -- drawing
  function chip(node, status) {
    node.className = 'chip';
    node.dataset.status = status;
    node.textContent = statusWord(status);
    return node;
  }
  const colourClass = br => `c${colourOf(br)}`;
  const inkOf = br => getComputedStyle(document.documentElement).getPropertyValue(`--b${colourOf(br)}`).trim() || '#c0392b';

  function render() {
    const prompts = state.prompts;
    $('compose').hidden = !isBrother();
    tree = lineage(state);

    // whether a Lelelilo is around
    const working = prompts.find(p => p.status === 'running' && !p.stalled);
    const seen = state.agent.last_seen;
    const pill = $('agent');
    if (working) {
      pill.dataset.state = 'working';
      $('agent-text').textContent = t('pill_working', { id: working.id });
    } else if (seen && serverNow() - seen < ONLINE_FOR) {
      pill.dataset.state = 'online';
      $('agent-text').textContent = t('pill_online', { ago: ago(seen) });
    } else {
      pill.dataset.state = seen ? 'away' : 'unknown';
      $('agent-text').textContent = seen ? t('pill_away', { ago: ago(seen) }) : t('pill_never');
    }
    pill.title = seen ? t('pill_title', { when: full(seen) }) : '';

    const c = state.counts;
    const bits = [];
    if (c.queued) bits.push(t('n_waiting', { n: c.queued }));
    if (c.running) bits.push(t('n_working', { n: c.running }));
    if (c.done) bits.push(t('n_done', { n: c.done }));
    $('counts').textContent = bits.join(' · ');

    const newest = prompts[0];
    tabIcon(readyTitle ? 'ready' : working ? 'working' : null);
    document.title = readyTitle || (newest && newest.status === 'running' ? t('doc_working')
      : newest && newest.status === 'queued' ? t('doc_waiting') : t('doc'));

    // the prompt played "meanwhile" is ready (or stuck): back to it. While the player is open, what's in it stays put
    // (a newer result is offered in its bar), and the board catches up when it's closed
    if (meanwhile != null && !playing) {
      const w = prompts.find(p => p.id === meanwhile);
      if (!w || !(w.status === 'queued' || w.status === 'running') || w.stalled) {
        meanwhile = null;
        if (w) { selected = w.id; pinned = w.id !== newest.id; setHash(); }
      }
    }

    // which one the viewer shows: the newest, unless an older one was picked; with none sent yet, the starting point
    if (selected === START && !state.game) selected = null;
    if (selected == null || (!pinned && !playing)) selected = newest ? newest.id : state.game ? START : null;

    // the one in view just finished: Neri holds it up
    for (const p of prompts) {
      const before = lastStatus.get(p.id);
      if (before && before !== 'done' && p.status === 'done' && p.id === selected && p.result_url) celebrate(p.id);
      lastStatus.set(p.id, p.status);
    }

    const showList = view === 'list' && !kids;
    $('show-line').setAttribute('aria-selected', String(!showList));
    $('show-list').setAttribute('aria-selected', String(showList));
    $('line').hidden = showList;
    $('list').hidden = !showList;
    drawMap($('map'), 'view');
    if (showList) renderList(prompts);
    else drawLine();
    if ($('mapper').open) drawMapper();
    $('empty').hidden = prompts.length > 0 || !!state.game;
    drawBase();
    renderView();
  }

  function renderList(prompts) {
    const list = $('list');
    const keep = new Set();
    let prev = null;
    const place = (id, cls, parts) => {
      let li = cards.get(id);
      if (!li) {
        li = el('li');
        const b = el('button', cls);
        b.type = 'button';
        b.addEventListener('click', () => choose(id, true));
        li.append(b);
        cards.set(id, li);
      }
      const b = li.firstChild;
      b.setAttribute('aria-current', String(id === selected));
      b.replaceChildren(...parts);
      const want = prev ? prev.nextSibling : list.firstChild;
      if (li !== want) list.insertBefore(li, want);
      prev = li;
      keep.add(id);
    };
    const many = state.branches.length > 1;
    for (const p of prompts) {
      const head = el('div', 'head');
      const when = el('span', 'when', ago(p.created_at));
      when.title = full(p.created_at);
      head.append(el('span', 'n', `#${p.id}`), chip(el('span'), statusOf(p)));
      if (many) head.append(el('span', `btag ${colourClass(p.branch)}`, tagName(p.branch)));
      head.append(when);
      const parts = [head];
      if (p.title) parts.push(el('div', 'title', titleOf(p.id)));
      parts.push(el('div', 'text', p.text));
      if (p.last_note && (p.status === 'running' || p.status === 'failed')) parts.push(el('div', 'last', p.last_note));
      place(p.id, 'card', parts);
    }
    if (state.game) {
      const head = el('div', 'head');
      head.append(el('span', 'n', t('start')));
      place(START, 'card start', [head, el('div', 'text', prompts.length ? t('start_about_some') : t('start_about_none'))]);
    }
    for (const [id, li] of cards) if (!keep.has(id)) { li.remove(); cards.delete(id); }
  }

  // ---------------------------------------------------------------------------------------------------------------
  // The map. Every branch is a row (a thread), every live version a button sewn on it, in the column of its generation
  // (the starting point is 0, a version is its parent's + 1). A branch's row comes right under the row it splits off
  // from, the later splits first, so no thread crosses a button. Versions that didn't work hang off the one they were
  // built on, as a loose thread with a cross. In the picker, the next prompt's place shows as a ghost button.
  function mapSizes(mode) {
    const big = mode !== 'view';
    if (kids) return big ? { row: 60, col: 58, knot: 38, lab: 70 } : { row: 54, col: 52, knot: 36, lab: 66 };
    if (PHONE.matches && !big) return { row: 44, col: 42, knot: 30, lab: 50 };
    return big ? { row: 54, col: 54, knot: 34, lab: 62 } : { row: 46, col: 44, knot: 30, lab: 54 };
  }

  function mapLayout(ghostBase) {
    const gen = new Map([[START, 0]]);
    for (const p of tree.order) if (live(p)) gen.set(p.id, (gen.get(p.parent ?? START) ?? 0) + 1);
    // each branch with a live version, where it splits off, and which row that is on
    const first = new Map();
    for (const p of tree.order) if (live(p) && !first.has(p.branch)) first.set(p.branch, p);
    const forks = new Map();  // branch → the version it splits off from
    for (const [br, p] of first) if (br !== 0) forks.set(br, p.parent ?? START);
    let ghost = null;
    if (ghostBase != null && gen.has(ghostBase)) {
      const br = branchOf(ghostBase);
      ghost = { base: ghostBase, gen: gen.get(ghostBase) + 1, row: goesOn(ghostBase, br) ? 'ghost' : br };
      if (ghost.row === 'ghost') forks.set('ghost', ghostBase);
    }
    const under = new Map();  // row → the branches splitting off it
    for (const [br, at] of forks) {
      const owner = at === START ? 0 : branchOf(at);
      if (!under.has(owner)) under.set(owner, []);
      under.get(owner).push(br);
    }
    const rows = [];
    const visit = br => {
      rows.push(br);
      const ks = (under.get(br) || []).sort((a, b) => (gen.get(forks.get(b)) - gen.get(forks.get(a)))
        || (a === 'ghost' ? 1 : b === 'ghost' ? -1 : a - b));
      ks.forEach(visit);
    };
    visit(0);
    const rowOf = new Map(rows.map((br, i) => [br, i]));
    let maxGen = 0;
    for (const g of gen.values()) maxGen = Math.max(maxGen, g);
    if (ghost) maxGen = Math.max(maxGen, ghost.gen);
    return { gen, rows, rowOf, forks, ghost, maxGen };
  }

  // draw (or redraw) a map into `box`: mode 'view' (a tap opens it), 'browse' (the same, large) or 'pick'
  function drawMap(box, mode) {
    const pick = mode === 'pick';
    const at = pick ? candidate : selected;
    const ghostBase = pick ? candidate : branching() ? baseNow() : null;
    const L = mapLayout(ghostBase);
    const sz = mapSizes(mode);
    const key = JSON.stringify([mode, at, ghostBase, sz, lang, kids, L.rows, tree.order.map(p =>
      [p.id, p.status, p.stalled, p.parent, p.branch, p.title])]);
    if (drawn.get(box) === key) return;
    const firstDraw = !drawn.has(box);
    drawn.set(box, key);

    const x = g => sz.lab + 20 + g * sz.col;
    const y = r => r * sz.row + sz.row / 2;
    const width = x(L.maxGen) + sz.col / 2 + 30;
    const height = L.rows.length * sz.row;
    const posOf = v => ({ x: x(L.gen.get(v)), y: y(L.rowOf.get(v === START ? 0 : branchOf(v))) });
    // the open version's line glows, from the starting point down to it (for one that didn't work: to where it was built)
    const lit = at == null ? null : at === START || live(tree.byId.get(at)) ? at : (tree.byId.get(at) || {}).parent ?? START;
    const onPath = new Set(lit == null ? [] : ancestry(lit));

    let scroller = box.querySelector('.map-scroll');
    const keepLeft = scroller ? scroller.scrollLeft : 0;
    if (!scroller) {
      scroller = el('div', 'map-scroll');
      box.replaceChildren(scroller, el('div', 'fade'), el('div', 'tip'));
      box.querySelector('.tip').hidden = true;
      wireMap(box, scroller);
    }
    for (const [k, v] of Object.entries(sz)) box.style.setProperty(`--${k}`, `${v}px`);
    const canvas = el('div', 'map-canvas');
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;

    // the rows, with their tags stuck to the left
    for (const br of L.rows) {
      const row = el('div', 'mrow');
      const lab = el('div', 'mlabel');
      if (br === 'ghost') {
        lab.append(el('span', 'ghost', nextName()));
      } else {
        const tag = el('button', colourClass(br), br === 0 ? t('main_short') : shortName(br));
        tag.type = 'button';
        tag.title = tagName(br);
        tag.addEventListener('click', () => {
          const tip = tipOf(br);
          if (tip == null) return;
          if (pick) { candidate = tip; drawMapper(); } else openFromMap(tip);
        });
        lab.append(tag);
      }
      row.append(lab);
      canvas.append(row);
    }

    // the threads: one piece per version, from the one it builds on
    const svg = svgEl('svg', { class: 'threads', width, height, viewBox: `0 0 ${width} ${height}` });
    const edge = (from, to, cls) => {
      const r = Math.min(12, (to.x - from.x) / 2);
      const d = from.y === to.y ? `M${from.x},${from.y} H${to.x}`
        : `M${from.x},${from.y} V${to.y - r} Q${from.x},${to.y} ${from.x + r},${to.y} H${to.x}`;
      svg.append(svgEl('path', { d, class: `edge ${cls}` }), svgEl('path', { d, class: `yarn ${cls}` }));
    };
    for (const p of tree.order) {
      if (!live(p)) continue;
      const cls = [colourClass(p.branch), onPath.has(p.id) ? 'on' : '', p.status === 'queued' ? 'todo' : ''].join(' ');
      edge(posOf(p.parent ?? START), posOf(p.id), cls);
    }
    if (L.ghost) {
      const from = posOf(L.ghost.base);
      edge(from, { x: x(L.ghost.gen), y: y(L.rowOf.get(L.ghost.row)) }, 'ghost');
    }

    // what didn't work: grouped under the version it was built on
    const dead = new Map();
    for (const p of tree.order) {
      if (live(p)) continue;
      const on = p.parent ?? START;
      if (!L.gen.has(on)) continue;
      if (!dead.has(on)) dead.set(on, []);
      dead.get(on).push(p);
    }
    const knots = [];
    for (const [on, ps] of dead) {
      const p0 = posOf(on);
      const sx = p0.x + sz.knot * .55, sy = p0.y + sz.row * .42;
      svg.append(svgEl('path', { d: `M${p0.x + 4},${p0.y + 6} C${p0.x + 8},${sy - 4} ${sx - 10},${sy - 8} ${sx},${sy}`, class: 'loose' }));
      const last = ps[ps.length - 1];
      const b = el('button', 'stub', ps.length > 1 ? `×${ps.length}` : '✕');
      b.type = 'button';
      b.style.left = `${sx}px`;
      b.style.top = `${sy}px`;
      b.setAttribute('aria-label', ps.length > 1 ? t('dead_many', { n: ps.length })
        : t('knot_label', { id: last.id, title: titleOf(last.id), status: statusWord(statusOf(last)), branch: tagName(last.branch) }));
      if (ps.some(q => q.id === selected) && !pick) b.setAttribute('aria-current', 'true');
      b.disabled = pick;
      b.dataset.tipFor = last.id;
      b.addEventListener('click', () => openFromMap(ps.some(q => q.id === selected) && ps.length > 1
        ? ps[(ps.findIndex(q => q.id === selected) + 1) % ps.length].id : last.id));
      knots.push(b);
    }

    // the buttons
    const knot = v => {
      const p = v === START ? null : tree.byId.get(v);
      const pos = posOf(v);
      const b = el('button', v === START ? 'knot start' : `knot ${colourClass(p.branch)}`);
      b.type = 'button';
      b.style.left = `${pos.x}px`;
      b.style.top = `${pos.y}px`;
      b.dataset.id = v;
      b.dataset.row = L.rowOf.get(v === START ? 0 : p.branch);
      b.dataset.gen = L.gen.get(v);
      if (p) {
        b.dataset.status = statusOf(p);
        b.append(el('span', 'n', String(p.id)));
        b.setAttribute('aria-label', t('knot_label', { id: p.id, title: titleOf(p.id), status: statusWord(statusOf(p)), branch: tagName(p.branch) }));
      } else {
        b.setAttribute('aria-label', t('start'));
      }
      if (v !== START && sewn.size && !sewn.has(v) && !STILL.matches) b.classList.add('new');
      if (v === at) b.setAttribute('aria-current', 'true');
      else if (at != null && !onPath.has(v)) b.classList.add('dim');
      if (pick) {
        b.disabled = !canBuildOn(v);
        if (v === candidate) { b.classList.add('pick'); b.removeAttribute('aria-current'); }
      }
      b.addEventListener('click', () => {
        if (pick) { candidate = v; drawMapper(); } else openFromMap(v);
      });
      return b;
    };
    knots.push(knot(START));
    for (const p of tree.order) if (live(p)) knots.push(knot(p.id));
    if (L.ghost) {
      const g = el('span', 'knot ghost');
      g.style.left = `${x(L.ghost.gen)}px`;
      g.style.top = `${y(L.rowOf.get(L.ghost.row))}px`;
      g.title = t('ghost');
      const s = svgEl('svg', { viewBox: '0 0 24 24', 'aria-hidden': 'true' });
      s.append(svgEl('use', { href: '#i-needle' }));
      g.append(s);
      knots.push(g);
    }
    canvas.append(svg, ...knots);
    scroller.replaceChildren(canvas);
    if (mode === 'view') { sewn.add(START); for (const p of tree.order) if (live(p)) sewn.add(p.id); }

    // keep the version in view where the eye is: on a new pick, scroll to it; otherwise leave the scroll alone
    const target = canvas.querySelector('.knot[aria-current="true"], .knot.pick, .stub[aria-current="true"]')
      || (pick && L.ghost ? null : canvas.querySelector(`.knot[data-id="${tipOf(0) ?? START}"]`));
    const lastAt = box.dataset.at;
    box.dataset.at = String(at);
    scroller.scrollLeft = keepLeft;
    if (target && (firstDraw || lastAt !== String(at))) {
      const left = parseFloat(target.style.left);
      const view = scroller.clientWidth;
      if (left < scroller.scrollLeft + sz.lab + sz.col || left > scroller.scrollLeft + view - sz.col) {
        scroller.scrollLeft = Math.max(0, left - view / 2);
      }
    }
    fadeEdge(box, scroller);
    box.querySelector('.tip').hidden = true;
  }

  function fadeEdge(box, scroller) {
    box.querySelector('.fade').classList.toggle('on', scroller.scrollLeft + scroller.clientWidth < scroller.scrollWidth - 2);
  }

  // the map's ways in: a card on hover or focus, the arrow keys, and dragging it sideways with a mouse
  function wireMap(box, scroller) {
    const tip = box.querySelector('.tip');
    const showTip = b => {
      const id = Number(b.dataset.id ?? b.dataset.tipFor);
      if (Number.isNaN(id)) return;
      const p = id === START ? null : tree.byId.get(id);
      tip.className = `tip ${p ? colourClass(p.branch) : ''}`;
      const ex = !p ? (state.prompts.length ? t('start_about_some') : t('start_about_none'))
        : p.status === 'running' ? (p.last_note || '') : p.status === 'done' || p.status === 'failed' ? (p.excerpt || '') : p.text;
      tip.replaceChildren(el('b', null, p ? `#${p.id} ${titleOf(p.id)}` : t('start')));
      if (p) tip.append(el('span', 't-st', `${statusWord(statusOf(p))} · ${tagName(p.branch)}`));
      if (ex && !kids) tip.append(el('span', 't-ex', ex));
      tip.hidden = false;
      const w = box.clientWidth, half = Math.min(tip.offsetWidth / 2, w / 2);
      const cx = b.offsetLeft + b.offsetWidth / 2 - scroller.scrollLeft;
      tip.style.left = `${Math.max(half, Math.min(w - half, cx))}px`;
      tip.style.top = `${b.offsetTop + 6}px`;
    };
    const hide = () => { tip.hidden = true; };
    scroller.addEventListener('pointerover', e => {
      const b = e.target.closest('.knot:not(.ghost), .stub');
      if (b && e.pointerType === 'mouse') showTip(b);
    });
    scroller.addEventListener('pointerout', e => { if (e.target.closest('.knot, .stub')) hide(); });
    scroller.addEventListener('focusin', e => { const b = e.target.closest('.knot, .stub'); if (b && !b.classList.contains('ghost')) showTip(b); });
    scroller.addEventListener('focusout', hide);
    scroller.addEventListener('scroll', () => { hide(); fadeEdge(box, scroller); }, { passive: true });

    scroller.addEventListener('keydown', e => {
      const b = e.target.closest('.knot');
      if (!b || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(e.key)) return;
      const id = Number(b.dataset.id);
      const all = [...scroller.querySelectorAll('.knot[data-id]')];
      let next = null;
      if (e.key === 'ArrowLeft' && id !== START) {
        const p = tree.byId.get(id);
        next = all.find(k => Number(k.dataset.id) === (p.parent ?? START));
      } else if (e.key === 'ArrowRight') {
        const kids = tree.kids.get(id) || [];
        const same = kids.find(q => q.branch === branchOf(id)) || kids[0];
        if (same) next = all.find(k => Number(k.dataset.id) === same.id);
      } else if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
        const row = Number(b.dataset.row) + (e.key === 'ArrowUp' ? -1 : 1);
        const gen = Number(b.dataset.gen);
        const there = all.filter(k => Number(k.dataset.row) === row);
        there.sort((a, c) => Math.abs(a.dataset.gen - gen) - Math.abs(c.dataset.gen - gen));
        next = there[0];
      }
      if (next) {
        e.preventDefault();
        next.focus();
        const left = next.offsetLeft;
        if (left < scroller.scrollLeft + 60 || left > scroller.scrollLeft + scroller.clientWidth - 40) {
          scroller.scrollLeft = left - scroller.clientWidth / 2;
        }
      }
    });

    let drag = null;
    scroller.addEventListener('pointerdown', e => {
      if (e.pointerType !== 'mouse' || e.button !== 0 || scroller.scrollWidth <= scroller.clientWidth) return;
      drag = { x: e.clientX, left: scroller.scrollLeft, moved: false };
    });
    addEventListener('pointermove', e => {
      if (!drag) return;
      const dx = e.clientX - drag.x;
      if (!drag.moved && Math.abs(dx) < 5) return;
      drag.moved = true;
      scroller.classList.add('grab');
      scroller.scrollLeft = drag.left - dx;
    });
    addEventListener('pointerup', () => {
      if (drag && drag.moved) {
        // the click that ends a drag doesn't open what's under it
        scroller.addEventListener('click', e => { e.stopPropagation(); e.preventDefault(); }, { capture: true, once: true });
        setTimeout(() => scroller.classList.remove('grab'), 0);
      }
      drag = null;
    });
  }

  function openFromMap(v) {
    if (!saved.get(KNOWS_MAP)) { saved.set(KNOWS_MAP, '1'); $('map-key').hidden = true; }
    if (mapperMode === 'browse') $('mapper').close();
    choose(v, true);
  }

  // ---------------------------------------------------------------------------------------------------------------
  // The line: the branch of the version in view, read from the starting point down to its newest version. What came
  // before it split off is folded away; wherever another branch goes its own way, a row says so and leads there.
  function lineOf(id) {
    let v = id == null ? START : id;
    let deadOne = null;
    if (v !== START) {
      const p = tree.byId.get(v);
      if (!p) v = START;
      else if (!live(p)) { deadOne = p.id; v = p.parent ?? START; }
    }
    const br = branchOf(v);
    const path = ancestry(v);
    for (let c = v, next; (next = childOn(c, br)); c = next.id) path.push(next.id);
    return { br, path, focus: v, deadOne };
  }

  function drawLine() {
    const box = $('line');
    const L = lineOf(selected);
    const next = branching() ? baseNow() : null;
    const key = JSON.stringify([selected, next, lang, kids, [...unfolded], tree.order.map(p =>
      [p.id, p.status, p.stalled, p.parent, p.branch, p.title, p.excerpt, p.last_note]), state.branches]);
    if (drawn.get(box) === key) return;
    drawn.set(box, key);
    const b = tree.branches.get(L.br);
    const f = branchFacts(b);

    const card = el('div', `bcard ${colourClass(L.br)}`);
    const head = el('div', 'bc-head');
    head.append(el('span', `btag ${colourClass(L.br)}`, tagName(L.br)), el('span', 'bc-meta', `${f.from} · ${f.count}`));
    card.append(head, el('p', 'bc-sum', f.summary));

    const items = [];
    const row = (cls, br, inner, onClick, current, status) => {
      const li = el('li', `lrow ${cls} ${colourClass(br)}`);
      if (status) li.dataset.status = status;
      if (current) li.setAttribute('aria-current', 'true');
      const rail = el('span', 'rail');
      rail.append(el('i', 'dot'));
      const body = el(onClick ? 'button' : 'div', 'lbody');
      if (onClick) { body.type = 'button'; body.addEventListener('click', onClick); }
      body.append(...inner);
      li.append(rail, body);
      items.push(li);
      return li;
    };
    const line = (...parts) => { const s = el('span', 'lline'); s.append(...parts.filter(Boolean)); return s; };
    const nextMark = v => (next === v ? el('span', 'here', t('next_here')) : null);

    // where other branches go their own way from `v` (not counting the line itself)
    const forksAt = v => {
      const out = new Map();
      for (const q of tree.kids.get(v) || []) if (!L.path.includes(q.id) && !out.has(q.branch)) out.set(q.branch, q);
      for (const [obr] of out) {
        const ob = tree.branches.get(obr);
        const of = branchFacts(ob);
        const goes = obr === branchOf(v);
        const sw = el('span', 'sw');
        const name = el('b', null, tagName(obr));
        const text = t(goes ? 'sw_goes_on' : 'sw_splits', { branch: '\u0000' }).split('\u0000');
        sw.append(text[0], name, text[1] || '');
        if (!kids) {
          sw.append(` · ${of.count}`);
          if (ob.summary) { const q = el('q', null, words(ob.summary, 9)); q.title = ob.summary; sw.append(' · ', q); }
        }
        row('switch', obr, [sw], () => choose(tipOf(obr) ?? v, true));
      }
    };
    const deadAt = v => {
      for (const q of tree.order) {
        if (live(q) || (q.parent ?? START) !== v || (q.branch !== L.br && q.id !== L.deadOne)) continue;
        row('dead', q.branch, [line(el('span', 'lnum', `#${q.id}`), el('span', 'ltitle', titleOf(q.id)), chip(el('span'), statusOf(q)))],
          () => choose(q.id, true), q.id === selected, q.status);
      }
    };

    // the starting point
    row('start', 0, [line(el('span', 'ltitle', t('start')), nextMark(START)),
      selected === START && !kids ? el('span', 'lsub', state.prompts.length ? t('start_about_some') : t('start_about_none')) : null]
      .filter(Boolean), state.game ? () => choose(START, true) : null, selected === START);
    deadAt(START);
    forksAt(START);

    // what it inherits, folded when there's more than a little
    const inherited = L.path.slice(1).filter(v => branchOf(v) !== L.br);
    const own = L.path.slice(1).filter(v => branchOf(v) === L.br);
    const foldKey = `${L.br}`;
    const fold = inherited.length > 2 && !unfolded.has(foldKey) && !inherited.slice(0, -1).includes(selected);
    const shownInherited = fold ? inherited.slice(-1) : inherited;
    if (fold) {
      const hidden = inherited.slice(0, -1);
      const brs = new Set(hidden.map(branchOf));
      const label = brs.size === 1 ? t('fold', { n: hidden.length, branch: tagName([...brs][0]) }) : t('fold_mixed', { n: hidden.length });
      row('inherit fold', branchOf(hidden[0]), [el('span', null, `${label} ▸`)], () => { unfolded.add(foldKey); drawn.delete(box); drawLine(); });
    }
    for (const v of shownInherited) {
      const p = tree.byId.get(v);
      row('inherit', p.branch, [line(el('span', 'lnum', `#${p.id}`), el('span', 'ltitle', titleOf(p.id)), nextMark(v))],
        () => choose(v, true), v === selected, statusOf(p));
      forksAt(v);
    }
    for (const v of own) {
      const p = tree.byId.get(v);
      const sub = p.status === 'running' ? (p.last_note || '') : p.status === 'queued' ? (p.title ? p.text : '')
        : (p.excerpt || '');
      row('own', p.branch, [line(el('span', 'lnum', `#${p.id}`), el('span', 'ltitle', titleOf(p.id)),
        p.status !== 'done' ? chip(el('span'), statusOf(p)) : null, nextMark(v)), sub ? el('span', 'lsub', sub) : null].filter(Boolean),
      () => choose(v, true), v === selected, statusOf(p));
      deadAt(v);
      forksAt(v);
    }
    if (items.length) items[items.length - 1].classList.add('last');
    const list = el('ol', 'lrows');
    list.append(...items);
    box.replaceChildren(card, list);
  }

  // ---------------------------------------------------------------------------------------------------------------
  // The viewer
  async function renderView() {
    const sheet = $('sheet');
    if (selected == null) {
      sheet.hidden = true;
      $('blank').hidden = false;
      showResult(null, null);
      direct();
      return;
    }
    $('blank').hidden = true;
    if (selected === START) {
      viewSeq++;
      shown = detail = null;
      drawStart(state.game);
      return;
    }
    const p = state.prompts.find(x => x.id === selected);
    const key = p ? `${p.id}:${p.updated_at}:${p.stalled}:${lang}:${kids}` : `${selected}`;
    if (key !== shown) {
      const seq = ++viewSeq;
      let d;
      try {
        d = await api(`prompts/${selected}`);
      } catch (e) {
        if (e.status === 404 && seq === viewSeq) { pinned = false; selected = null; shown = null; render(); }
        return;
      }
      if (seq !== viewSeq) return;  // something else was picked in the meantime
      shown = key;
      detail = d;
    }
    if (detail) drawSheet(detail);
  }

  function setFrame(url) {
    if (url === frameUrl) return;
    frameUrl = url;
    // a new frame each time, rather than a new address in the old one: that would add a step to the browser's history,
    // and Back would go back inside the frame instead of closing the player. Taking the old one out stops its game
    const old = $('v-frame');
    const frame = old.cloneNode(false);
    if (url) frame.src = url;
    else frame.removeAttribute('src');
    old.replaceWith(frame);
  }

  // the game, or (with no url) the word about where things are at
  function showResult(id, url) {
    result = { id, url: url || null };
    drawResult();
    if (wantPlay != null && wantPlay === id) {
      wantPlay = null;
      if (url) openPlayer();
    }
  }

  function drawResult() {
    const url = result.url;
    const phone = PHONE.matches;
    const inPage = !!url && (playing || !phone);  // a phone loads the game only in the player
    setFrame(inPage ? url : null);
    $('stage').hidden = !inPage;
    $('v-poster').hidden = !url || !phone;         // it stays under the player, so the page doesn't move
    $('v-placeholder').hidden = !!url;
    $('frame').classList.toggle('no-result', !url);
    $('frame').classList.toggle('poster', !!url && phone);
    $('v-open').hidden = $('v-full').hidden = $('v-reload').hidden = !url;
    if (url) $('v-open').href = $('v-tab').href = url;
    drawPlayerBar();
    direct();
  }

  function setText(text, about) {
    const tx = $('v-text');
    if (tx.textContent !== text) tx.textContent = text;
    tx.classList.toggle('about', about);
  }

  // long texts show their first lines, with a button to read them whole, when they don't fit
  function measure() {
    for (const [text, more] of [['v-text', 'v-more'], ['v-summary-text', 's-more']]) {
      const tx = $(text);
      $(more).hidden = tx.scrollHeight <= tx.clientHeight + 1;
    }
  }

  function seal(br, label) {
    const s = $('v-seal');
    const again = s.textContent !== label;
    s.className = `seal ${colourClass(br)}`;
    s.textContent = label;
    if (again && !STILL.matches) { void s.offsetWidth; s.classList.add('stamp'); }
    $('v-num').className = `num ${colourClass(br)}`;
  }

  function drawStart(game) {
    $('sheet').hidden = false;
    $('v-num').textContent = t('start');
    seal(0, '★');
    $('v-num').className = 'num';
    $('v-chip').hidden = true;
    $('v-when').textContent = '';
    $('v-when').title = '';
    setText(state.prompts.length ? t('start_about_some') : t('start_about_none'), true);
    drawLineage(START);
    showResult(START, game.start_url);
    $('v-summary').hidden = true;
    $('v-foot').hidden = true;
    measure();
  }

  function drawSheet(d) {
    const status = statusOf(d);
    const p = tree.byId.get(d.id);
    $('sheet').hidden = false;
    $('v-num').textContent = `#${d.id}`;
    seal(p ? p.branch : 0, `#${d.id}`);
    $('v-chip').hidden = false;
    chip($('v-chip'), status);
    $('v-when').textContent = t('sent_by', { ago: ago(d.created_at) });
    $('v-when').title = t('sent_title', { who: d.created_by, when: full(d.created_at) });
    setText(d.text, false);
    drawLineage(d.id);
    $('v-foot').hidden = false;

    // the result itself, or a word about where it's at
    const url = d.result_url;
    showResult(d.id, url);
    if (!url) placeholder(d, status);

    // what the Lelelilo says
    const summary = $('v-summary');
    summary.hidden = !d.summary;
    summary.dataset.tone = d.status === 'failed' ? 'failed' : '';
    if (d.summary && $('v-summary-text').textContent !== d.summary) $('v-summary-text').textContent = d.summary;

    // what the brother can do with it
    const brother = isBrother();
    $('v-cancel').hidden = !(brother && d.status === 'queued');
    $('v-retry').hidden = !(brother && (d.status === 'failed' || d.status === 'cancelled' || d.stalled));
    $('v-copy').hidden = !brother;

    drawNotes(d.events || []);
    measure();
  }

  // its line as crumbs (a tap opens any of them), the next one along its branch, and a way to build on it
  function drawLineage(id) {
    const p = id === START || !tree ? null : tree.byId.get(id);
    const bar = $('v-line');
    bar.hidden = !tree || (id !== START && !p);
    if (bar.hidden) return;
    const at = p && !live(p) ? (p.parent ?? START) : id;
    const path = ancestry(at);
    if (p && !live(p)) path.push(id);
    const crumbs = [];
    const shown = path.length > 6 ? [path[0], null, ...path.slice(-4)] : path;
    shown.forEach((v, i) => {
      if (i) crumbs.push(el('span', 'sep'));
      if (v === null) { crumbs.push(el('span', 'crumb gap', '…')); return; }
      const label = v === START ? t('start_short') : `#${v}`;
      const cls = v === START ? 'crumb start' : `crumb ${colourClass(branchOf(v))}`;
      if (v === id) { crumbs.push(el('span', `${cls} here`, label)); return; }
      const b = el('button', cls, label);
      b.type = 'button';
      b.title = versionLabel(v);
      b.addEventListener('click', () => choose(v, true));
      crumbs.push(b);
    });
    const child = live(p) || id === START ? childOn(id, id === START ? 0 : p.branch) : null;
    if (child) {
      const b = el('button', `crumb next ${colourClass(child.branch)}`, t('newer', { id: child.id }));
      b.type = 'button';
      b.title = versionLabel(child.id);
      b.addEventListener('click', () => choose(child.id, true));
      crumbs.push(el('span', 'sep ahead'), b);
    }
    $('v-crumbs').replaceChildren(...crumbs);
    const build = $('v-build');
    const can = branching() && (id === START || live(p));
    build.hidden = !can;
    if (can) build.textContent = !p || p.status === 'done' ? t('build_on') : t('build_after');
  }

  // what the next prompt builds on, above the box: "Continues branch B after #4", or "Starts branch C from #2"
  function drawBase() {
    const line = $('base');
    line.hidden = !branching() || !tree;
    if (line.hidden) {
      $('send-text').textContent = t('send');
      return;
    }
    const b = baseNow();
    const br = branchOf(b);
    const tag = $('base-tag');
    const text = $('base-text');
    if (goesOn(b, br)) {
      tag.className = 'btag new';
      tag.textContent = t('base_new_tag', { name: nextName() });
      text.textContent = t('base_new', { name: nextName(), what: versionLabel(b) });
      $('send-text').textContent = t('send_new', { name: nextName() });
    } else {
      const p = b === START ? null : tree.byId.get(b);
      tag.className = `btag ${colourClass(br)}`;
      tag.textContent = br === 0 ? t('main') : shortName(br);
      text.textContent = !p ? t('base_start')
        : t('base_continue', { branch: longName(br), what: versionLabel(b) }) + (p.status === 'done' ? '' : t('base_tbd'));
      $('send-text').textContent = t('send');
    }
    text.title = text.textContent;
    $('base-reset').hidden = base == null;
  }

  // the large map: to look around, or to pick what the next prompt builds on
  function openMapper(mode) {
    mapperMode = mode;
    candidate = mode === 'pick' ? baseNow() : null;
    drawn.delete($('big-map'));
    drawMapper();
    const d = $('mapper');
    if (!d.open) d.showModal();
  }
  function drawMapper() {
    const pick = mapperMode === 'pick';
    $('mapper-title').textContent = pick ? t('mapper_pick') : t('mapper_browse');
    $('mapper-sub').textContent = pick ? t('mapper_pick_sub') : t('mapper_browse_sub');
    drawMap($('big-map'), pick ? 'pick' : 'browse');
    $('pick-bar').hidden = !pick;
    $('pick-ok').hidden = !pick;
    $('mapper-close').textContent = pick ? t('cancel') : t('close');
    if (!pick) return;
    const ok = candidate != null && canBuildOn(candidate);
    $('pick-ok').disabled = !ok;
    $('pick-ok').textContent = t('pick_ok');
    if (!ok) {
      $('pick-what').textContent = t('pick_none');
      $('pick-sub').textContent = '';
      return;
    }
    const br = branchOf(candidate);
    $('pick-what').textContent = versionLabel(candidate).replace(/^./, c => c.toUpperCase());
    const p = candidate === START ? null : tree.byId.get(candidate);
    const how = goesOn(candidate, br) ? t('pick_new', { name: nextName() }) : t('pick_continue', { branch: longName(br) });
    $('pick-sub').textContent = how + (p && p.excerpt && !kids ? ` · ${words(p.excerpt, 24)}` : '');
  }

  function pickBase(v) {
    base = v;
    if ($('mapper').open) $('mapper').close();
    if (state) render();
    $('text').focus({ preventScroll: true });
    $('compose').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  // ---------------------------------------------------------------------------------------------------------------
  // Neri's workbench, for a prompt with nothing to play yet
  function stampScenes() {
    const tpl = $('scene-tpl');
    for (const box of document.querySelectorAll('[data-scene]')) {
      if (!box.firstElementChild) box.append(tpl.content.cloneNode(true));
    }
  }
  function setScene(box, mood, letters) {
    if (box.dataset.mood !== mood) {
      box.dataset.mood = mood;
      delete box.dataset.beat;
    }
    if (letters != null) box.dataset.letters = String(Math.min(3, letters));
    else delete box.dataset.letters;
  }

  // while it works, Neri goes round its jobs: sewing, a hop to the arcade, playtesting, a wipe of the brow, back
  let beatTimer = null;
  let beatAt = 0;
  function direct() {
    const box = $('v-scene');
    const on = box.dataset.mood === 'working' && !$('v-placeholder').hidden && !$('sheet').hidden && !document.hidden && !STILL.matches;
    if (!on) {
      clearTimeout(beatTimer);
      beatTimer = null;
      if (box.dataset.mood !== 'working') delete box.dataset.beat;
      return;
    }
    if (beatTimer) return;
    const step = () => {
      const [name, ms] = BEATS[beatAt % BEATS.length];
      box.dataset.beat = name;
      beatAt++;
      beatTimer = setTimeout(step, ms);
    };
    step();
  }

  function placeholder(d, status) {
    const box = $('v-placeholder');
    const scene = $('v-scene');
    const p = tree.byId.get(d.id);
    let title, sub, mood, letters = null;
    const away = !(state.agent.last_seen && serverNow() - state.agent.last_seen < ONLINE_FOR);
    switch (status) {
      case 'queued': {
        const ahead = state.prompts.filter(q => (q.status === 'queued' && q.id < d.id) || (q.status === 'running' && !q.stalled)).length;
        letters = ahead + 1;
        if (away && !state.prompts.some(q => q.status === 'running' && !q.stalled)) {
          mood = 'closed';
          title = t('q_closed_title');
          sub = t('q_closed');
        } else {
          mood = 'waiting';
          title = t('q_title');
          sub = ahead === 0 ? t('q_next') : ahead === 1 ? t('q_one') : t('q_many', { n: ahead });
        }
        break;
      }
      case 'running':
        mood = 'working';
        title = t('r_title');
        sub = d.last_note ? '' : t('r_sub');
        break;
      case 'stalled':
        mood = 'stalled';
        title = t('s_title');
        sub = t('s_sub');
        break;
      case 'done':
        mood = 'shrug';
        title = t('d_title');
        sub = t('d_sub');
        break;
      case 'failed':
        mood = 'failed';
        title = t('f_title');
        sub = t('f_sub');
        break;
      default:
        mood = 'returned';
        title = t('c_title');
        sub = t('c_sub');
    }
    box.dataset.tone = status;
    setScene(scene, mood, letters);
    const wax = p ? inkOf(p.branch) : '#c0392b';
    scene.querySelectorAll('.wax').forEach(w => { w.style.fill = wax; });
    $('v-status').textContent = title;
    $('v-sub').textContent = sub;
    $('v-sub').hidden = !sub;

    // what Neri is doing right now, on a pinned tag: it pops in when it changes
    const tag = $('v-tag');
    const note = status === 'running' ? d.last_note : null;
    tag.hidden = !note;
    if (note && tag.textContent !== note) {
      tag.textContent = note;
      tag.classList.remove('pop');
      void tag.offsetWidth;
      tag.classList.add('pop');
    }

    // how long it has been at it
    const prog = $('v-progress');
    prog.hidden = status !== 'running';
    if (status === 'running') {
      const since = d.started_at || d.created_at;
      const min = Math.max(0, (serverNow() - since) / 60);
      $('v-seam').style.width = `${(1 - Math.exp(-min / (SEAM_MINUTES * 1.1))) * 92}%`;
      const m = Math.floor(min);
      $('v-elapsed').textContent = m < 1 ? t('el_start') : m < 60 ? t('el_min', { n: m }) : t('el_h', { h: Math.floor(m / 60), m: m % 60 });
    }

    // something to play in the meantime: the version it builds on (or the one above that has something to play)
    const play = $('v-play');
    const target = meanwhileFor(d.id);
    play.hidden = target == null;
    if (target != null) {
      const what = target === START ? t('the_start') : `#${target}`;
      play.textContent = status === 'queued' || status === 'running' ? t('play_meanwhile', { what }) : t('play_base', { what });
      play.title = t('play_title', { what: target === START ? t('start') : `#${target}` });
    }
    direct();
  }

  function meanwhileFor(id) {
    const p = tree && tree.byId.get(id);
    return tree ? playableFrom(p ? p.parent : null) : null;
  }

  // a newer version of the branch being played, to offer in the player's bar
  function newerInBranch() {
    if (!tree || result.id == null) return null;
    const b = tree.branches.get(branchOf(result.id));
    const vs = b ? b.versions.filter(p => p.status === 'done' && p.result_url && p.id > result.id) : [];
    return vs.length ? vs[vs.length - 1].id : null;
  }

  // it's done, while you watched: Neri holds it up, golden stitches fly, and the tab says so for a while
  function celebrate(id) {
    readyTitle = t('doc_ready', { id });
    document.title = readyTitle;
    setTimeout(() => { readyTitle = null; if (state) render(); }, document.hidden ? 60000 : 8000);
    if (STILL.matches) return;
    const box = $('celebrate');
    const bits = [];
    const colours = ['#ffc94a', '#4fd8ff', '#ff7f6e', '#b39dff', '#9ad66b', '#fff4d6'];
    for (let i = 0; i < 34; i++) {
      const b = el('i', i % 4 === 0 ? 'star' : null);
      const a = Math.random() * Math.PI * 2;
      const r = 120 + Math.random() * 220;
      b.style.setProperty('--x', `${Math.cos(a) * r}px`);
      b.style.setProperty('--y', `${Math.sin(a) * r - 40}px`);
      b.style.setProperty('--r', `${Math.random() * 720 - 360}deg`);
      b.style.setProperty('--k', colours[i % colours.length]);
      b.style.animationDelay = `${Math.random() * 0.25}s`;
      bits.push(b);
    }
    $('confetti').replaceChildren(...bits);
    box.hidden = false;
    box.style.animation = 'none';
    void box.offsetWidth;
    box.style.animation = '';
    setTimeout(() => { box.hidden = true; }, 2600);
  }

  function drawNotes(events) {
    const agent = state.agent.user;
    const items = events.map(e => {
      const li = el('li');
      li.dataset.kind = e.kind;
      const time = el('time', null, short(e.at));
      time.dateTime = new Date(e.at * 1000).toISOString();
      time.title = full(e.at);
      const msg = el('span', 'msg');
      const who = e.by === agent ? t('a_lelelilo') : e.by;
      switch (e.kind) {
        case 'sent': msg.textContent = t('ev_sent', { who }); break;
        case 'retried': msg.textContent = t('ev_retried', { who }); break;
        case 'picked': msg.textContent = t('ev_picked', { who }); break;
        case 'note': msg.textContent = e.message || ''; break;
        case 'done': msg.textContent = t('ev_done'); break;
        case 'failed': msg.textContent = t('ev_failed'); break;
        case 'cancelled': msg.textContent = t('ev_cancelled', { who }); break;
        default: msg.textContent = e.message || e.kind;
      }
      li.append(time, msg);
      return li;
    });
    $('v-notes').replaceChildren(...items);
    $('v-notes-count').textContent = items.length ? `· ${items.length}` : '';
  }

  function setHash() {
    history.replaceState(history.state, '', selected == null || !pinned ? location.pathname
      : selected === START ? '#start' : `#p${selected}`);
  }

  function choose(id, byHand) {
    selected = id;
    if (byHand) {
      pinned = !!(state && state.prompts.length && id !== state.prompts[0].id);
      meanwhile = null;
      wantPlay = null;
    }
    setHash();
    if (state) render();
    if (byHand && PHONE.matches && !playing) $('view').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  // -- the player: the game over the whole screen (full screen, where the browser allows it), with a slim bar to
  // leave it. Back closes it too, rather than leaving the board
  function openPlayer() {
    if (playing || !result.url) return;
    playing = true;
    document.documentElement.classList.add('playing');
    const stage = $('stage');
    stage.classList.add('playing');
    drawResult();
    history.pushState({ play: true }, '');
    if (document.fullscreenEnabled && stage.requestFullscreen) stage.requestFullscreen().catch(() => {});
    $('v-frame').focus();
  }

  function closePlayer(how) {
    if (!playing) return;
    playing = false;
    document.documentElement.classList.remove('playing');
    $('stage').classList.remove('playing');
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    if (how !== 'history' && history.state && history.state.play) history.back();
    drawResult();         // on a phone, this stops the game
    if (state) render();  // and the board catches up with what came in meanwhile
  }

  function playSoon(id) {
    if (result.id === id && result.url) openPlayer();
    else wantPlay = id;
  }

  function drawPlayerBar() {
    $('p-name').textContent = result.id === START ? t('p_start') : result.id != null ? `#${result.id}` : '';
    const next = newerInBranch();
    const newer = playing && next != null;
    $('p-newer').hidden = !newer;
    if (newer) $('p-newer').textContent = t('p_newer', { id: next });
  }

  // -- the pop-up with a whole text
  function openReader(title, sub, text, agentSays) {
    $('reader-title').textContent = title;
    $('reader-sub').textContent = sub;
    $('reader-text').textContent = text;
    $('reader-text').classList.toggle('agent', agentSays);
    $('reader-copy').hidden = agentSays || !detail || !isBrother();
    const d = $('reader');
    if (!d.open) d.showModal();
    $('reader-body').scrollTop = 0;
  }

  let iconFor = undefined;
  let iconSvg = null;
  async function tabIcon(badge) {
    if (badge === iconFor) return;
    iconFor = badge;
    const link = document.querySelector('link[rel="icon"]');
    if (!badge) { link.href = 'favicon.svg'; return; }
    try {
      iconSvg = iconSvg || await (await fetch('favicon.svg')).text();
    } catch (e) { return; }
    const dot = badge === 'working'
      ? '<circle cx="51" cy="51" r="11" fill="#ffc94a" stroke="#070d18" stroke-width="3"/>'
      : '<circle cx="51" cy="51" r="11" fill="#2fd0c4" stroke="#070d18" stroke-width="3"/><path d="M45.5 51.5l4 4 7-8" fill="none" stroke="#070d18" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>';
    if (iconFor === badge) link.href = 'data:image/svg+xml,' + encodeURIComponent(iconSvg.replace('</svg>', dot + '</svg>'));
  }

  // the sky follows the hour, as on lelelilo.com: dawn, day, dusk or night
  function skyMood() {
    const h = new Date().getHours();
    document.documentElement.dataset.sky = h >= 5 && h < 8 ? 'dawn' : h >= 8 && h < 17 ? 'day' : h >= 17 && h < 20 ? 'dusk' : 'night';
  }

  // ---------------------------------------------------------------------------------------------------------------
  // Words on the page, and the settings: language, Kids mode, notifications
  function applyText() {
    document.documentElement.lang = lang;
    document.documentElement.classList.toggle('kids', kids);
    for (const n of document.querySelectorAll('[data-t]')) n.textContent = t(n.dataset.t);
    for (const n of document.querySelectorAll('[data-t-placeholder]')) n.placeholder = t(n.dataset.tPlaceholder);
    for (const n of document.querySelectorAll('[data-t-label]')) n.setAttribute('aria-label', t(n.dataset.tLabel));
    for (const n of document.querySelectorAll('[data-t-title]')) n.title = t(n.dataset.tTitle);
    $('hint').textContent = /Mac|iPhone|iPad/.test(navigator.userAgent) ? t('hint_mac') : t('hint');
    $('map-key').innerHTML = t('key');  // our own words, with the little marks in them
    $('map-key').hidden = !!saved.get(KNOWS_MAP);
    $('kids-tag').hidden = !kids;
    for (const b of document.querySelectorAll('.seg [data-lang]')) b.setAttribute('aria-pressed', String(b.dataset.lang === lang));
    $('kids-toggle').checked = kids;
    const starters = t('starters');
    const box = $('starters');
    box.hidden = !kids || !starters.length;
    box.replaceChildren(...starters.map(([icon, label, text]) => {
      const b = el('button', null, `${icon} ${label}`);
      b.type = 'button';
      b.addEventListener('click', () => {
        const area = $('text');
        area.value = (area.value.trim() ? `${area.value.trimEnd()}\n` : '') + text;
        saveDraft();
        area.focus();
        area.setSelectionRange(area.value.length, area.value.length);
      });
      return b;
    }));
    drawn.clear();
    shown = null;
  }

  function setLang(v) {
    lang = v;
    saved.set(LANG, v);
    applyText();
    if (state) render();
    notifySync();
  }
  function setKids(on) {
    kids = on;
    saved.set(KIDS, on ? '1' : '0');
    applyText();
    if (state) render();
    notifySync();
  }

  // notifications: a push subscription for this browser, which the board sends to when a prompt ends
  const isIOS = () => /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const standalone = () => navigator.standalone === true || matchMedia('(display-mode: standalone)').matches;
  const canPush = () => 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window && window.isSecureContext;
  const keyBytes = b64 => {
    const s = atob((b64 + '='.repeat((4 - (b64.length % 4)) % 4)).replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from(s, c => c.charCodeAt(0));
  };
  async function subscription() {
    if (!canPush()) return null;
    const reg = await navigator.serviceWorker.getRegistration();
    return reg ? reg.pushManager.getSubscription() : null;
  }
  function notifyState(text, bad) {
    const s = $('notify-state');
    s.hidden = !text;
    s.textContent = text || '';
    s.classList.toggle('bad', !!bad);
  }
  async function drawNotify() {
    const toggle = $('notify-toggle');
    const test = $('notify-test');
    test.hidden = true;
    toggle.disabled = true;
    if (state && !state.push) return notifyState(t('n_server_off'), true);
    if (!canPush()) {
      toggle.checked = false;
      return notifyState(isIOS() && !standalone() ? t('n_ios') : t('n_unsupported'), !isIOS());
    }
    if (Notification.permission === 'denied') { toggle.checked = false; return notifyState(t('n_blocked'), true); }
    const sub = await subscription().catch(() => null);
    toggle.checked = !!sub;
    toggle.disabled = !state;
    test.hidden = !sub;
    notifyState(sub ? t('n_on') : '');
  }
  async function notifyOn() {
    const perm = await Notification.requestPermission();
    if (perm !== 'granted') throw new Error(t('n_denied'));
    const reg = await navigator.serviceWorker.register('sw.js');
    await navigator.serviceWorker.ready;
    const key = keyBytes(state.push.key);
    let sub = await reg.pushManager.getSubscription();
    if (sub && sub.options.applicationServerKey) {
      const had = new Uint8Array(sub.options.applicationServerKey);
      if (had.length !== key.length || had.some((b, i) => b !== key[i])) { await sub.unsubscribe(); sub = null; }
    }
    if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
    await api('push/subscribe', { subscription: sub.toJSON(), lang, kids });
  }
  async function notifyOff() {
    const sub = await subscription();
    if (!sub) return;
    await api('push/unsubscribe', { endpoint: sub.endpoint }).catch(() => {});
    await sub.unsubscribe();
  }
  // the board keeps each subscription's language and Kids mode: tell it when they change (and on every visit, which
  // also catches a browser that renewed its subscription)
  async function notifySync() {
    try {
      const sub = await subscription();
      if (sub && state && state.push) await api('push/subscribe', { subscription: sub.toJSON(), lang, kids });
    } catch (e) { /* tried again next time */ }
  }

  // -- the viewer's buttons
  async function act(path) {
    try { await api(path, {}); } catch (e) { alert(e.message); }
    shown = null;
    poll();
  }
  $('v-cancel').addEventListener('click', () => detail && act(`prompts/${detail.id}/cancel`));
  $('v-retry').addEventListener('click', () => detail && act(`prompts/${detail.id}/retry`));
  function copyIntoBox() {
    if (!detail) return;
    const box = $('text');
    box.value = detail.text;
    saveDraft();
    box.focus();
    box.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
  $('v-copy').addEventListener('click', copyIntoBox);
  $('v-play').addEventListener('click', () => {
    const target = detail ? meanwhileFor(detail.id) : null;
    if (target == null) return;
    const waiting = !detail.stalled && (detail.status === 'queued' || detail.status === 'running') ? detail.id : null;
    choose(target, true);
    meanwhile = waiting;
    if (PHONE.matches) playSoon(target);  // straight into the game: on a phone there's nothing to see in the page
  });
  const reload = () => {
    const url = frameUrl;
    if (!url) return;
    frameUrl = null;
    setFrame(url);
  };
  $('v-reload').addEventListener('click', reload);
  $('p-reload').addEventListener('click', reload);
  $('v-full').addEventListener('click', openPlayer);
  $('v-go').addEventListener('click', openPlayer);
  $('p-close').addEventListener('click', () => closePlayer('button'));
  $('p-newer').addEventListener('click', () => {
    const next = newerInBranch();
    if (next != null) choose(next, true);
  });
  $('v-build').addEventListener('click', () => { if (selected != null) pickBase(selected); });
  $('base-change').addEventListener('click', () => openMapper('pick'));
  $('base-reset').addEventListener('click', () => { base = null; if (state) render(); });
  $('map-big').addEventListener('click', () => openMapper('browse'));
  $('mapper-close').addEventListener('click', () => $('mapper').close());
  $('mapper').addEventListener('close', () => { mapperMode = null; candidate = null; });
  $('mapper').addEventListener('click', e => { if (e.target === $('mapper')) $('mapper').close(); });
  $('pick-ok').addEventListener('click', () => { if (candidate != null && canBuildOn(candidate)) pickBase(candidate); });
  for (const [id, v] of [['show-line', 'line'], ['show-list', 'list']]) {
    $(id).addEventListener('click', () => {
      view = v;
      saved.set(VIEW, v);
      if (state) render();
    });
  }
  addEventListener('popstate', () => {
    if (playing) closePlayer('history');
    setHash();
  });
  document.addEventListener('fullscreenchange', () => {
    // Esc, or a phone's Back, left full screen: the player closes too. A moment later, in case Back also went back
    if (!document.fullscreenElement && playing) setTimeout(() => closePlayer('fullscreen'), 150);
  });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && playing) closePlayer('key'); });
  PHONE.addEventListener('change', () => {
    document.documentElement.classList.toggle('phone', PHONE.matches);
    drawn.clear();
    drawResult();
    if (state) render();
  });

  $('v-more').addEventListener('click', () => openReader($('v-num').textContent, $('v-when').textContent, $('v-text').textContent, false));
  $('s-more').addEventListener('click', () => openReader(t('reader_note', { num: $('v-num').textContent }), '', $('v-summary-text').textContent, true));
  $('reader-close').addEventListener('click', () => $('reader').close());
  $('reader').addEventListener('click', e => { if (e.target === $('reader')) $('reader').close(); });  // a tap beside it
  $('reader-copy').addEventListener('click', () => { $('reader').close(); copyIntoBox(); });
  if (window.ResizeObserver) {
    const ro = new ResizeObserver(measure);
    ro.observe($('v-text'));
    ro.observe($('v-summary-text'));
  }

  // settings
  const openSettings = () => { drawNotify(); $('settings').showModal(); };
  $('settings-open').addEventListener('click', openSettings);
  $('kids-tag').addEventListener('click', openSettings);
  $('settings-close').addEventListener('click', () => $('settings').close());
  $('settings').addEventListener('click', e => { if (e.target === $('settings')) $('settings').close(); });
  for (const b of document.querySelectorAll('.seg [data-lang]')) b.addEventListener('click', () => { setLang(b.dataset.lang); drawNotify(); });
  $('kids-toggle').addEventListener('change', e => setKids(e.target.checked));
  $('notify-toggle').addEventListener('change', async e => {
    const on = e.target.checked;
    e.target.disabled = true;
    try {
      if (on) await notifyOn(); else await notifyOff();
    } catch (err) {
      notifyState(err.message === t('n_denied') ? err.message : t('n_error', { msg: err.message }), true);
      e.target.checked = !on;
      e.target.disabled = false;
      return;
    }
    drawNotify();
  });
  $('notify-test').addEventListener('click', async () => {
    const sub = await subscription();
    if (!sub) return;
    try { await api('push/test', { endpoint: sub.endpoint }); notifyState(t('n_test_sent')); } catch (err) { notifyState(t('n_error', { msg: err.message }), true); }
  });
  if ('serviceWorker' in navigator) {
    // a tap on a notification while the board is open: show that prompt
    navigator.serviceWorker.addEventListener('message', e => {
      const m = /#p(\d+)$/.exec((e.data && e.data.url) || '');
      if (e.data && e.data.kind === 'open' && m) choose(Number(m[1]), true);
      if (e.data && e.data.kind === 'finished') poll();
    });
  }

  // -- the box to write in
  const box = $('text');
  function saveDraft() {
    saved.set(DRAFT, box.value);
    $('send').disabled = !box.value.trim();
  }
  box.value = saved.get(DRAFT) || '';
  box.addEventListener('input', saveDraft);
  box.addEventListener('keydown', e => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); $('compose').requestSubmit(); }
  });
  // the letter folds into an envelope and flies to the Lelelilo
  function flyLetter() {
    if (STILL.matches || !Element.prototype.animate) return;
    const from = box.getBoundingClientRect();
    const to = $('agent').getBoundingClientRect();
    const env = el('div', 'envelope');
    env.style.left = `${from.left + from.width / 2 - 23}px`;
    env.style.top = `${from.top + from.height / 2 - 16}px`;
    document.body.append(env);
    const dx = to.left + 20 - (from.left + from.width / 2);
    const dy = to.top + to.height / 2 - (from.top + from.height / 2);
    env.animate([
      { transform: 'translate(0, 0) scale(1.6) rotate(0deg)', opacity: 0 },
      { transform: 'translate(0, -10px) scale(1.2) rotate(-6deg)', opacity: 1, offset: 0.2 },
      { transform: `translate(${dx * 0.55}px, ${dy * 0.55 - 60}px) scale(.8) rotate(10deg)`, opacity: 1, offset: 0.6 },
      { transform: `translate(${dx}px, ${dy}px) scale(.3) rotate(18deg)`, opacity: 0 },
    ], { duration: 1100, easing: 'cubic-bezier(.4, 0, .3, 1)' }).finished.then(() => env.remove(), () => env.remove());
  }
  $('compose').addEventListener('submit', async e => {
    e.preventDefault();
    const text = box.value.trim();
    if (!text) return;
    const send = $('send');
    send.disabled = true;
    $('formerr').hidden = true;
    try {
      const body = { text, lang, kids };
      if (branching()) body.base = baseNow();
      const p = await api('prompts', body);
      flyLetter();
      base = null;
      box.value = '';
      saveDraft();
      pinned = false;
      selected = p.id;
      meanwhile = null;
      setHash();
      await poll();
      if (PHONE.matches) $('view').scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (err) {
      $('formerr').textContent = err.message;
      $('formerr').hidden = false;
    } finally {
      send.disabled = !box.value.trim();
    }
  });

  // -- start: a link to #p12 opens that prompt, #start the starting point. On a phone the notes start folded
  stampScenes();
  skyMood();
  setInterval(skyMood, 10 * 60 * 1000);
  applyText();
  $('send').disabled = !box.value.trim();
  document.documentElement.classList.toggle('phone', PHONE.matches);
  $('v-notes-box').open = !PHONE.matches;
  const m = /^#p(\d+)$/.exec(location.hash);
  if (m) { selected = +m[1]; pinned = true; }
  else if (location.hash === '#start') { selected = START; pinned = true; }
  addEventListener('hashchange', () => {
    const h = /^#p(\d+)$/.exec(location.hash);
    if (h) choose(+h[1], true);
  });
  poll().then(() => notifySync());
  setInterval(() => { if (!document.hidden) poll(); }, POLL_MS);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); direct(); });
})();
