import re
from datetime import date, datetime, time, timedelta
from html import unescape
import requests

API_BASE = "https://msapi.top-academy.ru/api/v2"
APPLICATION_KEY = "6a56a5df2667e65aab73ce76d1dd737f7d1faef9c52e8b8c55ac75f565d8e8a6"


class TopAuthError(PermissionError): """ """
#Выбрасывается, когда токен Top Academy недействителен или истёк (HTTP 401/403).


#Авторизация / Сессия

def get_access_token(username: str, password: str) -> str:
    url = f"{API_BASE}/auth/login"
    headers = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "ru_RU, ru",
        "content-type": "application/json",
        "origin": "https://journal.top-academy.ru",
        "referer": "https://journal.top-academy.ru/",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
    }
    payload = {
        "application_key": APPLICATION_KEY,
        "id_city": None,
        "password": password,
        "username": username,
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=25)
    except requests.RequestException as exc:
        raise RuntimeError(f"Не удалось связаться с сервером Top Academy ({exc.__class__.__name__}).") from exc

    if r.status_code != 200:
        if r.status_code in (400, 401, 403):
            raise TopAuthError("Вход был отклонён. Проверьте введённые логин и пароль.")
        raise RuntimeError(f"Сервер Top Academy вернул ошибку HTTP {r.status_code}.")

    try:
        data = r.json()
    except ValueError as exc:
        raise RuntimeError("Top Academy вернул ответ в неожиданном формате.") from exc

    def find_token(obj, depth=0):
        if depth > 4 or not isinstance(obj, dict):
            return None
        for key in ("access_token", "token"):
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
        raise RuntimeError("Нет токена авторизации.")
    return token


def build_session(token: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru_RU, ru",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Origin": "https://journal.top-academy.ru",
        "Referer": "https://journal.top-academy.ru/",
    })
    return s


def api_get(session: requests.Session, path: str, params: dict = None, quiet: bool = False):
#Выполняет GET-запрос к API. При 401/403 выбрасывает TopAuthError.
    if not path.startswith("/"):
        path = "/" + path
    url = f"{API_BASE}{path}"
    clean_params = {k: v for k, v in (params or {}).items() if v is not None}

    try:
        response = session.get(url, params=clean_params, timeout=20)
    except requests.RequestException as exc:
        if not quiet:
            print(f"[API Network Error] {path}: {exc.__class__.__name__}")
        return None

    if response.status_code in (401, 403):
        raise TopAuthError("Сессия Top Academy истекла. Пожалуйста, войдите снова.")

    if response.status_code != 200:
        if not quiet:
            print(f"[API HTTP {response.status_code}] {path}")
        return None

    try:
        return response.json()
    except ValueError:
        return None


#Парсинг

def field(obj, *names, default=None):
    if not isinstance(obj, dict):
        return default
    for name in names:
        value = obj.get(name)
        if value is not None and value != "":
            return value
    return default


def _display_value(value) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, dict):
        inner = field(
            value, "name", "title", "fio", "fio_teach", "full_name",
            "teacher_name", "room_name", "auditory_name", "text_comment",
            "comment", "value", default=""
        )
        return _display_value(inner)
    if isinstance(value, (list, tuple)):
        return ", ".join(x for x in (_display_value(v) for v in value) if x)
    text = unescape(str(value)).replace("\\n", "\n").replace("\\r", "")
    return " ".join(text.split())


def extract_list(data, *keys) -> list:
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []

    preferred = list(keys) + [
        "homework_list", "lesson_list", "lessons", "visits",
        "items", "list", "result", "data"
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


def _time_text(value) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, datetime):
        return value.strftime("%H:%M")
    text = str(value).strip()
    if "T" in text:
        text = text.split("T", 1)[1]
    match = re.search(r"(\d{1,2}:\d{2})", text)
    return match.group(1).zfill(5) if match else text[:5]


