"""Employee Attendance & Analytics API."""
import calendar
import hashlib
import json
import os
import re
import secrets
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Literal

from bson import json_util
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError

load_dotenv()
client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"), serverSelectionTimeoutMS=3000, tz_aware=True)
db = client[os.getenv("MONGO_DB", "attendance_db")]
IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc
PRESENCE = ("PRESENT", "WFH", "ON_DUTY")
STATUSES = (*PRESENCE, "ABSENT", "LEAVE")
app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
password_hasher = PasswordHasher()
SESSION_TTL = timedelta(hours=12)
SESSION_COOKIE = "northstar_session"
CSRF_COOKIE = "northstar_csrf"


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=120)
    password: str = Field(min_length=1, max_length=256)


class EmployeeAccountIn(BaseModel):
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    password: str = Field(min_length=12, max_length=256)


class DeleteEmployeeIn(BaseModel):
    confirmation: str = Field(min_length=1, max_length=20)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def secure_cookie() -> bool:
    return os.getenv("COOKIE_SECURE", "false").lower() in {"1", "true", "yes"}


def current_user(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(401, "Authentication required")
    session = db.auth_sessions.find_one({"token_hash": token_hash(token), "expires_at": {"$gt": datetime.now(UTC)}})
    if not session:
        raise HTTPException(401, "Session expired; sign in again")
    csrf = request.cookies.get(CSRF_COOKIE)
    header = request.headers.get("x-csrf-token")
    if request.method not in {"GET", "HEAD", "OPTIONS"} and (not csrf or not header or not secrets.compare_digest(csrf, header) or not secrets.compare_digest(token_hash(csrf), session["csrf_hash"])):
        raise HTTPException(403, "CSRF validation failed")
    user = db.auth_users.find_one({"_id": session["user_id"], "active": True})
    if not user:
        raise HTTPException(401, "Account is unavailable")
    return user


def admin_user(user=Depends(current_user)):
    if user.get("role") != "admin":
        raise HTTPException(403, "Administrator access required")
    return user


def employee_user(user=Depends(current_user)):
    if user.get("role") != "employee":
        raise HTTPException(403, "Employee access required")
    if not user.get("emp_code"):
        raise HTTPException(403, "Employee account is not linked")
    return user


@app.get("/", include_in_schema=False)
def frontend():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.on_event("startup")
def create_indexes():
    db.auth_users.create_index([("email", ASCENDING)], unique=True, name="uq_auth_email")
    db.auth_users.create_index([("emp_code", ASCENDING)], unique=True, sparse=True, name="uq_auth_emp_code")
    db.auth_sessions.create_index([("token_hash", ASCENDING)], unique=True, name="uq_session_token")
    db.auth_sessions.create_index([("expires_at", ASCENDING)], expireAfterSeconds=0, name="ttl_session_expiry")
    db.employees.create_index([("emp_code", ASCENDING)], unique=True, name="uq_employee_code")
    db.employees.create_index([("joined_on", ASCENDING)], name="employee_joined_on")
    db.employees.create_index([("department", ASCENDING), ("joined_on", ASCENDING)], name="department_joined")
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("date", ASCENDING)], unique=True, name="uq_attendance_employee_date")
    db.attendance_logs.create_index([("date", DESCENDING), ("emp_code", ASCENDING)], name="attendance_date_employee")
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("date", DESCENDING)], name="attendance_employee_date")
    db.attendance_logs.create_index([("status", ASCENDING), ("date", DESCENDING)], name="attendance_status_date")


@app.post("/auth/login")
def login(body: LoginIn, response: Response):
    email = body.email.strip().lower()
    user = db.auth_users.find_one({"email": email, "active": True})
    if not user:
        raise HTTPException(401, "Invalid email or password")
    try:
        password_hasher.verify(user["password_hash"], body.password)
    except (VerifyMismatchError, VerificationError):
        raise HTTPException(401, "Invalid email or password")
    if password_hasher.check_needs_rehash(user["password_hash"]):
        db.auth_users.update_one({"_id": user["_id"]}, {"$set": {"password_hash": password_hasher.hash(body.password)}})
    raw_token, csrf_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    now = datetime.now(UTC).replace(microsecond=0)
    db.auth_sessions.insert_one({"token_hash": token_hash(raw_token), "csrf_hash": token_hash(csrf_token), "user_id": user["_id"], "created_at": now, "expires_at": now + SESSION_TTL})
    response.set_cookie(SESSION_COOKIE, raw_token, httponly=True, secure=secure_cookie(), samesite="lax", max_age=int(SESSION_TTL.total_seconds()), path="/")
    response.set_cookie(CSRF_COOKIE, csrf_token, httponly=False, secure=secure_cookie(), samesite="lax", max_age=int(SESSION_TTL.total_seconds()), path="/")
    return auth_profile(user)


