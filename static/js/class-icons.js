/* Character classes (DL-43): the little class icon shown beside a name, and the picker on the character form. */
(function () {
  const LABELS = window.CLASS_LABELS || {};

  // <span class="class-icon">…</span> for a class key, or '' when there is none / it is unknown.
  window.classIconHtml = function (key) {
    if (!key || !LABELS[key]) return '';
    const name = LABELS[key];
    return `<span class="class-icon" title="${name}" role="img" aria-label="${name}"><svg aria-hidden="true"><use href="/static/icons/classes.svg#${key}"></use></svg></span>`;
  };

  // ---- Dropdown on the character form ----
  const picker = document.getElementById('class-picker');
  if (!picker) return;
  const input = document.getElementById('class_key');
  const trigger = document.getElementById('class-trigger');
  const triggerIcon = document.getElementById('class-trigger-icon');
  const triggerText = document.getElementById('class-trigger-text');
  const menu = document.getElementById('class-menu');
  const options = [...menu.querySelectorAll('.class-option')];

  function select(value) {
    input.value = value;
    const opt = options.find((o) => o.dataset.value === value) || options[0];
    options.forEach((o) => o.setAttribute('aria-selected', String(o === opt)));
    triggerText.textContent = value ? opt.dataset.tipTitle : 'No class';
    triggerIcon.innerHTML = value ? `<svg aria-hidden="true"><use href="/static/icons/classes.svg#${value}"></use></svg>` : '';
    picker.classList.toggle('has-value', !!value);
  }

  // One shared hover guide (name + what the class is), shown beside the option, kept inside the window.
  const tip = document.createElement('div');
  tip.className = 'class-tip';
  tip.setAttribute('role', 'tooltip');
  tip.hidden = true;
  document.body.appendChild(tip);
  function showTip(opt) {
    tip.innerHTML = '';
    const b = document.createElement('b'); b.textContent = opt.dataset.tipTitle;
    const p = document.createElement('span'); p.textContent = opt.dataset.tip;
    tip.append(b, p);
    tip.hidden = false;
    const m = menu.getBoundingClientRect(), r = opt.getBoundingClientRect(), w = tip.offsetWidth, h = tip.offsetHeight;
    let left = m.right + 10;
    if (left + w > window.innerWidth - 8) left = m.left - w - 10;          // no room on the right: go left
    if (left < 8) { left = Math.max(8, Math.min(m.left, window.innerWidth - w - 8)); }
    let top = Math.max(8, Math.min(r.top + r.height / 2 - h / 2, window.innerHeight - h - 8));
    tip.style.left = `${left + window.scrollX}px`;
    tip.style.top = `${top + window.scrollY}px`;
  }
  const hideTip = () => { tip.hidden = true; };

  let active = -1;
  function setActive(i) {
    active = Math.max(0, Math.min(options.length - 1, i));
    options.forEach((o, n) => o.classList.toggle('active', n === active));
    options[active].scrollIntoView({ block: 'nearest' });
    showTip(options[active]);
  }
  function open() {
    menu.hidden = false; trigger.setAttribute('aria-expanded', 'true');
    setActive(Math.max(0, options.findIndex((o) => o.dataset.value === input.value)));
  }
  function close(focus) {
    menu.hidden = true; trigger.setAttribute('aria-expanded', 'false'); hideTip(); active = -1;
    options.forEach((o) => o.classList.remove('active'));
    if (focus) trigger.focus();
  }
  function choose(opt) { select(opt.dataset.value); close(true); }

  trigger.addEventListener('click', () => (menu.hidden ? open() : close(false)));
  trigger.addEventListener('keydown', (e) => {
    if (menu.hidden && ['ArrowDown', 'ArrowUp'].includes(e.key)) { e.preventDefault(); open(); }
    else if (!menu.hidden) {
      if (e.key === 'ArrowDown') { e.preventDefault(); setActive(active + 1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(active - 1); }
      else if (e.key === 'Home') { e.preventDefault(); setActive(0); }
      else if (e.key === 'End') { e.preventDefault(); setActive(options.length - 1); }
      else if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); if (active >= 0) choose(options[active]); }
      else if (e.key === 'Escape') { e.preventDefault(); close(true); }
      else if (e.key === 'Tab') close(false);
    }
  });
  options.forEach((opt, n) => {
    opt.addEventListener('mouseenter', () => { active = n; options.forEach((o, k) => o.classList.toggle('active', k === n)); showTip(opt); });
    opt.addEventListener('click', () => choose(opt));
  });
  menu.addEventListener('mouseleave', hideTip);
  document.addEventListener('pointerdown', (e) => { if (!menu.hidden && !picker.contains(e.target)) close(false); });

  select(input.value);
}());
