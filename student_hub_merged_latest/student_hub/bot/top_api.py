import requests
import json
import re
from datetime import datetime, timedelta, date, time
from html import unescape


API_BASE = "https://msapi.top-academy.ru/api/v2"


# ============================================================
#                   АВТОРИЗАЦИЯ
# ============================================================

def get_access_token(username, password):
    url = f"{API_BASE}/auth/login"
    headers = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "ru_RU, ru",
        "content-type": "application/json",
        "origin": "https://journal.top-academy.ru",
        "referer": "https://journal.top-academy.ru/",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    payload = {
        "application_key": "6a56a5df2667e65aab73ce76d1dd737f7d1faef9c52e8b8c55ac75f565d8e8a6",
        "id_city": None,
        "password": password,
        "username": username
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=25)
    except requests.RequestException as exc:
        raise RuntimeError(f"Не удалось связаться с сервером Top Academy ({exc.__class__.__name__}).") from exc
    if r.status_code != 200:
        # Do not include response body: it can contain private account information.
        if r.status_code in (400, 401, 403):
            raise PermissionError("Top Academy отклонила вход. Проверьте логин и пароль, а также доступ к аккаунту.")
        raise RuntimeError(f"Сервер Top Academy вернул ошибку HTTP {r.status_code}.")
    try:
        data = r.json()
    except ValueError as exc:
        raise RuntimeError("Top Academy вернула ответ в неожиданном формате.") from exc

    # API responses may wrap the token in data/result/auth objects.
    def find_token(obj, depth=0):
        if depth > 4 or not isinstance(obj, dict):
            return None
        for key in ("token", "access_token"):
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for key in ("data", "result", "auth", "payload"):
            found = find_token(obj.get(key), depth + 1)
            if found:
                return found
        return None

    token = find_token(data)
    if not token:
        raise RuntimeError("В ответе Top Academy нет токена авторизации. Возможно, изменился API входа.")
    return token


def build_session(token):
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru_RU, ru",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/144.0.0.0 YaBrowser/26.3.0.0 Safari/537.36"
        ),
        "Origin": "https://journal.top-academy.ru",
        "Referer": "https://journal.top-academy.ru/"
    }
    s = requests.Session()
    s.headers.update(headers)
    return s


# ============================================================
#                   БАЗОВЫЙ GET
# ============================================================

LAST_API_ERROR = None


def api_get(session, path, params=None, quiet=False):
    """GET к API. Сохраняет понятную причину неудачного запроса."""
    global LAST_API_ERROR
    if not path.startswith("/"):
        path = "/" + path
    url = f"{API_BASE}{path}"
    clean_params = {k: v for k, v in (params or {}).items() if v is not None}
    try:
        response = session.get(url, params=clean_params, timeout=25)
    except requests.RequestException as exc:
        LAST_API_ERROR = f"Сетевая ошибка при запросе {path}: {exc.__class__.__name__}"
        if not quiet:
            print(f"[!] {LAST_API_ERROR}")
        return None

    if response.status_code != 200:
        LAST_API_ERROR = f"API {path}: HTTP {response.status_code}"
        if not quiet:
            print(f"[!] {LAST_API_ERROR}")
        return None

    LAST_API_ERROR = None
    try:
        return response.json()
    except ValueError:
        LAST_API_ERROR = f"API {path}: сервер вернул не JSON"
        if not quiet:
            print(f"[!] {LAST_API_ERROR}")
        return None


# ============================================================
#                   УТИЛИТЫ
# ============================================================

def field(obj, *names, default=None):
    if not isinstance(obj, dict):
        return default
    for name in names:
        value = obj.get(name)
        if value is not None and value != "":
            return value
    return default


def _display_value(value):
    """Убирает вложенные API-объекты и оставляет человекочитаемое значение."""
    if value is None or value == "":
        return ""
    if isinstance(value, dict):
        value = field(
            value, "name", "title", "fio", "fio_teach", "full_name",
            "teacher_name", "room_name", "auditory_name", "value", default=""
        )
        return _display_value(value)
    if isinstance(value, (list, tuple)):
        return ", ".join(x for x in (_display_value(v) for v in value) if x)
    text = unescape(str(value)).replace("\\n", "\n").replace("\\r", "")
    return " ".join(text.split())


