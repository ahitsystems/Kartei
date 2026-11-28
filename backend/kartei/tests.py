"""Shared helpers for the test modules: sample data, field sets, the API test base class."""
import json
import uuid
from pathlib import Path

from django.contrib.auth.models import User
from django.test import TestCase

from . import graph
from .api import import_fields
from .importer import import_csv
from .models import FieldDef, Profile, Record, Role, Settings

PERSON_UID = uuid.UUID("ad081c47-96f6-4b88-9d9b-67faf688772d")


COMPANY_UID = uuid.UUID("cb114125-b78f-43fd-85a0-4fcdea9a8cac")


EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


# Import mapping for the test CSVs: columns map by suggestion; companies put their short name into "Vorname",
# IDs "F-…" are companies, "P-…" persons.
IMPORT_MAPPING = {"format": "kartei-import-mapping", "version": 1,
                  "columns": {"Vorname": {"person": "core:first_name", "company": "field:short_name"}},
                  "kind": {"column": "", "company_prefix": "F-", "person_prefix": "P-"}}


def example(name):
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def load_fields(name="fields-example.json"):
    """A full field set through the field import."""
    res = import_fields(example(name), apply=True)["fields"]
    assert all(r["status"] == "new" for r in res), res


def import_test_csv(raw, dry_run=True):
    return import_csv(raw, mapping=IMPORT_MAPPING, dry_run=dry_run)


def sample_records():
    load_fields()
    muster = Record.objects.create(kind="company", name="Muster IT GmbH")
    person = Record.objects.create(
        uid=PERSON_UID, kind="person", first_name="Alexander", last_name="Meier", company=muster, data={
            "academic_title": "Prof. Dr. Dr.", "middle_name": "Ralph", "nickname": "Alex",
            "phonetic_first": "Ahlecksanda", "phonetic_middle": "Ralf", "phonetic_last": "Maiher",
            "job_title": "Rechenzentrums- & Systembetrieb", "department": "Informatik",
            "email": "a.meier@acme.com", "email_private": "alex.meier@gmx.de", "email_other": "a.meier@gmail.com",
            "mobile": "01701234567", "phone_home": "050311234567", "phone_work": "05111234567",
            "assistant_phone": "05111234568", "fax": "051112345671117", "fax_home": "0267612312311",
            "website": "https://acme.com", "birthday": "1994-01-01",
            "work_street": "Muster Straße 1", "work_extra": "Empfang", "work_city": "Hannover", "work_zip": "30159",
            "work_country": "Deutschland", "home_street": "Muster Straße 24a", "home_extra": "3. OG",
            "home_city": "Garbsen", "home_zip": "30823", "home_country": "Deutschland",
            "assistant": "Friedrich März", "manager": "Donald Trumpet", "internal_contact": "KK",
            "internal_contacts": "KA,ZZ", "description": "Trink seinen Kaffe mit Milch"})
    acme = Record.objects.create(
        uid=COMPANY_UID, kind="company", name="ACME GmbH", data={
            "short_name": "ACME", "company_email": "info@acme.com", "email_privacy": "datenschutz@acme.com",
            "email_billing": "einkauf@acme.com", "company_phone": "05111231232", "company_fax": "0511123123123",
            "website": "https://acme.com", "work_street": "Muster Straße 1", "work_city": "Hannover",
            "work_zip": "30159", "work_country": "Deutschland", "ceo": "Angela Merklin",
            "internal_contact": "KK", "internal_contacts": "KA,UI", "description": "Test"})
    return person, acme


class ApiTestBase(TestCase):
    def setUp(self):
        Settings.get()
        load_fields()
        FieldDef.objects.filter(key="internal_contact").update(required=False)  # ReviewTests cover the required case
        self.users = {}
        for role in Role.values:
            u = User.objects.create_user(role, password="pw-" + role * 3)
            Profile.objects.create(user=u, role=role)
            self.users[role] = u

    def as_(self, role):
        self.client.force_login(self.users[role])

    def j(self, method, url, body=None, status=200):
        resp = getattr(self.client, method)(url, json.dumps(body) if body is not None else "",
                                            content_type="application/json")
        self.assertEqual(resp.status_code, status, resp.content[:500])
        return resp.json() if resp.get("Content-Type", "").startswith("application/json") else resp


class FakeGraph:
    """In-memory stand-in for graph.http: one group, mailboxes with contact folders."""
    def __init__(self, users):
        self.users = dict(users)  # id -> mail
        self.folders = {}  # (user, fid) -> {"name", "contacts": {cid: body}}
        self.calls = 0
        self.n = 0
        self.photo_fails = False

    def _id(self):
        self.n += 1
        return f"id{self.n}"

    def __call__(self, method, url, body=None, token=None, form=None, ctype=None):
        from urllib.parse import unquote
        self.calls += 1
        if "login.microsoftonline.com" in url:
            return {"access_token": "tok"}
        path = unquote(url.removeprefix(graph.GRAPH))
        if path == "/$batch":
            return {"responses": [{"id": r["id"], **self._one(r["method"], r["url"], r.get("body"))}
                                  for r in body["requests"]]}
        res = self._one(method, path, body)
        if res["status"] >= 400:
            raise graph.GraphError(f"HTTP {res['status']}", res["status"])
        return res["body"]

    def _one(self, method, path, body):
        p, _, q = path.partition("?")
        parts = p.strip("/").split("/")
        if parts[0] == "groups":
            return {"status": 200, "body": {"value": [{"@odata.type": "#microsoft.graph.user", "id": u, "mail": m,
                                                       "userPrincipalName": f"{u}@x.de"}
                                                      for u, m in self.users.items()]}}
        user = parts[1]
        if user not in self.users and not any(k[0] == user for k in self.folders):
            return {"status": 404, "body": {"error": {"message": "no mailbox"}}}
        if len(parts) == 3:  # contactFolders
            if method == "POST":
                fid = self._id()
                self.folders[(user, fid)] = {"name": body["displayName"], "contacts": {}}
                return {"status": 201, "body": {"id": fid}}
            name = q.split("'")[1]
            return {"status": 200, "body": {"value": [{"id": f} for (u, f), d in self.folders.items()
                                                      if u == user and d["name"] == name]}}
        folder = self.folders.get((user, parts[3]))
        if folder is None:
            return {"status": 404, "body": {}}
        if len(parts) == 4 and method == "DELETE":
            del self.folders[(user, parts[3])]
            return {"status": 204, "body": {}}
        contacts = folder["contacts"]
        if len(parts) == 5 and method == "GET":
            return {"status": 200, "body": {"value": [
                {"id": cid, **({"singleValueExtendedProperties": c["singleValueExtendedProperties"]}
                               if "singleValueExtendedProperties" in c else {})} for cid, c in contacts.items()]}}
        if method == "POST":
            cid = self._id()
            contacts[cid] = body
            return {"status": 201, "body": {"id": cid}}
        if parts[5] not in contacts:
            return {"status": 404, "body": {}}
        if parts[6:] == ["photo", "$value"]:
            if self.photo_fails:
                return {"status": 500, "body": {"error": {"message": "boom"}}}
            contacts[parts[5]]["photo"] = body
            return {"status": 200, "body": {}}
        if method == "PATCH":
            contacts[parts[5]] |= body
        else:
            del contacts[parts[5]]
        return {"status": 200, "body": {}}

    def contacts(self, user):
        return [c for (u, _), d in self.folders.items() if u == user for c in d["contacts"].values()]
