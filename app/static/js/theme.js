(() => {
  const KEY = 'kpf-theme';
  const root = document.documentElement;
  const meta = document.querySelector('meta[name="theme-color"]');
  const media = window.matchMedia('(prefers-color-scheme: dark)');

  const read = () => {
    try {
      const value = localStorage.getItem(KEY);
      return value === 'dark' || value === 'light' ? value : null;
    } catch (error) {
      return null;
    }
  };

  const write = (mode) => {
    try {
      localStorage.setItem(KEY, mode);
    } catch (error) {
      /* Storage can be blocked; the theme still applies for this page. */
    }
  };

  const apply = (mode) => {
    const dark = mode === 'dark';
    root.dataset.theme = dark ? 'dark' : 'light';
    if (meta) meta.setAttribute('content', dark ? '#0b1211' : '#f4f7f6');
  };

  apply(read() || (media.matches ? 'dark' : 'light'));

  const ready = () => {
    const buttons = document.querySelectorAll('[data-theme-toggle]');

    const sync = () => {
      const dark = root.dataset.theme === 'dark';
      buttons.forEach((button) => {
        // The label names the target mode, so the icon must match it.
        button.setAttribute('aria-label', dark ? 'Ganti ke tema terang' : 'Ganti ke tema gelap');
        const label = button.querySelector('[data-theme-label]');
        if (label) label.textContent = dark ? 'Terang' : 'Gelap';
      });
    };

    buttons.forEach((button) => {
      button.addEventListener('click', () => {
        const next = root.dataset.theme === 'dark' ? 'light' : 'dark';
        apply(next);
        write(next);
        sync();
      });
    });

    sync();

    // Follow the OS only while the visitor has not made an explicit choice.
    const onSystemChange = (event) => {
      if (read()) return;
      apply(event.matches ? 'dark' : 'light');
      sync();
    };
    if (typeof media.addEventListener === 'function') {
      media.addEventListener('change', onSystemChange);
    } else {
      media.addListener(onSystemChange);
    }

    // Keep tabs in step when the choice is changed in another one.
    window.addEventListener('storage', (event) => {
      if (event.key !== KEY || !read()) return;
      apply(read());
      sync();
    });
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', ready);
  } else {
    ready();
  }
})();
