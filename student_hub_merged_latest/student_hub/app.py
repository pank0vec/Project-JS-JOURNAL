import os, secrets, sys
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).parent / "bot"))
import top_api as api

app = FastAPI(title="Student Hub")
SESSIONS = {}  # MVP: process memory only; use encrypted persistent session store in production.
COOKIE = "student_hub_session"

class LoginData(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=500)

def get_session(request: Request):
    sid = request.cookies.get(COOKIE)
    data = SESSIONS.get(sid or "")
    if not data:
        raise HTTPException(401, "Войдите в аккаунт Top Academy")
    return data

@app.post("/api/login")
def login(data: LoginData, response: Response):
    username = data.username.strip()
    if not username or not data.password:
        raise HTTPException(400, "Введите логин и пароль.")
    try:
        token = api.get_access_token(username, data.password)
        session = api.build_session(token)
    except PermissionError as exc:
        raise HTTPException(401, str(exc)) from exc
    except Exception as exc:
        # Keep technical detail in server console, never expose credentials/tokens.
        print(f"[login] {type(exc).__name__}: {exc}")
        raise HTTPException(502, "Не удалось связаться с сервисом входа Top Academy. Попробуйте позже.") from exc

    # Successful token exchange is sufficient to establish the local session.
    # Profile/group endpoints are optional and must not turn a valid login into a failure.
    try:
        info = api.get_user_info(session) or {}
    except Exception as exc:
        print(f"[login profile] {type(exc).__name__}: {exc}")
        info = {}
    try:
        group_id = api.field(info, "current_group_id", "group_id", "id_group") or api.get_current_group_id(session)
    except Exception as exc:
        print(f"[login group] {type(exc).__name__}: {exc}")
        group_id = None

    sid = secrets.token_urlsafe(32)
    SESSIONS[sid] = {"session": session, "info": info, "group_id": group_id}
    response.set_cookie(
        key=COOKIE, value=sid, httponly=True,
        secure=os.getenv("COOKIE_SECURE", "0") == "1",
        samesite="lax", max_age=60 * 60 * 12, path="/"
    )
    return {"ok": True, "profile": profile_data(info)}

def profile_data(info):
    group = info.get("current_group") if isinstance(info,dict) else {}
    group = group if isinstance(group,dict) else {}
    def val(*keys):
        return api._display_value(api.field(group,*keys) or api.field(info,*keys))
    return {"name":val("full_name","fio","student_name","name"),
            "group":val("name","group_name","title") or api._display_value(api.field(info,"group_name","current_group_name")),
            "stream":val("stream_name","stream"), "level":val("level","level_name")}

@app.post("/api/logout")
def logout(request: Request, response: Response):
    sid=request.cookies.get(COOKIE)
    if sid: SESSIONS.pop(sid,None)
    response.delete_cookie(COOKIE, path="/")
    return {"ok":True}

@app.get("/api/me")
def me(request: Request):
    s=get_session(request)
    return {"profile":profile_data(s["info"])}