def _date_value(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except (TypeError, ValueError):
        return None


def _parse_deadline(value):
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

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    elif parsed.hour == 0 and parsed.minute == 0 and parsed.second == 0:
        parsed = parsed.replace(hour=23, minute=59, second=59)
    return parsed


def _lesson_sort_key(lesson):
    return _time_text(field(
        lesson, "started_at", "time_start", "start_time", "begin_time", "start", default="99:99"
    )) or "99:99"


#Профиль & группы

def get_user_info(session: requests.Session) -> dict:
    return api_get(session, "/settings/user-info") or {}


def get_current_group_id(session: requests.Session, user_info: dict = None):
    ui = user_info if isinstance(user_info, dict) and user_info else get_user_info(session)
    gid = field(ui, "current_group_id", "group_id", "id_group")
    if gid is not None:
        return gid
    current_group = ui.get("current_group")
    if isinstance(current_group, dict):
        gid = field(current_group, "id", "group_id", "id_group")
        if gid is not None:
            return gid
    groups = ui.get("groups")
    if isinstance(groups, list) and groups:
        return field(groups[0], "id", "group_id")
    return None


#Расписание

def get_schedule_month(session: requests.Session, any_date_in_month: date):
    d = any_date_in_month.isoformat() if hasattr(any_date_in_month, "isoformat") else str(any_date_in_month)
    return api_get(session, "/schedule/operations/get-month", {"date_filter": d})


def get_schedule_by_date(session: requests.Session, when: date):
    d = when.isoformat() if hasattr(when, "isoformat") else str(when)
    return api_get(session, "/schedule/operations/get-by-date", {"date_filter": d})


def _lessons_of(data) -> list:
    return extract_list(
        data,
        "lessons", "lesson_list", "schedule", "data", "items",
        "days", "result", "days_lessons", "lessons_list"
    )


def lessons_for_day(session: requests.Session, when: date = None):
    when = when or date.today()
    data = get_schedule_by_date(session, when)
    if data is None:
        return None
    return sorted(_lessons_of(data), key=_lesson_sort_key)


def lessons_for_week(session: requests.Session, target_date: date = None):
    target_date = target_date or date.today()
    start = target_date - timedelta(days=target_date.weekday())
    end = start + timedelta(days=6)

    data = get_schedule_month(session, start)
    if data is None:
        return None

    lessons = _lessons_of(data)
    #Если неделя пересекает границу двух месяцев, подгружаем второй месяц
    if end.month != start.month:
        data_next = get_schedule_month(session, end)
        if data_next:
            lessons = lessons + _lessons_of(data_next)

    result = []
    seen = set()
    for lesson in lessons:
        l_date = _date_value(field(lesson, "date", "date_lesson", "day", "lesson_date"))
        if not l_date or not (start <= l_date <= end):
            continue
        start_t = _lesson_sort_key(lesson)
        subj = _display_value(field(lesson, "spec_name", "subject_name", "name_spec", "subject"))
        dedup_key = (l_date.isoformat(), start_t, subj)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        result.append(lesson)

    return sorted(result, key=lambda l: (
        _date_value(field(l, "date", "date_lesson", "day", "lesson_date")) or date.max,
        _lesson_sort_key(l),
    ))


#ДЗ

def get_homework_list(session: requests.Session, group_id, page=1, status=None, hw_type=None):
    return api_get(
        session,
        "/homework/operations/list",
        {"page": page, "status": status, "type": hw_type, "group_id": group_id},
        quiet=True,
    )


def all_homework(session: requests.Session, group_id, max_pages_per_status: int = 3) -> list:
    """
    Загружает ДЗ по всем основным статусам Top Academy:
    3 - активные/текущие, 2 - на проверке, 0 - просроченные, 1 - проверенные.
    Также пробует общий запрос status=None на случай другой конфигурации API.
    """
    collected = []
    seen_ids = set()

    def fetch_pages(status_code, max_p):
        for page in range(1, max_p + 1):
            data = get_homework_list(session, group_id, page=page, status=status_code)
            if not data:
                break
            items = extract_list(data, "homework_list", "homework", "items", "data", "tasks")
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
                if status_code is not None and item.get("status") is None:
                    item["_queried_status"] = status_code
                collected.append(item)
                added += 1
            if added == 0:
                break

    #Сначала пробуем получить список по конкретным статусам (в MyStat status обязателен)
    for st in (3, 2, 0, 1):
        fetch_pages(st, max_pages_per_status)

    #Если ничего не вернулось по статусам, пробуем без фильтра status
    if not collected:
        fetch_pages(None, 6)

    return collected


def parse_homework_item(h: dict, index: int = 0) -> dict:
    get = lambda *keys: _display_value(field(h, *keys))
    raw_status = field(h, "status", "common_status", "_queried_status")
    status_text = get("status_name", "status_text", "status")
    status_value = str(raw_status if raw_status is not None else status_text or "").strip().lower()
    due = get("completion_time", "overdue_time")
    created = get("creation_time", "created_at")

    hw_stud = h.get("homework_stud") if isinstance(h.get("homework_stud"), dict) else {}
    mark = field(hw_stud, "mark", "grade", "auto_mark") or field(h, "mark", "grade")
    if mark is not None:
        mark = str(mark).strip()

    try:
        status_id = int(raw_status) if raw_status is not None else None
    except (TypeError, ValueError):
        status_id = None

    if status_id == 5 or any(x in status_value for x in ("удален", "удалён", "deleted")):
        state, state_label = "deleted", "Удалено"
    elif status_id == 1 or (mark and mark.isdigit()) or any(x in status_value for x in ("выполн", "провер", "checked", "done")):
        state, state_label = "done", "Проверено"
    elif status_id == 2 or (hw_stud and not mark) or any(x in status_value for x in ("на проверке", "review", "uploaded")):
        state, state_label = "review", "На проверке"
    elif status_id == 0 or any(x in status_value for x in ("просроч", "overdue", "late")):
        state, state_label = "overdue", "Просрочено"
    else:
        deadline = _parse_deadline(due) if due else None
        if deadline and deadline < datetime.now():
            state, state_label = "overdue", "Просрочено"
        else:
            state, state_label = "pending", "Активно"

    comment_obj = h.get("homework_comment")
    teacher_comment = ""
    if isinstance(comment_obj, dict):
        teacher_comment = _display_value(field(comment_obj, "text_comment", "comment", "message"))
    elif isinstance(comment_obj, str):
        teacher_comment = _display_value(comment_obj)

    file_url = field(h, "file_path", "file_url", "download_url")
    stud_file_url = field(hw_stud, "file_path", "file_url")

    return {
        "id": str(field(h, "id", default=f"hw-{index}")),
        "subject": get("name_spec", "spec_name", "subject_name") or "Предмет не указан",
        "title": get("theme", "name", "title") or "Домашнее задание",
        "description": get("comment", "description", "task_text"),
        "due": due,
        "created": created,
        "teacher": get("fio_teach", "teacher_name"),
        "status": state_label,
        "state": state,
        "mark": mark if mark else None,
        "teacher_comment": teacher_comment,
        "file_url": file_url if isinstance(file_url, str) and file_url.startswith("http") else None,
        "stud_file_url": stud_file_url if isinstance(stud_file_url, str) and stud_file_url.startswith("http") else None,
    }


#Оценки и посещаемость 

def get_visits(session: requests.Session) -> list:
    data = api_get(session, "/progress/operations/student-visits")
    return extract_list(data, "visits", "student_visits", "items", "data")


def build_grades_and_attendance(session: requests.Session, homework_items: list = None) -> dict:
    visits = get_visits(session) or []

    subjects_att = {}
    grades = []
    seen_grade_ids = set()

    mark_fields = [
        ("class_work_mark", "Классная работа"),
        ("control_work_mark", "Контрольная работа"),
        ("home_work_mark", "Домашняя работа"),
        ("lab_work_mark", "Лабораторная работа"),
        ("practical_work_mark", "Практическая работа"),
        ("mark", "Оценка за занятие"),
    ]

    for idx, v in enumerate(visits):
        if not isinstance(v, dict):
            continue
        subj = _display_value(field(v, "spec_name", "subject_name", "name_spec", default="Предмет"))
        visit_date = str(field(v, "date_visit", "date", "lesson_date", default=""))[:10]
        teacher = _display_value(field(v, "teacher_name", "fio_teach", "teacher"))
        theme = _display_value(field(v, "lesson_theme", "theme", "topic"))
        status_was = v.get("status_was")

        #Считаем посещаемость
        entry = subjects_att.setdefault(subj, {"subject": subj, "total": 0, "visited": 0, "absent": 0})
        entry["total"] += 1
        if status_was in (1, 2, True, "1", "2"):
            entry["visited"] += 1
        else:
            entry["absent"] += 1

        #Извлекаем все оценки за этот урок
        for key, label in mark_fields:
            raw_mark = v.get(key)
            if raw_mark is None or str(raw_mark).strip() in ("", "0", "None"):
                continue
            try:
                val = int(float(str(raw_mark).strip()))
            except ValueError:
                continue
            if val <= 0:
                continue

            gid = f"visit-{v.get('lesson_id', idx)}-{key}-{visit_date}"
            if gid in seen_grade_ids:
                continue
            seen_grade_ids.add(gid)

            grades.append({
                "id": gid,
                "subject": subj,
                "value": val,
                "type": label,
                "theme": theme or label,
                "teacher": teacher,
                "date": visit_date,
            })

    #Дополняем оценками из проверенных ДЗ (если они не попали в visits)
    for hw in (homework_items or []):
        mark = hw.get("mark")
        if not mark:
            continue
        try:
            val = int(float(str(mark).strip()))
        except ValueError:
            continue
        if val <= 0:
            continue
        due_date = str(hw.get("due") or hw.get("created") or "")[:10]
        gid = f"hw-{hw.get('id')}"
        if gid in seen_grade_ids:
            continue
        seen_grade_ids.add(gid)
        grades.append({
            "id": gid,
            "subject": hw.get("subject", "Предмет"),
            "value": val,
            "type": "Домашнее задание",
            "theme": hw.get("title", "ДЗ"),
            "teacher": hw.get("teacher", ""),
            "date": due_date,
        })

    #Сортируем оценки: сначала самые свежие
    grades.sort(key=lambda g: g.get("date") or "0000-00-00", reverse=True)

    #Формируем массив посещаемости по предметам
    attendance_list = []
    total_lessons = 0
    total_visited = 0
    for subj, stats in sorted(subjects_att.items()):
        tot = stats["total"]
        vis = stats["visited"]
        total_lessons += tot
        total_visited += vis
        pct = round((vis / tot) * 100, 1) if tot > 0 else 0.0
        attendance_list.append({
            "subject": subj,
            "total": tot,
            "visited": vis,
            "absent": stats["absent"],
            "percent": pct,
        })

    avg_grade = round(sum(g["value"] for g in grades) / len(grades), 2) if grades else None
    overall_att = round((total_visited / total_lessons) * 100, 1) if total_lessons > 0 else None

    return {
        "available": True,
        "summary": {
            "average_grade": avg_grade,
            "total_grades": len(grades),
            "overall_attendance": overall_att,
            "total_lessons": total_lessons,
            "visited_lessons": total_visited,
        },
        "items": grades,
        "attendance": attendance_list,
    }