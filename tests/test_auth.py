"""Focused auth boundary tests that do not require a live MongoDB server."""
import os
import unittest
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import patch

from starlette.requests import Request
from starlette.responses import Response

_old_uri = os.environ.get("MONGO_URI")
os.environ["MONGO_URI"] = "mongodb://127.0.0.1:27017"
from app import main as api
if _old_uri is None:
    os.environ.pop("MONGO_URI", None)
else:
    os.environ["MONGO_URI"] = _old_uri


class Cursor:
    def sort(self, *args): return self
    def skip(self, *args): return self
    def limit(self, *args): return self
    def __iter__(self): return iter([])


class DeleteResult:
    def __init__(self, deleted_count): self.deleted_count = deleted_count


class Collection:
    def __init__(self, docs=()):
        self.docs = [deepcopy(doc) for doc in docs]
        self.last_query = None

    def find_one(self, query, projection=None):
        self.last_query = query
        for doc in self.docs:
            if all(doc.get(key) == value for key, value in query.items() if not isinstance(value, dict)):
                if "expires_at" in query and not doc["expires_at"] > query["expires_at"]["$gt"]:
                    continue
                return deepcopy(doc)
        return None

    def insert_one(self, doc):
        self.docs.append(deepcopy(doc))

    def update_one(self, *args, **kwargs): pass
    def delete_one(self, query):
        before = len(self.docs)
        self.docs = [doc for doc in self.docs if not all(doc.get(key) == value for key, value in query.items())]
        return DeleteResult(before - len(self.docs))
    def delete_many(self, query):
        before = len(self.docs)
        self.docs = [doc for doc in self.docs if not all(doc.get(key) == value for key, value in query.items())]
        return DeleteResult(before - len(self.docs))
    def count_documents(self, query):
        self.last_query = query
        return 0
    def find(self, query, projection):
        self.last_query = query
        return Cursor()


class AttendanceCollection(Collection):
    def find_one_and_update(self, query, update, return_document=None):
        doc = self.docs[0]
        doc.update(deepcopy(update["$set"]))
        doc.setdefault("history", []).append(deepcopy(update["$push"]["history"]))
        return deepcopy(doc)


class FakeDatabase:
    pass


