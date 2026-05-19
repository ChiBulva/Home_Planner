from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from urllib.parse import quote

import pyotp
from fastapi import Depends, FastAPI, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from app.auth import (
    get_current_user,
    has_users,
    provisioning_uri,
    qr_data_uri,
    require_admin,
    verify_totp,
)
from app.config import settings
from app.database import get_db, init_db
from app.models import AppSetting, Task, TaskHistory, User, utcnow
from app.realtime import manager
from app.scheduler import next_reset_time, reset_due_tasks, start_scheduler
from app.weather import get_forecast, search_locations


templates = Jinja2Templates(directory="app/templates")
scheduler = None


def local_today() -> date:
    return datetime.now().date()


def short_date(value: datetime | None) -> str:
    if not value:
        return ""
    return f"{value:%b} {value.day}"


def datetime_local(value: datetime | None) -> str:
    if not value:
        return ""
    return value.strftime("%Y-%m-%dT%H:%M")


def chore_reset_time(value: str | None) -> str:
    value = value or ""
    if "time=" in value:
        return value.split("time=", 1)[1][:5]
    if len(value) >= 5 and value[2:3] == ":":
        return value[:5]
    return "04:00"


def chore_weekdays(value: str | None) -> set[int]:
    value = value or ""
    if value.startswith("days="):
        value = value.split(";", 1)[0].removeprefix("days=")
    return {int(part) for part in value.split(",") if part.strip().isdigit()}


templates.env.filters["short_date"] = short_date
templates.env.filters["datetime_local"] = datetime_local
templates.env.filters["chore_reset_time"] = chore_reset_time
templates.env.filters["chore_weekdays"] = chore_weekdays


@asynccontextmanager
async def lifespan(app: FastAPI):
    global scheduler
    init_db()
    scheduler = start_scheduler()
    await reset_due_tasks()
    yield
    if scheduler:
        scheduler.shutdown(wait=False)


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    max_age=settings.session_max_age,
    same_site="lax",
)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


def render(request: Request, name: str, context: dict | None = None) -> HTMLResponse:
    theme = request.session.get("ui_theme", "day")
    row_mode = request.session.get("ui_row_mode", "normal")
    col_mode = request.session.get("ui_col_mode", "normal")
    session_highlight = valid_color(request.session.get("ui_session_highlight", "#147d74"), "#147d74")
    row_scale_map = {"compact": "0.86", "normal": "1", "comfortable": "1.18"}
    column_map = {
        "narrow_chores": ("minmax(230px, 0.58fr)", "minmax(560px, 1.62fr)"),
        "normal": ("minmax(250px, 0.65fr)", "minmax(520px, 1.55fr)"),
        "balanced": ("minmax(280px, 0.8fr)", "minmax(500px, 1.35fr)"),
    }
    chores_col, main_col = column_map.get(col_mode, column_map["normal"])
    ctx = {
        "request": request,
        "app_name": settings.app_name,
        "current_path": request.url.path,
        "current_full_path": str(request.url.path) + (f"?{request.url.query}" if request.url.query else ""),
        "ui_theme": theme if theme in {"day", "night"} else "day",
        "ui_row_mode": row_mode if row_mode in row_scale_map else "normal",
        "ui_col_mode": col_mode if col_mode in column_map else "normal",
        "ui_row_scale": row_scale_map.get(row_mode, "1"),
        "ui_chores_col": chores_col,
        "ui_main_col": main_col,
        "ui_session_highlight": session_highlight,
    }
    ctx.update(context or {})
    return templates.TemplateResponse(name, ctx)


def redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def task_query(db: Session, user: User | None = None):
    stmt = select(Task).order_by(
        Task.status,
        Task.project_name,
        Task.project_order,
        Task.priority.desc(),
        Task.created_at.desc(),
    )
    return db.scalars(stmt).all()


def render_task_list(request: Request, db: Session, user: User) -> HTMLResponse:
    tasks = task_query(db, user)
    users = db.scalars(select(User).order_by(User.username)).all()
    projects = project_names(db)
    groups = task_browser_groups(tasks)
    active_task_ids = active_task_ids_for_date(db, local_today())
    return render(
        request,
        "_tasks.html",
        {
            "user": user,
            "tasks": tasks,
            "users": users,
            "projects": projects,
            "active_task_ids": active_task_ids,
            **groups,
        },
    )


def render_chore_list(request: Request, db: Session, user: User) -> HTMLResponse:
    tasks = task_query(db, user)
    chores = [task for task in tasks if (task.type or "chore") == "chore"]
    daily_chores = [task for task in chores if (task.reset_frequency or "daily") == "daily"]
    weekday_buckets: dict[int, list[Task]] = {idx: [] for idx in range(7)}
    for task in chores:
        if (task.reset_frequency or "daily") != "weekdays":
            continue
        days = sorted(chore_weekdays(task.reset_value))
        if not days:
            days = list(range(7))
        for day in days:
            if day in weekday_buckets:
                weekday_buckets[day].append(task)
    weekday_sections = [
        {"label": label, "index": idx, "chores": weekday_buckets[idx]}
        for idx, label in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
    ]
    weekday_total = sum(len(section["chores"]) for section in weekday_sections)
    return render(
        request,
        "_chores.html",
        {
            "user": user,
            "daily_chores": daily_chores,
            "weekday_sections": weekday_sections,
            "weekday_total": weekday_total,
        },
    )


def project_names(db: Session) -> list[str]:
    names = db.scalars(
        select(Task.project_name)
        .where(Task.project_name != "")
        .distinct()
        .order_by(Task.project_name)
    ).all()
    return list(names)


def parse_active_projects(value: str) -> set[str]:
    return {name for name in value.split("\n") if name}


def active_project_names(db: Session) -> set[str]:
    return parse_active_projects(get_setting(db, "active_projects"))


def save_active_project_names(db: Session, names: set[str]) -> None:
    set_setting(db, "active_projects", "\n".join(sorted(names)))


def parse_active_task_ids(value: str) -> set[int]:
    return {int(part) for part in value.split("\n") if part.strip().isdigit()}


