(() => {
  document.documentElement.classList.add('js');

  document.querySelectorAll('[data-dismiss-flash]').forEach((button) => {
    button.addEventListener('click', () => {
      const flash = button.closest('.flash');
      if (flash) flash.remove();
    });
  });

  // Keep the user on the form if no massage area has been rated.
  document.querySelectorAll('.rating-form').forEach((form) => {
    const error = form.querySelector('[data-score-error]');
    form.addEventListener('submit', (event) => {
      const hasScore = [...form.querySelectorAll('input[type="radio"][name^="score_"]:checked')]
        .some((input) => input.value !== '');
      if (!hasScore && error) {
        event.preventDefault();
        error.textContent = 'Beri bintang untuk sedikitnya satu area yang Anda rasakan.';
        error.hidden = false;
        error.focus();
      }
    });
    form.addEventListener('change', (event) => {
      if (error && event.target.matches('input[type="radio"][name^="score_"]') && event.target.value) {
        error.hidden = true;
      }
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

  // Password visibility toggles keep long passphrases verifiable on touch
  // devices without exposing them by default.
  document.querySelectorAll('[data-password-toggle]').forEach((button) => {
    button.addEventListener('click', () => {
      const input = document.querySelector(button.dataset.passwordToggle);
      if (!input) return;
      const show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      button.setAttribute('aria-pressed', String(show));
      button.setAttribute('aria-label', show ? 'Sembunyikan kata sandi' : 'Tampilkan kata sandi');
      button.classList.toggle('is-showing', show);
      input.focus({ preventScroll: true });
    });
  });

  // Destructive forms (e.g. deleting a rating) ask before acting.
  document.querySelectorAll('form[data-confirm]').forEach((form) => {
    form.addEventListener('submit', (event) => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });

  // Search autocomplete: fetch suggestions while the user types on home.
  const searchInput = document.querySelector('.search-line input[name="q"]');
  const suggestBox = document.getElementById('suggest-box');
  if (searchInput && suggestBox) {
    let suggestTimer = null;
    let suggestController = null;
    let activeIndex = -1;

    const setExpanded = (open) => {
      searchInput.setAttribute('aria-expanded', String(open));
      if (!open) {
        searchInput.removeAttribute('aria-activedescendant');
        activeIndex = -1;
      }
    };

    const closeSuggest = () => {
      suggestBox.hidden = true;
      suggestBox.innerHTML = '';
      setExpanded(false);
    };

    const options = () => [...suggestBox.querySelectorAll('.suggest-item')];

    const setActive = (index) => {
      const items = options();
      if (!items.length) return;
      activeIndex = (index + items.length) % items.length;
      items.forEach((item, i) => item.classList.toggle('is-active', i === activeIndex));
      searchInput.setAttribute('aria-activedescendant', items[activeIndex].id);
      items[activeIndex].scrollIntoView({ block: 'nearest' });
    };

    const suggestLink = (href, label, kind) => {
      const a = document.createElement('a');
      a.href = href;
      a.className = 'suggest-item';
      a.id = `suggest-option-${suggestBox.childElementCount}`;
      a.setAttribute('role', 'option');
      a.tabIndex = -1;
      const kindSpan = document.createElement('span');
      kindSpan.className = 'suggest-kind';
      kindSpan.textContent = kind;
      const text = document.createElement('span');
      text.textContent = label;
      a.append(kindSpan, text);
      return a;
    };

    searchInput.addEventListener('input', () => {
      window.clearTimeout(suggestTimer);
      activeIndex = -1;
      searchInput.removeAttribute('aria-activedescendant');
      const term = searchInput.value.trim();
      if (term.length < 2) {
        closeSuggest();
        return;
      }
      suggestTimer = window.setTimeout(async () => {
        if (suggestController) suggestController.abort();
        suggestController = new AbortController();
        try {
          const res = await fetch(`/api/v1/suggest?q=${encodeURIComponent(term)}`, {
            signal: suggestController.signal,
          });
          if (!res.ok) return;
          const data = await res.json();
          suggestBox.innerHTML = '';
          data.venues.forEach((v) =>
            suggestBox.append(suggestLink(`/venue/${v.slug}`, `${v.name} · ${v.city}`, 'Gedung'))
          );
          data.spots.forEach((s) =>
            suggestBox.append(
              suggestLink(`/venue/${s.venue_slug}/titik/${s.spot_slug}`, `${s.name} · Lt. ${s.floor} (${s.venue_name})`, 'Titik')
            )
          );
          data.brands.forEach((b) => suggestBox.append(suggestLink('/merk', b.name, 'Merk')));
          if (!suggestBox.childNodes.length) {
            closeSuggest();
            return;
          }
          suggestBox.hidden = false;
          setExpanded(true);
        } catch (err) {
          if (err.name !== 'AbortError') closeSuggest();
        }
      }, 250);
    });

    searchInput.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        closeSuggest();
        return;
      }
      if (suggestBox.hidden) return;
      const items = options();
      if (!items.length) return;
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        setActive(activeIndex + 1);
      } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        setActive(activeIndex < 0 ? items.length - 1 : activeIndex - 1);
      } else if (event.key === 'Enter' && activeIndex >= 0) {
        event.preventDefault();
        items[activeIndex].click();
      }
    });

    suggestBox.addEventListener('click', (event) => {
      if (event.target.closest('.suggest-item')) closeSuggest();
    });

    document.addEventListener('click', (event) => {
      if (!event.target.closest('.search-field')) closeSuggest();
    });
    searchInput.closest('form').addEventListener('submit', closeSuggest);
  }
})();
