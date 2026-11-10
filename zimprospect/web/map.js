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
  var state = { group: 0, base: 'satellite', showAll: false, active: -1 };
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
    ui.handle = el('button', { class: 'zp-handle', type: 'button', 'aria-label': 'Expand panel', 'aria-expanded': 'false' });
    ui.chips = el('div', { class: 'zp-chips', role: 'radiogroup', 'aria-label': data.groupLabel });
    data.groups.forEach(function (g, i) {
      ui.chips.appendChild(el('button', { class: 'zp-chip', type: 'button', role: 'radio', 'aria-checked': 'false',
        tabindex: '-1', text: g.short, onclick: function () { selectGroup(i, true); } }));
    });
    ui.chips.addEventListener('keydown', onChipKey);
    ui.head = el('header', { class: 'zp-head' }, [
      el('h1', { class: 'zp-title', text: data.title }), el('p', { class: 'zp-subtitle', text: data.subtitle }), ui.chips]);
    ui.card = el('section', { class: 'zp-card' });
    ui.listTitle = el('h3', { class: 'zp-section-title' });
    ui.list = el('ol', { class: 'zp-list' });
    ui.scroll = el('div', { class: 'zp-scroll' }, [ui.card, legend(), ui.listTitle, ui.list, settings(),
      el('p', { class: 'zp-note', text: data.disclaimer })]);
    ui.panel = el('aside', { class: 'zp-panel', 'aria-label': data.title }, [ui.handle, ui.head, ui.scroll, ui.live]);
    document.body.appendChild(ui.panel);
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
    var nodes = [el('h3', { class: 'zp-section-title', text: 'Map' }), ui.segment];
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
    ui.card.replaceChildren.apply(ui.card, [el('h2', { text: g.name }), el('p', { class: 'zp-desc', text: g.desc }),
      badge, stats].filter(Boolean));
  }

  function renderList(g) {
    ui.listTitle.textContent = data.pointLabel + ' (' + g.points.length + ')';
    if (!g.points.length) { ui.list.replaceChildren(el('li', { class: 'zp-empty', text: data.emptyText })); return; }
    ui.list.replaceChildren.apply(ui.list, g.points.map(function (p, i) {
      var row = el('button', { class: 'zp-row', type: 'button', 'aria-current': 'false',
        'aria-label': '#' + p.rank + ', ' + p.title + ', ' + p.sub, onclick: function () { focusPoint(i); } }, [
        el('span', { class: 'zp-pin zp-pin--small zp-pin--' + tone(p.tone), text: String(p.rank), 'aria-hidden': 'true' }),
        el('span', {}, [el('span', { class: 'zp-row-main', text: p.title }), el('span', { class: 'zp-row-sub', text: p.sub })])]);
      return el('li', {}, [row]);
    }));
  }

  function onChipKey(e) {
    var keys = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };
    if (!keys[e.key]) return;
    e.preventDefault();
    var n = data.groups.length, next = (state.group + keys[e.key] + n) % n;
    selectGroup(next, true);
    ui.chips.children[next].focus();
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
    requestAnimationFrame(function () { requestAnimationFrame(function () { next.setOpacity(1); }); });
    if (old) {
      old.setOpacity(0);
      setTimeout(function () { map.removeLayer(old); }, REDUCED.matches ? 0 : 260);
    }
  }

  function drawPoints(g) {
    layers.points.clearLayers();
    markers = g.points.map(function (p, i) {
      var icon = L.divIcon({ className: 'zp-pin-wrap', iconSize: [30, 30], iconAnchor: [15, 15], popupAnchor: [0, -14],
        html: '<span class="zp-pin zp-pin--' + tone(p.tone) + '">' + Number(p.rank) + '</span>' });
      var m = L.marker([p.lat, p.lon], { icon: icon, title: '#' + p.rank + ' ' + p.title, riseOnHover: true })
        .bindPopup(function () { return popup(g, p); }, POPUP_OPTIONS)
        .on('click', function () { setActive(i); });
      layers.points.addLayer(m);
      return m;
    });
  }

  function drawContext() {
    layers.context.clearLayers();
    var pts = state.showAll ? data.allContext : (data.groups[state.group].context || []);
    pts.forEach(function (c) {
      L.circleMarker([c[0], c[1]], { radius: 4, weight: 1.5, color: '#111', fillColor: '#fff', fillOpacity: 0.95 })
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
    state.group = i;
    state.active = -1;
    Array.prototype.forEach.call(ui.chips.children, function (c, j) {
      c.setAttribute('aria-checked', String(j === i));
      c.tabIndex = j === i ? 0 : -1;
    });
    if (userAction) ui.chips.children[i].scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: REDUCED.matches ? 'auto' : 'smooth' });
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
      var pin = m.getElement() && m.getElement().querySelector('.zp-pin');
      if (pin) pin.classList.toggle('is-active', j === i);
    });
    Array.prototype.forEach.call(ui.list.querySelectorAll('.zp-row'), function (r, j) {
      r.setAttribute('aria-current', String(j === i));
    });
  }

  function focusPoint(i) {
    var p = data.groups[state.group].points[i];
    setActive(i);
    if (PHONE.matches) sheet.snapTo('peek');
    fitTo(L.latLng(p.lat, p.lon).toBounds(data.focusRadiusM), function () { markers[i].openPopup(); }, false);
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
      [ui.handle, ui.head].forEach(function (n) { n.addEventListener('pointerdown', down); });
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', up);
      window.addEventListener('pointercancel', up);
      ui.panel.addEventListener('click', onClickCapture, true);
      ui.handle.addEventListener('click', function () { setSnap(snap === 'peek' ? 'half' : 'peek', 0); });
      window.addEventListener('resize', sync);
      if (PHONE.addEventListener) PHONE.addEventListener('change', sync);
      sync();
    }
    return {
      init: init,
      snapTo: function (name) { if (PHONE.matches) setSnap(name, 0); },
      visibleHeight: function () { return Math.max(0, ui.panel.offsetHeight - y); }
    };
  })();

  /* ---------- start ---------- */
  map.createPane('zpHeat').style.zIndex = 350;
  map.zoomControl.setPosition('topright');
  function placeChrome() { map.attributionControl.setPosition(PHONE.matches ? 'topleft' : 'bottomright'); }
  function markDistance() { map.getContainer().classList.toggle('zp-far', map.getZoom() < FAR_ZOOM); }
  placeChrome();
  if (PHONE.addEventListener) PHONE.addEventListener('change', placeChrome);
  map.on('zoomend', markDistance);
  BASES.satellite.addTo(map);
  layers.context.addTo(map);
  layers.points.addTo(map);
  buildPanel();
  sheet.init();
  var start = 0;
  data.groups.forEach(function (g, i) { if ('#' + g.key === window.location.hash) start = i; });
  if (!data.fitOnSelect) fitTo(L.latLngBounds(data.initialBounds), null, true);
  selectGroup(start, false);
  markDistance();
})(ZP_DATA, ZP_MAP);