def request_with_cookies(method, token, csrf, header=None):
    headers = [(b"cookie", f"northstar_session={token}; northstar_csrf={csrf}".encode())]
    if header is not None:
        headers.append((b"x-csrf-token", header.encode()))
    return Request({"type": "http", "method": method, "path": "/test", "headers": headers,
                    "query_string": b"", "server": ("test", 80), "client": ("test", 1), "scheme": "http"})


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeDatabase()
        self.db.auth_users = Collection([{"_id": "admin-id", "email": "admin@example.com", "name": "Admin",
                                          "password_hash": api.password_hasher.hash("correct-horse-battery"),
                                          "role": "admin", "active": True}])
        self.db.auth_sessions = Collection()
        self.db.employees = Collection([{"emp_code": "EMP0001", "email": "employee@example.com", "name": "Employee",
                                         "shift_start": "09:30", "shift_end": "18:30"}])
        self.db.attendance_logs = AttendanceCollection()
        self.patch_db = patch.object(api, "db", self.db)
        self.patch_db.start()
        self.addCleanup(self.patch_db.stop)

    def test_invalid_password_is_rejected(self):
        with self.assertRaises(api.HTTPException) as raised:
            api.login(api.LoginIn(email="admin@example.com", password="incorrect-password"), Response())
        self.assertEqual(raised.exception.status_code, 401)
        self.assertEqual(self.db.auth_sessions.docs, [])

    def test_login_stores_hashed_session_and_sets_http_only_cookie(self):
        response = Response()
        result = api.login(api.LoginIn(email="ADMIN@example.com", password="correct-horse-battery"), response)
        self.assertEqual(result["role"], "admin")
        self.assertEqual(len(self.db.auth_sessions.docs), 1)
        session = self.db.auth_sessions.docs[0]
        self.assertNotIn("correct-horse-battery", str(session))
        self.assertEqual(session["expires_at"].tzinfo, timezone.utc)
        self.assertIn("HttpOnly", response.headers["set-cookie"])

    def test_csrf_token_must_match_cookie_and_session(self):
        token, csrf = "opaque-session", "csrf-value"
        self.db.auth_sessions.docs.append({"token_hash": api.token_hash(token), "csrf_hash": api.token_hash(csrf),
                                          "user_id": "admin-id", "expires_at": datetime.now(timezone.utc) + timedelta(hours=1)})
        self.assertEqual(api.current_user(request_with_cookies("POST", token, csrf, csrf))["role"], "admin")
        with self.assertRaises(api.HTTPException) as raised:
            api.current_user(request_with_cookies("POST", token, csrf, "wrong"))
        self.assertEqual(raised.exception.status_code, 403)

    def test_admin_and_employee_roles_are_separated(self):
        with self.assertRaises(api.HTTPException) as raised:
            api.admin_user({"role": "employee"})
        self.assertEqual(raised.exception.status_code, 403)
        with self.assertRaises(api.HTTPException) as raised:
            api.employee_user({"role": "admin"})
        self.assertEqual(raised.exception.status_code, 403)

    def test_employee_history_query_is_scoped_to_linked_code(self):
        api.my_attendance(None, None, 1, 20, {"role": "employee", "emp_code": "EMP0001"})
        self.assertEqual(self.db.attendance_logs.last_query, {"emp_code": "EMP0001"})

    def test_future_punch_in_is_rejected(self):
        tomorrow = datetime.combine(date.today() + timedelta(days=1), time(10), tzinfo=api.IST)
        with self.assertRaises(api.HTTPException) as raised:
            api.punch_in(api.PunchInIn(emp_code="EMP0001", punched_at=int(tomorrow.timestamp() * 1000)), {"role": "admin"})
        self.assertEqual(raised.exception.status_code, 422)

    def test_admin_can_move_future_record_to_correct_day_with_audit(self):
        tomorrow = date.today() + timedelta(days=1)
        old_punch = datetime.combine(tomorrow, time(10), tzinfo=api.IST).astimezone(timezone.utc)
        old_punch_out = old_punch + timedelta(hours=1)
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=10)
        today = now.astimezone(api.IST).date().isoformat()
        self.db.attendance_logs = AttendanceCollection([{"_id": "record1", "emp_code": "EMP0001", "date": tomorrow.isoformat(),
                                                          "status": "PRESENT", "punch_in": old_punch, "punch_out": old_punch_out,
                                                          "work_hours": None, "late_minutes": 30, "overtime_minutes": 0,
                                                          "half_day": False, "history": []}])
        self.db.attendance_logs.find_one = lambda query, *args, **kwargs: deepcopy(self.db.attendance_logs.docs[0])
        body = api.RegularizeIn(new_date=today, clear_punch_out=True, status="PRESENT", punch_in=int(now.timestamp() * 1000),
                                reason="Correct accidental future date", regularized_by="Ignored client value")
        result = api.regularize("EMP0001", tomorrow.isoformat(), body, {"role": "admin", "email": "admin@example.com"})
        self.assertEqual(result["date"], today)
        self.assertIsNone(result["punch_out"])
        self.assertEqual(result["history"][0]["by"], "admin@example.com")
        self.assertEqual(result["history"][0]["changes"]["date"], {"from": tomorrow.isoformat(), "to": today})
        self.assertIsNone(result["history"][0]["changes"]["punch_out"]["to"])

    def test_employee_delete_requires_exact_code_confirmation_and_cascades(self):
        self.db.employees = Collection([{"_id": "emp-doc", "emp_code": "EMP0001", "name": "Employee"}])
        self.db.auth_users.docs.append({"_id": "employee-id", "emp_code": "EMP0001", "role": "employee"})
        self.db.auth_sessions.docs.append({"token_hash": "employee-session", "user_id": "employee-id"})
        self.db.attendance_logs.docs.append({"emp_code": "EMP0001", "date": "2026-10-12"})
        with self.assertRaises(api.HTTPException) as raised:
            api.delete_employee("EMP0001", api.DeleteEmployeeIn(confirmation="EMP0002"), {"role": "admin"})
        self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(len(self.db.employees.docs), 1)

        result = api.delete_employee("EMP0001", api.DeleteEmployeeIn(confirmation="EMP0001"), {"role": "admin"})
        self.assertEqual(result["attendance_records_deleted"], 1)
        self.assertTrue(result["account_deleted"])
        self.assertEqual(result["sessions_deleted"], 1)
        self.assertEqual(self.db.employees.docs, [])
        self.assertEqual(self.db.attendance_logs.docs, [])


if __name__ == "__main__":
    unittest.main()