def auth_profile(user):
    profile = {"role": user["role"], "email": user["email"]}
    if user["role"] == "employee":
        employee = db.employees.find_one({"emp_code": user["emp_code"]}, {"_id": 0, "name": 1, "emp_code": 1, "department": 1, "shift_start": 1, "shift_end": 1})
        if not employee:
            raise HTTPException(403, "Employee account is not linked to an employee record")
        profile.update(employee)
    else:
        profile["name"] = user.get("name", "Administrator")
    return profile


@app.get("/auth/me")
def who_am_i(user=Depends(current_user)):
    return auth_profile(user)


@app.post("/auth/logout")
def logout(request: Request, response: Response, user=Depends(current_user)):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        db.auth_sessions.delete_one({"token_hash": token_hash(token)})
    response.delete_cookie(SESSION_COOKIE, path="/", secure=secure_cookie(), httponly=True, samesite="lax")
    response.delete_cookie(CSRF_COOKIE, path="/", secure=secure_cookie(), samesite="lax")
    return {"status": "ok"}


@app.post("/admin/employee-accounts", status_code=201)
def provision_employee_account(body: EmployeeAccountIn, user=Depends(admin_user)):
    employee = db.employees.find_one({"emp_code": body.emp_code}, {"email": 1, "name": 1})
    if not employee:
        raise HTTPException(404, "Employee not found")
    email = employee["email"].strip().lower()
    doc = {"email": email, "name": employee["name"], "password_hash": password_hasher.hash(body.password), "role": "employee", "emp_code": body.emp_code, "active": True, "created_at": datetime.now(UTC).replace(microsecond=0)}
    try:
        db.auth_users.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "An account already exists for this email or employee")
    return {"email": email, "emp_code": body.emp_code, "role": "employee"}


def valid_date(value: str) -> str:
    try:
        date.fromisoformat(value)
    except (ValueError, TypeError):
        raise HTTPException(422, "must be a valid YYYY-MM-DD date")
    if len(value) != 10:
        raise HTTPException(422, "must be a valid YYYY-MM-DD date")
    return value


def epoch_datetime(ms: int | None) -> datetime:
    if ms is None:
        return datetime.now(UTC).replace(microsecond=0)
    if type(ms) is not int or ms < 100_000_000_000 or ms > 4_102_444_800_000:
        raise HTTPException(422, "timestamp must be epoch milliseconds in the supported range")
    return datetime.fromtimestamp(ms / 1000, UTC).replace(microsecond=0)


def to_ms(value: datetime | None):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp() * 1000)


def public_record(doc: dict) -> dict:
    result = {k: v for k, v in doc.items() if k != "_id"}
    for field in ("punch_in", "punch_out"):
        result[field] = to_ms(result.get(field))
    result.setdefault("history", [])
    for entry in result["history"]:
        entry["at"] = to_ms(entry.get("at"))
        for change in entry.get("changes", {}).values():
            if isinstance(change.get("from"), datetime): change["from"] = to_ms(change["from"])
            if isinstance(change.get("to"), datetime): change["to"] = to_ms(change["to"])
    result.setdefault("late_minutes", 0); result.setdefault("overtime_minutes", 0); result.setdefault("half_day", False)
    result.setdefault("work_hours", None)
    return result


def public_employee(doc: dict) -> dict:
    result = {k: v for k, v in doc.items() if k != "_id"}
    result["created_at"] = to_ms(result.get("created_at"))
    return result


def shift_start_dt(att_date: str, shift: str) -> datetime:
    return datetime.combine(date.fromisoformat(att_date), time.fromisoformat(shift), tzinfo=IST)


def attendance_date(punch: datetime, start: str, end: str) -> str:
    local = punch.astimezone(IST)
    d = local.date()
    if end <= start and local.strftime("%H:%M") < end:
        d -= timedelta(days=1)
    return d.isoformat()


def late_minutes(punch: datetime, att_date: str, shift: str) -> int:
    start = shift_start_dt(att_date, shift).astimezone(UTC)
    elapsed = int((punch - start).total_seconds())
    return elapsed // 60 if elapsed > 600 else 0