def active_task_ids_for_date(db: Session, selected_date: date) -> set[int]:
    return parse_active_task_ids(get_setting(db, f"active_tasks:{selected_date.isoformat()}"))


def save_active_task_ids_for_date(db: Session, selected_date: date, task_ids: set[int]) -> None:
    set_setting(db, f"active_tasks:{selected_date.isoformat()}", "\n".join(str(i) for i in sorted(task_ids)))


def inactive_project_task_ids_for_date(db: Session, selected_date: date) -> set[int]:
    return parse_active_task_ids(get_setting(db, f"inactive_project_tasks:{selected_date.isoformat()}"))


def save_inactive_project_task_ids_for_date(db: Session, selected_date: date, task_ids: set[int]) -> None:
    set_setting(
        db,
        f"inactive_project_tasks:{selected_date.isoformat()}",
        "\n".join(str(i) for i in sorted(task_ids)),
    )


def project_color(db: Session, name: str) -> str:
    return get_setting(db, f"project_color:{name}", "#147d74")


def valid_color(value: str, default: str = "#147d74") -> str:
    value = (value or "").strip()
    if len(value) == 7 and value.startswith("#"):
        return value
    return default


def priority_color(priority: int | None) -> str:
    if priority == 3:
        return "#ad3434"  # High
    if priority == 1:
        return "#f9c74f"  # Low
    return "#f08c2e"  # Normal


def project_quote(value: str) -> str:
    return quote(value, safe="")


def project_sort_key(task: Task) -> tuple[int, int, datetime]:
    order = task.project_order or 0
    return (1 if order <= 0 else 0, order if order > 0 else 999_999, task.created_at)


def get_setting(db: Session, key: str, default: str = "") -> str:
    setting = db.get(AppSetting, key)
    return setting.value if setting else default


def set_setting(db: Session, key: str, value: str) -> None:
    setting = db.get(AppSetting, key)
    if setting:
        setting.value = value
        setting.updated_at = utcnow()
    else:
        db.add(AppSetting(key=key, value=value, updated_at=utcnow()))


def weather_context(db: Session) -> dict:
    label = get_setting(db, "weather_label")
    latitude = get_setting(db, "weather_latitude")
    longitude = get_setting(db, "weather_longitude")
    if not latitude or not longitude:
        return {"configured": False}
    try:
        return {
            "configured": True,
            **get_forecast(float(latitude), float(longitude), label or "Local weather"),
        }
    except Exception:
        return {"configured": True, "ok": False, "label": label or "Local weather"}


