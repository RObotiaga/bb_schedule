(() => {
  const rawFetch = window.fetch.bind(window);
  const archive = {
    group: null,
    days: [],
    selectedDate: null,
  };

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
    if (path.startsWith("/api/schedule")) return "schedule_view";
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

  function dayDifference(dateString) {
    const target = parseLocalDate(dateString);
    const today = parseLocalDate(localDateString());
    return Math.round((target - today) / 86400000);
  }

  function chooseNearestAvailableDate(days) {
    if (!days.length) return null;
    const today = localDateString();
    const exact = days.find(day => day.date === today);
    if (exact) return exact.date;
    const next = days.find(day => day.date > today);
    if (next) return next.date;
    return days[days.length - 1].date;
  }

  function formatShortDate(dateString) {
    const dt = parseLocalDate(dateString);
    return new Intl.DateTimeFormat("ru-RU", {day: "2-digit", month: "2-digit", year: "numeric"}).format(dt);
  }

  function installArchiveControls() {
    if ($("archiveControls")) return;
    const controls = document.createElement("div");
    controls.id = "archiveControls";
    controls.innerHTML = `
      <div class="archive-head">
        <div>
          <div class="archive-label">Доступные даты в базе</div>
          <div class="archive-range" id="archiveRange">После выбора группы</div>
        </div>
        <button class="mini-btn" id="archiveNearestBtn" type="button">К текущей дате</button>
      </div>
      <select id="archiveDateSelect" class="archive-select" aria-label="Выбрать дату расписания" disabled>
        <option>Нет данных</option>
      </select>
    `;
    $("dayStrip").before(controls);

    const style = document.createElement("style");
    style.textContent = `
      #archiveControls{margin:0 0 9px}.archive-head{display:flex;align-items:center;justify-content:space-between;gap:10px;margin:0 2px 8px}.archive-label{font-size:12px;font-weight:720;color:var(--text)}.archive-range{font-size:11px;color:var(--muted);margin-top:2px}.archive-select{width:100%;min-height:44px;border:1px solid var(--separator);border-radius:14px;padding:9px 12px;background:var(--surface);color:var(--text);outline:none}.analytics-card{margin-top:12px;padding:14px;border-radius:16px;background:var(--bg)}.analytics-list{display:grid;gap:7px;margin-top:10px}.analytics-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;align-items:center;padding:10px 0;border-bottom:1px solid var(--separator)}.analytics-row:last-child{border-bottom:0}.analytics-name{font-size:13px;font-weight:680}.analytics-meta{font-size:11px;color:var(--muted);margin-top:2px}.analytics-count{font-size:14px;font-weight:800;color:var(--accent)}.never-list{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}.never-chip{font-size:10px;padding:5px 7px;border-radius:999px;background:color-mix(in srgb,var(--danger) 10%,var(--surface));color:var(--danger)}
    `;
    document.head.appendChild(style);

    $("archiveDateSelect").addEventListener("change", event => {
      selectArchiveDate(event.target.value, true);
    });
    $("archiveNearestBtn").addEventListener("click", () => {
      const nearest = chooseNearestAvailableDate(archive.days);
      if (nearest) selectArchiveDate(nearest, true);
    });
    $("dayStrip").addEventListener("click", event => {
      const button = event.target.closest("[data-archive-date]");
      if (!button) return;
      event.preventDefault();
      event.stopPropagation();
      selectArchiveDate(button.dataset.archiveDate, true);
    }, true);
  }

  function updateArchiveControls() {
    const select = $("archiveDateSelect");
    const range = $("archiveRange");
    if (!select || !range) return;

    if (!archive.days.length) {
      select.disabled = true;
      select.innerHTML = "<option>Нет дат в базе</option>";
      range.textContent = "Для этой группы расписание в базе отсутствует";
      return;
    }

    select.disabled = false;
    select.innerHTML = archive.days.map(day => {
      const selected = day.date === archive.selectedDate ? " selected" : "";
      return `<option value="${esc(day.date)}"${selected}>${esc(day.date_display || formatShortDate(day.date))}</option>`;
    }).join("");
    range.textContent = `${archive.days.length} дат · ${formatShortDate(archive.days[0].date)} — ${formatShortDate(archive.days[archive.days.length - 1].date)}`;
  }

  const originalBuildDayStrip = buildDayStrip;
  buildDayStrip = function archiveDayStrip() {
    if (!archive.days.length) {
      $("dayStrip").innerHTML = "";
      return;
    }
    let selectedIndex = archive.days.findIndex(day => day.date === archive.selectedDate);
    if (selectedIndex < 0) selectedIndex = 0;
    const start = Math.max(0, Math.min(selectedIndex - 4, archive.days.length - 9));
    const visible = archive.days.slice(start, start + 9);
    $("dayStrip").innerHTML = visible.map(day => {
      const dt = parseLocalDate(day.date);
      const dow = new Intl.DateTimeFormat("ru-RU", {weekday: "short"}).format(dt).replace(".", "");
      const active = day.date === archive.selectedDate ? " active" : "";
      return `<button class="day-chip${active}" data-archive-date="${esc(day.date)}" type="button"><span class="dow">${esc(dow)}</span><span class="num">${dt.getDate()}</span></button>`;
    }).join("");
  };

  function renderSelectedArchiveDay() {
    const day = archive.days.find(item => item.date === archive.selectedDate);
    if (!day) {
      $("scheduleView").innerHTML = `<div class="empty"><strong>Расписание не найдено</strong>В базе нет выбранной даты.</div>`;
      updateArchiveControls();
      buildDayStrip();
      return;
    }
    state.dayOffset = dayDifference(day.date);
    renderSchedule({group: state.group, days: [day]});
    updateArchiveControls();
  }

  async function loadArchiveForGroup(quiet = false) {
    if (!state.group) {
      archive.group = null;
      archive.days = [];
      archive.selectedDate = null;
      updateArchiveControls();
      buildDayStrip();
      $("scheduleView").innerHTML = `<div class="empty"><strong>Группа не выбрана</strong>Выберите её один раз — после этого будут доступны все даты, реально сохранённые в базе.</div>`;
      return;
    }

    if (!quiet) $("scheduleView").innerHTML = `<div class="loading-card">Загружаю все доступные даты…</div>`;
    const data = await api(`/api/schedule?group=${encodeURIComponent(state.group)}`);
    archive.group = state.group;
    archive.days = (data.days || []).slice().sort((a, b) => a.date.localeCompare(b.date));
    if (!archive.days.some(day => day.date === archive.selectedDate)) {
      archive.selectedDate = chooseNearestAvailableDate(archive.days);
    }
    renderSelectedArchiveDay();
  }

  async function selectArchiveDate(dateString, userInitiated = false) {
    if (!archive.days.some(day => day.date === dateString)) return;
    archive.selectedDate = dateString;
    renderSelectedArchiveDay();
    if (userInitiated) {
      haptic();
      track("schedule_date_change");
      track("schedule_view");
    }
  }

  const originalLoadSchedule = loadSchedule;
  loadSchedule = async function archiveAwareLoadSchedule(offset = state.dayOffset, quiet = false) {
    try {
      if (archive.group !== state.group || !archive.days.length) {
        await loadArchiveForGroup(quiet);
        return;
      }

      const requested = new Date();
      requested.setDate(requested.getDate() + Number(offset || 0));
      const requestedDate = localDateString(requested);
      const exact = archive.days.find(day => day.date === requestedDate);
      if (exact) archive.selectedDate = exact.date;
      renderSelectedArchiveDay();
    } catch (error) {
      $("scheduleView").innerHTML = `<div class="error-card"><strong>Не удалось загрузить расписание</strong>${esc(error.message)}</div>`;
    }
  };

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
              <div><div class="analytics-name">${esc(featureNames[item.feature] || item.feature)}</div><div class="analytics-meta">${item.unique_users} уник. пользователей${item.last_used_at ? ` · последнее ${esc(new Date(item.last_used_at).toLocaleString("ru-RU"))}` : ""}</div></div>
              <div class="analytics-count">${item.uses}</div>
            </div>
          `).join("") : `<div class="list-row-sub">Событий использования пока нет.</div>`}
        </div>
        <div class="section-label">Ни разу не использовались</div>
        <div class="never-list">${never.length ? never.map(name => `<span class="never-chip">${esc(featureNames[name] || name)}</span>`).join("") : `<span class="list-row-sub">Все отслеживаемые функции уже использовались.</span>`}</div>
      </div>
    `;
  }

  installArchiveControls();
  installAdminAnalytics();
  buildDayStrip();
  track("miniapp_open");
})();
