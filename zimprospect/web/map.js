/* zim-prospect map UI. Expects ZP_DATA (page data) and ZP_MAP (a Leaflet map) to be defined.
   One group (mineral, or zoom target) is shown at a time; its points are listed and pinned. */
(function (data, map) {
  'use strict';
  var PHONE = window.matchMedia('(max-width: 767px)');
  var REDUCED = window.matchMedia('(prefers-reduced-motion: reduce)');
  var TONE_NAMES = { greenfield: 'Greenfield target', known: 'In a known mining district', anomaly: 'Surface anomaly' };
  var BADGE_TONES = ['good', 'moderate', 'weak'];
  var SPRING_RESPONSE = 0.32;   // seconds; critically damped (no overshoot)
  var DRAG_SLOP = 10;           // px of movement before a drag commits to a direction
  var DECELERATION = 0.998;     // momentum projection, as in iOS scroll views
  var FLY_SECONDS = 0.8;
  var POPUP_OPTIONS = { maxWidth: 300, autoPanPaddingTopLeft: [16, 72], autoPanPaddingBottomRight: [72, 16] };
  var FAR_ZOOM = 7;             // below this zoom, pins shrink so clusters stay readable
  var BASES = {
    satellite: L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      { attribution: 'Imagery &copy; Esri', maxZoom: 18 }),
    streets: L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',
      { attribution: '&copy; OpenStreetMap contributors', maxZoom: 19 })
  };
  var state = { group: 0, base: 'streets', showAll: false, showContext: true, active: -1,
    opacity: 0.65, showOverlay: true, tab: 'targets', targetType: 'all', sort: 'rank', shortlists: {} };
  var layers = { overlay: null, points: L.layerGroup(), context: L.layerGroup() };
  var markers = [];
  var ui = {};

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === 'text') node.textContent = attrs[k];
      else if (k === 'class') node.className = attrs[k];
      else if (k.slice(0, 2) === 'on') node.addEventListener(k.slice(2), attrs[k]);
      else node.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) { if (c) node.appendChild(c); });
    return node;
  }
  function tone(t) { return Object.prototype.hasOwnProperty.call(TONE_NAMES, t) ? t : 'greenfield'; }
  function announce(msg) { ui.live.textContent = msg; }

  /* ---------- panel ---------- */
  function buildPanel() {
    ui.live = el('div', { class: 'zp-sr', 'aria-live': 'polite' });
    ui.handleText = el('span', { text: 'View ' + (data.fitOnSelect ? 'anomalies' : 'targets') });
    ui.handle = el('button', { class: 'zp-handle', type: 'button', 'aria-label': 'Expand panel',
      'aria-expanded': 'false' }, [ui.handleText, el('span', { class: 'zp-handle-grip', 'aria-hidden': 'true' })]);
    ui.select = el('select', { id: 'zp-group', class: 'zp-select', 'aria-label': data.groupLabel,
      onchange: function (e) { selectGroup(Number(e.target.value), true); } });
    data.groups.forEach(function (g, i) {
      ui.select.appendChild(el('option', { value: String(i), text: g.short }));
    });
    ui.brand = el('div', { class: 'zp-brand' }, [el('h1', { class: 'zp-title', text: data.title }),
      el('p', { class: 'zp-subtitle', text: data.subtitle })]);
    buildTabs();
    ui.head = el('header', { class: 'zp-head' }, [ui.brand,
      el('label', { class: 'zp-picker', for: 'zp-group' }, [el('span', { text: data.groupLabel }), ui.select]), ui.tabs]);
    ui.card = el('section', { class: 'zp-card' });
    ui.listTitle = el('h3', { class: 'zp-section-title' });
    ui.list = el('ol', { class: 'zp-list' });
    ui.targetPane = el('div', { id: 'zp-pane-targets', role: 'tabpanel', 'aria-labelledby': 'zp-tab-targets' },
      [ui.card, targetControls(), ui.listTitle, ui.list]);
    ui.comparePane = el('div', { id: 'zp-pane-compare', role: 'tabpanel', 'aria-labelledby': 'zp-tab-compare' });
    ui.comparePane.hidden = true;
    ui.mapPane = el('div', { id: 'zp-pane-map', role: 'tabpanel', 'aria-labelledby': 'zp-tab-map' },
      [settings(), legend(), el('p', { class: 'zp-note', text: data.disclaimer })]);
    ui.mapPane.hidden = true;
    ui.scroll = el('div', { class: 'zp-scroll' }, [ui.targetPane, ui.comparePane, ui.mapPane]);
    ui.panel = el('aside', { class: 'zp-panel', 'aria-label': data.title }, [ui.handle, ui.head, ui.scroll, ui.live]);
    document.body.appendChild(ui.panel);
    buildMapTools();
  }

  function buildTabs() {
    ui.tabs = el('div', { class: 'zp-tabs', role: 'tablist', 'aria-label': 'Explorer views' });
    var keys = data.fitOnSelect ? ['targets', 'map'] : ['targets', 'compare', 'map'];
    keys.forEach(function (key) {
      ui.tabs.appendChild(el('button', { id: 'zp-tab-' + key, type: 'button', role: 'tab',
        'aria-controls': 'zp-pane-' + key, 'aria-selected': String(key === state.tab), tabindex: key === state.tab ? '0' : '-1',
        text: key === 'map' ? 'Map & legend' : key === 'compare' ? 'Compare (0)' : (data.fitOnSelect ? 'Anomalies' : 'Targets'),
        onclick: function () { showTab(key); if (PHONE.matches) sheet.snapTo('full'); } }));
    });
    ui.tabs.addEventListener('keydown', function (e) {
      if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].indexOf(e.key) < 0) return;
      e.preventDefault();
      var next = (keys.indexOf(state.tab) + (e.key === 'ArrowLeft' ? -1 : 1) + keys.length) % keys.length;
      var key = e.key === 'Home' ? keys[0] : e.key === 'End' ? keys[keys.length - 1] : keys[next];
      showTab(key);
      document.getElementById('zp-tab-' + key).focus();
    });
  }

  function targetControls() {
    ui.filter = el('select', { class: 'zp-filter', 'aria-label': 'Target type', onchange: function (e) {
      state.targetType = e.target.value; state.active = -1; map.closePopup(); renderList(data.groups[state.group]);
      drawPoints(data.groups[state.group]);
    } }, [el('option', { value: 'all', text: 'All targets' }), el('option', { value: 'greenfield', text: 'Greenfield' }),
      el('option', { value: 'known', text: 'Known districts' })]);
    ui.sort = el('select', { class: 'zp-filter', 'aria-label': 'Sort targets', onchange: function (e) {
      state.sort = e.target.value; renderList(data.groups[state.group]);
    } }, [el('option', { value: 'rank', text: 'Original rank' }), el('option', { value: 'areaKm2', text: 'Smallest zones' }),
      el('option', { value: 'meanRank', text: 'Highest mean rank' }),
      el('option', { value: 'zoneDistanceKm', text: 'Farthest from sites' })]);
    ui.compareHint = el('p', { class: 'zp-compare-hint', text: 'Tick up to 3 targets to compare.' });
    var controls = el('div', { class: 'zp-target-controls' }, [
      el('label', {}, [el('span', { text: 'Target type' }), ui.filter]),
      el('label', {}, [el('span', { text: 'Sort by' }), ui.sort]), ui.compareHint]);
    controls.hidden = data.fitOnSelect;
    return controls;
  }

  function buildMapTools() {
    ui.fitTargets = el('button', { type: 'button', text: data.fitOnSelect ? 'Fit anomalies' : 'Fit targets', onclick: fitTargets });
    var toolbar = el('nav', { class: 'zp-map-tools', 'aria-label': 'Map navigation' }, [
      el('button', { type: 'button', text: 'Zimbabwe', 'aria-label': 'Show all Zimbabwe', onclick: function () {
        if (PHONE.matches) sheet.snapTo('peek');
        fitTo(L.latLngBounds(data.countryBounds || data.initialBounds), null, false);
      } }), ui.fitTargets]);
    document.body.appendChild(toolbar);
  }

  function showTab(key) {
    state.tab = key;
    ui.targetPane.hidden = key !== 'targets'; ui.mapPane.hidden = key !== 'map';
    ui.comparePane.hidden = key !== 'compare';
    Array.prototype.forEach.call(ui.tabs.children, function (tab) {
      var selected = tab.id === 'zp-tab-' + key;
      tab.setAttribute('aria-selected', String(selected)); tab.tabIndex = selected ? 0 : -1;
    });
    ui.scroll.scrollTop = 0;
  }

  function legend() {
    var lg = data.legend;
    var keys = el('div', { class: 'zp-keys' }, lg.keys.map(function (k) {
      var sample = k.tone === 'context' ? el('span', { class: 'zp-dot', 'aria-hidden': 'true' })
        : el('span', { class: 'zp-pin zp-pin--small zp-pin--' + tone(k.tone), text: '1', 'aria-hidden': 'true' });
      return el('div', { class: 'zp-key' }, [sample, el('span', { text: k.label })]);
    }));
    return el('section', {}, [el('h3', { class: 'zp-section-title', text: lg.title }),
      el('div', { class: 'zp-ramp', style: 'background:' + lg.gradient, 'aria-hidden': 'true' }),
      el('div', { class: 'zp-ramp-labels' }, [el('span', { text: lg.lo }), el('span', { text: lg.hi })]), keys]);
  }

  function settings() {
    ui.segment = el('div', { class: 'zp-segment', role: 'group', 'aria-label': 'Base map' });
    Object.keys(BASES).forEach(function (key) {
      ui.segment.appendChild(el('button', { type: 'button', 'aria-pressed': String(key === state.base),
        text: key === 'satellite' ? 'Satellite' : 'Streets', onclick: function () { setBase(key); } }));
    });
    ui.opacityValue = el('output', { for: 'zp-opacity', text: '65%' });
    ui.opacity = el('input', { id: 'zp-opacity', type: 'range', min: '0', max: '100', value: '65',
      'aria-label': 'Prospectivity opacity', oninput: function (e) {
        state.opacity = Number(e.target.value) / 100;
        ui.opacityValue.textContent = e.target.value + '%'; updateOverlay();
      } });
    var nodes = [el('h3', { class: 'zp-section-title', text: 'Base map' }), ui.segment,
      el('label', { class: 'zp-switch' }, [el('span', { text: data.fitOnSelect ? 'Show anomaly overlay' : 'Show prospectivity overlay' }),
        el('input', { type: 'checkbox', checked: '', onchange: function (e) {
          state.showOverlay = e.target.checked; updateOverlay();
        } })]),
      el('div', { class: 'zp-opacity' }, [el('label', { for: 'zp-opacity', text: 'Overlay opacity' }), ui.opacityValue, ui.opacity]),
      el('label', { class: 'zp-switch' }, [el('span', { text: data.fitOnSelect ? 'Show national target peak' : 'Show recorded sites' }),
        el('input', { type: 'checkbox', checked: '', onchange: function (e) {
          state.showContext = e.target.checked; drawContext();
        } })])];
    if (data.allContext && data.allContext.length) {
      var box = el('input', { type: 'checkbox', onchange: function (e) { state.showAll = e.target.checked; drawContext(); } });
      nodes.push(el('label', { class: 'zp-switch' }, [el('span', { text: data.allContextLabel }), box]));
    }
    return el('section', {}, nodes);
  }

  function renderCard(g) {
    var badge = g.badge && BADGE_TONES.indexOf(g.badge.tone) >= 0
      ? el('span', { class: 'zp-badge zp-badge--' + g.badge.tone, text: g.badge.text }) : null;
    var stats = el('dl', { class: 'zp-stats' }, g.stats.map(function (s) {
      return el('div', {}, [el('dt', { text: s[0] }), el('dd', { text: s[1] })]);
    }));
    var evidence = data.fitOnSelect ? stats : el('details', { class: 'zp-validation' },
      [el('summary', {}, [badge || el('span', { text: 'Validation' })]), stats]);
    ui.card.replaceChildren(el('h2', { text: g.name }), el('p', { class: 'zp-desc', text: g.desc }), evidence);
  }

  function renderList(g) {
    var points = visiblePoints(g);
    ui.listTitle.textContent = points.length === g.points.length ? g.points.length + ' ' + (data.fitOnSelect ? 'anomalies' : 'ranked targets')
      : points.length + ' of ' + g.points.length + ' targets';
    ui.fitTargets.disabled = !points.length;
    if (!points.length) { ui.list.replaceChildren(el('li', { class: 'zp-empty', text: g.points.length ? 'No targets match this filter.' : data.emptyText })); return; }
    ui.list.replaceChildren.apply(ui.list, points.map(function (item) {
      var p = item.point, i = item.index;
      var row = el('button', { class: 'zp-row', type: 'button', 'aria-current': 'false',
        'data-point-index': String(i), 'aria-label': '#' + p.rank + ', ' + p.title + ', ' + p.sub,
        onclick: function () { focusPoint(i); } }, [
        el('span', { class: 'zp-pin zp-pin--small zp-pin--' + tone(p.tone), text: String(p.rank), 'aria-hidden': 'true' }),
        el('span', {}, [el('span', { class: 'zp-row-main', text: p.title }), el('span', { class: 'zp-row-sub', text: p.sub })])]);
      var compare = data.fitOnSelect ? null : el('label', { class: 'zp-compare-check', title: 'Compare target #' + p.rank },
        [el('input', { type: 'checkbox', 'data-compare-index': String(i), 'aria-label': 'Compare target #' + p.rank,
          onchange: function (e) { toggleCompare(i, e.target.checked); } })]);
      return el('li', { class: data.fitOnSelect ? '' : 'zp-target-item' }, [row, compare]);
    }));
    updateCompare();
  }

  function visiblePoints(g) {
    return g.points.map(function (point, index) { return { point: point, index: index }; })
      .filter(function (item) { return state.targetType === 'all' || item.point.tone === state.targetType; })
      .sort(function (a, b) {
        if (state.sort === 'rank') return a.point.rank - b.point.rank;
        var av = (a.point.metrics || {})[state.sort], bv = (b.point.metrics || {})[state.sort];
        if (av == null) return bv == null ? a.index - b.index : 1;
        if (bv == null) return -1;
        return (state.sort === 'areaKm2' ? av - bv : bv - av) || a.index - b.index;
      });
  }

  /* ---------- comparison shortlist, scoped to the selected mineral ---------- */
  function shortlist() { return state.shortlists[data.groups[state.group].key] || []; }

  function toggleCompare(index, checked) {
    var selected = shortlist().filter(function (i) { return i !== index; });
    if (checked && selected.length < 3) selected.push(index);
    state.shortlists[data.groups[state.group].key] = selected;
    updateCompare();
    announce(selected.length + ' targets selected for comparison');
  }

  function updateCompare() {
    if (data.fitOnSelect) return;
    var selected = shortlist();
    document.getElementById('zp-tab-compare').textContent = 'Compare (' + selected.length + ')';
    ui.compareHint.textContent = selected.length ? selected.length + ' of 3 selected · open Compare to view them.' : 'Tick up to 3 targets to compare.';
    ui.list.querySelectorAll('[data-compare-index]').forEach(function (box) {
      box.checked = selected.indexOf(Number(box.dataset.compareIndex)) >= 0;
      box.disabled = !box.checked && selected.length === 3;
    });
    markers.forEach(function (marker, index) {
      var pin = marker && marker.getElement() && marker.getElement().querySelector('.zp-pin');
      if (pin) pin.classList.toggle('is-compared', selected.indexOf(index) >= 0);
    });
    renderComparison(selected);
  }

  function renderComparison(selected) {
    var group = data.groups[state.group];
    var children = [el('h2', { class: 'zp-compare-title', text: 'Compare targets' }),
      el('p', { class: 'zp-desc', text: group.short + ' · select up to 3 from the target list' })];
    if (!selected.length) {
      children.push(el('p', { class: 'zp-empty', text: 'Tick the checkbox beside a target to add it here.' }));
      ui.comparePane.replaceChildren.apply(ui.comparePane, children); return;
    }
    var points = selected.map(function (i) { return group.points[i]; });
    var header = el('tr', {}, [el('th', { scope: 'col', text: 'Target' })].concat(points.map(function (p, col) {
      return el('th', { scope: 'col' }, [el('button', { type: 'button', text: '#' + p.rank,
        'aria-label': 'Show target #' + p.rank + ' on map', onclick: function () { focusPoint(selected[col]); } })]);
    })));
    var rows = [['Setting', null, 0], ['Area (km²)', 'areaKm2', 1], ['Mean rank', 'meanRank', 2],
      ['Zone to site (km)', 'zoneDistanceKm', 1]].map(function (field) {
      return el('tr', {}, [el('th', { scope: 'row', text: field[0] })].concat(points.map(function (p) {
        var value = field[1] ? (p.metrics || {})[field[1]] : p.tone === 'known' ? 'Known district' : 'Greenfield';
        var text = value == null ? '–' : typeof value === 'number' ? value.toLocaleString(undefined,
          { minimumFractionDigits: field[2], maximumFractionDigits: field[2] }) : value;
        return el('td', { text: text });
      })));
    });
    rows.push(el('tr', {}, [el('th', { scope: 'row', text: 'Shortlist' })].concat(selected.map(function (index) {
      return el('td', {}, [el('button', { type: 'button', text: 'Remove',
        'aria-label': 'Remove target #' + group.points[index].rank, onclick: function () { toggleCompare(index, false); } })]);
    }))));
    children.push(el('table', { class: 'zp-comparison', 'aria-label': group.short + ' target comparison' },
      [el('thead', {}, [header]), el('tbody', {}, rows)]),
      el('button', { class: 'zp-compare-fit', type: 'button', text: 'Show selected on map', onclick: function () {
        revealAllPoints();
        if (PHONE.matches) sheet.snapTo('peek');
        fitTo(L.latLngBounds(points.map(function (p) { return [p.lat, p.lon]; })), null, false);
      } }), el('p', { class: 'zp-note', text: 'Mean rank averages the zone’s 0–100 percentile ranks across Zimbabwe. Distance is measured from the zone, not its pin.' }));
    ui.comparePane.replaceChildren.apply(ui.comparePane, children);
  }

  function fitTargets() {
    var points = visiblePoints(data.groups[state.group]);
    if (!points.length) return;
    if (PHONE.matches) sheet.snapTo('peek');
    fitTo(L.latLngBounds(points.map(function (item) { return [item.point.lat, item.point.lon]; })), null, false);
  }

  /* ---------- map layers ---------- */
  function setBase(key) {
    Object.keys(BASES).forEach(function (k) { map.removeLayer(BASES[k]); });
    BASES[key].addTo(map);
    state.base = key;
    Array.prototype.forEach.call(ui.segment.children, function (b, i) {
      b.setAttribute('aria-pressed', String(Object.keys(BASES)[i] === key));
    });
  }

  function setOverlay(g) {
    var old = layers.overlay;
    var next = L.imageOverlay(g.image, g.bounds, { pane: 'zpHeat', opacity: 0, interactive: false, alt: g.name }).addTo(map);
    layers.overlay = next;
    requestAnimationFrame(function () { requestAnimationFrame(function () { if (layers.overlay === next) updateOverlay(); }); });
    if (old) {
      old.setOpacity(0);
      setTimeout(function () { map.removeLayer(old); }, REDUCED.matches ? 0 : 260);
    }
  }

  function updateOverlay() {
    if (layers.overlay) layers.overlay.setOpacity(state.showOverlay ? state.opacity : 0);
  }

  function drawPoints(g) {
    layers.points.clearLayers();
    markers = g.points.map(function (p, i) {
      if (state.targetType !== 'all' && p.tone !== state.targetType) return null;
      var icon = L.divIcon({ className: 'zp-pin-wrap', iconSize: [30, 30], iconAnchor: [15, 15], popupAnchor: [0, -14],
        html: '<span class="zp-pin zp-pin--' + tone(p.tone) + '">' + Number(p.rank) + '</span>' });
      var m = L.marker([p.lat, p.lon], { icon: icon, title: '#' + p.rank + ' ' + p.title, riseOnHover: true })
        .bindPopup(function () { return popup(g, p); }, POPUP_OPTIONS)
        .on('click', function () { setActive(i); });
      layers.points.addLayer(m);
      return m;
    });
    updateCompare();
  }

  function drawContext() {
    layers.context.clearLayers();
    if (!state.showContext || !data.groups[state.group]) return;
    var pts = state.showAll ? data.allContext : (data.groups[state.group].context || []);
    pts.forEach(function (c) {
      L.circleMarker([c[0], c[1]], { radius: 3, weight: 1, color: '#111', fillColor: '#fff', fillOpacity: 0.85 })
        .bindTooltip(el('span', { text: c[2] }), { direction: 'top' }).addTo(layers.context);
    });
  }

  function popup(g, p) {
    var coords = p.lat.toFixed(5) + ', ' + p.lon.toFixed(5);
    var copy = el('button', { type: 'button', text: 'Copy coordinates' });
    copy.addEventListener('click', function () {
      copyText(coords).then(function () { copy.textContent = 'Copied'; announce('Coordinates copied'); },
        function () { copy.textContent = 'Copy failed: select the numbers'; });
    });
    var directions = el('a', { href: 'https://www.google.com/maps/dir/?api=1&destination=' + encodeURIComponent(p.lat + ',' + p.lon),
      target: '_blank', rel: 'noopener noreferrer', text: 'Directions' });
    return el('div', { class: 'zp-pop' }, [el('h3', { text: g.name + ' · #' + p.rank }), el('p', { text: TONE_NAMES[tone(p.tone)] })]
      .concat(p.lines.map(function (t) { return el('p', { text: t }); }))
      .concat([el('p', { class: 'zp-coords', text: coords }), el('div', { class: 'zp-pop-actions' }, [copy, directions]),
        data.popupNote ? el('p', { class: 'zp-pop-note', text: data.popupNote }) : null]));
  }

  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
    return new Promise(function (resolve, reject) {
      var area = el('textarea', { readonly: '', style: 'position:fixed;opacity:0' });
      area.value = text;
      document.body.appendChild(area);
      area.select();
      var ok = document.execCommand('copy');
      document.body.removeChild(area);
      if (ok) resolve(); else reject(new Error('copy refused'));
    });
  }

  /* ---------- selection ---------- */
  function selectGroup(i, userAction) {
    var g = data.groups[i];
    if (!g) return;
    map.stop(); map.closePopup();
    state.group = i;
    state.active = -1;
    ui.select.value = String(i);
    ui.scroll.scrollTop = 0;
    renderCard(g);
    renderList(g);
    setOverlay(g);
    drawPoints(g);
    drawContext();
    if (data.fitOnSelect) fitTo(L.latLngBounds(g.bounds), null, !userAction);
    announce('Showing ' + g.name + ': ' + g.points.length + ' ' + data.pointLabel.toLowerCase());
    if (window.location.protocol !== 'file:') history.replaceState(null, '', '#' + g.key);
  }

  function setActive(i) {
    state.active = i;
    markers.forEach(function (m, j) {
      if (!m) return;
      var pin = m.getElement() && m.getElement().querySelector('.zp-pin');
      if (pin) pin.classList.toggle('is-active', j === i);
    });
    Array.prototype.forEach.call(ui.list.querySelectorAll('.zp-row'), function (r) {
      r.setAttribute('aria-current', String(Number(r.dataset.pointIndex) === i));
    });
  }

  function focusPoint(i) {
    var p = data.groups[state.group].points[i];
    if (!markers[i]) revealAllPoints();
    var marker = markers[i], group = state.group;
    setActive(i);
    if (PHONE.matches) sheet.snapTo('peek');
    fitTo(L.latLng(p.lat, p.lon).toBounds(data.focusRadiusM), function () {
      if (state.group === group && markers[i] === marker) marker.openPopup();
    }, false);
  }

  function revealAllPoints() {
    if (state.targetType === 'all') return;
    state.targetType = 'all'; ui.filter.value = 'all';
    renderList(data.groups[state.group]); drawPoints(data.groups[state.group]);
  }

  function padding() {
    if (PHONE.matches) return { paddingTopLeft: [16, 16], paddingBottomRight: [16, sheet.visibleHeight() + 16] };
    return { paddingTopLeft: [ui.panel.offsetWidth + 32, 16], paddingBottomRight: [16, 16] };
  }

  function fitTo(bounds, then, instant) {
    var opts = padding();
    opts.maxZoom = data.focusZoom;
    if (instant || REDUCED.matches) {
      map.fitBounds(bounds, opts);
      if (then) then();
      return;
    }
    var done = false;
    function finish() { if (!done && then) { done = true; then(); } }
    map.once('moveend', finish);
    setTimeout(finish, FLY_SECONDS * 1000 + 300);   // moveend may not fire if the view does not change
    opts.duration = FLY_SECONDS;
    map.flyToBounds(bounds, opts);
  }

  /* ---------- phone bottom sheet: 1:1 drag, momentum projection, critically damped spring ---------- */
  var sheet = (function () {
    var y = 0, frame = null, snap = 'peek', drag = null, suppressClick = false;

    function snaps() {
      var h = ui.panel.offsetHeight;
      var peekVisible = ui.head.offsetTop + ui.head.offsetHeight + 4;
      return { full: 0, half: Math.max(0, h - window.innerHeight * 0.55), peek: Math.max(0, h - peekVisible) };
    }
    function apply(v) { y = v; ui.panel.style.transform = 'translate3d(0,' + v.toFixed(1) + 'px,0)'; }
    function stop() { if (frame) cancelAnimationFrame(frame); frame = null; }
    function animateTo(target, velocity) {
      stop();
      if (REDUCED.matches) { apply(target); return; }
      var omega = 2 * Math.PI / SPRING_RESPONSE, x0 = y - target, v0 = velocity || 0, start = performance.now();
      function step(now) {
        var t = (now - start) / 1000;
        var x = (x0 + (v0 + omega * x0) * t) * Math.exp(-omega * t);   // critically damped, starts from the live value
        if (Math.abs(x) < 0.5 && t > 0.05) { apply(target); frame = null; return; }
        apply(target + x);
        frame = requestAnimationFrame(step);
      }
      frame = requestAnimationFrame(step);
    }
    function rubberband(over) {
      var d = window.innerHeight;
      return (over * d * 0.55) / (d + 0.55 * Math.abs(over));
    }
    function clampSoft(v, s) {
      if (v < s.full) return s.full - rubberband(s.full - v);
      if (v > s.peek) return s.peek + rubberband(v - s.peek);
      return v;
    }
    function velocity(hist) {
      if (hist.length < 2) return 0;
      var a = hist[0], b = hist[hist.length - 1], dt = (b[0] - a[0]) / 1000;
      return dt > 0 ? (b[1] - a[1]) / dt : 0;
    }
    function project(v) { return (v / 1000) * DECELERATION / (1 - DECELERATION); }
    function setSnap(name, v) {
      snap = name;
      ui.handleText.textContent = name === 'peek' ? 'View ' + (data.fitOnSelect ? 'anomalies' : 'targets') : 'Back to map';
      ui.handle.setAttribute('aria-expanded', String(name !== 'peek'));
      ui.handle.setAttribute('aria-label', name === 'peek' ? 'Expand panel' : 'Collapse panel');
      animateTo(snaps()[name], v);
    }
    function nearest(pos) {
      var s = snaps();
      return Object.keys(s).reduce(function (best, k) { return Math.abs(s[k] - pos) < Math.abs(s[best] - pos) ? k : best; }, 'peek');
    }
    function down(e) {
      if (!PHONE.matches || e.button > 0) return;
      stop();
      drag = { id: e.pointerId, x: e.clientX, y: e.clientY, origin: y, hist: [[e.timeStamp, e.clientY]], active: false };
    }
    function move(e) {
      if (!drag || e.pointerId !== drag.id) return;
      var dy = e.clientY - drag.y, dx = e.clientX - drag.x;
      if (!drag.active) {
        if (Math.abs(dy) < DRAG_SLOP && Math.abs(dx) < DRAG_SLOP) return;
        if (Math.abs(dx) > Math.abs(dy)) { drag = null; return; }   // horizontal: let the chips scroll
        drag.active = true;
        ui.panel.setPointerCapture(e.pointerId);
      }
      apply(clampSoft(drag.origin + dy, snaps()));
      drag.hist.push([e.timeStamp, e.clientY]);
      if (drag.hist.length > 5) drag.hist.shift();
    }
    function up(e) {
      if (!drag || e.pointerId !== drag.id) return;
      var d = drag;
      drag = null;
      if (!d.active) return;
      suppressClick = true;
      setTimeout(function () { suppressClick = false; }, 60);
      var v = velocity(d.hist);
      setSnap(nearest(y + project(v)), v);
    }
    function onClickCapture(e) { if (suppressClick) { e.stopPropagation(); e.preventDefault(); suppressClick = false; } }
    function sync() {
      if (!PHONE.matches) { stop(); ui.panel.style.transform = ''; return; }
      apply(snaps()[snap]);
    }
    function init() {
      [ui.handle, ui.brand].forEach(function (n) { n.addEventListener('pointerdown', down); });
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', up);
      window.addEventListener('pointercancel', up);
      ui.panel.addEventListener('click', onClickCapture, true);
      ui.handle.addEventListener('click', function () { setSnap(snap === 'peek' ? 'full' : 'peek', 0); });
      window.addEventListener('resize', sync);
      if (PHONE.addEventListener) PHONE.addEventListener('change', sync);
      sync();
    }
    return {
      init: init,
      snapTo: function (name) { if (PHONE.matches) setSnap(name, 0); },
      visibleHeight: function () { return Math.max(0, ui.panel.offsetHeight - snaps()[snap]); }
    };
  })();

  /* ---------- start ---------- */
  map.createPane('zpHeat').style.zIndex = 350;
  map.createPane('zpBoundary').style.zIndex = 450;
  map.getPane('zpBoundary').style.pointerEvents = 'none';
  if (data.country) {
    L.geoJSON(data.country, { pane: 'zpBoundary', interactive: false,
      style: { color: '#111111', weight: 6, opacity: 0.8, fill: false, className: 'zp-country-casing' } }).addTo(map);
    L.geoJSON(data.country, { pane: 'zpBoundary', interactive: false,
      style: { color: '#ffffff', weight: 2, opacity: 1, fill: false, className: 'zp-country-border' } }).addTo(map);
  }
  map.zoomControl.setPosition('topright');
  function placeChrome() { map.attributionControl.setPosition(PHONE.matches ? 'topleft' : 'bottomright'); }
  function markDistance() { map.getContainer().classList.toggle('zp-far', map.getZoom() < FAR_ZOOM); }
  placeChrome();
  if (PHONE.addEventListener) PHONE.addEventListener('change', placeChrome);
  map.on('zoomend', markDistance);
  BASES[state.base].addTo(map);
  layers.context.addTo(map);
  layers.points.addTo(map);
  buildPanel();
  sheet.init();
  var start = 0;
  data.groups.forEach(function (g, i) { if ('#' + g.key === window.location.hash) start = i; });
  if (!data.fitOnSelect) fitTo(L.latLngBounds(data.initialBounds), null, true);
  selectGroup(start, false);
  window.addEventListener('hashchange', function () {
    data.groups.some(function (g, i) {
      if ('#' + g.key !== window.location.hash) return false;
      if (i !== state.group) selectGroup(i, false);
      return true;
    });
  });
  markDistance();
})(ZP_DATA, ZP_MAP);