def dashboard_groups(
    tasks: list[Task],
    selected_date: date,
    active_projects: set[str],
    active_task_ids: set[int],
    inactive_project_task_ids: set[int] | None = None,
    show_up_next_completed: bool = False,
    show_due_next_completed: bool = False,
) -> dict:
    def is_complete(task: Task) -> bool:
        return (task.status or "").strip().lower() == "complete"

    inactive_project_task_ids = inactive_project_task_ids or set()
    open_tasks = [task for task in tasks if not is_complete(task)]
    done_tasks = [task for task in tasks if is_complete(task)]
    project_has_incomplete: dict[str, bool] = {}
    project_progress: dict[str, dict[str, int]] = {}
    for task in tasks:
        if not task.project_name or (task.type or "chore") == "chore":
            continue
        if task.project_name not in project_has_incomplete:
            project_has_incomplete[task.project_name] = False
        if task.project_name not in project_progress:
            project_progress[task.project_name] = {"complete": 0, "total": 0, "percent": 0}
        project_progress[task.project_name]["total"] += 1
        if is_complete(task):
            project_progress[task.project_name]["complete"] += 1
        else:
            project_has_incomplete[task.project_name] = True
    for name, progress in project_progress.items():
        total = progress["total"]
        progress["percent"] = round((progress["complete"] / total) * 100) if total else 0
    daily_chores = [
        task
        for task in tasks
        if chore_visible_on(task, selected_date)
    ]
    tasks_today = [
        task
        for task in open_tasks
        if (task.type or "chore") != "chore"
        and (
            (task.id in active_task_ids)
            or (
                task.project_name
                and task.project_name in active_projects
                and task.id not in inactive_project_task_ids
            )
            or (task.due_at is not None and task.due_at.date() == selected_date)
        )
    ]
    standalone_today = [task for task in tasks_today if not task.project_name]
    project_today = [task for task in tasks_today if task.project_name]
    project_tasks = [task for task in tasks if task.project_name]
    projects = []
    for name in sorted({task.project_name for task in project_tasks if task.project_name}):
        items = sorted(
            [task for task in project_tasks if task.project_name == name],
            key=project_sort_key,
        )
        complete = len([task for task in items if task.status == "complete"])
        total = len(items)
        projects.append(
            {
                "name": name,
                "tasks": items,
                "complete": complete,
                "total": total,
                "percent": round((complete / total) * 100) if total else 0,
            }
        )
    project_today_groups = []
    for name in sorted({task.project_name for task in project_today if task.project_name}):
        items = sorted(
            [task for task in project_today if task.project_name == name],
            key=project_sort_key,
        )
        project_today_groups.append({"name": name, "tasks": items})
    three_day_start = selected_date + timedelta(days=1)
    three_day_end = selected_date + timedelta(days=3)
    working_ids = {task.id for task in tasks_today}
    up_next_all = [
        task
        for task in tasks
        if (task.type or "chore") != "chore"
        and (not task.project_name or project_has_incomplete.get(task.project_name, False))
        and task.due_at is None
        and task.id not in working_ids
    ]
    due_next_all = [
        task
        for task in tasks
        if (task.type or "chore") != "chore"
        and (not task.project_name or project_has_incomplete.get(task.project_name, False))
        and task.due_at is not None
        and three_day_start <= task.due_at.date() <= three_day_end
        and task.id not in working_ids
    ]

    def section_groups(
        section_all_tasks: list[Task],
        show_completed: bool,
        due_sort: bool = False,
    ) -> tuple[list[Task], list[dict], dict, list[Task]]:
        # Completed standalone tasks stay out of these dashboard sections.
        standalone = [
            task
            for task in section_all_tasks
            if not task.project_name and not is_complete(task)
        ]
        standalone = sorted(
            standalone,
            key=(lambda task: (task.due_at, task.created_at)) if due_sort else (lambda task: ((task.priority or 2) * -1, task.created_at)),
        )
        grouped = []
        visible = list(standalone)
        for name in sorted({task.project_name for task in section_all_tasks if task.project_name}):
            all_items = sorted([task for task in section_all_tasks if task.project_name == name], key=project_sort_key)
            shown_items = all_items if show_completed else [task for task in all_items if not is_complete(task)]
            if not shown_items:
                continue
            progress = project_progress.get(name, {"complete": 0, "total": 0, "percent": 0})
            grouped.append(
                {
                    "name": name,
                    "tasks": shown_items,
                    "complete": progress["complete"],
                    "total": progress["total"],
                    "percent": progress["percent"],
                }
            )
            visible.extend(shown_items)
        grouped.sort(key=lambda group: (-group["percent"], group["total"] - group["complete"], group["name"].lower()))
        complete_total = len([task for task in section_all_tasks if is_complete(task)])
        total = len(section_all_tasks)
        progress = {
            "complete": complete_total,
            "total": total,
            "percent": round((complete_total / total) * 100) if total else 0,
        }
        return standalone, grouped, progress, visible

    up_next_standalone, up_next_project_groups, up_next_progress, up_next = section_groups(
        up_next_all, show_up_next_completed, due_sort=False
    )
    due_next_standalone, due_next_project_groups, due_next_progress, due_next_three_days = section_groups(
        due_next_all, show_due_next_completed, due_sort=True
    )
    long_term_projects = sorted(
        [
            task
            for task in open_tasks
            if (task.type or "chore") != "chore"
            and task.project_name
            and task.due_at is not None
            and task.due_at.date() > three_day_end
            and task.id not in working_ids
        ],
        key=lambda task: (task.due_at, project_sort_key(task)),
    )
    long_term_project_groups = []
    for name in sorted({task.project_name for task in long_term_projects if task.project_name}):
        items = sorted([task for task in long_term_projects if task.project_name == name], key=project_sort_key)
        long_term_project_groups.append(
            {
                "name": name,
                "tasks": items,
                "next_due": min((task.due_at for task in items if task.due_at), default=None),
            }
        )
    naturally_today_ids = {
        task.id
        for task in open_tasks
        if (task.type or "chore") != "chore"
        and (
            (task.project_name and task.project_name in active_projects)
            or (task.due_at is not None and task.due_at.date() == selected_date)
        )
    }
    manual_active_task_ids = {
        task.id for task in tasks_today if task.id in active_task_ids and task.id not in naturally_today_ids
    }
    removable_working_task_ids = set(manual_active_task_ids)
    for task in tasks_today:
        due_today = task.due_at is not None and task.due_at.date() == selected_date
        if (
            task.project_name
            and task.project_name in active_projects
            and task.id not in active_task_ids
            and not due_today
        ):
            removable_working_task_ids.add(task.id)
    return {
        "open_tasks": open_tasks,
        "done_tasks": done_tasks,
        "daily_chores": daily_chores,
        "daily_chores_open": [task for task in daily_chores if (task.status or "incomplete") != "complete"],
        "tasks_today": tasks_today,
        "standalone_today": standalone_today,
        "project_today_groups": project_today_groups,
        "up_next": up_next,
        "due_next_three_days": due_next_three_days,
        "up_next_standalone": up_next_standalone,
        "up_next_project_groups": up_next_project_groups,
        "up_next_progress": up_next_progress,
        "show_up_next_completed": show_up_next_completed,
        "due_next_standalone": due_next_standalone,
        "due_next_project_groups": due_next_project_groups,
        "due_next_progress": due_next_progress,
        "show_due_next_completed": show_due_next_completed,
        "long_term_project_groups": long_term_project_groups,
        "projects": projects,
        "active_projects": active_projects,
        "active_task_ids": active_task_ids,
        "manual_active_task_ids": manual_active_task_ids,
        "inactive_project_task_ids": inactive_project_task_ids,
        "removable_working_task_ids": removable_working_task_ids,
    }


def task_browser_groups(tasks: list[Task]) -> dict:
    grouped = dashboard_groups(tasks, local_today(), set(), set())
    daily_ids = {task.id for task in grouped["daily_chores"]}
    today_ids = {task.id for task in grouped["tasks_today"]}
    project_ids = {task.id for project in grouped["projects"] for task in project["tasks"]}
    completed = [
        task
        for task in grouped["done_tasks"]
        if not task.project_name and (task.type or "chore") != "chore"
    ]
    completed_ids = {task.id for task in completed}
    other_tasks = [
        task
        for task in grouped["open_tasks"]
        if (task.type or "chore") != "chore"
        if task.id not in daily_ids and task.id not in today_ids and task.id not in project_ids
    ]
    return {
        "daily_chores": grouped["daily_chores"],
        "tasks_today": [
            task
            for task in grouped["tasks_today"]
            if not task.project_name and (task.type or "chore") != "chore"
        ],
        "other_tasks": other_tasks,
        "completed_tasks": completed,
        "completed_ids": completed_ids,
    }


def parse_due_at(value: str) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def chore_visible_on(task: Task, selected_date: date) -> bool:
    if (task.type or "chore") != "chore":
        return False
    frequency = task.reset_frequency or "daily"
    if frequency == "daily":
        return True
    if frequency == "weekdays":
        days = chore_weekdays(task.reset_value)
        return selected_date.weekday() in days if days else True
    return False


def parse_dashboard_date(value: str | None) -> date:
    if not value:
        return local_today()
    try:
        return date.fromisoformat(value)
    except ValueError:
        return local_today()