def rounded_hours(start: datetime | None, end: datetime | None):
    if start is None or end is None: return None
    seconds = Decimal(str((end - start).total_seconds()))
    return float((seconds / Decimal(3600)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def overtime_minutes(punch: datetime | None, att_date: str, shift_start: str, shift_end: str) -> int:
    if punch is None: return 0
    end_date = date.fromisoformat(att_date) + (timedelta(days=1) if shift_end <= shift_start else timedelta())
    h, m = map(int, shift_end.split(":"))
    end = datetime.combine(end_date, time(h, m), tzinfo=IST).astimezone(UTC)
    mins = int((punch - end).total_seconds()) // 60
    return mins if mins >= 30 else 0


def computed_fields(status: str, pin: datetime | None, pout: datetime | None, day: str, emp: dict):
    is_present = status in PRESENCE
    hours = rounded_hours(pin, pout) if is_present else None
    return {
        "late_minutes": late_minutes(pin, day, emp["shift_start"]) if is_present and pin else 0,
        "work_hours": hours,
        "overtime_minutes": overtime_minutes(pout, day, emp["shift_start"], emp["shift_end"]) if is_present else 0,
        "half_day": bool(hours is not None and hours < 4.5),
    }


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class EmployeeIn(StrictModel):
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = Field(default="09:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    shift_end: str = Field(default="18:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    joined_on: str
    @field_validator("joined_on")
    @classmethod
    def check_joined(cls, v): return valid_date(v)
    @model_validator(mode="after")
    def check_shift(self):
        if self.shift_start == self.shift_end: raise ValueError("shift_start and shift_end must differ")
        return self


class PunchInIn(StrictModel):
    emp_code: str
    punched_at: int | None = Field(default=None, strict=True)
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"
    @field_validator("punched_at")
    @classmethod
    def check_ts(cls, v):
        if v is not None and (type(v) is not int or not 100_000_000_000 <= v <= 4_102_444_800_000): raise ValueError("must be epoch milliseconds")
        return v


class NonWorkingAttendanceIn(StrictModel):
    emp_code: str
    date: str
    status: Literal["ABSENT", "LEAVE"]
    @field_validator("date")
    @classmethod
    def check_attendance_date(cls, v): return valid_date(v)


class PunchOutIn(StrictModel):
    emp_code: str
    punched_at: int | None = Field(default=None, strict=True)
    @field_validator("punched_at")
    @classmethod
    def check_ts(cls, v):
        if v is not None and (type(v) is not int or not 100_000_000_000 <= v <= 4_102_444_800_000): raise ValueError("must be epoch milliseconds")
        return v


class RegularizeIn(StrictModel):
    new_date: str | None = None
    clear_punch_out: bool = False
    status: Literal["PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"] | None = None
    punch_in: int | None = Field(default=None, strict=True)
    punch_out: int | None = Field(default=None, strict=True)
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)
    @field_validator("punch_in", "punch_out")
    @classmethod
    def check_ts(cls, v):
        if v is not None and (type(v) is not int or not 100_000_000_000 <= v <= 4_102_444_800_000): raise ValueError("must be epoch milliseconds")
        return v
    @field_validator("new_date")
    @classmethod
    def check_new_date(cls, v): return valid_date(v) if v is not None else v
    @model_validator(mode="after")
    def reject_explicit_nulls(self):
        for key in ("new_date", "status", "punch_in", "punch_out"):
            if key in self.model_fields_set and getattr(self, key) is None:
                raise ValueError(f"{key} cannot be null")
        if self.clear_punch_out and "punch_out" in self.model_fields_set:
            raise ValueError("provide either punch_out or clear_punch_out, not both")
        return self


@app.get("/health")
def health():
    try: client.admin.command("ping")
    except Exception: raise HTTPException(503, "MongoDB unavailable")
    return {"status": "ok"}


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn, user=Depends(admin_user)):
    doc = body.model_dump()
    doc["created_at"] = datetime.now(UTC).replace(microsecond=0)
    try: db.employees.insert_one(doc)
    except DuplicateKeyError: raise HTTPException(409, "emp_code already exists")
    return public_employee(doc)


@app.get("/employees")
def list_employees(department: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), user=Depends(admin_user)):
    query = {"department": department} if department is not None else {}
    total = db.employees.count_documents(query)
    items = [public_employee(d) for d in db.employees.find(query, {"_id": 0}).sort("emp_code", ASCENDING).skip((page-1)*page_size).limit(page_size)]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.delete("/employees/{emp_code}")
