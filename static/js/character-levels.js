/* DL-105: on the character form, changing Level brings back the stats saved for that level: max HP, AC, the six scores,
   speed, and which saving throws and skills the character is trained in.  What is typed for a level is kept while the page
   is open, so going 3 -> 4 -> 3 does not lose edits. */
(function () {
  const level = document.getElementById('level');
  const hint = document.getElementById('level-hint');
  if (!level || !hint) return;
  const NUMBERS = ['max_hp', 'armor_class', 'str_score', 'dex_score', 'con_score', 'int_score', 'wis_score', 'cha_score', 'speed'];
  const form = level.form;
  let saved = {};
  try { saved = JSON.parse(level.dataset.levelStats || '{}') || {}; } catch (err) { saved = {}; }
  const levelOf = () => String(Math.max(1, Math.min(30, parseInt(level.value, 10) || 1)));
  let shown = levelOf();

  function say(text) { hint.textContent = text; hint.hidden = !text; }
  const saves = () => Array.from(form.querySelectorAll('input[data-save]'));
  const skillKeys = () => Array.from(new Set(Array.from(form.querySelectorAll('input[data-skill]')).map((r) => r.dataset.skill)));

  function read() {                                                  // what the form shows right now
    const out = {};
    NUMBERS.forEach((k) => { const el = document.getElementById(k); if (el) out[k] = el.value; });
    out.save_prof = saves().filter((b) => b.checked).map((b) => b.dataset.save);
    out.skill_prof = {};
    skillKeys().forEach((k) => {
      const on = form.querySelector(`input[data-skill="${k}"]:checked`);
      if (on && on.value) out.skill_prof[k] = parseInt(on.value, 10);
    });
    return out;
  }

  function write(row) {
    NUMBERS.forEach((k) => {
      const el = document.getElementById(k);
      if (el && row[k] !== undefined && row[k] !== '') { el.value = row[k]; el.dispatchEvent(new Event('input', { bubbles: true })); }
    });
    if (Array.isArray(row.save_prof)) saves().forEach((b) => { b.checked = row.save_prof.includes(b.dataset.save); });
    if (row.skill_prof && typeof row.skill_prof === 'object') {
      skillKeys().forEach((k) => {
        const want = String(row.skill_prof[k] || '');
        const radio = Array.from(form.querySelectorAll(`input[data-skill="${k}"]`)).find((r) => r.value === want);
        if (radio) radio.checked = true;
      });
    }
    form.dispatchEvent(new Event('change', { bubbles: true }));       // refresh the live bonus preview
  }

  level.addEventListener('change', (e) => {
    if (e.target !== level) return;
    const next = levelOf();
    if (next === shown) return;
    saved[shown] = read();                                            // what is on screen belongs to the level we are leaving
    const row = saved[next];
    if (row) {
      write(row);
      say(`Showing the stats saved for level ${next}.`);
    } else {
      say(`No stats saved for level ${next} yet. The current numbers carry over; saving keeps them for level ${next}.`);
    }
    shown = next;
  });
})();