def extract_list(data, *keys):
    """Извлекает список как из обычного массива, так и из обёрток API."""
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []

    preferred = list(keys) + [
        "homework_list", "lesson_list", "lessons", "items", "list", "result", "data"
    ]
    checked = set()
    for key in preferred:
        if key in checked:
            continue
        checked.add(key)
        value = data.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = extract_list(value, *keys)
            if nested:
                return nested
    return []


def _time_text(value):
    if value is None or value == "":
        return ""
    if isinstance(value, datetime):
        return value.strftime("%H:%M")
    text = str(value).strip()
    if "T" in text:
        text = text.split("T", 1)[1]
    match = re.search(r"(\d{1,2}:\d{2})", text)
    return match.group(1) if match else text[:5]


def _date_value(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _lesson_sort_key(lesson):
    return _time_text(field(
        lesson, "started_at", "time_start", "start_time", "begin_time", "start", default="99:99"
    )) or "99:99"


# ============================================================
#                   USER-INFO / ГРУППЫ
# ============================================================

def get_user_info(session):
    return api_get(session, "/settings/user-info")


def get_current_group_id(session):
    ui = get_user_info(session) or {}
    return field(ui, "current_group_id", "group_id", "id_group")


def get_user_groups(session):
    ui = get_user_info(session) or {}
    raw_groups = ui.get("groups") or []

    result = []
    for g in raw_groups:
        gid = field(g, "id", "group_id")
        if gid is None:
            continue
        result.append({
            "id": gid,
            "name": field(g, "name", "group_name", "title", default=""),
            "is_primary": bool(field(g, "is_primary", default=False)),
            "raw": g,
        })

    if result:
        return result

    data = api_get(session, "/group-specs", {"include_planned": 1}, quiet=True)
    if not data:
        return []

    items = extract_list(data, "groups", "specs", "data", "items", "result", "list")
    for g in items:
        gid = field(g, "id", "group_id", "spec_id")
        if gid is None:
            continue
        result.append({
            "id": gid,
            "name": field(g, "name", "group_name", "spec_name", "title", default=""),
            "is_primary": bool(field(g, "is_primary", default=False)),
            "raw": g,
        })
    return result


def debug_groups(session):
    ui = get_user_info(session) or {}
    specs = api_get(session, "/group-specs", {"include_planned": 1}, quiet=True)
    return {
        "user_info_keys": sorted(ui.keys()) if isinstance(ui, dict) else None,
        "has_groups_key": "groups" in ui if isinstance(ui, dict) else False,
        "groups_from_user_info": ui.get("groups") if isinstance(ui, dict) else None,
        "current_group_id": ui.get("current_group_id") if isinstance(ui, dict) else None,
        "group_name": ui.get("group_name") if isinstance(ui, dict) else None,
        "group_specs_type": type(specs).__name__ if specs is not None else None,
        "group_specs": specs,
    }


# ============================================================
#                   РАСПИСАНИЕ
# ============================================================

def get_schedule_month(session, any_date_in_month):
    d = any_date_in_month.isoformat() if hasattr(any_date_in_month, "isoformat") else str(any_date_in_month)
    return api_get(session, "/schedule/operations/get-month", {"date_filter": d})


def get_schedule_by_date(session, when):
    d = when.isoformat() if hasattr(when, "isoformat") else str(when)
    return api_get(session, "/schedule/operations/get-by-date", {"date_filter": d})


def _lessons_of(data):
    return extract_list(
        data,
        "lessons", "lesson_list", "schedule", "data", "items", "days", "result",
        "days_lessons", "lessons_list"
    )


def format_lesson(lesson, number=None):
    """Единый аккуратный блок занятия для Telegram."""
    subject = _display_value(field(
        lesson, "spec_name", "subject_name", "name_spec", "subject", "name",
        "discipline_name", "lesson_name", default="Предмет не указан"
    )) or "Предмет не указан"
    start_time = _time_text(field(
        lesson, "started_at", "time_start", "start_time", "begin_time", "start", default=""
    ))
    end_time = _time_text(field(
        lesson, "finished_at", "time_end", "end_time", "finish_time", "end", default=""
    ))
    time_text = f"{start_time or '??:??'}–{end_time or '??:??'}"

    teacher = _display_value(field(
        lesson, "fio_teach", "teacher_name", "teacher_fio", "prepod", "teacher", "teachers", default=""
    ))
    room = _display_value(field(
        lesson, "auditory_name", "room_name", "auditory", "room", "cabinet",
        "classroom", "auditory_number", default=""
    ))
    lesson_no = field(lesson, "lesson", "lesson_number", "number", "num", default=number)
    title = f"{lesson_no}. " if lesson_no not in (None, "") else ""

    return "\n".join([
        f"{title}📘 {subject}",
        f"   🕒 {time_text}",
        f"   👨‍🏫 {teacher or 'Преподаватель не указан'}",
        f"   🚪 {room or 'Кабинет не указан'}",
    ])


def lessons_for_day(session, when=None):
    when = when or date.today()
    data = get_schedule_by_date(session, when)
    if data is None:
        return None
    return sorted(_lessons_of(data), key=_lesson_sort_key)


def lessons_for_week(session, start=None):
    start = start or date.today()
    start = start - timedelta(days=start.weekday())
    end = start + timedelta(days=6)

    data = get_schedule_month(session, start)
    if data is None:
        return None
    lessons = _lessons_of(data)
    result = []
    for lesson in lessons:
        lesson_date = _date_value(field(lesson, "date", "date_lesson", "day", "lesson_date", default=""))
        if lesson_date and start <= lesson_date <= end:
            result.append(lesson)
    return sorted(result, key=lambda lesson: (
        _date_value(field(lesson, "date", "date_lesson", "day", "lesson_date", default="")) or date.max,
        _lesson_sort_key(lesson),
    ))


def find_next_lesson(session, days_ahead=14):
    today = date.today()
    data = get_schedule_month(session, today)
    if data is None:
        return None
    lessons = _lessons_of(data)
    if not lessons:
        return None

    now = datetime.now()
    limit = today + timedelta(days=days_ahead)
    upcoming = []
    for lesson in lessons:
        lesson_date = _date_value(field(lesson, "date", "date_lesson", "day", "lesson_date"))
        start_time = _time_text(field(
            lesson, "started_at", "time_start", "start_time", "begin_time", "start"
        ))
        if not lesson_date or not start_time:
            continue
        try:
            start_dt = datetime.combine(lesson_date, datetime.strptime(start_time, "%H:%M").time())
        except ValueError:
            continue
        if now <= start_dt <= datetime.combine(limit, time.max):
            upcoming.append((start_dt, lesson))

    return min(upcoming, key=lambda item: item[0]) if upcoming else None


# ============================================================
#                   ДОМАШКИ
# ============================================================

def get_homework_count(session, group_id, hw_type=None):
    return api_get(session, "/count/homework", {"type": hw_type, "group_id": group_id})


def get_homework_list(session, group_id, page=1, status=None, hw_type=None):
    """Список заданий: актуальный маршрут /homework/operations/list."""
    return api_get(session, "/homework/operations/list", {
        "page": page,
        "status": status,
        "type": hw_type,
        "group_id": group_id,
    })


def _homework_items(data):
    return extract_list(data, "homework_list", "homework", "items", "data", "tasks", "result", "list")


def all_homework(session, group_id, status=None, hw_type=None, max_pages=20):
    """Загружает страницы списка ДЗ. status=None означает все доступные статусы."""
    collected = []
    seen_ids = set()
    for page in range(1, max_pages + 1):
        data = get_homework_list(
            session, group_id, page=page, status=status, hw_type=hw_type
        )
        if data is None:
            if collected:
                # Некоторые версии API отвечают 404 на страницу за последней.
                LAST_API_ERROR = None
            break
        items = _homework_items(data)
        if not items:
            break

        added = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = item.get("id")
            if item_id is not None and item_id in seen_ids:
                continue
            if item_id is not None:
                seen_ids.add(item_id)
            collected.append(item)
            added += 1
        # Если сервер игнорирует page и возвращает ту же страницу, прекращаем цикл.
        if added == 0:
            break

    return collected


def _parse_deadline(value):
    """ДЗ обычно содержит только дату; в таком случае дедлайн — конец этого дня."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time(23, 59, 59))
    elif value:
        raw = str(value).strip()
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            parsed_date = _date_value(raw)
            if not parsed_date:
                return None
            parsed = datetime.combine(parsed_date, time(23, 59, 59))
    else:
        return None

    # Приводим timezone-aware значения к локальному времени процесса.
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    elif parsed.hour == 0 and parsed.minute == 0 and parsed.second == 0:
        # Дата ISO без времени тоже означает последний момент указанного дня.
        parsed = parsed.replace(hour=23, minute=59, second=59)
    return parsed


def homework_with_deadlines(session, group_id, hours_before=72):
    """Возвращает активные задания, срок которых наступит в заданное число часов."""
    now = datetime.now()
    soon = []
    items = all_homework(session, group_id, status=None)

    for homework in items:
        status = homework.get("status", homework.get("common_status"))
        try:
            status = int(status) if status is not None else None
        except (TypeError, ValueError):
            status = None
        if status in (0, 1, 5):  # просрочено, проверено, удалено
            continue

        deadline = _parse_deadline(
            homework.get("completion_time") or homework.get("overdue_time")
        )
        if not deadline:
            continue
        hours_left = (deadline - now).total_seconds() / 3600
        if 0 <= hours_left <= hours_before:
            soon.append((deadline, homework, hours_left))

    soon.sort(key=lambda item: item[0])
    return soon


def format_user_profile(user_info):
    """Показывает только нужные поля профиля, не печатает весь JSON API."""
    if not isinstance(user_info, dict):
        return "👤 Профиль\nНе удалось получить данные профиля. Попробуй ещё раз позже."

    current_group = user_info.get("current_group") or {}
    if not isinstance(current_group, dict):
        current_group = {}

    full_name = _display_value(field(
        current_group, "full_name", "fio", "student_name", default=""
    ) or field(user_info, "full_name", "fio", "student_name", "name", default=""))
    group_name = _display_value(field(
        current_group, "name", "group_name", "title", default=""
    ) or field(user_info, "group_name", "current_group_name", default=""))
    stream_name = _display_value(field(
        current_group, "stream_name", "stream", default=""
    ) or field(user_info, "stream_name", "stream", default=""))
    level = _display_value(field(current_group, "level", "level_name", default=""))

    lines = ["👤 МОЙ ПРОФИЛЬ"]
    if full_name:
        lines.append(f"Имя: {full_name}")
    if group_name:
        lines.append(f"Группа: {group_name}")
    if stream_name:
        lines.append(f"Поток: {stream_name}")
    if level:
        lines.append(f"Уровень: {level}")
    if len(lines) == 1:
        lines.append("Основная информация профиля пока недоступна.")
    return "\n".join(lines)


# ============================================================
#                   ПОСЕЩАЕМОСТЬ / НАГРУЗКА
# ============================================================

def get_visits(session):
    return api_get(session, "/progress/operations/student-visits") or []


def student_load_by_subject(json_data):
    subjects = {}
    for l in json_data:
        name = l.get("spec_name") or "—"
        subjects.setdefault(name, {"total": 0, "visited": 0})
        subjects[name]["total"] += 1
        if l.get("status_was"):
            subjects[name]["visited"] += 1
    return subjects


def attendance_for_subject(json_data, subject_name):
    q = subject_name.lower()
    lessons = [l for l in json_data if q in (l.get("spec_name") or "").lower()]
    if not lessons:
        return None
    all_count = len(lessons)
    visit_count = sum(1 for l in lessons if l.get("status_was"))
    return {
        "subject": subject_name,
        "total": all_count,
        "visited": visit_count,
        "percent": round(visit_count / all_count * 100, 1) if all_count else 0,
    }