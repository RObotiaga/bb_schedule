from __future__ import annotations

import os
from datetime import datetime

from fastapi import Body, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse, Response

from app.core.config import BASE_DIR
from app.core.database import get_db_connection
from app.core.repositories.analytics import get_usage_statistics, record_usage_event
from app.web.app import app, get_current_user, require_admin, _schedule_for_group


_ENHANCEMENTS_PATH = os.path.join(BASE_DIR, "app", "web", "miniapp_enhancements.js")
_ENHANCEMENTS_TAG = '<script src="/miniapp-enhancements.js?v=2"></script>'


@app.get("/api/schedule/range")
async def api_schedule_range(group: str):
    """Return the factual date bounds stored in DB for one group.

    This endpoint never generates dates or predicts lessons. The client uses
    the bounds only to build a scrollable calendar navigator, while every
    selected day is queried from the schedule table separately.
    """
    group = group.strip()
    if not group:
        raise HTTPException(status_code=400, detail="group is required")

    db = await get_db_connection()
    async with db.execute(
        """
        SELECT MIN(lesson_date) AS min_date, MAX(lesson_date) AS max_date
        FROM schedule
        WHERE group_name = ?
        """,
        (group,),
    ) as cursor:
        row = await cursor.fetchone()

    return {
        "group": group,
        "min_date": row["min_date"] if row else None,
        "max_date": row["max_date"] if row else None,
    }


@app.get("/api/schedule/by-date")
async def api_schedule_by_date(
    group: str,
    date_value: str,
    include_subscriptions: bool = True,
    x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"),
):
    """Return only rows actually stored for an exact calendar date."""
    group = group.strip()
    if not group:
        raise HTTPException(status_code=400, detail="group is required")
    try:
        datetime.strptime(date_value, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="date_value must be YYYY-MM-DD") from exc

    user = None
    if x_telegram_init_data:
        user = await get_current_user(x_telegram_init_data)

    days = await _schedule_for_group(
        group,
        date_value,
        user["id"] if user and include_subscriptions else None,
    )
    return {"group": group, "date": date_value, "days": days}


@app.post("/api/analytics/event")
async def api_record_analytics_event(
    payload: dict = Body(...),
    user: dict = Depends(get_current_user),
):
    feature = str(payload.get("feature", "")).strip()
    if not feature:
        raise HTTPException(status_code=400, detail="feature is required")
    try:
        await record_usage_event(user["id"], feature)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="unknown feature") from exc
    return {"status": "ok"}


@app.get("/api/admin/analytics")
async def api_admin_analytics(admin: dict = Depends(require_admin)):
    await record_usage_event(admin["id"], "admin_analytics_view")
    return await get_usage_statistics()


@app.get("/miniapp-enhancements.js", include_in_schema=False)
async def miniapp_enhancements_script():
    return FileResponse(
        _ENHANCEMENTS_PATH,
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )


@app.middleware("http")
async def inject_miniapp_enhancements(request: Request, call_next):
    response = await call_next(request)
    if request.url.path != "/" or response.status_code != 200:
        return response

    content_type = response.headers.get("content-type", "")
    if "text/html" not in content_type:
        return response

    chunks = [chunk async for chunk in response.body_iterator]
    body = b"".join(chunks)
    text = body.decode("utf-8")
    if _ENHANCEMENTS_TAG not in text:
        text = text.replace("</body>", f"{_ENHANCEMENTS_TAG}</body>")

    headers = dict(response.headers)
    headers.pop("content-length", None)
    return Response(
        content=text,
        status_code=response.status_code,
        headers=headers,
        media_type="text/html",
        background=response.background,
    )
