/* The Ally switch only makes sense for an NPC: greyed out until NPC is on, and cleared when NPC goes off. */
(function () {
  const npc = document.querySelector('input[name="is_npc"]');
  const ally = document.querySelector('input[name="is_ally"]');
  if (!npc || !ally) return;
  const wrap = ally.closest('[data-ally-toggle]');
  function sync() {
    ally.disabled = !npc.checked;
    if (!npc.checked) ally.checked = false;
    if (wrap) {
      wrap.classList.toggle('is-disabled', !npc.checked);
      wrap.title = npc.checked ? 'An ally shows its stats to Players and stays visible when NPCs are hidden' : 'Switch on NPC first';
    }
  }
  npc.addEventListener('change', sync);
  sync();
})();
