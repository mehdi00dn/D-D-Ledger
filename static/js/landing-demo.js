/* Landing page-only product previews. Every control changes local demo state; nothing is saved or sent. */
(function () {
  'use strict';
  var root = document.querySelector('main#main');
  if (!root) return;

  function setPressed(button, pressed) {
    button.setAttribute('aria-pressed', pressed ? 'true' : 'false');
  }

  root.addEventListener('click', function (event) {
    var button = event.target.closest('button');
    if (!button || !root.contains(button)) return;

    /* Campaign preview tabs */
    if (button.hasAttribute('data-demo-tab')) {
      var tabs = button.closest('[data-demo]');
      if (!tabs) return;
      var value = button.getAttribute('data-demo-tab');
      tabs.querySelectorAll('[data-demo-tab]').forEach(function (tab) {
        setPressed(tab, tab === button);
      });
      var windowEl = tabs.closest('.demo-window');
      if (windowEl) {
        windowEl.querySelectorAll('[data-demo-view]').forEach(function (panel) {
          panel.hidden = panel.getAttribute('data-demo-view') !== value;
        });
      }
      return;
    }

    /* Character action preview */
    if (button.hasAttribute('data-action-tab')) {
      var actionTabs = button.closest('[data-demo]');
      if (!actionTabs) return;
      var action = button.getAttribute('data-action-tab');
      actionTabs.querySelectorAll('[data-action-tab]').forEach(function (tab) {
        setPressed(tab, tab === button);
      });
      var actionCard = actionTabs.closest('.demo-actions-card');
      if (actionCard) {
        actionCard.querySelectorAll('[data-action-panel]').forEach(function (panel) {
          panel.hidden = panel.getAttribute('data-action-panel') !== action;
        });
      }
      return;
    }

    /* Battle hit points: a tiny local control, never a real character mutation. */
    if (button.hasAttribute('data-hp-step')) {
      var hpPanel = button.closest('.demo-hp-panel');
      if (!hpPanel) return;
      var hpNode = hpPanel.querySelector('[data-demo-hp]');
      var hp = Number(hpNode.textContent) || 0;
      var max = 45;
      hp = Math.max(0, Math.min(max, hp + Number(button.getAttribute('data-hp-step'))));
      hpNode.textContent = String(hp);
      var bar = hpPanel.querySelector('[data-demo-hp-bar]');
      if (bar) {
        var level = hp <= 11 ? 'low' : (hp <= 27 ? 'mid' : 'high');
        bar.setAttribute('data-level', level);
      }
      var status = hpPanel.querySelector('[data-demo-hp-status]');
      if (status) status.textContent = hp === 0 ? 'Mira is unconscious in this preview.' : 'Mira has ' + hp + ' HP remaining.';
      return;
    }

    /* Advance initiative in the demo only. */
    if (button.hasAttribute('data-turn-next')) {
      var battle = button.closest('.demo-battle');
      if (!battle) return;
      var combatants = Array.prototype.slice.call(battle.querySelectorAll('[data-combatant]'));
      if (!combatants.length) return;
      var active = combatants.findIndex(function (row) { return row.classList.contains('is-active'); });
      var next = (active + 1) % combatants.length;
      combatants.forEach(function (row, index) {
        row.classList.toggle('is-active', index === next);
        var marker = row.querySelector('i');
        if (marker) marker.remove();
      });
      var activeName = combatants[next].querySelector('strong');
      var name = activeName ? activeName.textContent : 'Next combatant';
      var status = battle.querySelector('[data-demo-turn-status]');
      if (status) status.textContent = name + "'s turn";
      var roundNode = battle.querySelector('[data-demo-round]');
      if (next === 0 && roundNode) roundNode.textContent = String((Number(roundNode.textContent) || 1) + 1);
      return;
    }

    /* Fog-of-war toggle */
    if (button.hasAttribute('data-map-fog')) {
      var map = button.closest('.demo-map');
      var fog = map && map.querySelector('[data-map-fog-layer]');
      if (!fog) return;
      var on = fog.getAttribute('data-on') !== 'true';
      fog.setAttribute('data-on', on ? 'true' : 'false');
      setPressed(button, on);
      button.textContent = on ? 'Fog on' : 'Fog off';
      var mapStatus = map.querySelector('[data-map-status]');
      if (mapStatus) mapStatus.textContent = on ? 'A hidden passage waits beyond the fog.' : 'The shoreline is revealed.';
      return;
    }

    /* Place/remove a map marker */
    if (button.hasAttribute('data-map-marker')) {
      var mapRoot = button.closest('.demo-map');
      var marker = mapRoot && mapRoot.querySelector('[data-map-marker-dot]');
      if (!marker) return;
      var visible = marker.hidden;
      marker.hidden = !visible;
      setPressed(button, visible);
      button.textContent = visible ? 'Remove marker' : 'Place marker';
      var markerStatus = mapRoot.querySelector('[data-map-status]');
      if (markerStatus) markerStatus.textContent = visible ? 'A point of interest is marked.' : 'A hidden passage waits beyond the fog.';
      return;
    }

    /* Three tiny onboarding-style previews */
    if (button.hasAttribute('data-step-toggle')) {
      var key = button.getAttribute('data-step-toggle');
      var stepCard = button.closest('.demo-step-card');
      var feedback = stepCard && stepCard.querySelector('[data-step-feedback="' + key + '"]');
      var pressed = button.getAttribute('aria-pressed') !== 'true';
      setPressed(button, pressed);
      if (feedback) feedback.hidden = !pressed;
      var label = button.firstChild;
      if (label && label.nodeType === 3) {
        var labels = { campaign: 'Preview campaign', party: 'View party', session: 'Enter session preview' };
        label.textContent = pressed ? 'Preview ready' : labels[key];
      }
      return;
    }

    /* The dice card is a self-contained d20 example. */
    if (button.hasAttribute('data-roll-d20')) {
      var result = root.querySelector('[data-d20-result]');
      if (result) {
        var roll = Math.floor(Math.random() * 20) + 1;
        result.textContent = String(roll);
        result.setAttribute('data-roll', roll === 20 ? 'critical' : (roll === 1 ? 'natural-one' : 'normal'));
      }
    }
  });
})();