def date_window(selected: date) -> list[dict]:
    today = local_today()
    weekday_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    days = []
    for offset in range(-3, 6):
        current = today + timedelta(days=offset)
        if current == today:
            label = "Today"
        elif offset == -1:
            label = "Yesterday"
        elif offset == 1:
            label = "Tomorrow"
        else:
            label = short_date(datetime.combine(current, datetime.min.time()))
        days.append(
            {
                "date": current,
                "value": current.isoformat(),
                "label": label,
                "weekday": weekday_names[current.weekday()],
                "is_weekend": current.weekday() >= 5,
                "selected": current == selected,
            }
        )
    return days


def date_window_with_weather(selected: date, weather: dict) -> list[dict]:
    days = date_window(selected)
    daily = weather.get("daily", {}) if weather.get("ok") else {}
    for day in days:
        day["weather"] = daily.get(day["value"])
        high = day["weather"].get("high") if day["weather"] else None
        if high is None:
            day["temp_class"] = "temp-mild"
        elif high >= 90:
            day["temp_class"] = "temp-hot"
        elif high >= 75:
            day["temp_class"] = "temp-warm"
        elif high >= 55:
            day["temp_class"] = "temp-mild"
        else:
            day["temp_class"] = "temp-cool"
    return days


def used_project_orders(db: Session, project_name: str, exclude_task_id: int | None = None) -> set[int]:
    if not project_name:
        return set()
    stmt = select(Task.project_order).where(
        Task.project_name == project_name,
        Task.project_order > 0,
    )
    if exclude_task_id:
        stmt = stmt.where(Task.id != exclude_task_id)
    return set(db.scalars(stmt).all())


def resolve_project_order(
    db: Session,
    project_name: str,
    requested: int,
    exclude_task_id: int | None = None,
) -> int:
    requested = max(0, requested)
    if requested == 0 or not project_name:
        return 0
    used = used_project_orders(db, project_name, exclude_task_id)
    order = requested
    while order in used:
        order += 1
    return order


def next_project_order(db: Session, project_name: str) -> int:
    used = used_project_orders(db, project_name)
    order = 1
    while order in used:
        order += 1
    return order


def render_dashboard_panel(request: Request, db: Session, user: User, selected: date) -> HTMLResponse:
    tasks = task_query(db, user)
    active_projects = active_project_names(db)
    active_task_ids = active_task_ids_for_date(db, selected)
    show_up_next_completed = True
    show_due_next_completed = True
    section_state_query = ""
    groups = dashboard_groups(
        tasks,
        selected,
        active_projects,
        active_task_ids,
        inactive_project_task_ids=inactive_project_task_ids_for_date(db, selected),
        show_up_next_completed=show_up_next_completed,
        show_due_next_completed=show_due_next_completed,
    )
    completed_today = [
        {"completed_at": task.updated_at, "task": task}
        for task in sorted(
            [task for task in tasks if task.status == "complete" and task.completed_date == selected],
            key=lambda task: task.updated_at,
            reverse=True,
        )
    ]
    weather = weather_context(db)
    today = local_today()
    day_metrics = {}
    for day in date_window(selected):
        day_value = day["date"]
        metric_groups = dashboard_groups(
            tasks,
            day_value,
            active_projects,
            active_task_ids_for_date(db, day_value),
            inactive_project_task_ids=inactive_project_task_ids_for_date(db, day_value),
            show_up_next_completed=True,
            show_due_next_completed=True,
        )
        project_count = len({t.project_name for t in metric_groups["tasks_today"] if t.project_name})
        day_metrics[day["value"]] = {
            "chores": len(metric_groups["daily_chores_open"]),
            "working": len(metric_groups["tasks_today"]),
            "projects": project_count,
        }
    date_days = date_window_with_weather(selected, weather)
    for day in date_days:
        metric = day_metrics.get(day["value"], {"chores": 0, "working": 0, "projects": 0})
        day["metrics"] = metric
    return render(
        request,
        "_dashboard.html",
        {
            "user": user,
            "tasks": tasks,
            "weather": weather,
            "selected_date": selected,
            "selected_date_value": selected.isoformat(),
            "previous_date": (selected - timedelta(days=1)).isoformat(),
            "next_date": (selected + timedelta(days=1)).isoformat(),
            "is_today": selected == today,
            "is_future_selected": selected > today,
            "can_toggle_day": selected <= today,
            "date_window": date_days,
            "project_color": lambda name: project_color(db, name),
            "project_quote": project_quote,
            "priority_color": priority_color,
            "completed_today": completed_today,
            "section_state_query": section_state_query,
            **groups,
        },
    )


def normalized_task_fields(
    task_type: str,
    project_name: str,
    new_project_name: str,
    project_order: int,
    reset_frequency: str,
    reset_value: str,
    due_at: str,
    color: str,
    chore_weekdays_enabled: str = "",
    chore_weekdays_selected: list[str] | None = None,
    chore_reset_time_value: str = "04:00",
) -> dict:
    resolved_project_name = new_project_name.strip() or project_name.strip()
    if task_type == "chore":
        weekdays = [
            day for day in (chore_weekdays_selected or []) if day.isdigit() and 0 <= int(day) <= 6
        ]
        reset_time = chore_reset_time_value or "04:00"
        if chore_weekdays_enabled and weekdays:
            frequency = "weekdays"
            reset_value = f"days={','.join(sorted(weekdays, key=int))};time={reset_time}"
        else:
            frequency = "daily"
            reset_value = reset_time
        return {
            "project_name": "",
            "project_order": 0,
            "reset_frequency": frequency,
            "reset_value": reset_value,
            "due_at": None,
            "color": valid_color(color, "#ad3434"),
        }
    if task_type == "project":
        return {
            "project_name": resolved_project_name,
            "project_order": max(0, project_order),
            "reset_frequency": "none",
            "reset_value": "",
            "due_at": parse_due_at(due_at),
            "color": valid_color(color),
        }
    return {
        "project_name": "",
        "project_order": 0,
        "reset_frequency": "none",
        "reset_value": "",
        "due_at": parse_due_at(due_at),
        "color": valid_color(color),
    }


@app.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)):
    if not has_users(db):
        return redirect("/setup")
    if request.session.get("user_id"):
        return redirect("/dashboard")
    return redirect("/login")