@app.get("/api/schedule")
def schedule(request: Request, day: date = None, timezone: str = "Asia/Irkutsk"):
    s = get_session(request)
    try:
        local_tz = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(400, "Неизвестный часовой пояс устройства")
    now_local = datetime.now(local_tz)
    day = day or now_local.date()
    lessons = api.lessons_for_day(s["session"], day)
    if lessons is None:
        raise HTTPException(502, "Не удалось получить расписание Top Academy")
    result = []
    for item in lessons:
        get = lambda *keys: api._display_value(api.field(item, *keys))
        start_text = api._time_text(api.field(item, "started_at", "time_start", "start_time", "begin_time", "start"))
        end_text = api._time_text(api.field(item, "finished_at", "time_end", "end_time", "finish_time", "end"))
        state = "upcoming"
        try:
            start_dt = datetime.combine(day, datetime.strptime(start_text, "%H:%M").time()).replace(tzinfo=local_tz)
            end_dt = datetime.combine(day, datetime.strptime(end_text, "%H:%M").time()).replace(tzinfo=local_tz)
            if end_dt <= start_dt:
                from datetime import timedelta
                end_dt += timedelta(days=1)
            if now_local >= end_dt:
                state = "past"
            elif start_dt <= now_local < end_dt:
                state = "current"
        except (TypeError, ValueError):
            pass
        result.append({
            "id": str(api.field(item, "id", "lesson_id", default=f"{day}-{len(result)}")),
            "subject": get("spec_name", "subject_name", "name_spec", "subject", "name", "discipline_name") or "Предмет не указан",
            "start": start_text, "end": end_text,
            "teacher": get("fio_teach", "teacher_name", "teacher_fio", "teacher", "teachers"),
            "room": get("auditory_name", "room_name", "auditory", "room", "cabinet"),
            "state": state,
            "state_label": {"past": "Прошла", "current": "Сейчас идёт", "upcoming": "Предстоит"}[state]
        })
    return {"date": day.isoformat(), "timezone": timezone, "local_now": now_local.isoformat(), "lessons": result}

@app.get("/api/homework")
def homework(request: Request):
    s=get_session(request)
    if not s.get("group_id"): return {"items":[],"warning":"Не удалось определить учебную группу"}
    items=api.all_homework(s["session"],s["group_id"],status=None)
    result=[]
    for h in items:
        get=lambda *keys: api._display_value(api.field(h,*keys))
        raw_status = api.field(h, "status", "common_status")
        status_text = get("status_name", "status_text", "status")
        status_value = str(raw_status if raw_status is not None else status_text or "").strip().lower()
        due = get("completion_time", "overdue_time")

        # Status IDs confirmed by the supplied Top Academy bot source:
        # 0 = overdue, 1 = checked/completed, 5 = deleted.
        try:
            status_id = int(raw_status) if raw_status is not None else None
        except (TypeError, ValueError):
            status_id = None
        if status_id == 5 or any(x in status_value for x in ("удален", "удалён", "deleted", "removed")):
            state = "deleted"
            state_label = "Удалено"
        elif status_id == 1 or any(x in status_value for x in ("выполн", "провер", "готов", "completed", "checked", "done")):
            state = "done"
            state_label = "Выполнено"
        elif status_id == 0 or any(x in status_value for x in ("просроч", "overdue", "late")):
            state = "overdue"
            state_label = "Просрочено"
        else:
            deadline = api._parse_deadline(due) if due else None
            if deadline and deadline < datetime.now():
                state = "overdue"
                state_label = "Просрочено"
            else:
                state = "pending"
                state_label = "Не выполнено"

        result.append({"id":str(api.field(h,"id",default=len(result))),
          "subject":get("name_spec","spec_name","subject_name") or "Предмет не указан",
          "title":get("theme","name","title","description") or "Домашнее задание",
          "due":due,"teacher":get("fio_teach","teacher_name"),
          "status":state_label,"state":state})
    # Active work first; completed tasks are deliberately placed at the end.
    order = {"pending": 0, "overdue": 1, "deleted": 2, "done": 3}
    result.sort(key=lambda item: order.get(item["state"], 0))
    return {"items":result}

@app.get("/api/grades")
def grades(request: Request):
    get_session(request)
    # The supplied bot code contains no verified grades endpoint. Do not guess and show fake grades.
    return {"available":False,"items":[],"message":"Подключение оценок ожидает проверки официального маршрута API Top Academy."}

@app.get("/api/notifications")
def notifications(request: Request):
    get_session(request)
    # Browser-side polling can surface real new grades once the grades endpoint is verified.
    return {"items":[],"grades_available":False}

app.mount("/assets", StaticFiles(directory="web/assets"), name="assets")
@app.get("/")
def index(): return FileResponse("web/index.html")
@app.get("/manifest.webmanifest")
def manifest(): return FileResponse("web/manifest.webmanifest", media_type="application/manifest+json")
@app.get("/sw.js")
def sw(): return FileResponse("web/sw.js", media_type="application/javascript")
