/* The Characters page: search, PC/NPC switch, faction chips, sorting and 12-per-page paging.
 * Everything is done in the browser on the cards the page already holds, and the state lives in the address
 * (?q=&type=&faction=&sort=&page=) so a reload, a shared link or the Back button comes back to the same view. */
(function () {
  'use strict';
  var PAGE_SIZE = 12;
  var grid = document.getElementById('character-grid');
  if (!grid) return;

  var cards = Array.prototype.slice.call(grid.querySelectorAll('.dossier-card'));
  var filterBar = document.getElementById('group-filter');
  var typeSwitch = document.getElementById('type-switch');
  var searchInput = document.getElementById('character-search');
  var sortSelect = document.getElementById('character-sort');
  var pager = document.getElementById('character-pager');
  var noMatches = document.getElementById('character-no-matches');
  var count = document.getElementById('character-count');

  var SORTS = {
    'name-asc':   function (a, b) { return byName(a, b); },
    'name-desc':  function (a, b) { return byName(b, a); },
    'level-desc': function (a, b) { return num(b, 'level') - num(a, 'level') || byName(a, b); },
    'level-asc':  function (a, b) { return num(a, 'level') - num(b, 'level') || byName(a, b); },
    'newest':     function (a, b) { return num(b, 'id') - num(a, 'id'); },
    'oldest':     function (a, b) { return num(a, 'id') - num(b, 'id'); }
  };
  function num(card, key) { return parseInt(card.dataset[key], 10) || 0; }
  function byName(a, b) { return a.dataset.name.localeCompare(b.dataset.name) || num(a, 'id') - num(b, 'id'); }

  // ---- state (from the address) ----
  var params = new URLSearchParams(window.location.search);
  var state = {
    q: (params.get('q') || '').trim().toLowerCase(),
    type: ['pc', 'npc'].indexOf(params.get('type')) >= 0 ? params.get('type') : 'all',
    faction: params.get('faction') || 'all',
    sort: SORTS[params.get('sort')] ? params.get('sort') : 'name-asc',
    page: Math.max(1, parseInt(params.get('page'), 10) || 1)
  };

  function matches(card) {
    if (state.q && card.dataset.name.indexOf(state.q) < 0) return false;
    if (state.type !== 'all' && card.dataset.type !== state.type) return false;
    if (state.faction !== 'all' && (card.dataset.group || '').split(' ').indexOf(state.faction) < 0) return false;
    return true;
  }

  function writeUrl() {
    var p = new URLSearchParams();
    if (state.q) p.set('q', state.q);
    if (state.type !== 'all') p.set('type', state.type);
    if (state.faction !== 'all') p.set('faction', state.faction);
    if (state.sort !== 'name-asc') p.set('sort', state.sort);
    if (state.page > 1) p.set('page', String(state.page));
    var keep = new URLSearchParams(window.location.search);          // leave unrelated parameters (e.g. ?imported=1) alone
    ['q', 'type', 'faction', 'sort', 'page'].forEach(function (k) { keep.delete(k); });
    p.forEach(function (v, k) { keep.set(k, v); });
    var qs = keep.toString();
    try { history.replaceState(null, '', window.location.pathname + (qs ? '?' + qs : '')); } catch (e) { /* not critical */ }
  }

  function pageList(current, last) {
    // 1 … 4 [5] 6 … 12 : always the first, the last and the neighbours of the current page
    var out = [], prev = 0;
    for (var i = 1; i <= last; i++) {
      if (i === 1 || i === last || Math.abs(i - current) <= 1) {
        if (prev && i - prev > 1) out.push('gap');
        out.push(i); prev = i;
      }
    }
    return out;
  }

  function button(label, page, opts) {
    var b = document.createElement('button');
    b.type = 'button'; b.className = 'pager-btn'; b.textContent = label;
    if (opts && opts.aria) b.setAttribute('aria-label', opts.aria);
    if (opts && opts.current) { b.classList.add('is-current'); b.setAttribute('aria-current', 'page'); }
    if (opts && opts.disabled) b.disabled = true;
    else b.dataset.page = String(page);
    return b;
  }

  function renderPager(total, last) {
    if (!pager) return;
    pager.replaceChildren();
    pager.hidden = total <= PAGE_SIZE;
    if (pager.hidden) return;
    var from = (state.page - 1) * PAGE_SIZE + 1, to = Math.min(total, state.page * PAGE_SIZE);
    var info = document.createElement('span');
    info.className = 'pager-info'; info.textContent = from + '–' + to + ' of ' + total;
    var nav = document.createElement('div');
    nav.className = 'pager-pages';
    nav.append(button('‹', state.page - 1, { aria: 'Previous page', disabled: state.page <= 1 }));
    pageList(state.page, last).forEach(function (p) {
      if (p === 'gap') { var g = document.createElement('span'); g.className = 'pager-gap'; g.textContent = '…'; nav.append(g); }
      else nav.append(button(String(p), p, { current: p === state.page, aria: 'Page ' + p }));
    });
    nav.append(button('›', state.page + 1, { aria: 'Next page', disabled: state.page >= last }));
    pager.append(info, nav);
  }

  function render() {
    var shown = cards.filter(matches).sort(SORTS[state.sort]);
    var last = Math.max(1, Math.ceil(shown.length / PAGE_SIZE));
    state.page = Math.min(state.page, last);
    var onPage = shown.slice((state.page - 1) * PAGE_SIZE, state.page * PAGE_SIZE);
    var order = shown.concat(cards.filter(function (c) { return shown.indexOf(c) < 0; }));
    order.forEach(function (c) { grid.appendChild(c); });             // DOM order = sorted order (also what a screen reader reads)
    cards.forEach(function (c) { c.style.display = onPage.indexOf(c) >= 0 ? '' : 'none'; });
    if (noMatches) noMatches.hidden = shown.length > 0;
    if (count) count.hidden = false;
    renderPager(shown.length, last);
    writeUrl();
  }

  function change(patch) { Object.keys(patch).forEach(function (k) { state[k] = patch[k]; }); if (!('page' in patch)) state.page = 1; render(); }

  // ---- controls: show the state they start in, then listen ----
  if (searchInput) {
    searchInput.value = state.q;
    if (state.q) { var box = searchInput.closest('[data-collapsible-search]'); if (box) box.classList.add('is-open'); }
    searchInput.addEventListener('input', function () { change({ q: searchInput.value.trim().toLowerCase() }); });
  }
  if (typeSwitch) {
    typeSwitch.querySelectorAll('[data-type-switch]').forEach(function (b) { b.classList.toggle('active', b.dataset.typeSwitch === state.type); });
    typeSwitch.addEventListener('click', function (e) {
      var btn = e.target.closest('[data-type-switch]');
      if (!btn) return;
      typeSwitch.querySelectorAll('[data-type-switch]').forEach(function (b) { b.classList.remove('active'); });
      btn.classList.add('active');
      change({ type: btn.dataset.typeSwitch });
    });
  }
  if (filterBar) {
    filterBar.querySelectorAll('.chip').forEach(function (c) { c.classList.toggle('active', c.dataset.filter === state.faction); });
    filterBar.addEventListener('click', function (e) {
      var btn = e.target.closest('.chip');
      if (!btn) return;
      // A faction chip toggles: click it to filter to that faction, click the active one again to clear.
      var was = btn.classList.contains('active');
      filterBar.querySelectorAll('.chip').forEach(function (c) { c.classList.remove('active'); });
      if (!was) btn.classList.add('active');
      change({ faction: was ? 'all' : btn.dataset.filter });
    });
  }
  if (sortSelect) {
    sortSelect.value = state.sort;
    sortSelect.addEventListener('change', function () { change({ sort: sortSelect.value }); });
  }
  if (pager) {
    pager.addEventListener('click', function (e) {
      var btn = e.target.closest('[data-page]');
      if (!btn) return;
      state.page = parseInt(btn.dataset.page, 10) || 1;
      render();
      var top = grid.getBoundingClientRect().top + window.pageYOffset - 70;
      window.scrollTo({ top: Math.max(0, top), behavior: 'smooth' });
    });
  }
  render();
}());
