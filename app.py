import json
import os
import secrets
import sys
import time as time_module
from datetime import date, datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

sys.path.insert(0, str(BASE_DIR / "bot"))
import top_api as api
from top_api import TopAuthError

app = FastAPI(title="Student Hub")

COOKIE_NAME = "student_hub_session"
SESSION_FILE = BASE_DIR / ".session_store.json"
CACHE_TTL_SECONDS = 120  # Кэширование тяжелых запросов на 2 минуты

SESSIONS: dict[str, dict] = {}
CACHE: dict[str, tuple[float, object]] = {}


class LoginData(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=500)


#Хранилище сессий

def _save_sessions_to_disk():
#Сохраняет токены на диск, чтобы перезапуск сервера не выкидывал из аккаунта.
    serializable = {}
    for sid, data in SESSIONS.items():
        serializable[sid] = {
            "token": data.get("token"),
            "info": data.get("info", {}),
            "group_id": data.get("group_id"),
            "username": data.get("username"),
        }
    try:
        SESSION_FILE.write_text(json.dumps(serializable, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"[session save warning] {exc}")


def _load_sessions_from_disk():
    if not SESSION_FILE.exists():
        return
    try:
        raw = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        for sid, item in raw.items():
            token = item.get("token")
            if not token:
                continue
            SESSIONS[sid] = {
                "token": token,
                "session": api.build_session(token),
                "info": item.get("info") or {},
                "group_id": item.get("group_id"),
                "username": item.get("username"),
            }
    except Exception as exc:
        print(f"[session load warning] {exc}")


_load_sessions_from_disk()


def cache_get(sid: str, key: str, force: bool = False):
    if force:
        return None
    entry = CACHE.get(f"{sid}:{key}")
    if not entry:
        return None
    ts, value = entry
    if time_module.time() - ts > CACHE_TTL_SECONDS:
        CACHE.pop(f"{sid}:{key}", None)
        return None
    return value


def cache_set(sid: str, key: str, value: object):
    CACHE[f"{sid}:{key}"] = (time_module.time(), value)
    return value


def cache_clear_for(sid: str):
    keys = [k for k in CACHE if k.startswith(f"{sid}:")]
    for k in keys:
        CACHE.pop(k, None)


def _resolve_timezone(tz_name: str):
#Безопасное получение часового пояса (не падает на Windows).
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        #Фоллбэк на локальный часовой пояс ОС или UTC+8 (Иркутск)
        local_tz = datetime.now().astimezone().tzinfo
        return local_tz or dt_timezone(timedelta(hours=8))


def _try_relogin_from_env(session_data: dict) -> bool:
    """Если в .env заданы TOP_USERNAME и TOP_PASSWORD, автоматически обновляет истёкший токен."""
    env_user = os.getenv("TOP_USERNAME", "").strip()
    env_pass = os.getenv("TOP_PASSWORD", "").strip()
    if not env_user or not env_pass:
        return False
    try:
        new_token = api.get_access_token(env_user, env_pass)
        session_data["token"] = new_token
        session_data["session"] = api.build_session(new_token)
        _save_sessions_to_disk()
        return True
    except Exception:
        return False


def get_session(request: Request) -> tuple[str, dict]:
    sid = request.cookies.get(COOKIE_NAME)
    if sid and sid in SESSIONS:
        return sid, SESSIONS[sid]

    #Если печенька потерялась, но локально на ПК есть единственная сохраненная сессия — подхватываем её
    if len(SESSIONS) == 1:
        only_sid = next(iter(SESSIONS))
        return only_sid, SESSIONS[only_sid]

    raise HTTPException(401, "Войдите в аккаунт Top Academy")


def profile_data(info: dict) -> dict:
    group = info.get("current_group") if isinstance(info, dict) else {}
    group = group if isinstance(group, dict) else {}

    def val(*keys):
        return api._display_value(api.field(group, *keys) or api.field(info, *keys))

    return {
        "name": val("full_name", "fio", "student_name", "name") or "Студент Top Academy",
        "group": val("name", "group_name", "title") or api._display_value(api.field(info, "group_name", "current_group_name")),
        "stream": val("stream_name", "stream"),
        "level": val("level", "level_name"),
    }


def _format_lesson_item(item: dict, lesson_date: date, now_local: datetime, local_tz, idx: int) -> dict:
    get = lambda *keys: api._display_value(api.field(item, *keys))
    start_text = api._time_text(api.field(item, "started_at", "time_start", "start_time", "begin_time", "start"))
    end_text = api._time_text(api.field(item, "finished_at", "time_end", "end_time", "finish_time", "end"))

    state = "upcoming"
    try:
        start_dt = datetime.combine(lesson_date, datetime.strptime(start_text, "%H:%M").time()).replace(tzinfo=local_tz)
        end_dt = datetime.combine(lesson_date, datetime.strptime(end_text, "%H:%M").time()).replace(tzinfo=local_tz)
        if end_dt <= start_dt:
            end_dt += timedelta(days=1)
        if now_local >= end_dt:
            state = "past"
        elif start_dt <= now_local < end_dt:
            state = "current"
    except (TypeError, ValueError):
        pass

    return {
        "id": str(api.field(item, "id", "lesson_id", default=f"{lesson_date}-{idx}")),
        "date": lesson_date.isoformat(),
        "subject": get("spec_name", "subject_name", "name_spec", "subject", "name", "discipline_name") or "Предмет не указан",
        "start": start_text,
        "end": end_text,
        "teacher": get("fio_teach", "teacher_name", "teacher_fio", "teacher", "teachers"),
        "room": get("auditory_name", "room_name", "auditory", "room", "cabinet"),
        "state": state,
        "state_label": {"past": "Прошла", "current": "Сейчас идёт", "upcoming": "Предстоит"}[state],
    }


#API маршруты

@app.post("/api/login")
def login(data: LoginData, response: Response):
    username = data.username.strip()
    if not username or not data.password:
        raise HTTPException(400, "Введите логин и пароль.")
    try:
        token = api.get_access_token(username, data.password)
        session = api.build_session(token)
    except TopAuthError as exc:
        raise HTTPException(401, str(exc)) from exc
    except Exception as exc:
        print(f"[login error] {type(exc).__name__}: {exc}")
        raise HTTPException(502, "Не удалось связаться с сервером Top Academy. Попробуйте позже.") from exc

    try:
        info = api.get_user_info(session) or {}
    except Exception as exc:
        print(f"[login profile warning] {exc}")
        info = {}

    try:
        group_id = api.get_current_group_id(session, info)
    except Exception as exc:
        print(f"[login group warning] {exc}")
        group_id = None

    sid = secrets.token_urlsafe(32)
    SESSIONS.clear()  # Для локального ПК храним одну активную сессию
    SESSIONS[sid] = {
        "token": token,
        "session": session,
        "info": info,
        "group_id": group_id,
        "username": username,
    }
    _save_sessions_to_disk()

    response.set_cookie(
        key=COOKIE_NAME,
        value=sid,
        httponly=True,
        secure=os.getenv("COOKIE_SECURE", "0") == "1",
        samesite="lax",
        max_age=60 * 60 * 24 * 30,  # 30 дней для локального использования
        path="/",
    )
    return {"ok": True, "profile": profile_data(info)}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    sid = request.cookies.get(COOKIE_NAME)
    if sid:
        cache_clear_for(sid)
        SESSIONS.pop(sid, None)
    else:
        SESSIONS.clear()
    _save_sessions_to_disk()
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@app.get("/api/me")
def me(request: Request, response: Response):
    sid, s = get_session(request)
    # Обновляем печеньку, если сессия была восстановлена с диска
    if request.cookies.get(COOKIE_NAME) != sid:
        response.set_cookie(
            key=COOKIE_NAME, value=sid, httponly=True,
            samesite="lax", max_age=60 * 60 * 24 * 30, path="/"
        )
    return {"profile": profile_data(s["info"])}


@app.get("/api/schedule")
def schedule(
    request: Request,
    day: date = None,
    mode: str = "day",
    timezone: str = "Asia/Irkutsk",
    force: bool = False,
):
    sid, s = get_session(request)
    local_tz = _resolve_timezone(timezone)
    now_local = datetime.now(local_tz)
    target_day = day or now_local.date()

    cache_key = f"schedule:{mode}:{target_day.isoformat()}"
    cached = cache_get(sid, cache_key, force=force)
    if cached is not None:
        return cached

    def fetch():
        if mode == "week":
            return api.lessons_for_week(s["session"], target_day)
        return api.lessons_for_day(s["session"], target_day)

    try:
        raw_lessons = fetch()
    except TopAuthError as exc:
        if _try_relogin_from_env(s):
            raw_lessons = fetch()
        else:
            SESSIONS.pop(sid, None)
            _save_sessions_to_disk()
            raise HTTPException(401, str(exc)) from exc

    if raw_lessons is None:
        raise HTTPException(502, "Не удалось получить расписание Top Academy")

    result = []
    for idx, item in enumerate(raw_lessons):
        l_date = api._date_value(api.field(item, "date", "date_lesson", "day", "lesson_date")) or target_day
        result.append(_format_lesson_item(item, l_date, now_local, local_tz, idx))

    payload = {
        "date": target_day.isoformat(),
        "mode": mode,
        "timezone": timezone,
        "local_now": now_local.isoformat(),
        "lessons": result,
    }
    return cache_set(sid, cache_key, payload)


@app.get("/api/homework")
def homework(request: Request, force: bool = False):
    sid, s = get_session(request)
    cached = cache_get(sid, "homework", force=force)
    if cached is not None:
        return cached

    if not s.get("group_id"):
        try:
            s["group_id"] = api.get_current_group_id(s["session"], s.get("info"))
            _save_sessions_to_disk()
        except TopAuthError as exc:
            raise HTTPException(401, str(exc)) from exc

    if not s.get("group_id"):
        return {"items": [], "warning": "Не удалось определить учебную группу"}

    try:
        raw_items = api.all_homework(s["session"], s["group_id"])
    except TopAuthError as exc:
        if _try_relogin_from_env(s):
            raw_items = api.all_homework(s["session"], s["group_id"])
        else:
            SESSIONS.pop(sid, None)
            _save_sessions_to_disk()
            raise HTTPException(401, str(exc)) from exc

    result = [api.parse_homework_item(h, i) for i, h in enumerate(raw_items)]
    order = {"pending": 0, "overdue": 1, "review": 2, "done": 3, "deleted": 4}
    result.sort(key=lambda item: order.get(item["state"], 0))

    return cache_set(sid, "homework", {"items": result})


@app.get("/api/grades")
def grades(request: Request, force: bool = False):
    sid, s = get_session(request)
    cached = cache_get(sid, "grades", force=force)
    if cached is not None:
        return cached

    #Используем уже закэшированное ДЗ или подгружаем его для включения оценок за ДЗ
    hw_data = cache_get(sid, "homework", force=force)
    hw_items = hw_data.get("items", []) if isinstance(hw_data, dict) else []

    try:
        data = api.build_grades_and_attendance(s["session"], homework_items=hw_items)
    except TopAuthError as exc:
        if _try_relogin_from_env(s):
            data = api.build_grades_and_attendance(s["session"], homework_items=hw_items)
        else:
            SESSIONS.pop(sid, None)
            _save_sessions_to_disk()
            raise HTTPException(401, str(exc)) from exc

    return cache_set(sid, "grades", data)


@app.get("/api/notifications")
def notifications(request: Request):
    sid, _ = get_session(request)
    grades_data = cache_get(sid, "grades", force=False)
    items = grades_data.get("items", [])[:5] if isinstance(grades_data, dict) else []
    return {"items": items, "grades_available": True}


#Статика и pwa
app.mount("/assets", StaticFiles(directory=BASE_DIR / "web" / "assets"), name="assets")


@app.get("/")
def index():
    return FileResponse(BASE_DIR / "web" / "index.html")


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(BASE_DIR / "web" / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
def sw():
    return FileResponse(BASE_DIR / "web" / "sw.js", media_type="application/javascript")