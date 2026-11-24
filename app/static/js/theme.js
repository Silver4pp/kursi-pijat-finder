(() => {
  const KEY = 'kpf-theme';
  const root = document.documentElement;
  const meta = document.querySelector('meta[name="theme-color"]');
  const media = window.matchMedia('(prefers-color-scheme: dark)');

  const stored = () => {
    try {
      return localStorage.getItem(KEY);
    } catch (error) {
      return null;
    }
  };

  const apply = (mode) => {
    const dark = mode === 'dark';
    root.dataset.theme = dark ? 'dark' : 'light';
    if (meta) meta.setAttribute('content', dark ? '#0b1211' : '#f4f7f6');
  };

  apply(stored() || (media.matches ? 'dark' : 'light'));

  const ready = () => {
    document.querySelectorAll('[data-theme-toggle]').forEach((button) => {
      const label = button.querySelector('[data-theme-label]');
      const sync = () => {
        const dark = root.dataset.theme === 'dark';
        button.setAttribute('aria-pressed', String(dark));
        button.setAttribute('aria-label', dark ? 'Ganti ke tema terang' : 'Ganti ke tema gelap');
        if (label) label.textContent = dark ? 'Terang' : 'Gelap';
      };
      sync();
      button.addEventListener('click', () => {
        const next = root.dataset.theme === 'dark' ? 'light' : 'dark';
        apply(next);
        try {
          localStorage.setItem(KEY, next);
        } catch (error) {
          /* Storage can be blocked; the theme still applies for this page. */
        }
        sync();
      });
    });
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', ready);
  } else {
    ready();
  }
})();
