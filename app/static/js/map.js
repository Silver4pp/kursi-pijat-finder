(() => {
  document.documentElement.classList.add('js');
  const explorer = document.getElementById('explorer');
  const mapElement = document.getElementById('map');
  const mapLoading = document.getElementById('map-loading');
  const panel = document.getElementById('venue-panel');
  const panelTitle = document.getElementById('panel-title');
  const panelSubtitle = document.getElementById('panel-subtitle');
  const panelSpots = document.getElementById('panel-spots');
  const closeButton = document.getElementById('panel-close');
  const nearMeButton = document.getElementById('near-me');
  const searchStatus = document.getElementById('search-status');
  const viewButtons = [...document.querySelectorAll('[data-view-target]')];
  const venueButtons = [...document.querySelectorAll('[data-venue-id]')];

  const escapeHTML = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  }[char]));

  const formatDate = (value) => {
    if (!value) return '';
    const date = new Date(`${value}T00:00:00`);
    return Number.isNaN(date.getTime())
      ? value
      : new Intl.DateTimeFormat('id-ID', { day: 'numeric', month: 'long', year: 'numeric' }).format(date);
  };

  const setSearchStatus = (message) => {
    if (searchStatus) searchStatus.textContent = message;
  };

  let map = null;
  let venueLayer = null;
  let activeVenueId = null;
  let lastFocusedElement = null;
  let pendingBounds = null;
  const markers = new Map();

  function fitPendingBounds() {
    if (!map || !pendingBounds || !map.getSize().x || !map.getSize().y) return;
    const bounds = pendingBounds;
    pendingBounds = null;
    if (bounds.length === 1) map.setView(bounds[0], 14);
    else if (bounds.length > 1) map.fitBounds(bounds, { padding: [38, 38], maxZoom: 13 });
  }

  function setExplorerView(view, { focusMap = false } = {}) {
    if (!explorer) return;
    explorer.dataset.view = view;
    viewButtons.forEach((button) => {
      button.setAttribute('aria-pressed', String(button.dataset.viewTarget === view));
    });
    if (view === 'map' && map) {
      // On mobile the map begins hidden behind the list. Measure it before
      // fitting bounds or flying to a venue; a zero-size map yields NaN.
      map.invalidateSize({ animate: false });
      fitPendingBounds();
      window.setTimeout(() => map.invalidateSize({ animate: false }), 80);
      if (focusMap) mapElement?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }

  viewButtons.forEach((button) => {
    button.addEventListener('click', () => setExplorerView(button.dataset.viewTarget));
  });

  function setNearButtonState(isLoading, label) {
    if (!nearMeButton) return;
    nearMeButton.disabled = isLoading;
    nearMeButton.classList.toggle('is-loading', isLoading);
    const labelElement = nearMeButton.querySelector('[data-near-label]');
    if (labelElement && label) labelElement.textContent = label;
  }

  if (nearMeButton) {
    nearMeButton.addEventListener('click', () => {
      if (!navigator.geolocation) {
        setSearchStatus('Peramban ini tidak mendukung lokasi perangkat. Cari berdasarkan nama gedung atau daerah.');
        return;
      }
      setNearButtonState(true, 'Mencari lokasi…');
      setSearchStatus('Meminta izin lokasi untuk mengurutkan gedung terdekat…');
      navigator.geolocation.getCurrentPosition(
        ({ coords }) => {
          const params = new URLSearchParams(window.location.search);
          params.set('lat', coords.latitude.toFixed(6));
          params.set('lng', coords.longitude.toFixed(6));
          window.location.search = params.toString();
        },
        () => {
          setNearButtonState(false, 'Terdekat dari saya');
          setSearchStatus('Lokasi perangkat tidak dapat dibaca. Hasil tetap dapat dicari berdasarkan nama gedung atau daerah.');
        },
        { timeout: 10000, maximumAge: 300000 }
      );
    });
  }

  function clearActiveVenue({ closePanel = true } = {}) {
    activeVenueId = null;
    venueButtons.forEach((button) => {
      button.setAttribute('aria-pressed', 'false');
      button.closest('[data-venue-card]')?.classList.remove('is-selected');
    });
    markers.forEach(({ marker }) => {
      marker.getElement()?.querySelector('.map-pin')?.classList.remove('is-active');
    });
    if (closePanel && panel) panel.hidden = true;
    if (lastFocusedElement) lastFocusedElement.focus({ preventScroll: true });
  }

  function markActiveVenue(venueId) {
    activeVenueId = venueId;
    venueButtons.forEach((button) => {
      const isActive = button.dataset.venueId === venueId;
      button.setAttribute('aria-pressed', String(isActive));
      button.closest('[data-venue-card]')?.classList.toggle('is-selected', isActive);
    });
    markers.forEach(({ marker }, id) => {
      marker.getElement()?.querySelector('.map-pin')?.classList.toggle('is-active', id === venueId);
    });
  }

  function showPanelLoading() {
    if (panelSubtitle) panelSubtitle.textContent = 'Memuat lantai, patokan, dan kursi…';
    if (panelSpots) {
      panelSpots.innerHTML = `
        <div class="loading-card" aria-hidden="true">
          <span></span><span></span><span></span>
        </div>
        <p class="sr-only">Memuat titik…</p>`;
    }
  }

  async function selectVenue(venue, sourceElement = null) {
    if (!panel || !panelTitle || !panelSpots) return;
    lastFocusedElement = sourceElement;
    markActiveVenue(venue.id);
    setExplorerView('map');
    panel.hidden = false;
    panelTitle.textContent = venue.name;
    showPanelLoading();
    panel.focus({ preventScroll: true });

    if (map && Number.isFinite(Number(venue.lat)) && Number.isFinite(Number(venue.lng))) {
      const record = markers.get(venue.id);
      if (record && venueLayer && typeof venueLayer.zoomToShowLayer === 'function') {
        venueLayer.zoomToShowLayer(record.marker, () => {
          if (activeVenueId !== venue.id) return;
          markActiveVenue(venue.id);
          if (map.getZoom() < 15) {
            map.flyTo([Number(venue.lat), Number(venue.lng)], 15, { duration: 0.5 });
          }
        });
      } else {
        map.flyTo([Number(venue.lat), Number(venue.lng)], Math.max(map.getZoom(), 15), { duration: 0.5 });
      }
    }

    try {
      const response = await fetch(`/api/v1/venues/${encodeURIComponent(venue.id)}/spots${window.location.search}`);
      if (!response.ok) throw new Error('Titik tidak dapat dimuat');
      const data = await response.json();

      if (activeVenueId !== venue.id) return;
      if (!data.spots.length) {
        if (panelSubtitle) panelSubtitle.textContent = 'Tidak ada titik yang cocok dengan filter saat ini.';
        panelSpots.innerHTML = `
          <div class="panel-empty">
            <strong>Filter terlalu sempit.</strong>
            <p>Tidak ada titik yang cocok pada gedung ini. Longgarkan filter untuk melihat semua titiknya.</p>
          </div>`;
        return;
      }

      if (panelSubtitle) {
        panelSubtitle.textContent = `${data.spots.length} titik ditemukan. Buka detailnya untuk rute, akses, dan rating.`;
      }

      panelSpots.innerHTML = data.spots.map((spot) => `
        <article class="panel-spot">
          <div class="panel-spot-head">
            <span class="floor-badge">Lantai ${escapeHTML(spot.floor)}</span>
            <span class="status is-ada">${escapeHTML(spot.status_label)}</span>
          </div>
          <h3>${escapeHTML(spot.name)}</h3>
          <p class="panel-landmark">${escapeHTML(spot.landmark)}</p>
          <div class="panel-meta">
            ${spot.last_verified ? `<span>Terakhir diperiksa ${escapeHTML(formatDate(spot.last_verified))}</span>` : ''}
            <span>Akses: ${spot.access.map(escapeHTML).join(', ')}</span>
          </div>
          ${spot.placements.map((placement) => `
            <div class="panel-placement">
              <span class="panel-placement-main">
                <strong>${escapeHTML(placement.brand)}</strong>
                <small>${escapeHTML(placement.type)}</small>
                ${placement.payments?.length ? `<small>${placement.payments.map(escapeHTML).join(' · ')}</small>` : ''}
              </span>
              <span class="panel-placement-price">
                <strong>${placement.quantity ?? '?'} kursi</strong>
                <small>${placement.price ? `${escapeHTML(placement.price)}${placement.duration ? ` / ${placement.duration} menit` : ''}` : escapeHTML(placement.cost_label)}</small>
              </span>
            </div>`).join('')}
          <a class="button secondary small panel-detail-link" href="/venue/${encodeURIComponent(spot.venue_slug)}/titik/${encodeURIComponent(spot.slug)}">Detail titik &amp; rute</a>
        </article>`).join('');
    } catch (error) {
      if (activeVenueId !== venue.id) return;
      if (panelSubtitle) panelSubtitle.textContent = 'Titik belum dapat dimuat.';
      panelSpots.innerHTML = `
        <div class="panel-empty">
          <strong>Koneksi atau server sedang bermasalah.</strong>
          <p>Daftar gedung tetap dapat digunakan. Coba pilih gedung ini lagi beberapa saat nanti.</p>
        </div>`;
    }
  }

  venueButtons.forEach((button) => {
    button.addEventListener('click', () => {
      const record = markers.get(button.dataset.venueId);
      const venue = record?.venue || {
        id: button.dataset.venueId,
        name: button.dataset.name || 'Gedung',
        lat: Number(button.dataset.lat),
        lng: Number(button.dataset.lng)
      };
      selectVenue(venue, button);
      mapElement?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    });
  });

  if (closeButton) closeButton.addEventListener('click', () => clearActiveVenue());
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && panel && !panel.hidden) clearActiveVenue();
  });

  if (!mapElement || typeof L === 'undefined') {
    if (mapLoading) mapLoading.textContent = 'Peta tidak tersedia. Daftar gedung tetap dapat digunakan.';
    return;
  }

  map = L.map('map', {
    scrollWheelZoom: true,
    zoomControl: true,
    attributionControl: true
  }).setView([-6.2146, 106.8451], 11);

  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '© OpenStreetMap contributors'
  }).addTo(map);

  // One marker represents one building; the cluster count is buildings,
  // while the number on an individual pin is the number of matching spots.
  venueLayer = typeof L.markerClusterGroup === 'function'
    ? L.markerClusterGroup({
        maxClusterRadius: 60,
        showCoverageOnHover: false,
        zoomToBoundsOnClick: true,
        spiderfyOnMaxZoom: true,
        iconCreateFunction: (cluster) => {
          const count = cluster.getChildCount();
          return L.divIcon({
            className: 'venue-cluster-icon',
            html: `<span class="venue-cluster" aria-label="${count} gedung. Pilih untuk memperbesar peta." title="${count} gedung"><b>${count}</b></span>`,
            iconSize: [48, 48],
            iconAnchor: [24, 24]
          });
        }
      })
    : L.layerGroup();
  venueLayer.addTo(map);

  async function loadVenues() {
    try {
      const response = await fetch(`/api/v1/venues${window.location.search}`);
      if (!response.ok) throw new Error('Venue tidak dapat dimuat');
      const data = await response.json();
      const bounds = [];

      data.venues.forEach((venue) => {
        const marker = L.marker([venue.lat, venue.lng], {
          title: venue.name,
          alt: `Pin gedung ${venue.name}`,
          icon: L.divIcon({
            className: 'venue-marker',
            html: `<span class="map-pin"><b>${venue.spot_count}</b></span>`,
            iconSize: [40, 40],
            iconAnchor: [20, 36]
          })
        });

        marker.bindTooltip(`${escapeHTML(venue.name)} · ${venue.spot_count} titik`, {
          direction: 'top',
          offset: [0, -32],
          opacity: 0.96
        });
        marker.on('click', () => selectVenue(venue, marker.getElement()));
        marker.on('add', () => {
          marker.getElement()?.querySelector('.map-pin')?.classList.toggle('is-active', activeVenueId === venue.id);
        });
        markers.set(venue.id, { marker, venue });
        venueLayer.addLayer(marker);
        bounds.push([venue.lat, venue.lng]);
      });

      pendingBounds = bounds;
      fitPendingBounds();

      if (mapLoading) {
        mapLoading.textContent = data.venues.length
          ? `${data.venues.length} pin gedung dimuat.`
          : 'Tidak ada pin untuk filter ini.';
        mapLoading.classList.add('is-done');
        window.setTimeout(() => mapLoading.remove(), 700);
      }
    } catch (error) {
      if (mapLoading) {
        mapLoading.innerHTML = 'Pin tidak dapat dimuat. Daftar gedung tetap tersedia.';
        mapLoading.classList.add('is-error');
      }
    }
  }

  loadVenues();
})();
