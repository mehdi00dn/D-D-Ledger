/* Account settings: one section at a time (the page shows everything if this script does not run), and the theme choice. */
(function () {
  'use strict';
  var nav = document.querySelector('.account-nav');
  if (nav) {
    var links = Array.prototype.slice.call(nav.querySelectorAll('[data-section-link]'));
    var parts = Array.prototype.slice.call(document.querySelectorAll('[data-section]'));
    var names = links.map(function (l) { return l.getAttribute('data-section-link'); });

    var show = function (name, remember) {
      parts.forEach(function (p) { p.hidden = p.getAttribute('data-section').split(' ').indexOf(name) < 0; });
      links.forEach(function (l) {
        var on = l.getAttribute('data-section-link') === name;
        l.classList.toggle('active', on);
        if (on) l.setAttribute('aria-current', 'page'); else l.removeAttribute('aria-current');
      });
      if (remember) { try { sessionStorage.setItem('account-section', name); } catch (e) { /* private mode */ } }
    };

    var sectionOf = function (el) {
      var host = el && el.closest('[data-section]');
      return host ? host.getAttribute('data-section').split(' ')[0] : null;
    };

    var initial = function () {
      var hash = decodeURIComponent(location.hash.slice(1));
      if (names.indexOf(hash) >= 0) return { name: hash };
      var target = hash && document.getElementById(hash);                       // e.g. #email-panel, #showcase
      if (target && sectionOf(target)) return { name: sectionOf(target), scrollTo: target };
      if (document.querySelector('#email-error, #username-error, #password-error') || /[?&](pw|un|out|mail)=/.test(location.search)) return { name: 'security' };
      if (document.querySelector('#delete-error')) return { name: 'data' };
      try {
        var saved = sessionStorage.getItem('account-section');
        if (names.indexOf(saved) >= 0) return { name: saved };
      } catch (e) { /* ignore */ }
      return { name: 'profile' };
    };

    var start = initial();
    show(start.name, false);
    if (start.scrollTo) start.scrollTo.scrollIntoView({ block: 'start' });

    window.addEventListener('hashchange', function () {                  // a link to #data or #email-panel from this same page
      var hash = decodeURIComponent(location.hash.slice(1));
      var target = names.indexOf(hash) >= 0 ? null : (hash && document.getElementById(hash));
      var name = names.indexOf(hash) >= 0 ? hash : sectionOf(target);
      if (!name) return;
      show(name, true);
      if (target) target.scrollIntoView({ block: 'start' });
    });

    nav.addEventListener('click', function (e) {
      var link = e.target.closest('[data-section-link]');
      if (!link) return;
      e.preventDefault();
      var name = link.getAttribute('data-section-link');
      show(name, true);
      if (history.replaceState) history.replaceState(null, '', '#' + name);
    });
  }

  // Theme: dark, light, or follow the device.  The head script of every page reads the same key.
  var radios = document.querySelectorAll('input[name="theme_pref"]');
  if (radios.length) {
    var media = window.matchMedia('(prefers-color-scheme: light)');
    var apply = function (pref) {
      var theme = pref === 'system' ? (media.matches ? 'light' : 'dark') : pref;
      document.documentElement.setAttribute('data-theme', theme);
    };
    var stored = null;
    try { stored = localStorage.getItem('ledger-theme'); } catch (e) { /* ignore */ }
    var current = stored === 'light' || stored === 'dark' || stored === 'system' ? stored : (document.documentElement.getAttribute('data-theme') || 'dark');
    radios.forEach(function (r) { r.checked = r.value === current; });
    radios.forEach(function (r) {
      r.addEventListener('change', function () {
        try { localStorage.setItem('ledger-theme', r.value); } catch (e) { /* ignore */ }
        apply(r.value);
      });
    });
    var onDeviceChange = function () {
      var now = null;
      try { now = localStorage.getItem('ledger-theme'); } catch (e) { /* ignore */ }
      if (now === 'system') apply('system');
    };
    if (media.addEventListener) media.addEventListener('change', onDeviceChange);
  }
}());
