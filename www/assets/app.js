const state = {
  tab: "schedule",
  scheduleMode: "day",
  currentDate: new Date(),
  hwFilter: "all",
  homeworkItems: [],
  gradesData: null,
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

function escapeHtml(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function showToast(msg) {
  const t = $("#toast");
  if (!t) return;
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(showToast._timer);
  showToast._timer = setTimeout(() => t.classList.remove("show"), 2800);
}

function toIsoDate(d) {
  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function formatHumanDate(isoStr) {
  if (!isoStr) return "";
  const d = new Date(isoStr + "T00:00:00");
  return d.toLocaleDateString("ru-RU", {
    weekday: "short",
    day: "numeric",
    month: "long",
  });
}

async function apiFetch(url, options = {}) {
  try {
    return await LocalApi.handle(url, options);
  } catch (err) {
    if (err.status === 401) {
      showLoginScreen();
      throw new Error(err.message || "Требуется авторизация");
    }
    throw new Error(err.message || "Ошибка");
  }
}

// Тема и настройки

function initTheme() {
  const saved = localStorage.getItem("student_hub_theme") || "dark";
  const isLight = saved === "light";
  document.body.classList.toggle("light", isLight);
  const toggle = $("#theme-toggle");
  if (toggle) toggle.checked = isLight;
}

function toggleTheme() {
  const isLight = !document.body.classList.contains("light");
  document.body.classList.toggle("light", isLight);
  localStorage.setItem("student_hub_theme", isLight ? "light" : "dark");
  const toggle = $("#theme-toggle");
  if (toggle) toggle.checked = isLight;
}

// Переключение: Экран входа <-> Оболочка журнала

function showLoginScreen() {
  $("#login-screen").hidden = false;
  $("#app-shell").hidden = true;
}

function showWorkspace(profile) {
  $("#login-screen").hidden = true;
  $("#app-shell").hidden = false;
  renderProfile(profile);
}

function renderProfile(profile = {}) {
  const name = profile.name || "Студент";
  const group = profile.group ? `Группа: ${profile.group}` : "";
  const stream = [
    profile.stream,
    profile.level ? `Курс/Уровень: ${profile.level}` : "",
  ]
    .filter(Boolean)
    .join(" • ");

  $("#profile-name").textContent = name;
  $("#profile-group").textContent = group;
  $("#profile-stream").textContent = stream;
  $("#profile-avatar").textContent = name.charAt(0).toUpperCase();
  $("#header-user").textContent = profile.group
    ? `${name} (${profile.group})`
    : name;
}

// Расписание

function updateDateTitle() {
  const todayIso = toIsoDate(new Date());
  const selectedIso = toIsoDate(state.currentDate);
  if (state.scheduleMode === "day") {
    $("#date-title").textContent =
      selectedIso === todayIso ? "Сегодня" : formatHumanDate(selectedIso);
  } else {
    const d = new Date(state.currentDate);
    const day = d.getDay() || 7;
    const start = new Date(d);
    start.setDate(d.getDate() - day + 1);
    const end = new Date(start);
    end.setDate(start.getDate() + 6);
    $("#date-title").textContent =
      `${start.toLocaleDateString("ru-RU", { day: "numeric", month: "short" })} — ${end.toLocaleDateString("ru-RU", { day: "numeric", month: "short" })}`;
  }
}

function renderLessonCard(l) {
  return `
    <article class="lesson lesson-${escapeHtml(l.state)}">
      <div>
        <div class="time">${escapeHtml(l.start || "--:--")}</div>
        <div class="endtime">${escapeHtml(l.end || "--:--")}</div>
      </div>
      <div class="timeline"></div>
      <div>
        <div class="subject">${escapeHtml(l.subject)}</div>
        <div class="detail">
          ${escapeHtml(l.room ? `Ауд. ${l.room}` : "Аудитория не указана")}
          ${l.teacher ? ` • ${escapeHtml(l.teacher)}` : ""}
        </div>
      </div>
      <span class="tag lesson-state">${escapeHtml(l.state_label)}</span>
    </article>
  `;
}

async function loadSchedule(force = false) {
  updateDateTitle();
  const container = $("#lessons");
  container.innerHTML = `<div class="empty">Загрузка расписания...</div>`;

  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Irkutsk";
  const dayIso = toIsoDate(state.currentDate);
  try {
    const data = await apiFetch(
      `/api/schedule?day=${dayIso}&mode=${state.scheduleMode}&timezone=${encodeURIComponent(tz)}&force=${force}`,
    );
    const lessons = data.lessons || [];
    if (!lessons.length) {
      container.innerHTML = `<div class="empty">В выбранный период пар нет — можно отдохнуть!</div>`;
      return;
    }

    if (state.scheduleMode === "day") {
      container.innerHTML = lessons.map(renderLessonCard).join("");
    } else {
      const grouped = {};
      for (const l of lessons) {
        (grouped[l.date] = grouped[l.date] || []).push(l);
      }
      container.innerHTML = Object.entries(grouped)
        .map(
          ([dt, items]) => `
          <div class="day-group-header">${escapeHtml(formatHumanDate(dt))}</div>
          <div class="stack">${items.map(renderLessonCard).join("")}</div>
        `,
        )
        .join("");
    }
  } catch (err) {
    container.innerHTML = `<div class="empty">${escapeHtml(err.message)}</div>`;
  }
}

// ДЗ

function renderHomework() {
  const container = $("#homework-list");
  const filtered = state.homeworkItems.filter((item) => {
    if (state.hwFilter === "all") return item.state !== "deleted";
    return item.state === state.hwFilter;
  });

  const activeCount = state.homeworkItems.filter(
    (i) => i.state === "pending" || i.state === "overdue",
  ).length;
  const badge = $("#hw-badge");
  if (badge) {
    badge.hidden = activeCount === 0;
    badge.textContent = activeCount;
  }

  if (!filtered.length) {
    container.innerHTML = `<div class="empty">Нет заданий в выбранной категории.</div>`;
    return;
  }

  container.innerHTML = filtered
    .map((h) => {
      const markBadge = h.mark
        ? `<span class="tag">Оценка: <b>${escapeHtml(h.mark)}</b></span>`
        : "";
      const fileLink = h.file_url
        ? `<a class="hw-link" href="${escapeHtml(h.file_url)}" target="_blank" rel="noopener">⬇ Скачать задание</a>`
        : "";
      return `
        <article class="hw hw-${escapeHtml(h.state)}">
          <div>
            <div class="hw-top">
              <h3>${escapeHtml(h.subject)}</h3>
              <span class="tag status-tag">${escapeHtml(h.status)}</span>
            </div>
            <div class="hw-title">${escapeHtml(h.title)}</div>
            ${h.description ? `<p class="hw-desc">${escapeHtml(h.description)}</p>` : ""}
            ${h.teacher_comment ? `<p class="hw-desc">💬 Преподаватель: ${escapeHtml(h.teacher_comment)}</p>` : ""}
          </div>
          <div class="hw-meta">
            <span>${h.due ? `Срок: ${escapeHtml(h.due.slice(0, 10))}` : "Без дедлайна"} ${h.teacher ? `• ${escapeHtml(h.teacher)}` : ""}</span>
            <div>${markBadge} ${fileLink}</div>
          </div>
        </article>
      `;
    })
    .join("");
}

async function loadHomework(force = false) {
  const container = $("#homework-list");
  if (!state.homeworkItems.length || force) {
    container.innerHTML = `<div class="empty">Загрузка домашних заданий...</div>`;
  }
  try {
    const data = await apiFetch(`/api/homework?force=${force}`);
    state.homeworkItems = data.items || [];
    renderHomework();
  } catch (err) {
    container.innerHTML = `<div class="empty">${escapeHtml(err.message)}</div>`;
  }
}

// Оценки и посещаемость

async function loadGrades(force = false) {
  const summaryEl = $("#grades-summary");
  const attEl = $("#attendance-list");
  const gradesEl = $("#grades-list");

  if (!state.gradesData || force) {
    gradesEl.innerHTML = `<div class="empty">Загрузка оценок и посещаемости...</div>`;
  }

  try {
    const data = await apiFetch(`/api/grades?force=${force}`);
    state.gradesData = data;
    const s = data.summary || {};

    summaryEl.innerHTML = `
      <div class="stat-card">
        <div class="muted tiny">СРЕДНИЙ БАЛЛ</div>
        <div class="stat-val">${s.average_grade ?? "—"}</div>
      </div>
      <div class="stat-card">
        <div class="muted tiny">ПОСЕЩАЕМОСТЬ</div>
        <div class="stat-val">${s.overall_attendance != null ? `${s.overall_attendance}%` : "—"}</div>
      </div>
      <div class="stat-card">
        <div class="muted tiny">ВСЕГО ОЦЕНОК</div>
        <div class="stat-val">${s.total_grades ?? 0}</div>
      </div>
      <div class="stat-card">
        <div class="muted tiny">ПОСЕЩЕНО ЗАНЯТИЙ</div>
        <div class="stat-val">${s.visited_lessons ?? 0} / ${s.total_lessons ?? 0}</div>
      </div>
    `;

    const att = data.attendance || [];
    attEl.innerHTML = att.length
      ? att
          .map(
            (a) => `
          <div class="att-row">
            <div class="att-head">
              <span>${escapeHtml(a.subject)}</span>
              <span>${a.visited}/${a.total} (${a.percent}%)</span>
            </div>
            <div class="progress-track">
              <div class="progress-fill" style="width:${Math.min(100, a.percent)}%"></div>
            </div>
          </div>
        `,
          )
          .join("")
      : `<div class="empty">Данные о посещаемости пока отсутствуют.</div>`;

    const items = data.items || [];
    gradesEl.innerHTML = items.length
      ? items
          .map(
            (g) => `
          <div class="grade-item">
            <div>
              <div class="subject">${escapeHtml(g.subject)}</div>
              <div class="detail">${escapeHtml(g.type)} ${g.theme && g.theme !== g.type ? `• ${escapeHtml(g.theme)}` : ""}</div>
              <div class="detail">${escapeHtml(g.date || "")} ${g.teacher ? `• ${escapeHtml(g.teacher)}` : ""}</div>
            </div>
            <div class="grade-badge ${Number(g.value) < 3 ? "low" : ""}">${escapeHtml(g.value)}</div>
          </div>
        `,
          )
          .join("")
      : `<div class="empty">Оценок пока нет.</div>`;
  } catch (err) {
    gradesEl.innerHTML = `<div class="empty">${escapeHtml(err.message)}</div>`;
  }
}


// Навигация и события:

function switchTab(tab) {
  state.tab = tab;
  const titles = {
    schedule: "Расписание",
    homework: "Домашние задания",
    grades: "Оценки и посещаемость",
    profile: "Профиль",
  };
  $("#page-title").textContent = titles[tab] || "Журнал";

  $$("#nav button").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === tab);
  });

  ["schedule", "homework", "grades", "profile"].forEach((name) => {
    const page = $(`#${name}-page`);
    if (page) page.hidden = name !== tab;
  });

  if (tab === "schedule") loadSchedule(false);
  if (tab === "homework") loadHomework(false);
  if (tab === "grades") loadGrades(false);
}