def delete_employee(emp_code: str, body: DeleteEmployeeIn, user=Depends(admin_user)):
    employee = db.employees.find_one({"emp_code": emp_code})
    if not employee:
        raise HTTPException(404, "employee not found")
    if not secrets.compare_digest(body.confirmation, emp_code):
        raise HTTPException(422, "type the employee code exactly to confirm deletion")

    attendance_result = db.attendance_logs.delete_many({"emp_code": emp_code})
    account = db.auth_users.find_one({"emp_code": emp_code, "role": "employee"}, {"_id": 1})
    sessions_deleted = 0
    if account:
        sessions_deleted = db.auth_sessions.delete_many({"user_id": account["_id"]}).deleted_count
        db.auth_users.delete_one({"_id": account["_id"]})
    db.employees.delete_one({"_id": employee["_id"]})
    return {"emp_code": emp_code, "attendance_records_deleted": attendance_result.deleted_count,
            "account_deleted": account is not None, "sessions_deleted": sessions_deleted}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn, user=Depends(admin_user)):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp: raise HTTPException(404, "employee not found")
    punch = epoch_datetime(body.punched_at)
    day = attendance_date(punch, emp["shift_start"], emp["shift_end"])
    if punch > datetime.now(UTC) or date.fromisoformat(day) > datetime.now(IST).date():
        raise HTTPException(422, "Punch-in cannot be recorded for a future date or time")
    doc = {"emp_code": body.emp_code, "date": day, "status": body.status, "punch_in": punch, "punch_out": None,
           "work_hours": None, "late_minutes": late_minutes(punch, day, emp["shift_start"]), "overtime_minutes": 0, "half_day": False, "history": []}
    try: db.attendance_logs.insert_one(doc)
    except DuplicateKeyError: raise HTTPException(409, "already punched in for this date")
    return public_record(doc)


@app.post("/attendance/non-working", status_code=201)
def record_non_working_attendance(body: NonWorkingAttendanceIn, user=Depends(admin_user)):
    """Record an absent/leave day with no punch times; kept separate from punch-in."""
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp: raise HTTPException(404, "employee not found")
    if date.fromisoformat(body.date) > datetime.now(IST).date():
        raise HTTPException(422, "Attendance cannot be recorded for a future date")
    doc = {"emp_code": body.emp_code, "date": body.date, "status": body.status, "punch_in": None, "punch_out": None,
           "work_hours": None, "late_minutes": 0, "overtime_minutes": 0, "half_day": False, "history": []}
    try: db.attendance_logs.insert_one(doc)
    except DuplicateKeyError: raise HTTPException(409, "attendance already exists for this date")
    return public_record(doc)


