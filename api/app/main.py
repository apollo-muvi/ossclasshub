"""ClassHub OSS — Main FastAPI application (single-instance, no tenant)"""
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fastapi import (
    FastAPI, Depends, HTTPException, Request, UploadFile, File, Form, APIRouter, Query
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel
from typing import Optional

from app.config import get_settings
from app.db import get_db, _now, _gen_id
from app.security import create_token, gen_invite_code
from app.auth import get_admin_user, get_optional_parent
from app.google_drive import (
    GOOGLE_DRIVE_PROVIDER,
    ExternalIntegrationRepository,
    GoogleDriveApiError,
    GoogleDriveAuthError,
    GoogleDriveConfigError,
    GoogleDriveError,
    create_google_credential_provider,
)
from app.storage import ImageFile, StorageContext, get_storage_registry

import bcrypt


def hash_pw(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_pw(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


app = FastAPI(title="ClassHub OSS", version="0.1.0")
router = APIRouter(prefix="/api/v1")

s = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=s.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

Path(s.upload_dir).mkdir(parents=True, exist_ok=True)


# ============================================================
# Models
# ============================================================
class LoginReq(BaseModel):
    username: str
    password: str

class ChangePasswordReq(BaseModel):
    old_password: str
    new_password: str

class ClassCreate(BaseModel):
    name: str
    description: str = ""

class StudentCreate(BaseModel):
    name: str
    student_code: str = ""

class ParentCreate(BaseModel):
    display_name: str
    phone: str = ""

class PostCreate(BaseModel):
    class_id: str
    student_id: Optional[str] = None
    category: str = "announcement"
    title: str
    content: str
    need_confirm: bool = False
    require_confirmation: Optional[bool] = None
    post_date: Optional[str] = None

class ParentInviteReq(BaseModel):
    class_id: Optional[str] = None
    student_id: Optional[str] = None
    parent_id: Optional[str] = None
    new_student_name: Optional[str] = None
    student_name: Optional[str] = None
    guardian_name: str = ""
    parent_name: str = ""
    guardian_phone: str = ""
    phone: str = ""

class ParentBindReq(BaseModel):
    code: Optional[str] = None
    invite_token: Optional[str] = None
    guardian_name: str = ""

class ReplyCreate(BaseModel):
    content: str = ""
    body: str = ""
    student_id: Optional[str] = None

class FeedbackReq(BaseModel):
    body: str

class SetupReq(BaseModel):
    username: str
    password: str

class StorageSettingsReq(BaseModel):
    provider: str


def _owner_id_from_user(user) -> str:
    return user if isinstance(user, str) else user.get("username", "admin")


def _google_drive_http_error(exc: GoogleDriveError) -> HTTPException:
    if isinstance(exc, GoogleDriveConfigError):
        return HTTPException(503, str(exc))
    if isinstance(exc, GoogleDriveAuthError):
        return HTTPException(409, str(exc))
    if isinstance(exc, GoogleDriveApiError):
        return HTTPException(502, str(exc))
    return HTTPException(500, "Google Drive integration error")


def _current_image_storage_provider(db) -> str:
    provider = db.get_setting("image_storage_provider", s.image_storage_provider)
    return provider if provider in {"local", GOOGLE_DRIVE_PROVIDER} else "local"


def _require_google_drive_connected(db, owner_id: str) -> None:
    repo = ExternalIntegrationRepository(db)
    integration = repo.get(owner_id, GOOGLE_DRIVE_PROVIDER)
    if not integration or integration.status != "connected":
        raise HTTPException(409, "Google Drive must be connected before selecting it for image storage")


async def _do_delete_posts(db, ids):
    registry = get_storage_registry()
    image_rows = []
    with db.conn() as c:
        for pid in ids:
            imgs = c.execute(
                "SELECT storage_key, provider, owner_id FROM post_images WHERE post_id=?", (pid,)
            ).fetchall()
            for img in imgs:
                image_rows.append(dict(img))
    for img in image_rows:
        try:
            provider = img["provider"] or "local"
            adapter = registry.get(provider)
            if adapter and img["storage_key"]:
                await adapter.delete(
                    StorageContext(owner_id=img["owner_id"] or ""),
                    img["storage_key"],
                )
        except Exception:
            pass
    with db.conn() as c:
        for pid in ids:
            c.execute("DELETE FROM post_recipients WHERE post_id=?", (pid,))
            c.execute("DELETE FROM post_replies WHERE post_id=?", (pid,))
            c.execute("DELETE FROM parent_notifications WHERE post_id=?", (pid,))
            c.execute("DELETE FROM post_images WHERE post_id=?", (pid,))
            c.execute("DELETE FROM posts WHERE id=?", (pid,))


# ============================================================
# System endpoints
# ============================================================
# Version probe (no /v1 prefix) — used by apiClient getApiVersion()
@app.get("/api/app-settings")
async def app_settings_probe():
    return {"api_version": s.api_version, "app_name": s.app_name, "app_version": s.app_version}

@router.get("/app-settings")
async def app_settings():
    return {
        "api_version": s.api_version,
        "app_name": s.app_name,
        "app_version": s.app_version,
    }


@router.get("/integrations/google-drive/connect")
async def google_drive_connect(as_json: bool = Query(False), user=Depends(get_admin_user)):
    db = get_db()
    owner_id = _owner_id_from_user(user)
    provider = create_google_credential_provider(s, db)
    try:
        url = provider.build_authorization_url(owner_id)
    except GoogleDriveError as exc:
        raise _google_drive_http_error(exc)
    if as_json:
        return {"url": url, "provider": GOOGLE_DRIVE_PROVIDER}
    return RedirectResponse(url)


@router.get("/integrations/google-drive/callback")
async def google_drive_callback(
    code: str = Query(""),
    state: str = Query(""),
    error: str = Query(""),
):
    if error:
        raise HTTPException(400, error)
    if not code or not state:
        raise HTTPException(400, "missing Google Drive OAuth code or state")
    db = get_db()
    provider = create_google_credential_provider(s, db)
    try:
        owner_id = provider.owner_from_state(state)
        integration = await provider.connect_with_code(owner_id, code)
    except GoogleDriveError as exc:
        raise _google_drive_http_error(exc)
    return {
        "ok": True,
        "provider": integration.provider,
        "status": integration.status,
        "owner_id": integration.owner_id,
    }


@router.get("/integrations/google-drive/status")
async def google_drive_status(user=Depends(get_admin_user)):
    db = get_db()
    owner_id = _owner_id_from_user(user)
    provider = create_google_credential_provider(s, db)
    config_status = provider.config_status()
    repo = ExternalIntegrationRepository(db)
    integration = repo.get(owner_id, GOOGLE_DRIVE_PROVIDER)
    if not integration:
        return {
            "provider": GOOGLE_DRIVE_PROVIDER,
            "status": "disconnected",
            "connected": False,
            "owner_id": owner_id,
            "image_storage_provider": _current_image_storage_provider(db),
            "config": config_status,
        }
    return {
        "provider": integration.provider,
        "status": integration.status,
        "connected": integration.status == "connected",
        "owner_id": owner_id,
        "scopes": integration.scopes,
        "external_account_id": integration.external_account_id,
        "folder_id": integration.config.get("folder_id", ""),
        "last_error": integration.last_error,
        "image_storage_provider": _current_image_storage_provider(db),
        "config": config_status,
    }


@router.post("/integrations/google-drive/disconnect")
async def google_drive_disconnect(revoke: bool = Query(False), user=Depends(get_admin_user)):
    db = get_db()
    owner_id = _owner_id_from_user(user)
    provider = create_google_credential_provider(s, db)
    try:
        revoked = await provider.disconnect(owner_id, revoke=revoke)
    except GoogleDriveError as exc:
        raise _google_drive_http_error(exc)
    if _current_image_storage_provider(db) == GOOGLE_DRIVE_PROVIDER:
        db.set_setting("image_storage_provider", "local")
    return {
        "ok": True,
        "provider": GOOGLE_DRIVE_PROVIDER,
        "status": "disconnected",
        "revoked": revoked,
    }


@router.get("/storage/settings")
async def storage_settings(user=Depends(get_admin_user)):
    db = get_db()
    return {
        "provider": _current_image_storage_provider(db),
        "available_providers": ["local", GOOGLE_DRIVE_PROVIDER],
        "default_provider": s.image_storage_provider,
    }


@router.put("/storage/settings")
async def update_storage_settings(req: StorageSettingsReq, user=Depends(get_admin_user)):
    db = get_db()
    owner_id = _owner_id_from_user(user)
    provider = req.provider.strip()
    if provider not in {"local", GOOGLE_DRIVE_PROVIDER}:
        raise HTTPException(422, "unsupported image storage provider")
    if provider == GOOGLE_DRIVE_PROVIDER:
        _require_google_drive_connected(db, owner_id)
    db.set_setting("image_storage_provider", provider)
    return {
        "ok": True,
        "provider": provider,
        "available_providers": ["local", GOOGLE_DRIVE_PROVIDER],
    }


@router.get("/health")
async def health():
    db = get_db()
    return {
        "status": "ok",
        "setup_completed": db.admin_exists(),
        "schema_version": db.get_setting("schema_version", "0"),
    }


@router.post("/setup")
async def setup(req: SetupReq):
    db = get_db()
    if db.admin_exists():
        raise HTTPException(409, "Setup already completed")
    if len(req.username) < 2:
        raise HTTPException(422, "Username too short")
    if len(req.password) < 8:
        raise HTTPException(422, "Password must be at least 8 characters")
    db.create_admin(req.username, hash_pw(req.password))
    db.set_setting("schema_version", "1")
    db.set_setting("created_at", _now())
    return {"ok": True, "username": req.username}


# ============================================================
# Auth
# ============================================================
@router.post("/auth/login")
async def login(req: LoginReq):
    db = get_db()
    admin = db.get_admin(req.username)
    if not admin or not verify_pw(req.password, admin["password_hash"]):
        raise HTTPException(401, "帳號或密碼錯誤")
    token = create_token(admin["username"])
    return {"token": token, "access_token": token, "username": admin["username"]}


@router.post("/auth/change-password")
async def change_password(req: ChangePasswordReq, user=Depends(get_admin_user)):
    db = get_db()
    admin = db.get_first_admin()
    if admin is None or not verify_pw(req.old_password, admin["password_hash"]):
        raise HTTPException(403, "舊密碼不正確")
    if len(req.new_password) < 8:
        raise HTTPException(422, "新密碼至少 8 位")
    with db.conn() as c:
        c.execute(
            "UPDATE admins SET password_hash=? WHERE username=?",
            (hash_pw(req.new_password), admin["username"]),
        )
    return {"ok": True}


# ============================================================
# Classes
# ============================================================
@router.get("/classes")
async def list_classes(user=Depends(get_admin_user)):
    db = get_db()
    with db.conn() as c:
        rows = c.execute("SELECT * FROM classes ORDER BY created_at DESC").fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["active"] = bool(d["active"])
            cnt = c.execute(
                "SELECT COUNT(*) as n FROM memberships WHERE class_id=?", (r["id"],)
            ).fetchone()
            d["student_count"] = cnt["n"]
            result.append(d)
        return {"items": result}


@router.post("/classes")
async def create_class(req: ClassCreate, user=Depends(get_admin_user)):
    db = get_db()
    cid = _gen_id("cls")
    with db.conn() as c:
        c.execute(
            "INSERT INTO classes(id, name, description, active, created_at) VALUES(?,?,?,?,?)",
            (cid, req.name, req.description, 1, _now()),
        )
    return {"id": cid, "name": req.name, "description": req.description, "active": True}


@router.delete("/classes/{class_id}")
async def delete_class(class_id: str, user=Depends(get_admin_user)):
    db = get_db()
    with db.conn() as c:
        c.execute("DELETE FROM classes WHERE id=?", (class_id,))
    return {"ok": True}


# ============================================================
# Students
# ============================================================
@router.get("/students")
async def list_students(user=Depends(get_admin_user)):
    db = get_db()
    with db.conn() as c:
        rows = c.execute("SELECT * FROM students ORDER BY created_at DESC").fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["active"] = bool(d["active"])
            classes = c.execute(
                "SELECT c.id, c.name FROM memberships m JOIN classes c ON m.class_id=c.id "
                "WHERE m.student_id=? AND m.guardian_id IS NULL",
                (r["id"],),
            ).fetchall()
            d["classes"] = [x["name"] for x in classes]
            d["class_ids"] = [x["id"] for x in classes]
            result.append(d)
        return {"items": result}


@router.post("/students")
async def create_student(req: StudentCreate, user=Depends(get_admin_user)):
    db = get_db()
    with db.conn() as c:
        count = c.execute("SELECT COUNT(*) as n FROM students").fetchone()
        if count["n"] >= s.max_students:
            raise HTTPException(409, f"已達學生上限 {s.max_students} 人")
        sid = _gen_id("stu")
        c.execute(
            "INSERT INTO students(id, name, student_code, active, created_at) VALUES(?,?,?,?,?)",
            (sid, req.name, req.student_code, 1, _now()),
        )
    return {"id": sid, "name": req.name, "student_code": req.student_code, "active": True}


@router.patch("/students/{student_id}")
async def update_student(student_id: str, req: dict, user=Depends(get_admin_user)):
    db = get_db()
    with db.conn() as c:
        row = c.execute("SELECT * FROM students WHERE id=?", (student_id,)).fetchone()
        if not row:
            raise HTTPException(404, "學生不存在")
        name = req.get("name", row["name"])
        code = req.get("student_code", row["student_code"])
        active = 1 if req.get("active", bool(row["active"])) else 0
        c.execute(
            "UPDATE students SET name=?, student_code=?, active=? WHERE id=?",
            (name, code, active, student_id),
        )
    return {"ok": True}


@router.delete("/students/{student_id}")
async def delete_student(student_id: str, user=Depends(get_admin_user)):
    db = get_db()
    with db.conn() as c:
        c.execute("DELETE FROM students WHERE id=?", (student_id,))
    return {"ok": True}


# ============================================================
# Parents / Guardians
# ============================================================
@router.get("/parents")
async def list_parents(user=Depends(get_admin_user)):
    db = get_db()
    with db.conn() as c:
        rows = c.execute("SELECT * FROM guardians ORDER BY created_at DESC").fetchall()
        result = []
        for r in rows:
            students = c.execute(
                "SELECT s.id, s.name, m.class_id, cl.name AS class_name "
                "FROM memberships m JOIN students s ON m.student_id=s.id "
                "LEFT JOIN classes cl ON m.class_id=cl.id "
                "WHERE m.guardian_id=?",
                (r["id"],),
            ).fetchall()
            class_ids = list(set(x["class_id"] for x in students if x["class_id"]))
            if not students:
                result.append({
                    "id": r["id"], "name": r["display_name"], "display_name": r["display_name"],
                    "parent_name": r["display_name"], "phone": r["phone"],
                    "active": bool(r["active"]), "created_at": r["created_at"],
                    "student_id": "", "student_name": "", "class_name": "",
                    "class_ids": [],
                })
            else:
                for s in students:
                    result.append({
                        "id": r["id"], "name": r["display_name"], "display_name": r["display_name"],
                        "parent_name": r["display_name"], "phone": r["phone"],
                        "active": bool(r["active"]), "created_at": r["created_at"],
                        "student_id": s["id"], "student_name": s["name"],
                        "class_name": s["class_name"] or "", "class_ids": class_ids,
                    })
        return {"items": result}


@router.post("/parents")
async def create_parent(req: ParentCreate, user=Depends(get_admin_user)):
    db = get_db()
    gid = _gen_id("grd")
    with db.conn() as c:
        c.execute(
            "INSERT INTO guardians(id, display_name, phone, active, created_at) VALUES(?,?,?,?,?)",
            (gid, req.display_name, req.phone, 1, _now()),
        )
    return {"id": gid, "display_name": req.display_name, "phone": req.phone, "active": True}


@router.patch("/parents/{parent_id}")
async def update_parent(parent_id: str, req: dict, user=Depends(get_admin_user)):
    db = get_db()
    with db.conn() as c:
        row = c.execute("SELECT * FROM guardians WHERE id=?", (parent_id,)).fetchone()
        if not row:
            raise HTTPException(404, "家長不存在")
        name = req.get("display_name", row["display_name"])
        phone = req.get("phone", row["phone"])
        active = 1 if req.get("active", bool(row["active"])) else 0
        c.execute(
            "UPDATE guardians SET display_name=?, phone=?, active=? WHERE id=?",
            (name, phone, active, parent_id),
        )
    return {"ok": True}


@router.delete("/parents/{parent_id}")
async def delete_parent(parent_id: str, user=Depends(get_admin_user)):
    db = get_db()
    with db.conn() as c:
        c.execute("DELETE FROM guardians WHERE id=?", (parent_id,))
    return {"ok": True}


# ============================================================
# Memberships (class-student)
# ============================================================
@router.get("/memberships")
async def list_memberships(
    class_id: Optional[str] = Query(None),
    student_id: Optional[str] = Query(None),
    user=Depends(get_admin_user),
):
    db = get_db()
    with db.conn() as c:
        q = "SELECT * FROM memberships WHERE 1=1"
        params = []
        if class_id:
            q += " AND class_id=?"
            params.append(class_id)
        if student_id:
            q += " AND student_id=?"
            params.append(student_id)
        rows = c.execute(q, params).fetchall()
        return [dict(r) for r in rows]


@router.post("/memberships")
async def create_membership(req: dict, user=Depends(get_admin_user)):
    db = get_db()
    mid = _gen_id("mbr")
    with db.conn() as c:
        c.execute(
            "INSERT INTO memberships(id, class_id, student_id, guardian_id, role, created_at) "
            "VALUES(?,?,?,?,?,?)",
            (mid, req.get("class_id", ""), req.get("student_id", ""),
             req.get("guardian_id"), req.get("role", "student"), _now()),
        )
    return {"id": mid}


@router.delete("/memberships/{mid}")
async def delete_membership(mid: str, user=Depends(get_admin_user)):
    db = get_db()
    with db.conn() as c:
        c.execute("DELETE FROM memberships WHERE id=?", (mid,))
    return {"ok": True}


# ============================================================
# Posts (Messages)
# ============================================================
@router.get("/posts/today")
async def list_posts_today(
    class_id: Optional[str] = Query(None),
    days: int = Query(0),
    post_date: Optional[str] = Query(None),
    user=Depends(get_admin_user),
):
    db = get_db()
    with db.conn() as c:
        q = "SELECT * FROM posts"
        params = []
        conditions = []
        if class_id:
            conditions.append("class_id=?")
            params.append(class_id)
        if post_date:
            conditions.append("substr(created_at,1,10)=?")
            params.append(post_date)
        elif days > 0:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            conditions.append("created_at>=?")
            params.append(cutoff)
        if conditions:
            q += " WHERE " + " AND ".join(conditions)
        q += " ORDER BY created_at DESC"
        rows = c.execute(q, params).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["need_confirm"] = bool(d["need_confirm"])
            imgs = c.execute(
                "SELECT id, file_name FROM post_images WHERE post_id=?", (r["id"],)
            ).fetchall()
            d["images"] = [{"id": x["id"], "file_name": x["file_name"], "url": f"/posts/{r['id']}/images/{x['id']}"} for x in imgs]
            total = c.execute(
                "SELECT COUNT(*) as n FROM post_recipients WHERE post_id=?", (r["id"],)
            ).fetchone()
            confirmed = c.execute(
                "SELECT COUNT(*) as n FROM post_recipients WHERE post_id=? AND confirmed=1",
                (r["id"],),
            ).fetchone()
            replies = c.execute(
                "SELECT COUNT(*) as n FROM post_replies WHERE post_id=?", (r["id"],)
            ).fetchone()
            reply_rows = c.execute(
                "SELECT r.id, r.student_id, s.name as student_name, r.content, r.created_at "
                "FROM post_replies r JOIN students s ON r.student_id=s.id "
                "WHERE r.post_id=? ORDER BY r.created_at DESC", (r["id"],)
            ).fetchall()
            d["recipient_count"] = total["n"]
            d["confirmed_count"] = confirmed["n"]
            d["read_count"] = 0
            d["confirmation_count"] = confirmed["n"]
            d["reply_count"] = replies["n"]
            d["replies"] = [
                {"id": rr["id"], "student_id": rr["student_id"],
                 "student_name": rr["student_name"], "body": rr["content"],
                 "created_at": rr["created_at"]}
                for rr in reply_rows
            ]
            result.append(d)
        return {"items": result}


@router.post("/posts")
async def create_post(req: PostCreate, user=Depends(get_admin_user)):
    db = get_db()
    pid = _gen_id("msg")
    need_c = req.need_confirm if req.require_confirmation is None else req.require_confirmation
    with db.conn() as c:
        c.execute(
            """INSERT INTO posts(id, class_id, student_id, category, title, content,
               need_confirm, status, created_at, created_by)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (pid, req.class_id, req.student_id, req.category, req.title,
             req.content, 1 if need_c else 0, "published", _now(), user),
        )
        students = c.execute(
            "SELECT student_id FROM memberships WHERE class_id=? AND guardian_id IS NULL",
            (req.class_id,),
        ).fetchall()
        for st in students:
            rid = _gen_id("rcp")
            c.execute(
                "INSERT OR IGNORE INTO post_recipients(id, post_id, student_id) VALUES(?,?,?)",
                (rid, pid, st["student_id"]),
            )
            nid = _gen_id("ntf")
            c.execute(
                "INSERT INTO parent_notifications(id, student_id, post_id, title, body, created_at) "
                "VALUES(?,?,?,?,?,?)",
                (nid, st["student_id"], pid, req.title, req.content[:100], _now()),
            )
    return {"id": pid, "title": req.title, "category": req.category, "recipient_count": len(students)}


@router.patch("/posts/{post_id}")
async def update_post(post_id: str, req: dict, user=Depends(get_admin_user)):
    db = get_db()
    with db.conn() as c:
        row = c.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
        if not row:
            raise HTTPException(404, "訊息不存在")
        c.execute(
            "UPDATE posts SET title=?, content=?, category=?, need_confirm=? WHERE id=?",
            (req.get("title", row["title"]), req.get("content", row["content"]),
             req.get("category", row["category"]),
             1 if req.get("need_confirm", bool(row["need_confirm"])) else 0, post_id),
        )
    return {"ok": True}


@router.post("/posts/delete")
async def delete_posts(req: dict, user=Depends(get_admin_user)):
    db = get_db()
    ids = req.get("post_ids") or req.get("ids") or []
    await _do_delete_posts(db, ids)
    return {"ok": True, "post_ids": ids, "deleted_count": len(ids)}


@router.delete("/posts/{post_id}")
async def delete_single_post(post_id: str, user=Depends(get_admin_user)):
    db = get_db()
    await _do_delete_posts(db, [post_id])
    return {"ok": True, "deleted": 1, "deleted_count": 1}


@router.get("/posts/{post_id}/recipient-status")
async def recipient_status(post_id: str, user=Depends(get_admin_user)):
    db = get_db()
    with db.conn() as c:
        rows = c.execute(
            "SELECT r.id as recipient_id, r.student_id, r.confirmed, r.confirmed_at, "
            "s.name as student_name, s.student_code "
            "FROM post_recipients r JOIN students s ON r.student_id=s.id "
            "WHERE r.post_id=?",
            (post_id,),
        ).fetchall()
        items = []
        for r in rows:
            sid = r["student_id"]
            # parent name from guardian membership
            grow = c.execute(
                "SELECT g.display_name FROM guardians g "
                "JOIN memberships m ON m.guardian_id=g.id "
                "WHERE m.student_id=? LIMIT 1", (sid,)
            ).fetchone()
            # reply stats
            rrow = c.execute(
                "SELECT COUNT(*) as cnt, MAX(created_at) as latest_at "
                "FROM post_replies WHERE post_id=? AND student_id=?",
                (post_id, sid)
            ).fetchone()
            latest_reply_body = c.execute(
                "SELECT content FROM post_replies WHERE post_id=? AND student_id=? "
                "ORDER BY created_at DESC LIMIT 1",
                (post_id, sid)
            ).fetchone()
            items.append({
                "recipient_id": r["recipient_id"],
                "student_id": sid,
                "student_name": r["student_name"],
                "student_code": r["student_code"],
                "parent_name": grow["display_name"] if grow else "",
                "confirmed": bool(r["confirmed"]),
                "confirmed_at": r["confirmed_at"],
                "last_read_at": None,
                "reply_count": rrow["cnt"] if rrow else 0,
                "latest_reply_at": rrow["latest_at"] if rrow else None,
                "latest_reply_body": latest_reply_body["content"] if latest_reply_body else None,
            })
        return {"items": items}


# ============================================================
# Post Images
# ============================================================
@router.post("/posts/{post_id}/images")
async def upload_image(post_id: str, file: UploadFile = File(...), user=Depends(get_admin_user)):
    db = get_db()
    data = await file.read()
    if len(data) > s.max_image_size:
        raise HTTPException(413, "圖片檔案太大")
    img_id = _gen_id("img")
    fname = file.filename or "upload.jpg"
    ext = os.path.splitext(fname)[1] or ".jpg"
    mime = file.content_type or "image/jpeg"

    # Save via storage adapter
    owner_id = _owner_id_from_user(user)
    registry = get_storage_registry()
    provider = _current_image_storage_provider(db)
    try:
        adapter = registry.get(provider)
        stored = await adapter.save(
            StorageContext(owner_id=owner_id),
            ImageFile(image_id=img_id, data=data, mime_type=mime, ext=ext),
        )
    except GoogleDriveError as exc:
        raise _google_drive_http_error(exc)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    with db.conn() as c:
        c.execute(
            "INSERT INTO post_images(id, post_id, file_name, mime_type, file_path, provider, storage_key, owner_id, size, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (img_id, post_id, fname, mime, stored.storage_key,
             stored.provider, stored.storage_key, owner_id, len(data), _now()),
        )
    return {
        "id": img_id,
        "file_name": fname,
        "mime_type": mime,
        "size": len(data),
        "url": f"/posts/{post_id}/images/{img_id}",
    }


@router.get("/posts/{post_id}/images/{image_id}")
async def get_image(post_id: str, image_id: str):
    db = get_db()
    with db.conn() as c:
        row = c.execute(
            "SELECT * FROM post_images WHERE id=? AND post_id=?", (image_id, post_id)
        ).fetchone()
        if not row:
            raise HTTPException(404, "圖片不存在")

    # Read via storage adapter
    provider = row["provider"] or "local"
    storage_key = row["storage_key"] or row["file_path"]
    registry = get_storage_registry()
    try:
        adapter = registry.get(provider)
    except ValueError:
        # Fallback for legacy rows without provider
        adapter = registry.get("local")

    try:
        retrieved = await adapter.open(StorageContext(owner_id=row["owner_id"] or ""), storage_key)
        async def stream_image():
            try:
                yield retrieved.stream.read()
            finally:
                retrieved.stream.close()

        return StreamingResponse(stream_image(), media_type=row["mime_type"] or retrieved.mime_type)
    except FileNotFoundError:
        raise HTTPException(410, "圖片檔案已不存在")
    except GoogleDriveError as exc:
        raise _google_drive_http_error(exc)


# ============================================================
# Parent Invite + Bind
# ============================================================
@router.post("/parent-invites")
async def create_parent_invite_plural(req: ParentInviteReq, user=Depends(get_admin_user)):
    return await _create_parent_invite(req, user)

@router.post("/parent-invite")
async def create_parent_invite(req: ParentInviteReq, user=Depends(get_admin_user)):
    return await _create_parent_invite(req, user)

async def _create_parent_invite(req: ParentInviteReq, user):
    db = get_db()
    student_id = req.student_id
    student_name = req.new_student_name or req.student_name
    guardian_name = req.guardian_name or req.parent_name
    guardian_phone = req.guardian_phone or req.phone
    gid = req.parent_id or None
    with db.conn() as c:
        # Resolve class_id: use request value, or look up from existing student membership
        class_id = req.class_id or ""
        if not class_id and student_id:
            row = c.execute(
                "SELECT class_id FROM memberships WHERE student_id=? AND class_id!='' LIMIT 1",
                (student_id,),
            ).fetchone()
            if row:
                class_id = row["class_id"]

        if not student_id and student_name:
            count = c.execute("SELECT COUNT(*) as n FROM students").fetchone()
            if count["n"] >= s.max_students:
                raise HTTPException(409, f"已達學生上限 {s.max_students} 人")
            student_id = _gen_id("stu")
            c.execute(
                "INSERT INTO students(id, name, student_code, active, created_at) VALUES(?,?,?,?,?)",
                (student_id, student_name, "", 1, _now()),
            )
            mid = _gen_id("mbr")
            c.execute(
                "INSERT INTO memberships(id, class_id, student_id, role, created_at) VALUES(?,?,?,?,?)",
                (mid, class_id, student_id, "student", _now()),
            )
        elif not student_id:
            raise HTTPException(422, "需要 student_id 或 student_name")

        g_count = c.execute(
            "SELECT COUNT(*) as n FROM memberships WHERE student_id=? AND guardian_id IS NOT NULL",
            (student_id,),
        ).fetchone()
        if g_count["n"] >= s.max_guardians_per_student:
            raise HTTPException(409, f"已達家長上限 {s.max_guardians_per_student} 人")

        code = gen_invite_code()
        invite_id = _gen_id("inv")
        # Reuse existing guardian if parent_id provided, otherwise create new
        if not gid:
            gid = _gen_id("grd")
            if not guardian_name:
                guardian_name = ""
            c.execute(
                "INSERT INTO guardians(id, display_name, phone, active, created_at) VALUES(?,?,?,?,?)",
                (gid, guardian_name, guardian_phone, 1, _now()),
            )
            gmid = _gen_id("mbr")
            c.execute(
                "INSERT INTO memberships(id, class_id, student_id, guardian_id, role, created_at) "
                "VALUES(?,?,?,?,?,?)",
                (gmid, class_id, student_id, gid, "guardian", _now()),
            )
        else:
            # Fetch existing guardian name for the invite record
            grow = c.execute("SELECT display_name, phone FROM guardians WHERE id=?", (gid,)).fetchone()
            if grow:
                if not guardian_name:
                    guardian_name = grow["display_name"]
                if not guardian_phone:
                    guardian_phone = grow["phone"] or ""
        c.execute(
            "INSERT INTO parent_invites(id, student_id, guardian_name, guardian_phone, code, created_at) "
            "VALUES(?,?,?,?,?,?)",
            (invite_id, student_id, guardian_name, guardian_phone, code, _now()),
        )
    return {"code": code, "invite_token": code, "student_id": student_id, "parent_id": gid}


@router.get("/parent-invites")
async def list_parent_invites(user=Depends(get_admin_user)):
    db = get_db()
    with db.conn() as c:
        rows = c.execute(
            "SELECT i.*, s.name as student_name FROM parent_invites i "
            "JOIN students s ON i.student_id=s.id ORDER BY i.created_at DESC"
        ).fetchall()
        return [
            {
                "id": r["id"], "code": r["code"],
                "student_id": r["student_id"], "student_name": r["student_name"],
                "guardian_name": r["guardian_name"], "guardian_phone": r["guardian_phone"],
                "used": bool(r["used"]), "created_at": r["created_at"],
            }
            for r in rows
        ]


@router.post("/parent-auth/bind")
async def parent_bind(req: ParentBindReq):
    db = get_db()
    invite_code = req.code or req.invite_token
    if not invite_code:
        raise HTTPException(422, "需要邀請碼")
    with db.conn() as c:
        row = c.execute(
            "SELECT * FROM parent_invites WHERE code=? AND used=0", (invite_code,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "邀請碼無效或已使用")
        guardian_name = req.guardian_name or row["guardian_name"]
        # Reuse existing guardian if already created during invite
        existing = c.execute(
            "SELECT id FROM guardians WHERE display_name=? AND phone=? LIMIT 1",
            (guardian_name, row["guardian_phone"]),
        ).fetchone()
        if existing:
            gid = existing["id"]
        else:
            gid = _gen_id("grd")
            c.execute(
                "INSERT INTO guardians(id, display_name, phone, active, created_at) VALUES(?,?,?,?,?)",
                (gid, guardian_name, row["guardian_phone"], 1, _now()),
            )
        # Check if guardian already linked to this student
        already = c.execute(
            "SELECT id FROM memberships WHERE student_id=? AND guardian_id=? AND role='guardian'",
            (row["student_id"], gid),
        ).fetchone()
        if not already:
            mbr = c.execute(
                "SELECT class_id FROM memberships WHERE student_id=? AND role='student' LIMIT 1",
                (row["student_id"],),
            ).fetchone()
            bind_class_id = mbr["class_id"] if mbr else ""
            mid = _gen_id("mbr")
            c.execute(
                "INSERT INTO memberships(id, class_id, student_id, guardian_id, role, created_at) "
                "VALUES(?,?,?,?,?,?)",
                (mid, bind_class_id, row["student_id"], gid, "guardian", _now()),
            )
        c.execute("UPDATE parent_invites SET used=1 WHERE id=?", (row["id"],))
        token = create_token(f"parent:{row['student_id']}:{gid}")
        return {
            "token": token,
            "access_token": token,
            "student_id": row["student_id"],
            "student_ids": [row["student_id"]],
            "parent_id": gid,
            "guardian_name": guardian_name,
        }


# ============================================================
# Parent view endpoints
# ============================================================
@router.get("/parent/today")
async def parent_today(
    request: Request,
    class_id: Optional[str] = Query(None),
    days: int = Query(0),
    post_date: Optional[str] = Query(None),
    student_id: str = Query(...),
):
    if not get_optional_parent(request):
        raise HTTPException(401, "未綁定")
    db = get_db()
    with db.conn() as c:
        q = "SELECT * FROM posts WHERE student_id IS NULL OR student_id=?"
        params = [student_id]
        if post_date:
            q += " AND substr(created_at,1,10)=?"
            params.append(post_date)
        elif days > 0:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            q += " AND created_at>=?"
            params.append(cutoff)
        if class_id:
            q += " AND class_id=?"
            params.append(class_id)
        q += " ORDER BY created_at DESC"
        rows = c.execute(q, params).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["need_confirm"] = bool(d["need_confirm"])
            imgs = c.execute(
                "SELECT id, file_name FROM post_images WHERE post_id=?", (r["id"],)
            ).fetchall()
            d["images"] = [{"id": x["id"], "file_name": x["file_name"], "url": f"/posts/{r['id']}/images/{x['id']}"} for x in imgs]
            rec = c.execute(
                "SELECT confirmed FROM post_recipients WHERE post_id=? AND student_id=?",
                (r["id"], student_id),
            ).fetchone()
            is_confirmed = bool(rec["confirmed"]) if rec else False
            d["confirmed"] = is_confirmed
            d["parent_confirmed"] = is_confirmed
            reply_rows = c.execute(
                "SELECT id, content, created_at FROM post_replies WHERE post_id=? AND student_id=? "
                "ORDER BY created_at DESC", (r["id"], student_id)
            ).fetchall()
            d["replies"] = [
                {"id": rr["id"], "body": rr["content"], "created_at": rr["created_at"]}
                for rr in reply_rows
            ]
            result.append(d)
        return {"items": result}


@router.post("/parent/posts/{post_id}/confirm")
async def parent_confirm(
    request: Request, post_id: str, student_id: str = Query(...),
):
    if not get_optional_parent(request):
        raise HTTPException(401, "未綁定")
    db = get_db()
    with db.conn() as c:
        c.execute(
            "UPDATE post_recipients SET confirmed=1, confirmed_at=? WHERE post_id=? AND student_id=?",
            (_now(), post_id, student_id),
        )
    return {"ok": True}


@router.get("/parent/posts/{post_id}/replies")
async def list_replies(request: Request, post_id: str):
    if not get_optional_parent(request):
        raise HTTPException(401, "未綁定")
    db = get_db()
    with db.conn() as c:
        rows = c.execute(
            "SELECT r.*, s.name as student_name FROM post_replies r "
            "JOIN students s ON r.student_id=s.id WHERE r.post_id=?",
            (post_id,),
        ).fetchall()
        return [
            {
                "id": r["id"], "student_id": r["student_id"],
                "student_name": r["student_name"], "body": r["content"],
                "content": r["content"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]


@router.post("/parent/posts/{post_id}/replies")
async def create_reply(
    request: Request, post_id: str, req: ReplyCreate,
    student_id: str = Query(None),
):
    if not get_optional_parent(request):
        raise HTTPException(401, "未綁定")
    # Frontend sends body in JSON and student_id in JSON too (not query param)
    actual_student_id = student_id or req.student_id
    content = req.content or req.body
    if not content:
        raise HTTPException(422, "回覆內容不可為空")
    db = get_db()
    rid = _gen_id("rpl")
    with db.conn() as c:
        c.execute(
            "INSERT INTO post_replies(id, post_id, student_id, content, created_at) VALUES(?,?,?,?,?)",
            (rid, post_id, actual_student_id, content, _now()),
        )
    return {"id": rid, "body": content, "content": content}


@router.get("/parent/notifications")
async def parent_notifications(request: Request, student_id: str = Query(...)):
    if not get_optional_parent(request):
        raise HTTPException(401, "未綁定")
    db = get_db()
    with db.conn() as c:
        rows = c.execute(
            "SELECT * FROM parent_notifications WHERE student_id=? ORDER BY created_at DESC LIMIT 50",
            (student_id,),
        ).fetchall()
        items = [
            {
                "id": r["id"], "student_id": r["student_id"],
                "post_id": r["post_id"], "title": r["title"],
                "body": r["body"], "read": bool(r["read"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]
        return {"items": items}


@router.post("/parent/notifications/{nid}/read")
async def mark_notification_read(request: Request, nid: str):
    if not get_optional_parent(request):
        raise HTTPException(401, "未綁定")
    db = get_db()
    with db.conn() as c:
        c.execute("UPDATE parent_notifications SET read=1 WHERE id=?", (nid,))
    return {"ok": True}


@router.get("/parent/line-binding/status")
async def line_binding_status():
    return {"line_bound": False, "line_id": None}


@router.post("/parent/line-binding/start")
async def line_binding_start():
    return {"url": None, "message": "LINE 綁定功能尚未開放"}


# ============================================================
# Feedback
# ============================================================
@router.post("/feedback")
async def create_feedback(req: FeedbackReq):
    db = get_db()
    fid = _gen_id("fb")
    with db.conn() as c:
        c.execute(
            "INSERT INTO feedback(id, body, created_at) VALUES(?,?,?)",
            (fid, req.body, _now()),
        )
    return {"ok": True}


# ============================================================
# Image cleanup — remove orphaned images (post deleted but image row/file remains)
# ============================================================
@router.post("/image-cleanups/run")
async def image_cleanup(user=Depends(get_admin_user)):
    db = get_db()
    registry = get_storage_registry()

    # Find orphaned images: post_images rows whose post no longer exists
    with db.conn() as c:
        orphans = c.execute(
            "SELECT pi.id, pi.storage_key, pi.provider, pi.owner_id "
            "FROM post_images pi "
            "LEFT JOIN posts p ON pi.post_id = p.id "
            "WHERE p.id IS NULL"
        ).fetchall()

    deleted = 0
    failed = 0
    row_ids_to_delete = []

    for row in orphans:
        img = dict(row)
        provider = img["provider"] or "local"
        try:
            adapter = registry.get(provider)
            if img["storage_key"]:
                try:
                    await adapter.delete(
                        StorageContext(owner_id=img["owner_id"] or ""),
                        img["storage_key"],
                    )
                except FileNotFoundError:
                    pass  # file already gone — still clean DB row
            row_ids_to_delete.append(img["id"])
            deleted += 1
        except Exception:
            failed += 1

    if row_ids_to_delete:
        with db.conn() as c:
            c.executemany(
                "DELETE FROM post_images WHERE id=?",
                [(rid,) for rid in row_ids_to_delete],
            )

    return {"deleted": deleted, "failed": failed}


# ============================================================
# Batch class invites (frontend: POST /classes/{id}/parent-invites)
# ============================================================
@router.post("/classes/{class_id}/parent-invites")
async def batch_class_invites(class_id: str, user=Depends(get_admin_user)):
    db = get_db()
    items = []
    with db.conn() as c:
        cls = c.execute("SELECT name FROM classes WHERE id=?", (class_id,)).fetchone()
        class_name = cls["name"] if cls else ""
        students = c.execute(
            "SELECT s.id, s.name FROM students s "
            "JOIN memberships m ON m.student_id = s.id "
            "WHERE m.class_id=? AND m.guardian_id IS NULL AND s.active=1",
            (class_id,),
        ).fetchall()
        for st in students:
            existing = c.execute(
                "SELECT g.id FROM guardians g "
                "JOIN memberships m ON m.guardian_id = g.id "
                "WHERE m.student_id=? AND m.class_id=?",
                (st["id"], class_id),
            ).fetchall()
            if existing:
                for g in existing:
                    code = gen_invite_code()
                    inv_id = _gen_id("inv")
                    c.execute(
                        "INSERT INTO parent_invites(id, student_id, guardian_name, guardian_phone, code, created_at) "
                        "VALUES(?,?,?,?,?,?)",
                        (inv_id, st["id"], "", "", code, _now()),
                    )
                    items.append({
                        "parent_id": g["id"],
                        "student_id": st["id"],
                        "parent_name": "",
                        "student_name": st["name"],
                        "class_name": class_name,
                        "invite_token": code,
                        "expires_at": None,
                    })
            else:
                code = gen_invite_code()
                inv_id = _gen_id("inv")
                c.execute(
                    "INSERT INTO parent_invites(id, student_id, guardian_name, guardian_phone, code, created_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (inv_id, st["id"], "", "", code, _now()),
                )
                items.append({
                    "parent_id": inv_id,
                    "student_id": st["id"],
                    "parent_name": "",
                    "student_name": st["name"],
                    "class_name": class_name,
                    "invite_token": code,
                    "expires_at": None,
                })
    return {"items": items}


# ============================================================
# Roster import (CSV preview/apply/template)
# ============================================================
@router.get("/roster-imports/template.csv")
async def roster_template(user=Depends(get_admin_user)):
    import csv, io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["班級名稱", "學生姓名", "家長姓名", "家長電話"])
    w.writerow(["三年一班", "王小明", "王大華", "0912345678"])
    buf.seek(0)
    return Response(buf.read(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=classhub-roster-template.csv"})


@router.post("/roster-imports/preview")
async def roster_preview(file: UploadFile = File(...), user=Depends(get_admin_user)):
    import csv, io
    raw = (await file.read()).decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(raw))
    rows = list(reader)
    students = []
    errors = []
    for i, row in enumerate(rows, 2):
        cls = (row.get("班級名稱") or "").strip()
        name = (row.get("學生姓名") or "").strip()
        if not cls or not name:
            errors.append(f"第 {i} 列：缺少班級名稱或學生姓名")
            continue
        students.append({
            "class_name": cls,
            "student_name": name,
            "guardian_name": (row.get("家長姓名") or "").strip(),
            "guardian_phone": (row.get("家長電話") or "").strip(),
        })
    class_names = set()
    guardian_count = 0
    for s in students:
        class_names.add(s["class_name"])
        if s["guardian_name"]:
            guardian_count += 1
    return {
        "can_apply": len(errors) == 0 and len(students) > 0,
        "errors": errors,
        "preview": students[:20],
        "summary": {
            "total_rows": len(rows),
            "classes_to_create": len(class_names),
            "students_to_create": len(students),
            "students_to_update": 0,
            "students_to_deactivate": 0,
            "guardians_to_create": guardian_count,
            "guardians_to_deactivate": 0,
        },
    }


@router.post("/roster-imports/apply")
async def roster_apply(file: UploadFile = File(...), user=Depends(get_admin_user)):
    import csv, io
    raw = (await file.read()).decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(raw))
    rows = list(reader)
    db = get_db()
    created = 0
    with db.conn() as c:
        for row in rows:
            cls_name = (row.get("班級名稱") or "").strip()
            stu_name = (row.get("學生姓名") or "").strip()
            g_name = (row.get("家長姓名") or "").strip()
            g_phone = (row.get("家長電話") or "").strip()
            if not cls_name or not stu_name:
                continue
            cls = c.execute("SELECT id FROM classes WHERE name=?", (cls_name,)).fetchone()
            if not cls:
                cid = _gen_id("cls")
                c.execute("INSERT INTO classes(id, name, created_at) VALUES(?,?,?)",
                          (cid, cls_name, _now()))
            else:
                cid = cls["id"]
            count = c.execute("SELECT COUNT(*) as n FROM students").fetchone()
            if count["n"] >= s.max_students:
                break
            sid = _gen_id("stu")
            c.execute(
                "INSERT INTO students(id, name, student_code, active, created_at) VALUES(?,?,?,?,?)",
                (sid, stu_name, "", 1, _now()),
            )
            mid = _gen_id("mbr")
            c.execute(
                "INSERT INTO memberships(id, class_id, student_id, role, created_at) VALUES(?,?,?,?,?)",
                (mid, cid, sid, "student", _now()),
            )
            if g_name:
                gid = _gen_id("grd")
                c.execute(
                    "INSERT INTO guardians(id, display_name, phone, active, created_at) VALUES(?,?,?,?,?)",
                    (gid, g_name, g_phone, 1, _now()),
                )
                mid2 = _gen_id("mbr")
                c.execute(
                    "INSERT INTO memberships(id, class_id, student_id, guardian_id, role, created_at) VALUES(?,?,?,?,?,?)",
                    (mid2, cid, sid, gid, "guardian", _now()),
                )
            created += 1
    return {"applied": created}

app.include_router(router)