document.addEventListener("DOMContentLoaded", async () => {
  initTheme();
  showLoginScreen(); // По умолчанию всегда сначала экран входа

  $("#theme")?.addEventListener("click", toggleTheme);
  $("#theme-login")?.addEventListener("click", toggleTheme);
  $("#theme-toggle")?.addEventListener("change", toggleTheme);

  // Вход в аккаунт
  $("#login-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.currentTarget;
    const submitBtn = $("#login-submit");
    const errEl = $("#login-error");
    const remember = $("#remember-me")?.checked ?? true;

    errEl.textContent = "";
    submitBtn.disabled = true;
    submitBtn.textContent = "Входим...";

    try {
      const payload = {
        username: form.username.value.trim(),
        password: form.password.value,
      };
      const res = await apiFetch("/api/login", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      if (remember) {
        localStorage.setItem("student_hub_autologin", "1");
      } else {
        localStorage.removeItem("student_hub_autologin");
      }
      form.reset();
      showWorkspace(res.profile);
      switchTab("schedule");
      loadHomework(true);
    } catch (err) {
      errEl.textContent = err.message;
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = "Войти в аккаунт";
    }
  });

  // Выход из аккаунта - возврат на экран входа
  $("#logout")?.addEventListener("click", async () => {
    localStorage.removeItem("student_hub_autologin");
    await LocalApi.handle("/api/logout", { method: "POST" });
    showLoginScreen();
    showToast("Вы вышли из аккаунта");
  });

  // Переключение вкладок меню
  $$("#nav button").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });

  // Кнопка Обновить
  $("#refresh")?.addEventListener("click", () => {
    showToast("Обновляем данные...");
    if (state.tab === "schedule") loadSchedule(true);
    else if (state.tab === "homework") loadHomework(true);
    else if (state.tab === "grades") loadGrades(true);
  });

  // Переключение День / Неделя
  $$("#schedule-mode button").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.scheduleMode = btn.dataset.mode;
      $$("#schedule-mode button").forEach((b) =>
        b.classList.toggle("active", b.dataset.mode === state.scheduleMode),
      );
      loadSchedule(false);
    });
  });

  // Навигация по датам
  $("#prev-day")?.addEventListener("click", () => {
    const step = state.scheduleMode === "week" ? 7 : 1;
    state.currentDate.setDate(state.currentDate.getDate() - step);
    loadSchedule(false);
  });

  $("#next-day")?.addEventListener("click", () => {
    const step = state.scheduleMode === "week" ? 7 : 1;
    state.currentDate.setDate(state.currentDate.getDate() + step);
    loadSchedule(false);
  });

  $("#today-btn")?.addEventListener("click", () => {
    state.currentDate = new Date();
    loadSchedule(false);
  });

  // Фильтры ДЗ
  $$("#hw-filters button").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.hwFilter = btn.dataset.filter;
      $$("#hw-filters button").forEach((b) =>
        b.classList.toggle("active", b.dataset.filter === state.hwFilter),
      );
      renderHomework();
    });
  });

  // Уведомления браузера
  $("#notify-toggle")?.addEventListener("change", async (e) => {
    if (e.target.checked && "Notification" in window) {
      const perm = await Notification.requestPermission();
      if (perm !== "granted") {
        e.target.checked = false;
        showToast("Разрешите уведомления в настройках браузера");
      } else {
        showToast("Уведомления включены");
      }
    }
  });

  // Авто-вход только если пользователь ранее сам вошёл и оставил галочку "Запомнить меня"
  if (localStorage.getItem("student_hub_autologin") === "1") {
    try {
      const me = await apiFetch("/api/me");
      showWorkspace(me.profile);
      loadSchedule(false);
      loadHomework(false);
    } catch (_) {
      showLoginScreen();
    }
  }
});