@app.get("/attendance")
def list_attendance(emp_code: str | None = None, date_from: str | None = None, date_to: str | None = None,
                    status: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), user=Depends(admin_user)):
    if status is not None and status not in STATUSES: raise HTTPException(422, "invalid status")
    if date_from: valid_date(date_from)
    if date_to: valid_date(date_to)
    if date_from and date_to and date_from > date_to: raise HTTPException(422, "date_from must be on or before date_to")
    query = {}
    if emp_code: query["emp_code"] = emp_code
    if status: query["status"] = status
    if date_from or date_to:
        query["date"] = {}
        if date_from: query["date"]["$gte"] = date_from
        if date_to: query["date"]["$lte"] = date_to
    total = db.attendance_logs.count_documents(query)
    docs = db.attendance_logs.find(query, {"_id": 0}).sort([("date", DESCENDING), ("emp_code", ASCENDING)]).skip((page-1)*page_size).limit(page_size)
    return {"items": [public_record(d) for d in docs], "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn, user=Depends(admin_user)):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp: raise HTTPException(404, "employee not found")
    punch = epoch_datetime(body.punched_at)
    if punch > datetime.now(UTC):
        raise HTTPException(422, "Punch-out cannot be recorded for a future time")
    doc = db.attendance_logs.find_one({"emp_code": body.emp_code, "punch_in": {"$type": "date", "$lte": punch}}, sort=[("punch_in", DESCENDING)])
    if not doc: raise HTTPException(404, "no punch-in found")
    if doc.get("punch_out") is not None: raise HTTPException(409, "record already punched out")
    if punch <= doc["punch_in"] or punch - doc["punch_in"] > timedelta(hours=24): raise HTTPException(422, "punch_out must be after punch_in and within 24 hours")
    fields = computed_fields(doc["status"], doc["punch_in"], punch, doc["date"], emp)
    updated = db.attendance_logs.find_one_and_update({"_id": doc["_id"], "punch_out": None}, {"$set": {"punch_out": punch, **fields}}, return_document=ReturnDocument.AFTER)
    if not updated: raise HTTPException(409, "record already punched out")
    return public_record(updated)


@app.patch("/attendance/{emp_code}/{att_date}")
def regularize(emp_code: str, att_date: str, body: RegularizeIn, user=Depends(admin_user)):
    valid_date(att_date)
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp: raise HTTPException(404, "employee not found")
    query = {"emp_code": emp_code, "date": att_date}
    original = db.attendance_logs.find_one(query)
    if not original: raise HTTPException(404, "attendance record not found")
    data = body.model_dump(exclude_unset=True)
    target_day = data.get("new_date", att_date)
    if date.fromisoformat(target_day) > datetime.now(IST).date():
        raise HTTPException(422, "Attendance cannot be moved to a future date")
    status = data.get("status", original["status"])
    pin = epoch_datetime(data["punch_in"]) if data.get("punch_in") is not None else original.get("punch_in")
    pout = None if data.get("clear_punch_out") else (epoch_datetime(data["punch_out"]) if data.get("punch_out") is not None else original.get("punch_out"))
    if status in ("ABSENT", "LEAVE"):
        if "punch_in" in data or "punch_out" in data: raise HTTPException(422, "ABSENT and LEAVE cannot include punch times")
        pin = pout = None
    else:
        if pin is None: raise HTTPException(422, "presence status requires punch_in")
        if attendance_date(pin, emp["shift_start"], emp["shift_end"]) != target_day: raise HTTPException(422, "Punch-in date must match the corrected attendance date; update both dates to move this record")
        if pin > datetime.now(UTC): raise HTTPException(422, "Punch-in cannot be corrected to a future time")
        if pout is not None and (pout <= pin or pout-pin > timedelta(hours=24)): raise HTTPException(422, "punch_out must be after punch_in and within 24 hours")
        if pout is not None and pout > datetime.now(UTC): raise HTTPException(422, "Punch-out cannot be corrected to a future time")
    fields = {"date": target_day, "status": status, "punch_in": pin, "punch_out": pout, **computed_fields(status, pin, pout, target_day, emp)}
    changes = {}
    defaults = {"date": att_date, "late_minutes": 0, "overtime_minutes": 0, "half_day": False, "work_hours": None}
    for key, value in fields.items():
        old = original.get(key, defaults.get(key))
        if isinstance(old, datetime) and isinstance(value, datetime):
            if old.replace(tzinfo=UTC) != value.replace(tzinfo=UTC): changes[key] = {"from": old, "to": value}
        elif old != value: changes[key] = {"from": old, "to": value}
    if not changes: raise HTTPException(422, "regularization makes no changes")
    history_entry = {"at": datetime.now(UTC).replace(microsecond=0), "by": user.get("name") or user["email"], "reason": body.reason, "changes": changes}
    previous_history = original.get("history", [])
    history_guard = {"history": previous_history} if "history" in original else {"history": {"$exists": False}}
    try:
        updated = db.attendance_logs.find_one_and_update({**query, "_id": original["_id"], **history_guard},
            {"$set": fields, "$push": {"history": history_entry}}, return_document=ReturnDocument.AFTER)
    except DuplicateKeyError:
        raise HTTPException(409, "An attendance record already exists for this employee on the corrected date")
    if not updated: raise HTTPException(409, "record was concurrently regularized; retry")
    return public_record(updated)


def month_bounds(month: str):
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month): raise HTTPException(422, "month must be YYYY-MM")
    try:
        year, mon = map(int, month.split("-"))
        if year < 1: raise ValueError
        last = calendar.monthrange(year, mon)[1]
    except (ValueError, IndexError): raise HTTPException(422, "month must be YYYY-MM")
    return f"{month}-01", f"{month}-{last:02d}", date(year, mon, 1), date(year, mon, last)


def working_days(start: date, end: date) -> int:
    n = 0
    while start <= end:
        n += start.weekday() < 5
        start += timedelta(days=1)
    return n


def round_half_up(value, places=2):
    if value is None: return None
    return float(Decimal(str(value)).quantize(Decimal("1." + "0"*places), rounding=ROUND_HALF_UP))


def month_match(month: str):
    first, last, _, _ = month_bounds(month)
    return {"date": {"$gte": first, "$lte": last}}


