// Campaign Home: long text (the bio, the campaign notes) is cut to a few lines with a "Read more" button.
(function () {
  function sync(box) {
    var btn = box.nextElementSibling;
    if (!btn || !btn.hasAttribute('data-clamp-toggle')) return;
    var open = box.classList.contains('is-open');
    // Overflowing means there is more to show; once opened the button stays so the text can be folded again.
    var overflow = box.scrollHeight > box.clientHeight + 1;
    box.classList.toggle('is-clipped', !open && overflow);       // the fade at the bottom shows only while text is cut off
    btn.hidden = !(open || overflow);
  }
  document.querySelectorAll('[data-clamp]').forEach(function (box) {
    var btn = box.nextElementSibling;
    sync(box);
    if (btn) btn.addEventListener('click', function () {
      var open = box.classList.toggle('is-open');
      btn.textContent = open ? 'Read less' : 'Read more';
      sync(box);
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    window.addEventListener('resize', function () { sync(box); });
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(function () { sync(box); });
  });
})();
