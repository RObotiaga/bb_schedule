(() => {
  const rawFetch = window.fetch.bind(window);

  const featureNames = {
    miniapp_open: "Открытие Mini App",
    schedule_view: "Просмотр расписания",
    schedule_date_change: "Смена даты расписания",
    schedule_group_change: "Смена группы",
    teacher_search: "Поиск преподавателя",
    teacher_schedule_view: "Расписание преподавателя",
    teacher_subscription_change: "Подписка на преподавателя",
    session_view: "Просмотр сессии",
    session_refresh: "Обновление сессии",
    session_settings_change: "Фильтры сессии",
    subject_note_open: "Открытие заметки",
    subject_note_save: "Сохранение заметки",
    subject_checklist_change: "Чек-лист предмета",
    rating_view: "Рейтинг группы",
    subject_stats_search: "Поиск статистики предмета",
    subject_stats_view: "Просмотр статистики предмета",
    admin_status_view: "Статус фоновых задач",
    admin_job_start: "Запуск административной задачи",
    admin_analytics_view: "Просмотр статистики Mini App",
  };

  async function track(feature) {
    if (!state.initData) return;
    try {
      await rawFetch("/api/analytics/event", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Telegram-Init-Data": state.initData,
        },
        body: JSON.stringify({feature}),
        keepalive: true,
      });
    } catch (_) {}
  }

  function featureForRequest(url, options = {}) {
    const method = String(options.method || "GET").toUpperCase();
    const path = String(url);
    if (path.startsWith("/api/schedule/by-date")) return "schedule_view";
    if (path.startsWith("/api/schedule?") || path === "/api/schedule") return "schedule_view";
    if (path === "/api/me/group" && method === "POST") return "schedule_group_change";
    if (path.startsWith("/api/teachers/search")) return "teacher_search";
    if (/^\/api\/teachers\/[^/]+\/schedule/.test(path)) return "teacher_schedule_view";
    if (/^\/api\/teachers\/[^/]+\/subscribe/.test(path) && method === "POST") return "teacher_subscription_change";
    if (path.startsWith("/api/session/results")) return path.includes("refresh=true") ? "session_refresh" : "session_view";
    if (path === "/api/me/settings" && method === "POST") return "session_settings_change";
    if (path.startsWith("/api/session/notes/checklist") && method === "POST") return "subject_checklist_change";
    if (path.startsWith("/api/session/notes") && method === "GET") return "subject_note_open";
    if (path.startsWith("/api/session/notes") && method === "POST") return "subject_note_save";
    if (path === "/api/rating/group") return "rating_view";
    if (path.startsWith("/api/subjects?") || path === "/api/subjects") return "subject_stats_search";
    if (/^\/api\/subjects\/[^/]+\/stats/.test(path)) return "subject_stats_view";
    if (path === "/api/admin/status") return "admin_status_view";
    if (/^\/api\/admin\/jobs\/[^/]+\/start/.test(path) && method === "POST") return "admin_job_start";
    return null;
  }

  const originalApi = api;
  api = async function enhancedApi(url, options = {}) {
    const result = await originalApi(url, options);
    const feature = featureForRequest(url, options);
    if (feature) track(feature);
    return result;
  };

  const STRIP_INITIAL_DAYS = 14;
  const STRIP_LOAD_CHUNK_DAYS = 14;
  const MAX_STRIP_DAYS = 42;
  const STRIP_EDGE_PX = 110;

  const timeline = {
    group: null,
    minDate: null,
    maxDate: null,
    selectedDate: null,
    loadedStart: null,
    loadedEnd: null,
    shifting: false,
    pendingAnchor: null,
    centerNextRender: false,
  };

  function localDateString(dateValue = new Date()) {
    const y = dateValue.getFullYear();
    const m = String(dateValue.getMonth() + 1).padStart(2, "0");
    const d = String(dateValue.getDate()).padStart(2, "0");
    return `${y}-${m}-${d}`;
  }

  function parseLocalDate(dateString) {
    const [y, m, d] = String(dateString).split("-").map(Number);
    return new Date(y, m - 1, d, 12, 0, 0, 0);
  }

  function addDays(dateString, amount) {
    const dt = parseLocalDate(dateString);
    dt.setDate(dt.getDate() + Number(amount));
    return localDateString(dt);
  }

  function dayDifference(dateString) {
    const target = parseLocalDate(dateString);
    const today = parseLocalDate(localDateString());
    return Math.round((target - today) / 86400000);
  }

  function daysInclusive(start, end) {
    if (!start || !end || start > end) return 0;
    return dayDifferenceFrom(start, end) + 1;
  }

  function dayDifferenceFrom(start, end) {
    return Math.round((parseLocalDate(end) - parseLocalDate(start)) / 86400000);
  }

  function clampDate(dateString) {
    if (!timeline.minDate || !timeline.maxDate) return dateString;
    if (dateString < timeline.minDate) return timeline.minDate;
    if (dateString > timeline.maxDate) return timeline.maxDate;
    return dateString;
  }

  function fullDateLabel(dateString) {
    return new Intl.DateTimeFormat("ru-RU", {
      weekday: "long",
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
    }).format(parseLocalDate(dateString));
  }

  function resetTimeline() {
    timeline.group = null;
    timeline.minDate = null;
    timeline.maxDate = null;
    timeline.selectedDate = null;
    timeline.loadedStart = null;
    timeline.loadedEnd = null;
    timeline.pendingAnchor = null;
    timeline.centerNextRender = false;
  }

  function setInitialWindow(selectedDate) {
    let start = addDays(selectedDate, -Math.floor(STRIP_INITIAL_DAYS / 2));
    if (start < timeline.minDate) start = timeline.minDate;
    let end = addDays(start, STRIP_INITIAL_DAYS - 1);
    if (end > timeline.maxDate) {
      end = timeline.maxDate;
      start = addDays(end, -(STRIP_INITIAL_DAYS - 1));
      if (start < timeline.minDate) start = timeline.minDate;
    }
    timeline.loadedStart = start;
    timeline.loadedEnd = end;
  }

  function loadedDates() {
    if (!timeline.loadedStart || !timeline.loadedEnd) return [];
    const result = [];
    let current = timeline.loadedStart;
    while (current <= timeline.loadedEnd && result.length <= MAX_STRIP_DAYS) {
      result.push(current);
      current = addDays(current, 1);
    }
    return result;
  }

  const originalBuildDayStrip = buildDayStrip;
  buildDayStrip = function dynamicDatabaseDayStrip() {
    const strip = $("dayStrip");
    if (!timeline.loadedStart || !timeline.loadedEnd) {
      strip.innerHTML = "";
      return;
    }

    strip.innerHTML = loadedDates().map(dateString => {
      const dt = parseLocalDate(dateString);
      const dow = new Intl.DateTimeFormat("ru-RU", {weekday: "short"}).format(dt).replace(".", "");
      const active = dateString === timeline.selectedDate ? " active" : "";
      return `<button class="day-chip${active}" data-db-date="${esc(dateString)}" type="button"><span class="dow">${esc(dow)}</span><span class="num">${dt.getDate()}</span></button>`;
    }).join("");

    requestAnimationFrame(() => {
      if (timeline.pendingAnchor) {
        const {date, visualLeft} = timeline.pendingAnchor;
        const anchor = strip.querySelector(`[data-db-date="${date}"]`);
        if (anchor) strip.scrollLeft = anchor.offsetLeft - visualLeft;
        timeline.pendingAnchor = null;
        return;
      }
      if (timeline.centerNextRender && timeline.selectedDate) {
        const selected = strip.querySelector(`[data-db-date="${timeline.selectedDate}"]`);
        selected?.scrollIntoView({behavior: "auto", block: "nearest", inline: "center"});
        timeline.centerNextRender = false;
      }
    });
  };

  function canLoadBefore() {
    return Boolean(timeline.loadedStart && timeline.minDate && timeline.loadedStart > timeline.minDate);
  }

  function canLoadAfter() {
    return Boolean(timeline.loadedEnd && timeline.maxDate && timeline.loadedEnd < timeline.maxDate);
  }

  function shiftStripWindow(direction) {
    if (timeline.shifting) return;
    if (direction === "before" && !canLoadBefore()) return;
    if (direction === "after" && !canLoadAfter()) return;

    const strip = $("dayStrip");
    const anchorDate = direction === "before" ? timeline.loadedStart : timeline.loadedEnd;
    const anchorElement = strip.querySelector(`[data-db-date="${anchorDate}"]`);
    const visualLeft = anchorElement ? anchorElement.offsetLeft - strip.scrollLeft : 0;
    timeline.pendingAnchor = {date: anchorDate, visualLeft};
    timeline.shifting = true;

    if (direction === "before") {
      let newStart = addDays(timeline.loadedStart, -STRIP_LOAD_CHUNK_DAYS);
      if (newStart < timeline.minDate) newStart = timeline.minDate;
      timeline.loadedStart = newStart;
      if (daysInclusive(timeline.loadedStart, timeline.loadedEnd) > MAX_STRIP_DAYS) {
        timeline.loadedEnd = addDays(timeline.loadedStart, MAX_STRIP_DAYS - 1);
      }
    } else {
      let newEnd = addDays(timeline.loadedEnd, STRIP_LOAD_CHUNK_DAYS);
      if (newEnd > timeline.maxDate) newEnd = timeline.maxDate;
      timeline.loadedEnd = newEnd;
      if (daysInclusive(timeline.loadedStart, timeline.loadedEnd) > MAX_STRIP_DAYS) {
        timeline.loadedStart = addDays(timeline.loadedEnd, -(MAX_STRIP_DAYS - 1));
      }
    }

    buildDayStrip();
    requestAnimationFrame(() => { timeline.shifting = false; });
  }

  function handleStripScroll() {
    const strip = $("dayStrip");
    if (!strip || timeline.shifting) return;
    if (strip.scrollLeft <= STRIP_EDGE_PX) {
      shiftStripWindow("before");
      return;
    }
    if (strip.scrollLeft + strip.clientWidth >= strip.scrollWidth - STRIP_EDGE_PX) {
      shiftStripWindow("after");
    }
  }

  async function ensureTimelineForGroup() {
    if (!state.group) {
      resetTimeline();
      buildDayStrip();
      return false;
    }
    if (timeline.group === state.group && timeline.minDate && timeline.maxDate) return true;

    const range = await api(`/api/schedule/range?group=${encodeURIComponent(state.group)}`);
    timeline.group = state.group;
    timeline.minDate = range.min_date;
    timeline.maxDate = range.max_date;

    if (!timeline.minDate || !timeline.maxDate) {
      timeline.selectedDate = null;
      timeline.loadedStart = null;
      timeline.loadedEnd = null;
      buildDayStrip();
      return false;
    }

    timeline.selectedDate = clampDate(localDateString());
    setInitialWindow(timeline.selectedDate);
    timeline.centerNextRender = true;
    return true;
  }

  async function loadExactDate(dateString, quiet = false, userInitiated = false) {
    if (!state.group) return;
    const target = clampDate(dateString);
    timeline.selectedDate = target;
    state.dayOffset = dayDifference(target);
    timeline.centerNextRender = userInitiated;

    if (!quiet) $("scheduleView").innerHTML = `<div class="loading-card">Загружаю расписание…</div>`;
    const params = new URLSearchParams({group: state.group, date_value: target});
    const data = await api(`/api/schedule/by-date?${params}`);
    renderSchedule(data);

    if (!data.days?.length) {
      $("dayTitle").textContent = fullDateLabel(target);
      $("daySubtitle").textContent = `${state.group} · занятий в базе нет`;
    }

    if (userInitiated) {
      haptic();
      track("schedule_date_change");
    }
  }

  const originalLoadSchedule = loadSchedule;
  loadSchedule = async function databaseTimelineLoadSchedule(offset = state.dayOffset, quiet = false) {
    try {
      const ready = await ensureTimelineForGroup();
      if (!ready) {
        $("scheduleView").innerHTML = state.group
          ? `<div class="empty"><strong>Расписание отсутствует</strong>Для этой группы в базе пока нет ни одной даты.</div>`
          : `<div class="empty"><strong>Группа не выбрана</strong>Выберите её один раз — дальше расписание будет открываться сразу.</div>`;
        return;
      }

      const requested = new Date();
      requested.setDate(requested.getDate() + Number(offset || 0));
      const target = clampDate(localDateString(requested));

      if (target < timeline.loadedStart || target > timeline.loadedEnd) {
        setInitialWindow(target);
        timeline.centerNextRender = true;
      }
      await loadExactDate(target, quiet, false);
    } catch (error) {
      $("scheduleView").innerHTML = `<div class="error-card"><strong>Не удалось загрузить расписание</strong>${esc(error.message)}</div>`;
    }
  };

  function installDynamicStrip() {
    const strip = $("dayStrip");
    if (!strip || strip.dataset.dynamicDbStrip === "1") return;
    strip.dataset.dynamicDbStrip = "1";
    strip.addEventListener("scroll", handleStripScroll, {passive: true});
    strip.addEventListener("click", event => {
      const button = event.target.closest("[data-db-date]");
      if (!button) return;
      event.preventDefault();
      event.stopPropagation();
      loadExactDate(button.dataset.dbDate, false, true).catch(error => {
        $("scheduleView").innerHTML = `<div class="error-card"><strong>Не удалось загрузить расписание</strong>${esc(error.message)}</div>`;
      });
    }, true);
  }

  function installAdminAnalytics() {
    const panel = $("adminPanel");
    if (!panel || $("adminAnalyticsBtn")) return;
    const actions = panel.querySelector(".actions");
    if (!actions) return;

    const button = document.createElement("button");
    button.className = "action";
    button.id = "adminAnalyticsBtn";
    button.type = "button";
    button.innerHTML = `<div><strong>Статистика Mini App</strong><span>Пользователи и частота функций</span></div><div class="chevron">›</div>`;
    actions.appendChild(button);

    const output = document.createElement("div");
    output.id = "adminAnalyticsView";
    panel.querySelector(".panel-body").appendChild(output);

    button.addEventListener("click", async () => {
      output.innerHTML = `<div class="loading-card">Загружаю статистику…</div>`;
      try {
        const data = await api("/api/admin/analytics");
        renderAdminAnalytics(data, output);
      } catch (error) {
        output.innerHTML = `<div class="error-card"><strong>Не удалось загрузить статистику</strong>${esc(error.message)}</div>`;
      }
    });
  }

  function installAnalyticsStyles() {
    if ($("miniappAnalyticsStyles")) return;
    const style = document.createElement("style");
    style.id = "miniappAnalyticsStyles";
    style.textContent = `
      .analytics-card{margin-top:12px;padding:14px;border-radius:16px;background:var(--bg)}
      .analytics-list{display:grid;gap:7px;margin-top:10px}
      .analytics-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;align-items:center;padding:10px 0;border-bottom:1px solid var(--separator)}
      .analytics-row:last-child{border-bottom:0}.analytics-name{font-size:13px;font-weight:680}.analytics-meta{font-size:11px;color:var(--muted);margin-top:2px}.analytics-count{font-size:14px;font-weight:800;color:var(--accent)}
      .never-list{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}.never-chip{font-size:10px;padding:5px 7px;border-radius:999px;background:color-mix(in srgb,var(--danger) 10%,var(--surface));color:var(--danger)}
    `;
    document.head.appendChild(style);
  }

  function renderAdminAnalytics(data, output) {
    const adoption = data.total_users ? Math.round(data.active_today / data.total_users * 1000) / 10 : 0;
    const used = (data.features || []).filter(item => item.uses > 0);
    const never = data.never_used || [];
    output.innerHTML = `
      <div class="analytics-card">
        <div class="summary-grid">
          <div class="stat"><div class="stat-value">${data.total_users}</div><div class="stat-label">всего пользователей</div></div>
          <div class="stat"><div class="stat-value">${data.active_today}</div><div class="stat-label">активны сегодня</div></div>
          <div class="stat"><div class="stat-value">${adoption}%</div><div class="stat-label">доля активных</div></div>
        </div>
        <div class="section-label">Использование функций</div>
        <div class="analytics-list">
          ${used.length ? used.map(item => `
            <div class="analytics-row">
              <div>
                <div class="analytics-name">${esc(featureNames[item.feature] || item.feature)}</div>
                <div class="analytics-meta">${item.unique_users} уник. пользователей${item.last_used_at ? ` · последнее ${esc(new Date(item.last_used_at).toLocaleString("ru-RU"))}` : ""}</div>
              </div>
              <div class="analytics-count">${item.uses}</div>
            </div>
          `).join("") : `<div class="list-row-sub">Событий использования пока нет.</div>`}
        </div>
        <div class="section-label">Ни разу не использовались</div>
        <div class="never-list">
          ${never.length ? never.map(feature => `<span class="never-chip">${esc(featureNames[feature] || feature)}</span>`).join("") : `<span class="list-row-sub">Все отслеживаемые функции уже использовались.</span>`}
        </div>
      </div>
    `;
  }

  function waitForProfileAndTrackOpen(attempt = 0) {
    if (state.initData) {
      track("miniapp_open");
      return;
    }
    if (attempt < 20) setTimeout(() => waitForProfileAndTrackOpen(attempt + 1), 150);
  }

  installAnalyticsStyles();
  installDynamicStrip();
  installAdminAnalytics();
  waitForProfileAndTrackOpen();
})();