def employee_month_pipeline(emp_code: str, month: str):
    return [{"$match": {"emp_code": emp_code, **month_match(month)}}, {"$set": {"_day_of_week": {"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}}}, {"$group": {"_id": None,
        "present_days": {"$sum": {"$cond": [{"$and": [{"$in": ["$status", list(PRESENCE)]}, {"$gte": ["$_day_of_week", 2]}, {"$lte": ["$_day_of_week", 6]}]}, {"$cond": ["$half_day", 0.5, 1]}, 0]}},
        "leave_days": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
        "late_count": {"$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}},
        "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}}, "total_overtime_minutes": {"$sum": {"$ifNull": ["$overtime_minutes", 0]}}}}]


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str, user=Depends(admin_user)):
    emp = db.employees.find_one({"emp_code": emp_code}, {"_id": 0, "joined_on": 1})
    if not emp: raise HTTPException(404, "employee not found")
    _, _, first, last = month_bounds(month)
    start = max(first, date.fromisoformat(emp["joined_on"]))
    wd = working_days(start, last) if start <= last else 0
    vals = list(db.attendance_logs.aggregate(employee_month_pipeline(emp_code, month)))
    v = vals[0] if vals else {"present_days": 0, "leave_days": 0, "late_count": 0, "total_late_minutes": 0, "total_overtime_minutes": 0}
    return {"emp_code": emp_code, "month": month, "working_days": wd, "present_days": round_half_up(v["present_days"]), "leave_days": v["leave_days"],
        "late_count": v["late_count"], "total_late_minutes": v["total_late_minutes"], "total_overtime_minutes": v["total_overtime_minutes"],
        "attendance_pct": round_half_up(v["present_days"] / wd * 100, 4) if wd else None}


@app.get("/me/attendance")
def my_attendance(date_from: str | None = None, date_to: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), user=Depends(employee_user)):
    if date_from: valid_date(date_from)
    if date_to: valid_date(date_to)
    if date_from and date_to and date_from > date_to: raise HTTPException(422, "date_from must be on or before date_to")
    query = {"emp_code": user["emp_code"]}
    if date_from or date_to:
        query["date"] = {}
        if date_from: query["date"]["$gte"] = date_from
        if date_to: query["date"]["$lte"] = date_to
    total = db.attendance_logs.count_documents(query)
    docs = db.attendance_logs.find(query, {"_id": 0}).sort("date", DESCENDING).skip((page-1)*page_size).limit(page_size)
    return {"items": [public_record(d) for d in docs], "total": total, "page": page, "page_size": page_size}


@app.post("/me/attendance/punch-in", status_code=201)
def my_punch_in(body: PunchInIn, user=Depends(employee_user)):
    return punch_in(PunchInIn(emp_code=user["emp_code"], punched_at=body.punched_at, status=body.status), user)


@app.post("/me/attendance/punch-out")
def my_punch_out(body: PunchOutIn, user=Depends(employee_user)):
    return punch_out(PunchOutIn(emp_code=user["emp_code"], punched_at=body.punched_at), user)


@app.get("/me/attendance/monthly")
def my_monthly(month: str, user=Depends(employee_user)):
    return employee_monthly(user["emp_code"], month, user)


def department_summary_pipeline(month: str, department: str | None):
    first, last, _, _ = month_bounds(month)
    match = {"joined_on": {"$lte": last}}
    if department is not None: match["department"] = department
    return [{"$match": match}, {"$lookup": {"from": "attendance_logs", "let": {"code": "$emp_code", "dept": "$department"}, "pipeline": [
        {"$match": {"$expr": {"$and": [{"$eq": ["$emp_code", "$$code"]}, {"$gte": ["$date", first]}, {"$lte": ["$date", last]}]}}}], "as": "logs"}},
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}}, {"$set": {"_day_of_week": {"$cond": [{"$ne": ["$logs", None]}, {"$dayOfWeek": {"$dateFromString": {"dateString": "$logs.date"}}}, 0]}}}, {"$group": {"_id": "$department", "employees": {"$addToSet": "$emp_code"},
        "present_days": {"$sum": {"$cond": [{"$and": [{"$in": ["$logs.status", list(PRESENCE)]}, {"$gte": ["$_day_of_week", 2]}, {"$lte": ["$_day_of_week", 6]}]}, {"$cond": ["$logs.half_day", 0.5, 1]}, 0]}},
        "hours_sum": {"$sum": {"$cond": [{"$and": [{"$in": ["$logs.status", list(PRESENCE)]}, {"$ne": ["$logs.work_hours", None]}]}, "$logs.work_hours", 0]}},
        "hours_count": {"$sum": {"$cond": [{"$and": [{"$in": ["$logs.status", list(PRESENCE)]}, {"$ne": ["$logs.work_hours", None]}]}, 1, 0]}},
        "late_count": {"$sum": {"$cond": [{"$gt": ["$logs.late_minutes", 0]}, 1, 0]}}, "total_late_minutes": {"$sum": {"$ifNull": ["$logs.late_minutes", 0]}},
        "leave_count": {"$sum": {"$cond": [{"$eq": ["$logs.status", "LEAVE"]}, 1, 0]}}, "on_duty_count": {"$sum": {"$cond": [{"$eq": ["$logs.status", "ON_DUTY"]}, 1, 0]}}}},
        {"$project": {"_id": 0, "department": "$_id", "headcount": {"$size": "$employees"}, "present_days": 1, "avg_work_hours": {"$cond": [{"$gt": ["$hours_count", 0]}, {"$divide": ["$hours_sum", "$hours_count"]}, None]}, "late_count": 1, "total_late_minutes": 1, "leave_count": 1, "on_duty_count": 1}}, {"$sort": {"department": 1}}]


@app.get("/analytics/departments/summary")
def department_summary(month: str, department: str | None = None, user=Depends(admin_user)):
    vals = list(db.employees.aggregate(department_summary_pipeline(month, department), allowDiskUse=True))
    for item in vals:
        item["present_days"] = round_half_up(item["present_days"])
        item["avg_work_hours"] = round_half_up(item["avg_work_hours"]) if item["avg_work_hours"] is not None else None
    return {"month": month, "items": vals}


def leaderboard_pipeline(month: str, department: str | None, limit: int):
    first, last, _, _ = month_bounds(month)
    p = [{"$match": {"date": {"$gte": first, "$lte": last}}}, {"$group": {"_id": "$emp_code", "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}}, "late_count": {"$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}}}}, {"$match": {"total_late_minutes": {"$gt": 0}}},
         {"$lookup": {"from": "employees", "localField": "_id", "foreignField": "emp_code", "as": "emp"}}, {"$unwind": "$emp"}]
    if department is not None: p.append({"$match": {"emp.department": department}})
    p += [{"$setWindowFields": {"sortBy": {"total_late_minutes": -1}, "output": {"rank": {"$rank": {}}}}}, {"$match": {"rank": {"$lte": limit}}}, {"$sort": {"total_late_minutes": -1, "_id": 1}},
          {"$project": {"_id": 0, "rank": 1, "emp_code": "$_id", "name": "$emp.name", "department": "$emp.department", "total_late_minutes": 1, "late_count": 1}}]
    return p