@app.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request, db: Session = Depends(get_db)):
    if has_users(db):
        return redirect("/login")
    secret = request.session.get("setup_secret")
    username = request.session.get("setup_username", "")
    return render(
        request,
        "setup.html",
        {
            "username": username,
            "secret": secret,
            "qr": qr_data_uri(provisioning_uri(username, secret)) if secret and username else None,
            "uri": provisioning_uri(username, secret) if secret and username else None,
            "error": request.session.pop("setup_error", None),
        },
    )


@app.post("/setup/start")
def setup_start(request: Request, username: str = Form(...), db: Session = Depends(get_db)):
    if has_users(db):
        return redirect("/login")
    username = username.strip()
    if not username:
        request.session["setup_error"] = "Choose a username."
        return redirect("/setup")
    request.session["setup_username"] = username
    request.session["setup_secret"] = pyotp.random_base32()
    return redirect("/setup")


@app.post("/setup/confirm")
def setup_confirm(
    request: Request,
    code: str = Form(...),
    db: Session = Depends(get_db),
):
    if has_users(db):
        return redirect("/login")
    username = request.session.get("setup_username")
    secret = request.session.get("setup_secret")
    if not username or not secret or not verify_totp(secret, code):
        request.session["setup_error"] = "That code did not verify."
        return redirect("/setup")
    user = User(username=username, totp_secret=secret, role="admin")
    db.add(user)
    db.commit()
    db.refresh(user)
    request.session.clear()
    request.session["user_id"] = user.id
    return redirect("/dashboard")


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)):
    if not has_users(db):
        return redirect("/setup")
    return render(request, "login.html", {"error": request.session.pop("login_error", None)})


@app.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    code: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.scalar(select(User).where(func.lower(User.username) == username.strip().lower()))
    if not user or not user.active or not verify_totp(user.totp_secret, code):
        request.session["login_error"] = "Invalid username or authenticator code."
        return redirect("/login")
    request.session.clear()
    request.session["user_id"] = user.id
    return redirect("/dashboard")


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return redirect("/login")


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, user: User = Depends(get_current_user)):
    selected = parse_dashboard_date(request.query_params.get("date"))
    return render(request, "dashboard.html", {"user": user, "selected_date": selected.isoformat()})


