/* Порт бэкенда (app.py + bot/top_api.py) на JavaScript.
   Работает прямо в приложении, без Python-сервера.
   HTTP-запросы идут через нативный слой Capacitor (CapacitorHttp), поэтому CORS не мешает. */
(function () {
  const API_BASE = "https://msapi.top-academy.ru/api/v2";
  const APPLICATION_KEY = "6a56a5df2667e65aab73ce76d1dd737f7d1faef9c52e8b8c55ac75f565d8e8a6";
  const SESSION_KEY = "student_hub_session_v1";
  const CACHE_TTL_MS = 120 * 1000;

  class HttpError extends Error {
    constructor(status, message) { super(message); this.status = status; }
  }
  class TopAuthError extends Error {}

  // ---------- утилиты ----------
  const CACHE = new Map();
  const cacheGet = (key, force) => {
    if (force) return null;
    const e = CACHE.get(key);
    if (!e) return null;
    if (Date.now() - e.ts > CACHE_TTL_MS) { CACHE.delete(key); return null; }
    return e.value;
  };
  const cacheSet = (key, value) => { CACHE.set(key, { ts: Date.now(), value }); return value; };

  const pad = (n) => String(n).padStart(2, "0");
  const toIso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const parseIso = (s) => { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d, 12); };

  function field(obj, ...names) {
    if (!obj || typeof obj !== "object" || Array.isArray(obj)) return undefined;
    for (const n of names) {
      const v = obj[n];
      if (v !== undefined && v !== null && v !== "") return v;
    }
    return undefined;
  }

  const _ta = document.createElement("textarea");
  function unescapeHtml(s) { _ta.innerHTML = s; return _ta.value; }

  function displayValue(value) {
    if (value === undefined || value === null || value === "") return "";
    if (Array.isArray(value)) return value.map(displayValue).filter(Boolean).join(", ");
    if (typeof value === "object") {
      return displayValue(field(value, "name", "title", "fio", "fio_teach", "full_name",
        "teacher_name", "room_name", "auditory_name", "text_comment", "comment", "value") ?? "");
    }
    return unescapeHtml(String(value)).replace(/\\n/g, "\n").replace(/\\r/g, "").split(/\s+/).filter(Boolean).join(" ");
  }

  function extractList(data, ...keys) {
    if (Array.isArray(data)) return data;
    if (!data || typeof data !== "object") return [];
    const preferred = [...keys, "homework_list", "lesson_list", "lessons", "visits", "items", "list", "result", "data"];
    const checked = new Set();
    for (const key of preferred) {
      if (checked.has(key)) continue;
      checked.add(key);
      const v = data[key];
      if (Array.isArray(v)) return v;
      if (v && typeof v === "object") {
        const nested = extractList(v, ...keys);
        if (nested.length) return nested;
      }
    }
    return [];
  }

  function timeText(value) {
    if (value === undefined || value === null || value === "") return "";
    let text = String(value).trim();
    if (text.includes("T")) text = text.split("T")[1];
    const m = text.match(/(\d{1,2}:\d{2})/);
    return m ? m[1].padStart(5, "0") : text.slice(0, 5);
  }

  function dateValue(value) {
    if (!value) return null;
    const s = String(value).trim().slice(0, 10);
    return /^\d{4}-\d{2}-\d{2}$/.test(s) ? s : null;
  }

  function parseDeadline(value) {
    if (!value) return null;
    const raw = String(value).trim();
    if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) {
      const [y, m, d] = raw.split("-").map(Number);
      return new Date(y, m - 1, d, 23, 59, 59);
    }
    const hasTz = /(Z|[+-]\d{2}:?\d{2})$/.test(raw);
    const dt = new Date(raw.replace(" ", "T"));
    if (isNaN(dt)) {
      const d = dateValue(raw);
      if (!d) return null;
      const [y, m, dd] = d.split("-").map(Number);
      return new Date(y, m - 1, dd, 23, 59, 59);
    }
    if (!hasTz && dt.getHours() === 0 && dt.getMinutes() === 0 && dt.getSeconds() === 0) dt.setHours(23, 59, 59);
    return dt;
  }

  const START_KEYS = ["started_at", "time_start", "start_time", "begin_time", "start"];
  const lessonSortKey = (l) => timeText(field(l, ...START_KEYS) ?? "99:99") || "99:99";
  const lessonDate = (l) => dateValue(field(l, "date", "date_lesson", "day", "lesson_date"));

  // ---------- сессия ----------
  function loadSession() {
    try { return JSON.parse(localStorage.getItem(SESSION_KEY) || "null"); } catch (_) { return null; }
  }
  function saveSession(s) {
    try { localStorage.setItem(SESSION_KEY, JSON.stringify(s)); } catch (_) {}
  }
  function clearSession() {
    localStorage.removeItem(SESSION_KEY);
    CACHE.clear();
  }
  function requireSession() {
    const s = loadSession();
    if (!s || !s.token) throw new HttpError(401, "Войдите в аккаунт Top Academy");
    return s;
  }

  // ---------- HTTP к Top Academy ----------
  async function getAccessToken(username, password) {
    let r;
    try {
      r = await fetch(`${API_BASE}/auth/login`, {
        method: "POST",
        headers: {
          accept: "application/json, text/plain, */*",
          "accept-language": "ru_RU, ru",
          "content-type": "application/json",
          origin: "https://journal.top-academy.ru",
          referer: "https://journal.top-academy.ru/",
        },
        body: JSON.stringify({ application_key: APPLICATION_KEY, id_city: null, password, username }),
      });
    } catch (_) {
      throw new HttpError(502, "Не удалось связаться с сервером Top Academy. Проверьте интернет.");
    }
    if (!r.ok) {
      if ([400, 401, 403].includes(r.status)) throw new HttpError(401, "Вход был отклонён. Проверьте введённые логин и пароль.");
      throw new HttpError(502, `Сервер Top Academy вернул ошибку HTTP ${r.status}.`);
    }
    let data;
    try { data = await r.json(); } catch (_) { throw new HttpError(502, "Top Academy вернул ответ в неожиданном формате."); }
    const find = (obj, depth = 0) => {
      if (depth > 4 || !obj || typeof obj !== "object") return null;
      for (const k of ["access_token", "token"]) {
        if (typeof obj[k] === "string" && obj[k].trim()) return obj[k].trim();
      }
      for (const k of ["data", "result", "auth", "payload"]) {
        const f = find(obj[k], depth + 1);
        if (f) return f;
      }
      return null;
    };
    const token = find(data);
    if (!token) throw new HttpError(502, "Нет токена авторизации.");
    return token;
  }

  async function apiGet(token, path, params = {}) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined) qs.append(k, v);
    const url = `${API_BASE}${path}${qs.toString() ? "?" + qs : ""}`;
    let r;
    try {
      r = await fetch(url, {
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
          Accept: "application/json, text/plain, */*",
          "Accept-Language": "ru_RU, ru",
          Origin: "https://journal.top-academy.ru",
          Referer: "https://journal.top-academy.ru/",
        },
      });
    } catch (_) { return null; }
    if (r.status === 401 || r.status === 403) throw new TopAuthError("Сессия Top Academy истекла. Пожалуйста, войдите снова.");
    if (!r.ok) return null;
    try { return await r.json(); } catch (_) { return null; }
  }

  // ---------- профиль ----------
  const getUserInfo = async (token) => (await apiGet(token, "/settings/user-info")) || {};

  function getCurrentGroupId(info) {
    const ui = info || {};
    let gid = field(ui, "current_group_id", "group_id", "id_group");
    if (gid !== undefined) return gid;
    const cg = ui.current_group;
    if (cg && typeof cg === "object") {
      gid = field(cg, "id", "group_id", "id_group");
      if (gid !== undefined) return gid;
    }
    if (Array.isArray(ui.groups) && ui.groups.length) return field(ui.groups[0], "id", "group_id") ?? null;
    return null;
  }

  function profileData(info) {
    const group = info && typeof info.current_group === "object" && info.current_group ? info.current_group : {};
    const val = (...keys) => displayValue(field(group, ...keys) ?? field(info, ...keys));
    return {
      name: val("full_name", "fio", "student_name", "name") || "Студент Top Academy",
      group: val("name", "group_name", "title") || displayValue(field(info, "group_name", "current_group_name")),
      stream: val("stream_name", "stream"),
      level: val("level", "level_name"),
    };
  }

  // ---------- расписание ----------
  const lessonsOf = (data) => extractList(data, "lessons", "lesson_list", "schedule", "data", "items", "days", "result", "days_lessons", "lessons_list");

  async function lessonsForDay(token, iso) {
    const data = await apiGet(token, "/schedule/operations/get-by-date", { date_filter: iso });
    if (data === null) return null;
    return lessonsOf(data).sort((a, b) => lessonSortKey(a).localeCompare(lessonSortKey(b)));
  }

  async function lessonsForWeek(token, iso) {
    const target = parseIso(iso);
    const start = new Date(target); start.setDate(target.getDate() - ((target.getDay() + 6) % 7));
    const end = new Date(start); end.setDate(start.getDate() + 6);
    const sIso = toIso(start), eIso = toIso(end);

    const data = await apiGet(token, "/schedule/operations/get-month", { date_filter: sIso });
    if (data === null) return null;
    let lessons = lessonsOf(data);
    if (end.getMonth() !== start.getMonth()) {
      const next = await apiGet(token, "/schedule/operations/get-month", { date_filter: eIso });
      if (next) lessons = lessons.concat(lessonsOf(next));
    }
    const seen = new Set(), result = [];
    for (const l of lessons) {
      const d = lessonDate(l);
      if (!d || d < sIso || d > eIso) continue;
      const subj = displayValue(field(l, "spec_name", "subject_name", "name_spec", "subject"));
      const key = `${d}|${lessonSortKey(l)}|${subj}`;
      if (seen.has(key)) continue;
      seen.add(key);
      result.push(l);
    }
    return result.sort((a, b) => (lessonDate(a) || "9999").localeCompare(lessonDate(b) || "9999") || lessonSortKey(a).localeCompare(lessonSortKey(b)));
  }

  function formatLesson(item, iso, now, idx) {
    const get = (...k) => displayValue(field(item, ...k));
    const start = timeText(field(item, ...START_KEYS));
    const end = timeText(field(item, "finished_at", "time_end", "end_time", "finish_time", "end"));
    let state = "upcoming";
    if (/^\d{2}:\d{2}$/.test(start) && /^\d{2}:\d{2}$/.test(end)) {
      const [y, m, d] = iso.split("-").map(Number);
      const [sh, sm] = start.split(":").map(Number);
      const [eh, em] = end.split(":").map(Number);
      const sd = new Date(y, m - 1, d, sh, sm);
      let ed = new Date(y, m - 1, d, eh, em);
      if (ed <= sd) ed.setDate(ed.getDate() + 1);
      if (now >= ed) state = "past";
      else if (now >= sd) state = "current";
    }
    return {
      id: String(field(item, "id", "lesson_id") ?? `${iso}-${idx}`),
      date: iso,
      subject: get("spec_name", "subject_name", "name_spec", "subject", "name", "discipline_name") || "Предмет не указан",
      start, end,
      teacher: get("fio_teach", "teacher_name", "teacher_fio", "teacher", "teachers"),
      room: get("auditory_name", "room_name", "auditory", "room", "cabinet"),
      state,
      state_label: { past: "Прошла", current: "Сейчас идёт", upcoming: "Предстоит" }[state],
    };
  }

  // ---------- домашние задания ----------
  async function allHomework(token, groupId, maxPages = 3) {
    const collected = [], seen = new Set();
    const fetchPages = async (status, maxP) => {
      for (let page = 1; page <= maxP; page++) {
        const data = await apiGet(token, "/homework/operations/list", { page, status, type: null, group_id: groupId });
        if (!data) break;
        const items = extractList(data, "homework_list", "homework", "items", "data", "tasks");
        if (!items.length) break;
        let added = 0;
        for (const item of items) {
          if (!item || typeof item !== "object") continue;
          if (item.id !== undefined && item.id !== null) {
            if (seen.has(item.id)) continue;
            seen.add(item.id);
          }
          if (status !== null && (item.status === undefined || item.status === null)) item._queried_status = status;
          collected.push(item);
          added++;
        }
        if (!added) break;
      }
    };
    for (const st of [3, 2, 0, 1]) await fetchPages(st, maxPages);
    if (!collected.length) await fetchPages(null, 6);
    return collected;
  }

  function parseHomework(h, index) {
    const get = (...k) => displayValue(field(h, ...k));
    const rawStatus = field(h, "status", "common_status", "_queried_status");
    const statusText = get("status_name", "status_text", "status");
    const statusValue = String(rawStatus !== undefined ? rawStatus : (statusText || "")).trim().toLowerCase();
    const due = get("completion_time", "overdue_time");
    const created = get("creation_time", "created_at");
    const hwStud = h.homework_stud && typeof h.homework_stud === "object" ? h.homework_stud : {};
    let mark = field(hwStud, "mark", "grade", "auto_mark") ?? field(h, "mark", "grade");
    if (mark !== undefined && mark !== null) mark = String(mark).trim(); else mark = null;
    const hasStud = Object.keys(hwStud).length > 0;
    const sid = rawStatus !== undefined ? parseInt(rawStatus, 10) : NaN;
    const statusId = isNaN(sid) ? null : sid;
    const has = (...xs) => xs.some((x) => statusValue.includes(x));

    let state, label;
    if (statusId === 5 || has("удален", "удалён", "deleted")) { state = "deleted"; label = "Удалено"; }
    else if (statusId === 1 || (mark && /^\d+$/.test(mark)) || has("выполн", "провер", "checked", "done")) { state = "done"; label = "Проверено"; }
    else if (statusId === 2 || (hasStud && !mark) || has("на проверке", "review", "uploaded")) { state = "review"; label = "На проверке"; }
    else if (statusId === 0 || has("просроч", "overdue", "late")) { state = "overdue"; label = "Просрочено"; }
    else {
      const dl = due ? parseDeadline(due) : null;
      if (dl && dl < new Date()) { state = "overdue"; label = "Просрочено"; }
      else { state = "pending"; label = "Активно"; }
    }

    const co = h.homework_comment;
    let teacherComment = "";
    if (co && typeof co === "object") teacherComment = displayValue(field(co, "text_comment", "comment", "message"));
    else if (typeof co === "string") teacherComment = displayValue(co);

    const fileUrl = field(h, "file_path", "file_url", "download_url");
    const studFile = field(hwStud, "file_path", "file_url");
    const isUrl = (u) => typeof u === "string" && u.startsWith("http");
    return {
      id: String(field(h, "id") ?? `hw-${index}`),
      subject: get("name_spec", "spec_name", "subject_name") || "Предмет не указан",
      title: get("theme", "name", "title") || "Домашнее задание",
      description: get("comment", "description", "task_text"),
      due, created,
      teacher: get("fio_teach", "teacher_name"),
      status: label, state,
      mark: mark || null,
      teacher_comment: teacherComment,
      file_url: isUrl(fileUrl) ? fileUrl : null,
      stud_file_url: isUrl(studFile) ? studFile : null,
    };
  }

  // ---------- оценки и посещаемость ----------
  async function buildGrades(token, hwItems) {
    const data = await apiGet(token, "/progress/operations/student-visits");
    const visits = extractList(data, "visits", "student_visits", "items", "data");
    const subjects = {}, grades = [], seen = new Set();
    const markFields = [
      ["class_work_mark", "Классная работа"], ["control_work_mark", "Контрольная работа"],
      ["home_work_mark", "Домашняя работа"], ["lab_work_mark", "Лабораторная работа"],
      ["practical_work_mark", "Практическая работа"], ["mark", "Оценка за занятие"],
    ];
    visits.forEach((v, idx) => {
      if (!v || typeof v !== "object") return;
      const subj = displayValue(field(v, "spec_name", "subject_name", "name_spec") ?? "Предмет");
      const visitDate = String(field(v, "date_visit", "date", "lesson_date") ?? "").slice(0, 10);
      const teacher = displayValue(field(v, "teacher_name", "fio_teach", "teacher"));
      const theme = displayValue(field(v, "lesson_theme", "theme", "topic"));
      const e = (subjects[subj] ||= { subject: subj, total: 0, visited: 0, absent: 0 });
      e.total++;
      if ([1, 2, true, "1", "2"].includes(v.status_was)) e.visited++; else e.absent++;
      for (const [key, label] of markFields) {
        const raw = v[key];
        if (raw === undefined || raw === null || ["", "0", "None"].includes(String(raw).trim())) continue;
        const val = parseInt(parseFloat(String(raw).trim()), 10);
        if (isNaN(val) || val <= 0) continue;
        const gid = `visit-${v.lesson_id ?? idx}-${key}-${visitDate}`;
        if (seen.has(gid)) continue;
        seen.add(gid);
        grades.push({ id: gid, subject: subj, value: val, type: label, theme: theme || label, teacher, date: visitDate });
      }
    });
    for (const hw of hwItems || []) {
      if (!hw.mark) continue;
      const val = parseInt(parseFloat(String(hw.mark).trim()), 10);
      if (isNaN(val) || val <= 0) continue;
      const gid = `hw-${hw.id}`;
      if (seen.has(gid)) continue;
      seen.add(gid);
      grades.push({ id: gid, subject: hw.subject || "Предмет", value: val, type: "Домашнее задание",
        theme: hw.title || "ДЗ", teacher: hw.teacher || "", date: String(hw.due || hw.created || "").slice(0, 10) });
    }
    grades.sort((a, b) => (b.date || "0000-00-00").localeCompare(a.date || "0000-00-00"));

    const attendance = [];
    let totalL = 0, totalV = 0;
    for (const subj of Object.keys(subjects).sort()) {
      const s = subjects[subj];
      totalL += s.total; totalV += s.visited;
      attendance.push({ ...s, percent: s.total ? Math.round((s.visited / s.total) * 1000) / 10 : 0 });
    }
    const avg = grades.length ? Math.round((grades.reduce((a, g) => a + g.value, 0) / grades.length) * 100) / 100 : null;
    return {
      available: true,
      summary: {
        average_grade: avg, total_grades: grades.length,
        overall_attendance: totalL ? Math.round((totalV / totalL) * 1000) / 10 : null,
        total_lessons: totalL, visited_lessons: totalV,
      },
      items: grades, attendance,
    };
  }

  // ---------- «маршруты» (то, что раньше делал FastAPI) ----------
  async function guarded(fn) {
    try { return await fn(); }
    catch (e) {
      if (e instanceof TopAuthError) { clearSession(); throw new HttpError(401, e.message); }
      throw e;
    }
  }

  async function handle(url, options = {}) {
    const u = new URL(url, "http://local");
    const path = u.pathname;
    const q = u.searchParams;
    const force = q.get("force") === "true";
    const method = (options.method || "GET").toUpperCase();

    if (path === "/api/login" && method === "POST") {
      const { username, password } = JSON.parse(options.body || "{}");
      if (!username || !password) throw new HttpError(400, "Введите логин и пароль.");
      const token = await getAccessToken(username.trim(), password);
      let info = {};
      try { info = (await getUserInfo(token)) || {}; } catch (_) {}
      let groupId = null;
      try { groupId = getCurrentGroupId(info); } catch (_) {}
      CACHE.clear();
      saveSession({ token, info, groupId, username: username.trim() });
      return { ok: true, profile: profileData(info) };
    }

    if (path === "/api/logout") { clearSession(); return { ok: true }; }

    const s = requireSession();

    if (path === "/api/me") return { profile: profileData(s.info) };

    if (path === "/api/schedule") {
      const mode = q.get("mode") || "day";
      const iso = q.get("day") || toIso(new Date());
      const key = `schedule:${mode}:${iso}`;
      const cached = cacheGet(key, force);
      if (cached) return cached;
      const raw = await guarded(() => (mode === "week" ? lessonsForWeek(s.token, iso) : lessonsForDay(s.token, iso)));
      if (raw === null) throw new HttpError(502, "Не удалось получить расписание Top Academy");
      const now = new Date();
      const lessons = raw.map((item, idx) => formatLesson(item, lessonDate(item) || iso, now, idx));
      return cacheSet(key, { date: iso, mode, local_now: now.toISOString(), lessons });
    }

    if (path === "/api/homework") {
      const cached = cacheGet("homework", force);
      if (cached) return cached;
      if (!s.groupId) {
        s.groupId = getCurrentGroupId(s.info);
        saveSession(s);
      }
      if (!s.groupId) return { items: [], warning: "Не удалось определить учебную группу" };
      const raw = await guarded(() => allHomework(s.token, s.groupId));
      const items = raw.map((h, i) => parseHomework(h, i));
      const order = { pending: 0, overdue: 1, review: 2, done: 3, deleted: 4 };
      items.sort((a, b) => (order[a.state] ?? 0) - (order[b.state] ?? 0));
      return cacheSet("homework", { items });
    }

    if (path === "/api/grades") {
      const cached = cacheGet("grades", force);
      if (cached) return cached;
      const hw = cacheGet("homework", force);
      const hwItems = hw && hw.items ? hw.items : [];
      return cacheSet("grades", await guarded(() => buildGrades(s.token, hwItems)));
    }

    throw new HttpError(404, "Not found");
  }

  window.LocalApi = { handle, HttpError };
})();
