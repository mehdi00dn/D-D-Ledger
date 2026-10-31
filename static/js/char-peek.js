/* The small character card that opens from a map pin or a battle row.  A compact version of the details page:
 * who it is, the vitals, the six abilities with their checks and saves, skills, notes and sheet images.
 * Every bonus is click-to-roll (see character-rolls.js), exactly like on the full page.
 *
 *   CharPeek.open(characterId, participantId?)      fills and shows #detail-modal
 *
 * What a Player may see of an NPC is decided by the server (/api/characters/<id>/detail); a hidden creature arrives
 * without numbers and gets blurred placeholders here.
 */
(function () {
  'use strict';
  var modal = document.getElementById('detail-modal');
  if (!modal) return;
  var panel = modal.querySelector('.modal-panel');
  var title = document.getElementById('detail-modal-title');
  var body = document.getElementById('detail-modal-body');
  var currentId = null;
  var currentParticipant = null;   // which battle copy was opened (so its roll says "Goblin #2")
  panel.classList.add('peek-panel');

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function signed(n) { return (n >= 0 ? '+' : '−') + Math.abs(n); }
  function safeColor(c, fallback) { return /^#[0-9a-fA-F]{3,8}$/.test(c || '') ? c : fallback; }

  function roll(bonus, label, cls, text, tip) {
    return '<span class="' + cls + ' roll-target" role="button" tabindex="0" data-roll-bonus="' + bonus +
      '" data-roll-label="' + esc(label) + '" title="' + esc(tip || ('Roll ' + label)) + '">' + text + '</span>';
  }

  function chips(c) {
    var out = '<span class="cd-chip">Lvl ' + esc(c.level) + '</span>' +
      '<span class="cd-chip cd-chip--' + (c.is_npc ? 'npc' : 'pc') + '">' + (c.is_npc ? (c.is_ally ? 'NPC · Ally' : 'NPC') : 'PC') + '</span>';
    var fs = c.factions && c.factions.length ? c.factions : (c.group_name ? [{ name: c.group_name, color: c.group_color }] : []);
    fs.forEach(function (f) {
      out += '<span class="cd-chip cd-chip--faction"><span class="group-dot" data-bg="' + safeColor(f.color, '#c9a24b') + '"></span>' + esc(f.name) + '</span>';
    });
    return out;
  }

  function vital(label, value, extra) {
    return '<div class="peek-vital' + (extra && extra.cls ? ' ' + extra.cls : '') + '"' +
      (extra && extra.attrs ? ' ' + extra.attrs : '') + '><span class="peek-vital-label">' + label + '</span><span class="peek-vital-val">' + value + '</span></div>';
  }

  function render(c) {
    var fogged = !!c.hidden_stats;
    var s = c.sheet;
    var html = '<div class="peek-top">' +
      '<div class="avatar-frame peek-avatar">' + (c.avatar_path ? '<img src="/uploads/' + esc(c.avatar_path) + '" alt="">' : '') + '</div>' +
      '<div class="peek-id"><div class="peek-chips">' + chips(c) + '</div>' +
      '<div class="made-by">Made by ' + esc(c.made_by || 'a former member') + '</div></div>' +
      '<a class="btn-icon peek-open" href="/campaigns/' + window.CAMPAIGN_ID + '/characters/' + c.id + '" title="Open the full page" aria-label="Open the full page">&#8599;</a>' +
      '</div>';

    if (fogged || !s) {
      html += '<div class="peek-vitals stats-fogged" title="The DM hasn\'t revealed this creature\'s stats" aria-label="Stats hidden">' +
        vital('HP', '00') + vital('AC', '00') + vital('Speed', '00') + '</div>' +
        '<div class="peek-abs stats-fogged" aria-label="Stats hidden">' +
        ['STR', 'DEX', 'CON', 'INT', 'WIS', 'CHA'].map(function (l) {
          return '<div class="peek-ab"><span class="peek-ab-name">' + l + '</span><span class="peek-ab-mod">+0</span><span class="peek-ab-score">10</span></div>';
        }).join('') + '</div>';
    } else {
      html += '<div class="peek-vitals">' +
        vital('HP', esc(c.max_hp), { cls: 'peek-vital--hp' }) + vital('AC', esc(c.armor_class)) +
        vital('Speed', esc(s.speed) + ' ft') +
        vital('Init.', s.initiative_text, { cls: 'roll-target', attrs: 'role="button" tabindex="0" data-roll-bonus="' + s.initiative + '" data-roll-label="Initiative" title="Roll initiative"' }) +
        vital('Prof.', s.prof_text) + vital('Perc.', esc(s.passive_perception)) +
        '</div>';

      html += '<div class="peek-rollbar"><span class="peek-hint">Tap a bonus to roll it. Everyone sees the result.</span>' +
        '<fieldset class="roll-mode roll-mode--small" aria-label="Roll mode">' +
        '<label><input type="radio" name="peek-roll-mode" value="normal" checked> Normal</label>' +
        '<label title="Shift-click also rolls with advantage"><input type="radio" name="peek-roll-mode" value="advantage"> Adv.</label>' +
        '<label title="Ctrl-click also rolls with disadvantage"><input type="radio" name="peek-roll-mode" value="disadvantage"> Dis.</label>' +
        '</fieldset></div>';

      html += '<div class="peek-abs">' + s.abilities.map(function (ab, i) {
        var sv = s.saves[i];
        return '<div class="peek-ab" title="' + esc(ab.name) + '"><span class="peek-ab-name">' + esc(ab.label) + '</span>' +
          roll(ab.mod, ab.name + ' check', 'peek-ab-mod', ab.text, 'Roll ' + ab.name + ' check') +
          '<span class="peek-ab-score">' + esc(ab.score) + '</span>' +
          roll(sv.bonus, ab.name + ' saving throw', 'peek-ab-save' + (sv.proficient ? ' is-prof' : ''),
            '<span class="mark" aria-hidden="true">' + (sv.proficient ? '●' : '○') + '</span> Save <b>' + sv.text + '</b>',
            'Roll ' + ab.name + ' saving throw' + (sv.proficient ? ' (proficient)' : '')) + '</div>';
      }).join('') + '</div>';

      if (c.attacks && c.attacks.length) {
        html += '<div class="peek-section" aria-label="Attacks"><div class="peek-section-title">Attacks</div><ul class="atk-list">' +
          c.attacks.map(function (a) {
            return '<li class="atk-row"><span class="atk-name">' + esc(a.name) +
              (a.damage_type ? '<small class="atk-type">' + esc(a.damage_type) + '</small>' : '') + '</span>' +
              '<button type="button" class="atk-btn atk-btn--hit" title="Roll ' + esc(a.name) + ' to hit" data-roll-bonus="' + a.attack_bonus +
              '" data-roll-label="' + esc(a.name) + ' attack"><small>To hit</small><b>' + esc(a.to_hit_text) + '</b></button>' +
              (a.damage && a.dice_json ? '<button type="button" class="atk-btn atk-btn--dmg" title="Roll ' + esc(a.name) + ' damage" data-roll-bonus="' + a.damage_bonus +
                '" data-roll-dice="' + esc(a.dice_json) + '" data-roll-label="' + esc(a.name) + ' damage' + (a.damage_type ? ' (' + esc(a.damage_type) + ')' : '') +
                '"><small>Damage</small><b>' + esc(a.damage) + '</b></button>' : '') + '</li>';
          }).join('') + '</ul></div>';
      }

      var trained = s.skills.filter(function (k) { return k.level; });
      function skill(k, cls) {
        return '<div class="sheet-skill' + (k.level === 1 ? ' is-prof' : '') + (k.level === 2 ? ' is-expert' : '') + (cls || '') + '" role="button" tabindex="0"' +
          ' data-roll-bonus="' + k.bonus + '" data-roll-label="' + esc(k.label) + '" title="' + esc(k.label + ' (' + k.ability + ')') + ': tap to roll">' +
          '<span class="mark" aria-hidden="true">' + (k.level === 2 ? '\u25C6' : (k.level === 1 ? '\u25CF' : '\u25CB')) + '</span>' +
          '<span class="lbl">' + esc(k.label) + ' <small>' + esc(k.ability) + '</small></span><span class="val">' + k.text + '</span></div>';
      }
      if (trained.length) html += '<div class="peek-trained" aria-label="Trained skills">' + trained.map(function (k) { return skill(k); }).join('') + '</div>';
      html += '<details class="peek-more"><summary>All skills</summary><div class="peek-skills">' + s.skills.map(function (k) { return skill(k); }).join('') + '</div></details>';
    }

    html += '<details class="peek-more"' + (c.notes_html ? ' open' : '') + '><summary>Notes</summary><div class="detail-notes">' +
      (c.notes_html || '<em>No notes recorded.</em>') + '</div></details>';

    if (!fogged && c.sheets && c.sheets.length) {
      var canDelete = !!c.can_delete;
      html += '<details class="peek-more"><summary>Sheets <span class="cf-count">' + c.sheets.length + '</span></summary><div class="sheet-thumbs">' +
        c.sheets.map(function (sh) {
          var src = '/uploads/' + esc(sh.image_path);
          return '<div class="sheet-thumb"><img src="' + src + '" alt="Sheet" data-lightbox-src="' + src + '"' +
            (canDelete ? ' data-delete-url="/campaigns/' + window.CAMPAIGN_ID + '/characters/' + c.id + '/sheets/' + sh.id + '/delete"' : '') + '></div>';
        }).join('') + '</div></details>';
    }
    return html;
  }

  var token = 0;
  function open(id, participantId) {
    currentId = id;
    currentParticipant = participantId || null;
    var mine = ++token;
    return fetch('/campaigns/' + window.CAMPAIGN_ID + '/api/characters/' + id + '/detail', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (c) {
        if (mine !== token) return;
        title.innerHTML = (window.classIconHtml ? window.classIconHtml(c.class_key) : '') + esc(c.name);
        body.innerHTML = render(c);
        body.scrollTop = 0;
        modal.hidden = false;
      });
  }

  if (window.CharacterRolls) window.CharacterRolls.bind(body, function () { return currentId; }, 'peek-roll-mode', function () { return currentParticipant; });

  document.addEventListener('keydown', function (ev) {
    if (ev.key === 'Escape' && !modal.hidden && !document.querySelector('.lightbox-overlay:not([hidden])')) modal.hidden = true;
  });

  window.CharPeek = { open: open };
})();
