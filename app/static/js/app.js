(() => {
  document.documentElement.classList.add('js');

  document.querySelectorAll('[data-dismiss-flash]').forEach((button) => {
    button.addEventListener('click', () => {
      const flash = button.closest('.flash');
      if (flash) flash.remove();
    });
  });

  // Keep long internal forms easier to complete on touch devices: the first
  // invalid field is moved into view by the browser, and this makes the
  // focused control clear below the sticky header.
  document.querySelectorAll('input, select, textarea').forEach((field) => {
    field.addEventListener('invalid', () => {
      window.setTimeout(() => {
        field.scrollIntoView({ block: 'center', behavior: 'smooth' });
      }, 50);
    });
  });
})();