@app.get("/app", response_class=HTMLResponse)
def task_app(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return render(request, "tasks.html", {"user": user})


@app.get("/chores", response_class=HTMLResponse)
def chores_app(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return render(request, "chores.html", {"user": user})


@app.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    user: User = Depends(get_current_user),
):
    return render(request, "settings.html", {"user": user})


@app.post("/settings")
def save_settings(
    request: Request,
    theme: str = Form("day"),
    row_mode: str = Form("normal"),
    col_mode: str = Form("normal"),
    session_highlight: str = Form("#147d74"),
    return_to: str = Form("/dashboard"),
    user: User = Depends(get_current_user),
):
    request.session["ui_theme"] = theme if theme in {"day", "night"} else "day"
    request.session["ui_row_mode"] = row_mode if row_mode in {"compact", "normal", "comfortable"} else "normal"
    request.session["ui_col_mode"] = (
        col_mode if col_mode in {"narrow_chores", "normal", "balanced"} else "normal"
    )
    request.session["ui_session_highlight"] = valid_color(session_highlight, "#147d74")
    target = return_to if return_to.startswith("/") else "/dashboard"
    return redirect(target)


@app.get("/tasks/new", response_class=HTMLResponse)
def new_task_form(
    request: Request,
    type: str = "chore",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    default_type = type if type in {"chore", "task", "project"} else "chore"
    users = db.scalars(select(User).order_by(User.username)).all()
    projects = project_names(db)
    selected_project = projects[0] if default_type == "project" and projects else ""
    return render(
        request,
        "task_editor.html",
        {
            "user": user,
            "task": None,
            "users": users,
            "projects": projects,
            "mode": "new",
            "default_type": default_type,
            "selected_project": selected_project,
            "used_orders": used_project_orders(db, selected_project),
            "suggested_order": next_project_order(db, selected_project) if selected_project else 1,
            "action": "/tasks",
            "submit_label": "Create task",
        },
    )


@app.get("/partials/tasks", response_class=HTMLResponse)
def task_partial(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tasks = task_query(db, user)
    users = db.scalars(select(User).order_by(User.username)).all()
    projects = project_names(db)
    groups = task_browser_groups(tasks)
    active_task_ids = active_task_ids_for_date(db, local_today())
    return render(
        request,
        "_tasks.html",
        {
            "user": user,
            "tasks": tasks,
            "users": users,
            "projects": projects,
            "active_task_ids": active_task_ids,
            **groups,
        },
    )


@app.get("/partials/chores", response_class=HTMLResponse)
def chores_partial(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return render_chore_list(request, db, user)


@app.get("/partials/dashboard", response_class=HTMLResponse)
def dashboard_partial(
    request: Request,
    date: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    selected = parse_dashboard_date(date)
    return render_dashboard_panel(request, db, user, selected)


@app.get("/projects", response_class=HTMLResponse)
def projects_page(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tasks = task_query(db, user)
    active_projects = active_project_names(db)
    today = local_today()
    active_task_ids = active_task_ids_for_date(db, today)
    groups = dashboard_groups(tasks, today, active_projects, active_task_ids)
    return render(
        request,
        "projects.html",
        {
            "user": user,
            "projects": groups["projects"],
            "active_projects": active_projects,
            "active_task_ids": active_task_ids,
            "project_quote": project_quote,
            "project_color": lambda name: project_color(db, name),
        },
    )


@app.post("/projects/{project_name}/toggle")
async def toggle_project_active(
    project_name: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    active_projects = active_project_names(db)
    if project_name in active_projects:
        active_projects.remove(project_name)
    else:
        active_projects.add(project_name)
    save_active_project_names(db, active_projects)
    db.commit()
    await manager.broadcast("tasks_changed")
    return redirect("/projects")


@app.post("/projects/{project_name}/color")
async def update_project_color(
    project_name: str,
    color: str = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    set_setting(db, f"project_color:{project_name}", valid_color(color))
    db.commit()
    await manager.broadcast("tasks_changed")
    return redirect("/projects")


@app.post("/projects/{project_name}/due-date")
async def update_project_due_date(
    project_name: str,
    due_at: str = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    due_value = parse_due_at(due_at)
    now = utcnow()
    tasks = db.scalars(
        select(Task).where(
            Task.project_name == project_name,
            Task.status != "complete",
        )
    ).all()
    for task in tasks:
        task.due_at = due_value
        task.updated_at = now
    db.commit()
    await manager.broadcast("tasks_changed")
    return redirect("/projects")


@app.post("/projects/tasks/{task_id}/activate")
async def toggle_project_task_active_today(
    task_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = db.get(Task, task_id)
    if not task:
        return redirect("/projects")
    today = local_today()
    active_task_ids = active_task_ids_for_date(db, today)
    if task_id in active_task_ids:
        active_task_ids.remove(task_id)
    else:
        active_task_ids.add(task_id)
    save_active_task_ids_for_date(db, today, active_task_ids)
    db.commit()
    await manager.broadcast("tasks_changed")
    return redirect("/projects")


@app.post("/projects/tasks/{task_id}/toggle")
async def toggle_project_task(
    task_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = db.get(Task, task_id)
    if task and task.project_name:
        now = utcnow()
        task.status = "incomplete" if task.status == "complete" else "complete"
        task.updated_at = now
        if task.status == "complete":
            task.completed_date = local_today()
            db.add(TaskHistory(task_id=task.id, completed_by=user.id, completed_at=now))
        else:
            task.completed_date = None
        db.commit()
        await manager.broadcast("tasks_changed")
    return redirect("/projects")


@app.post("/projects/tasks/{task_id}/delete")
async def delete_project_task(
    task_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = db.get(Task, task_id)
    if task and task.project_name:
        db.delete(task)
        db.commit()
        await manager.broadcast("tasks_changed")
    return redirect("/projects")


@app.post("/projects/tasks/{task_id}/move")
async def move_project_task(
    task_id: int,
    direction: str = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = db.get(Task, task_id)
    if not task or not task.project_name or task.project_order <= 0:
        return redirect("/projects")
    ordered = db.scalars(
        select(Task).where(
            Task.project_name == task.project_name,
            Task.project_order > 0,
        )
    ).all()
    ordered = sorted(ordered, key=project_sort_key)
    index = next((idx for idx, item in enumerate(ordered) if item.id == task.id), None)
    if index is None:
        return redirect("/projects")
    swap_index = index - 1 if direction == "up" else index + 1
    if swap_index < 0 or swap_index >= len(ordered):
        return redirect("/projects")
    other = ordered[swap_index]
    task.project_order, other.project_order = other.project_order, task.project_order
    task.updated_at = utcnow()
    other.updated_at = utcnow()
    db.commit()
    await manager.broadcast("tasks_changed")
    return redirect("/projects")


@app.post("/tasks/{task_id}/schedule")
async def schedule_task(
    request: Request,
    task_id: int,
    due_at: str = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = db.get(Task, task_id)
    if task:
        task.due_at = parse_due_at(due_at)
        task.updated_at = utcnow()
        db.commit()
        await manager.broadcast("tasks_changed")
    dashboard_date = request.query_params.get("date") or local_today().isoformat()
    return redirect(f"/dashboard?date={dashboard_date}")


@app.post("/dashboard/tasks/{task_id}/toggle", response_class=HTMLResponse)
async def dashboard_toggle_task(
    request: Request,
    task_id: int,
    date: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = db.get(Task, task_id)
    selected = parse_dashboard_date(date)
    if selected > local_today():
        return render_dashboard_panel(request, db, user, selected)
    if task:
        now = utcnow()
        task.status = "incomplete" if task.status == "complete" else "complete"
        task.updated_at = now
        if task.status == "complete":
            task.completed_date = selected
            db.add(TaskHistory(task_id=task.id, completed_by=user.id, completed_at=now))
        else:
            task.completed_date = None
        db.commit()
        await manager.broadcast("tasks_changed")
    return render_dashboard_panel(request, db, user, selected)


@app.post("/dashboard/tasks/{task_id}/activate", response_class=HTMLResponse)
async def dashboard_toggle_task_active_today(
    request: Request,
    task_id: int,
    date: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = db.get(Task, task_id)
    selected = parse_dashboard_date(date)
    if selected > local_today():
        return render_dashboard_panel(request, db, user, selected)
    if task and (task.type or "chore") != "chore":
        active_task_ids = active_task_ids_for_date(db, selected)
        inactive_project_task_ids = inactive_project_task_ids_for_date(db, selected)
        active_projects = active_project_names(db)
        due_today = task.due_at is not None and task.due_at.date() == selected
        from_active_project = bool(task.project_name and task.project_name in active_projects)

        if task_id in active_task_ids:
            active_task_ids.remove(task_id)
        elif from_active_project and not due_today:
            if task_id in inactive_project_task_ids:
                inactive_project_task_ids.remove(task_id)
            else:
                inactive_project_task_ids.add(task_id)
        else:
            active_task_ids.add(task_id)

        if task.project_name and task.project_name in active_projects:
            open_project_tasks = db.scalars(
                select(Task).where(
                    Task.project_name == task.project_name,
                    Task.status != "complete",
                )
            ).all()
            any_active_for_project = any(
                (
                    project_task.id in active_task_ids
                    or (
                        project_task.due_at is not None
                        and project_task.due_at.date() == selected
                    )
                    or project_task.id not in inactive_project_task_ids
                )
                for project_task in open_project_tasks
                if (project_task.type or "chore") != "chore"
            )
            if not any_active_for_project:
                active_projects.remove(task.project_name)
                save_active_project_names(db, active_projects)

        save_active_task_ids_for_date(db, selected, active_task_ids)
        save_inactive_project_task_ids_for_date(db, selected, inactive_project_task_ids)
        db.commit()
        await manager.broadcast("tasks_changed")
    return render_dashboard_panel(request, db, user, selected)


@app.post("/dashboard/projects/{project_name}/activate", response_class=HTMLResponse)
async def dashboard_toggle_project_active(
    request: Request,
    project_name: str,
    date: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    selected = parse_dashboard_date(date)
    if selected > local_today():
        return render_dashboard_panel(request, db, user, selected)
    active_projects = active_project_names(db)
    inactive_project_task_ids = inactive_project_task_ids_for_date(db, selected)
    if project_name in active_projects:
        active_projects.remove(project_name)
        project_task_ids = set(
            db.scalars(select(Task.id).where(Task.project_name == project_name)).all()
        )
        inactive_project_task_ids -= project_task_ids
    else:
        active_projects.add(project_name)
    save_active_project_names(db, active_projects)
    save_inactive_project_task_ids_for_date(db, selected, inactive_project_task_ids)
    db.commit()
    await manager.broadcast("tasks_changed")
    return render_dashboard_panel(request, db, user, selected)


@app.post("/dashboard/chores/reset", response_class=HTMLResponse)
async def dashboard_chore_reset(
    request: Request,
    date: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if parse_dashboard_date(date) > local_today():
        return render_dashboard_panel(request, db, user, parse_dashboard_date(date))
    now = utcnow()
    chores = db.scalars(
        select(Task).where(Task.type == "chore", Task.reset_frequency == "daily")
    ).all()
    for chore in chores:
        chore.status = "incomplete"
        chore.completed_date = None
        chore.next_reset_at = next_reset_time(chore.reset_frequency, chore.reset_value, now=now)
        chore.updated_at = now
    db.commit()
    await manager.broadcast("tasks_changed")
    return render_dashboard_panel(request, db, user, parse_dashboard_date(date))


@app.post("/weather/search", response_class=HTMLResponse)
def weather_search(
    request: Request,
    date: str | None = None,
    query: str = Form(""),
    user: User = Depends(get_current_user),
):
    try:
        locations = search_locations(query)
        error = None if locations else "No matches found."
    except Exception:
        locations = []
        error = "Location search is unavailable."
    return render(
        request,
        "_weather_picker.html",
        {
            "user": user,
            "query": query,
            "locations": locations,
            "error": error,
            "selected_date_value": parse_dashboard_date(date).isoformat(),
        },
    )


@app.post("/weather/location", response_class=HTMLResponse)
async def weather_location(
    request: Request,
    label: str = Form(...),
    latitude: str = Form(...),
    longitude: str = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    set_setting(db, "weather_label", label.strip())
    set_setting(db, "weather_latitude", latitude)
    set_setting(db, "weather_longitude", longitude)
    db.commit()
    await manager.broadcast("tasks_changed")
    selected = parse_dashboard_date(request.query_params.get("date"))
    return render_dashboard_panel(request, db, user, selected)


@app.post("/tasks")
async def create_task(
    title: str = Form(...),
    description: str = Form(""),
    type: str = Form("chore"),
    priority: int = Form(2),
    assigned_to: str = Form(""),
    project_name: str = Form(""),
    new_project_name: str = Form(""),
    project_order: int = Form(0),
    reset_frequency: str = Form("daily"),
    reset_value: str = Form(""),
    due_at: str = Form(""),
    color: str = Form("#147d74"),
    chore_weekdays_enabled: str = Form(""),
    chore_weekdays: list[str] = Form([]),
    chore_reset_time: str = Form("04:00"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    now = utcnow()
    fields = normalized_task_fields(
        type,
        project_name,
        new_project_name,
        project_order,
        reset_frequency,
        reset_value,
        due_at,
        color,
        chore_weekdays_enabled,
        chore_weekdays,
        chore_reset_time,
    )
    if type == "project":
        fields["project_order"] = resolve_project_order(
            db,
            fields["project_name"],
            fields["project_order"],
        )
    task = Task(
        title=title.strip(),
        description=description.strip(),
        type=type,
        priority=priority,
        color=fields["color"],
        assigned_to=int(assigned_to) if assigned_to else None,
        project_name=fields["project_name"],
        project_order=fields["project_order"],
        reset_frequency=fields["reset_frequency"],
        reset_value=fields["reset_value"],
        due_at=fields["due_at"],
        next_reset_at=next_reset_time(fields["reset_frequency"], fields["reset_value"], now=now),
        created_at=now,
        updated_at=now,
    )
    db.add(task)
    db.commit()
    await manager.broadcast("tasks_changed")
    if type == "chore":
        return redirect("/chores")
    if type == "project":
        return redirect("/projects")
    return redirect("/app")


@app.get("/tasks/{task_id}/edit", response_class=HTMLResponse)
def edit_task_form(
    request: Request,
    task_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = db.get(Task, task_id)
    if not task:
        return redirect("/app")
    users = db.scalars(select(User).order_by(User.username)).all()
    projects = project_names(db)
    if task.project_name and task.project_name not in projects:
        projects.append(task.project_name)
        projects.sort()
    selected_project = task.project_name or (projects[0] if projects else "")
    return render(
        request,
        "task_editor.html",
        {
            "user": user,
            "task": task,
            "users": users,
            "projects": projects,
            "mode": "edit",
            "default_type": task.type or "task",
            "selected_project": selected_project,
            "used_orders": used_project_orders(db, selected_project, task.id),
            "suggested_order": task.project_order or 0,
            "action": f"/tasks/{task.id}/edit",
            "submit_label": "Save task",
        },
    )


@app.post("/tasks/{task_id}/edit")
async def update_task(
    request: Request,
    task_id: int,
    title: str = Form(...),
    description: str = Form(""),
    type: str = Form("chore"),
    status: str = Form("incomplete"),
    priority: int = Form(2),
    assigned_to: str = Form(""),
    project_name: str = Form(""),
    new_project_name: str = Form(""),
    project_order: int = Form(0),
    reset_frequency: str = Form("daily"),
    reset_value: str = Form(""),
    due_at: str = Form(""),
    color: str = Form("#147d74"),
    chore_weekdays_enabled: str = Form(""),
    chore_weekdays: list[str] = Form([]),
    chore_reset_time: str = Form("04:00"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = db.get(Task, task_id)
    if task:
        now = utcnow()
        fields = normalized_task_fields(
            type,
            project_name,
            new_project_name,
            project_order,
            reset_frequency,
            reset_value,
            due_at,
            color,
            chore_weekdays_enabled,
            chore_weekdays,
            chore_reset_time,
        )
        if type == "project":
            fields["project_order"] = resolve_project_order(
                db,
                fields["project_name"],
                fields["project_order"],
                task_id,
            )
        task.title = title.strip()
        task.description = description.strip()
        task.type = type
        task.status = status
        if status == "complete":
            task.completed_date = task.completed_date or now.date()
        else:
            task.completed_date = None
        task.priority = priority
        task.color = fields["color"]
        task.assigned_to = int(assigned_to) if assigned_to else None
        task.project_name = fields["project_name"]
        task.project_order = fields["project_order"]
        task.reset_frequency = fields["reset_frequency"]
        task.reset_value = fields["reset_value"]
        task.due_at = fields["due_at"]
        task.next_reset_at = next_reset_time(fields["reset_frequency"], fields["reset_value"], now=now)
        task.updated_at = now
        db.commit()
        await manager.broadcast("tasks_changed")
    if request.headers.get("hx-request"):
        return render_task_list(request, db, user)
    return redirect("/app")


@app.post("/tasks/{task_id}/toggle")
async def toggle_task(
    request: Request,
    task_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = db.get(Task, task_id)
    if task:
        now = utcnow()
        task.status = "incomplete" if task.status == "complete" else "complete"
        task.updated_at = now
        if task.status == "complete":
            task.completed_date = local_today()
            db.add(TaskHistory(task_id=task.id, completed_by=user.id, completed_at=now))
        else:
            task.completed_date = None
        db.commit()
        await manager.broadcast("tasks_changed")
    if request.headers.get("hx-request"):
        if request.headers.get("hx-target") in {"chore-list", "#chore-list"}:
            return render_chore_list(request, db, user)
        return render_task_list(request, db, user)
    return redirect("/app")


@app.post("/tasks/{task_id}/activate")
async def toggle_task_active_today(
    request: Request,
    task_id: int,
    date: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = db.get(Task, task_id)
    if task and (task.type or "chore") != "chore":
        selected = parse_dashboard_date(date)
        active_task_ids = active_task_ids_for_date(db, selected)
        if task_id in active_task_ids:
            active_task_ids.remove(task_id)
        else:
            active_task_ids.add(task_id)
        save_active_task_ids_for_date(db, selected, active_task_ids)
        db.commit()
        await manager.broadcast("tasks_changed")
    if request.headers.get("hx-request"):
        if request.headers.get("hx-target") in {"chore-list", "#chore-list"}:
            return render_chore_list(request, db, user)
        return render_task_list(request, db, user)
    return redirect("/app")


@app.post("/tasks/{task_id}/deactivate")
async def deactivate_task_today(
    request: Request,
    task_id: int,
    date: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    selected = parse_dashboard_date(date)
    active_task_ids = active_task_ids_for_date(db, selected)
    if task_id in active_task_ids:
        active_task_ids.remove(task_id)
        save_active_task_ids_for_date(db, selected, active_task_ids)
        db.commit()
        await manager.broadcast("tasks_changed")
    if request.headers.get("hx-request"):
        if request.headers.get("hx-target") in {"chore-list", "#chore-list"}:
            return render_chore_list(request, db, user)
        return render_task_list(request, db, user)
    return redirect("/app")


@app.post("/chores/reset")
async def manual_chore_reset(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    now = utcnow()
    chores = db.scalars(
        select(Task).where(Task.type == "chore", Task.reset_frequency == "daily")
    ).all()
    for chore in chores:
        chore.status = "incomplete"
        chore.completed_date = None
        chore.next_reset_at = next_reset_time(chore.reset_frequency, chore.reset_value, now=now)
        chore.updated_at = now
    db.commit()
    await manager.broadcast("tasks_changed")
    if request.headers.get("hx-request"):
        return render_chore_list(request, db, user)
    return redirect("/chores")


@app.post("/tasks/{task_id}/delete")
async def delete_task(
    request: Request,
    task_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = db.get(Task, task_id)
    if task:
        db.delete(task)
        db.commit()
        await manager.broadcast("tasks_changed")
    if request.headers.get("hx-request"):
        if request.headers.get("hx-target") in {"chore-list", "#chore-list"}:
            return render_chore_list(request, db, user)
        return render_task_list(request, db, user)
    return redirect("/app")


@app.get("/users", response_class=HTMLResponse)
def users_page(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    users = db.scalars(select(User).order_by(User.username)).all()
    pending = request.session.get("new_user_secret")
    pending_username = request.session.get("new_user_username")
    return render(
        request,
        "users.html",
        {
            "user": user,
            "users": users,
            "pending_username": pending_username,
            "pending_secret": pending,
            "qr": qr_data_uri(provisioning_uri(pending_username, pending))
            if pending and pending_username
            else None,
            "uri": provisioning_uri(pending_username, pending)
            if pending and pending_username
            else None,
            "error": request.session.pop("user_error", None),
        },
    )


@app.post("/users/start")
def user_start(
    request: Request,
    username: str = Form(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    username = username.strip()
    exists = db.scalar(select(User).where(func.lower(User.username) == username.lower()))
    if not username or exists:
        request.session["user_error"] = "Choose an unused username."
        return redirect("/users")
    request.session["new_user_username"] = username
    request.session["new_user_secret"] = pyotp.random_base32()
    return redirect("/users")


@app.post("/users/confirm")
def user_confirm(
    request: Request,
    code: str = Form(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    username = request.session.get("new_user_username")
    secret = request.session.get("new_user_secret")
    if not username or not secret or not verify_totp(secret, code):
        request.session["user_error"] = "That code did not verify."
        return redirect("/users")
    db.add(User(username=username, totp_secret=secret, role="user"))
    db.commit()
    request.session.pop("new_user_username", None)
    request.session.pop("new_user_secret", None)
    return redirect("/users")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