@app.get("/analytics/leaderboard/late")
def late_leaderboard(month: str, limit: int = Query(10, ge=1, le=50), department: str | None = None, user=Depends(admin_user)):
    return {"month": month, "items": list(db.attendance_logs.aggregate(leaderboard_pipeline(month, department, limit), allowDiskUse=True))}


def department_trend_pipeline(department: str, start: str, end: str):
    start_dt = datetime.combine(date.fromisoformat(start), time.min, tzinfo=UTC)
    end_dt = datetime.combine(date.fromisoformat(end), time.min, tzinfo=UTC)
    return [{"$match": {"department": department}}, {"$project": {"emp_code": 1, "joined_on": 1}},
        {"$addFields": {"days": {"$map": {"input": {"$range": [0, {"$add": [{"$dateDiff": {"startDate": start_dt, "endDate": end_dt, "unit": "day"}}, 1]}]}, "as": "offset", "in": {"$dateAdd": {"startDate": start_dt, "unit": "day", "amount": "$$offset"}}}}}},
        {"$unwind": "$days"},
        {"$lookup": {"from": "attendance_logs", "let": {"code": "$emp_code", "day": {"$dateToString": {"date": "$days", "format": "%Y-%m-%d"}}}, "pipeline": [{"$match": {"$expr": {"$and": [{"$eq": ["$emp_code", "$$code"]}, {"$eq": ["$date", "$$day"]}]}}}], "as": "logs"}},
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}}, {"$group": {"_id": "$days", "headcount": {"$sum": {"$cond": [{"$lte": ["$joined_on", {"$dateToString": {"date": "$days", "format": "%Y-%m-%d"}}]}, 1, 0]}},
        "present_count": {"$sum": {"$cond": [{"$in": ["$logs.status", list(PRESENCE)]}, {"$cond": ["$logs.half_day", 0.5, 1]}, 0]}},
        "late_count": {"$sum": {"$cond": [{"$gt": ["$logs.late_minutes", 0]}, 1, 0]}}}},
        {"$project": {"date": {"$dateToString": {"date": "$_id", "format": "%Y-%m-%d"}}, "headcount": 1, "present_count": 1, "late_count": 1,
        "is_working_day": {"$and": [{"$gte": [{"$dayOfWeek": "$_id"}, 2]}, {"$lte": [{"$dayOfWeek": "$_id"}, 6]}]}}},
        {"$set": {"attendance_rate": {"$cond": [{"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]}, {"$divide": ["$present_count", "$headcount"]}, None]}}},
        {"$setWindowFields": {"sortBy": {"date": 1}, "output": {"moving_avg_7d": {"$avg": "$attendance_rate", "window": {"documents": [-6, 0]}}}}},
        {"$project": {"_id": 0, "date": 1, "is_working_day": 1, "headcount": 1, "present_count": 1, "late_count": 1, "attendance_rate": 1, "moving_avg_7d": 1}}, {"$sort": {"date": 1}}]


@app.get("/analytics/departments/{department}/trend")
def department_trend(department: str, from_date: str = Query(alias="from"), to_date: str = Query(alias="to"), user=Depends(admin_user)):
    valid_date(from_date); valid_date(to_date)
    start, end = date.fromisoformat(from_date), date.fromisoformat(to_date)
    if end < start or (end-start).days + 1 > 92: raise HTTPException(422, "invalid trend date range")
    if not db.employees.find_one({"department": department}): raise HTTPException(404, "department not found")
    pipeline = department_trend_pipeline(department, from_date, to_date)
    items = list(db.employees.aggregate(pipeline, allowDiskUse=True))
    for item in items:
        item["attendance_rate"] = round_half_up(item["attendance_rate"], 4) if item["attendance_rate"] is not None else None
        item["moving_avg_7d"] = round_half_up(item["moving_avg_7d"], 4) if item["moving_avg_7d"] is not None else None
        item["present_count"] = round_half_up(item["present_count"])
    return {"department": department, "items": items}


def json_safe(value):
    return json.loads(json_util.dumps(value))


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(endpoint: str, emp_code: str | None = None, month: str | None = None, department: str | None = None,
                     limit: int = Query(10, ge=1, le=50), date_from: str | None = None, date_to: str | None = None,
                     status: str | None = None, from_date: str | None = Query(None, alias="from"), to_date: str | None = Query(None, alias="to"),
                     page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), user=Depends(admin_user)):
    if endpoint not in {"attendance_list", "employee_monthly", "department_summary", "late_leaderboard", "department_trend"}: raise HTTPException(422, "unknown endpoint")
    if endpoint != "attendance_list" and not month and endpoint in {"employee_monthly", "department_summary", "late_leaderboard"}: raise HTTPException(422, "month is required")
    if endpoint == "attendance_list":
        if status is not None and status not in STATUSES: raise HTTPException(422, "invalid status")
        if date_from: valid_date(date_from)
        if date_to: valid_date(date_to)
        if date_from and date_to and date_from > date_to: raise HTTPException(422, "date_from must be on or before date_to")
        query = {}
        if emp_code: query["emp_code"] = emp_code
        if status: query["status"] = status
        if date_from or date_to:
            query["date"] = {}
            if date_from: query["date"]["$gte"] = valid_date(date_from)
            if date_to: query["date"]["$lte"] = valid_date(date_to)
        command = {"find": "attendance_logs", "filter": query, "sort": {"date": -1, "emp_code": 1}, "skip": (page-1)*page_size, "limit": page_size}
        coll = "attendance_logs"
    elif endpoint == "employee_monthly":
        if not emp_code: raise HTTPException(422, "emp_code is required")
        command = {"aggregate": "attendance_logs", "pipeline": employee_month_pipeline(emp_code, month), "cursor": {}}
        coll = "attendance_logs"
    elif endpoint == "department_summary":
        command = {"aggregate": "employees", "pipeline": department_summary_pipeline(month, department), "cursor": {}}
        coll = "employees"
    elif endpoint == "late_leaderboard":
        command = {"aggregate": "attendance_logs", "pipeline": leaderboard_pipeline(month, department, limit), "cursor": {}}
        coll = "attendance_logs"
    else:
        if not department or not from_date or not to_date: raise HTTPException(422, "department, from, and to are required")
        valid_date(from_date); valid_date(to_date)
        start, end = date.fromisoformat(from_date), date.fromisoformat(to_date)
        if end < start or (end-start).days + 1 > 92: raise HTTPException(422, "invalid trend date range")
        command = {"aggregate": "employees", "pipeline": department_trend_pipeline(department, from_date, to_date), "cursor": {}}
        coll = "employees"
    try:
        raw = db.command("explain", command, verbosity="executionStats")
    except Exception as exc:
        raise HTTPException(422, f"could not explain endpoint: {exc}")
    return {"endpoint": endpoint, "collection": coll, "explain": json_safe(raw)}
