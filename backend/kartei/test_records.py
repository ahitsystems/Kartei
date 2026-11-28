import tempfile
from unittest import mock

from django.contrib.auth.models import User
from django.test import override_settings

from . import batch, exports
from .models import AuditEntry, ExportProfile, FieldDef, Record, ref_names
from .tests import ApiTestBase, import_test_csv
from .vcard import render


class RecordApiTests(ApiTestBase):
    def test_roles(self):
        self.client.logout()
        self.j("get", "/api/bootstrap", status=401)
        self.as_("read")
        self.j("post", "/api/records", {"kind": "company", "name": "X"}, status=403)
        self.j("get", "/api/admin/fields", status=403)
        self.as_("maintainer")
        self.j("post", "/api/records", {"kind": "company", "name": "X"}, status=201)
        self.j("get", "/api/admin/fields", status=403)
        self.as_("admin")
        self.j("get", "/api/admin/fields")

    def test_create_update_conflict_audit(self):
        self.as_("maintainer")
        c = self.j("post", "/api/records", {"kind": "company", "name": "ACME", "data": {"company_phone": "0511 1"}},
                   status=201)
        p = self.j("post", "/api/records", {"kind": "person", "last_name": "Meier", "company": c["uid"],
                                            "data": {"email": "a@b.de", "keywords": []}}, status=201)
        self.assertEqual(p["company"], c["uid"])
        p2 = self.j("patch", f"/api/records/{p['uid']}", {"version": p["version"], "data": {"mobile": "+49 170 1"}})
        self.assertEqual(p2["version"], p["version"] + 1)
        stale = self.j("patch", f"/api/records/{p['uid']}", {"version": p["version"], "data": {"mobile": "1"}},
                       status=409)
        self.assertEqual(stale["record"]["data"]["mobile"], "+49 170 1")
        entry = AuditEntry.objects.filter(target_id=p["uid"], action="update").first()
        self.assertEqual(entry.changes, {"mobile": [None, "+49 170 1"]})
        self.assertEqual(entry.username, "maintainer")

    def test_validation(self):
        self.as_("maintainer")
        FieldDef.objects.create(key="count", label_de="Anzahl", label_en="Count", applies_to="both", type="integer",
                                min_value=0, max_value=10)
        FieldDef.objects.create(key="rate", label_de="Satz", label_en="Rate", applies_to="both", type="decimal",
                                decimals=2)
        FieldDef.objects.create(key="tags", label_de="Tags", label_en="Tags", applies_to="both", type="multiselect",
                                options=[{"value": "a", "de": "A", "en": "A"}, {"value": "b", "de": "B", "en": "B"}])
        bad = self.j("post", "/api/records", {"kind": "person", "last_name": "X", "data": {
            "count": "11", "rate": "abc", "tags": ["c"], "email": "nope", "mobile": "abc", "nope": 1}}, status=400)
        self.assertEqual(bad["fields"], {"count": "too_large", "rate": "invalid_number", "tags": "invalid_option",
                                         "email": "invalid_email", "mobile": "invalid_phone", "nope": "unknown_field"})
        ok = self.j("post", "/api/records", {"kind": "person", "last_name": "X", "data": {
            "count": "3", "rate": "1.234,5", "tags": ["b", "a", "b"], "work_zip": "AB-1234", "website": "acme.com"}},
            status=201)
        self.assertEqual(ok["data"], {"count": 3, "rate": "1234.50", "tags": ["b", "a"], "work_zip": "AB-1234",
                                      "website": "https://acme.com"})
        self.j("post", "/api/records", {"kind": "person", "data": {}}, status=400)

    def test_field_access(self):
        f = FieldDef.objects.get(key="contract_no")
        f.visible_to, f.editable_by = "maintainer", "admin"
        f.save()
        r = Record.objects.create(kind="person", last_name="Y", data={"contract_no": "secret"})
        self.as_("read")
        rec = next(x for x in self.j("get", "/api/bootstrap")["records"] if x["uid"] == str(r.uid))
        self.assertNotIn("contract_no", rec["data"])
        self.as_("maintainer")
        rec = self.j("get", f"/api/records/{r.uid}")
        self.assertEqual(rec["data"]["contract_no"], "secret")
        bad = self.j("patch", f"/api/records/{r.uid}", {"version": 1, "data": {"contract_no": "x"}}, status=400)
        self.assertEqual(bad["fields"], {"contract_no": "forbidden"})
        self.j("patch", f"/api/records/{r.uid}", {"version": 1, "data": {"contract_no": "secret", "mobile": "1 2 3"}})

    def test_recycle_bin(self):
        self.as_("maintainer")
        r = Record.objects.create(kind="company", name="Gone")
        boot = self.j("get", "/api/bootstrap")
        self.j("delete", f"/api/records/{r.uid}")
        self.assertNotIn(str(r.uid), [x["uid"] for x in self.j("get", "/api/bootstrap")["records"]])
        ch = self.j("get", f"/api/changes?since={boot['cursor'].replace('+', '%2B')}")
        self.assertIn(str(r.uid), ch["deleted"])
        self.assertEqual([x["uid"] for x in self.j("get", "/api/trash")["records"]], [str(r.uid)])
        self.j("delete", f"/api/trash/{r.uid}", status=403)  # purge is admin-only
        self.j("post", f"/api/records/{r.uid}/restore")
        self.assertIn(str(r.uid), [x["uid"] for x in self.j("get", "/api/bootstrap")["records"]])
        actions = list(AuditEntry.objects.filter(target_id=str(r.uid)).values_list("action", flat=True))
        self.assertEqual(actions, ["restore", "delete"])
        self.j("delete", f"/api/records/{r.uid}")
        self.as_("admin")
        self.j("delete", f"/api/trash/{r.uid}")
        self.assertFalse(Record.objects.filter(uid=r.uid).exists())
        self.assertTrue(AuditEntry.objects.filter(target_id=str(r.uid), action="purge").exists())

    def test_image(self):
        self.as_("maintainer")
        r = Record.objects.create(kind="company", name="Img")
        resp = self.client.put(f"/api/img/{r.uid}", b"not an image", content_type="image/png")
        self.assertEqual(resp.status_code, 400)
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 20
        resp = self.client.put(f"/api/img/{r.uid}", png, content_type="image/png")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.client.get(f"/api/img/{r.uid}").content, png)


class PostalCodeTests(ApiTestBase):
    """PLZ/ZIP must stay text: leading zeros and letters survive every path."""
    CODES = ["01067", "SW1A 1AA", "1010", "A-1010", "D-10115"]

    def test_preset_fields_are_text(self):
        self.assertEqual(set(FieldDef.objects.filter(key__endswith="_zip").values_list("type", flat=True)), {"text"})

    def test_roundtrip(self):
        from openpyxl import load_workbook
        self.as_("maintainer")
        for code in self.CODES:
            r = self.j("post", "/api/records", {"kind": "company", "name": f"Firma {code}", "data": {"work_zip": code}},
                       status=201)
            self.assertEqual(r["data"]["work_zip"], code)
            rec = Record.objects.get(uid=r["uid"])
            self.assertIn(f";{code};", render(rec, list(FieldDef.objects.all()), "ios").split("ADR;WORK:")[1].split("\r\n")[0])
        d = tempfile.mkdtemp()
        with mock.patch.object(exports, "EXPORT_ROOT", d):
            cols = [{"header": "PLZ", "person": "field:work_zip", "company": "field:work_zip"}]
            for fmt in ("csv", "xlsx"):
                p = ExportProfile.objects.create(name=fmt, format=fmt, path=f"{d}/plz.{fmt}", columns=cols)
                self.assertEqual(exports.run(p), "ok", p.last_message)
            csv_text = open(f"{d}/plz.csv", encoding="utf-8-sig").read()
            ws = load_workbook(f"{d}/plz.xlsx").active
        xlsx_values = [c.value for c in ws["A"][1:]]
        for code in self.CODES:
            self.assertIn(code, csv_text.splitlines())
            self.assertIn(code, xlsx_values)  # stored as text cell, not number
        self.assertTrue(all(isinstance(v, str) for v in xlsx_values))

    def test_import_keeps_leading_zero(self):
        csv_data = "ID;Vorname;Nachname;Firma;PLZ\nP-1;A;B;;01067\n".encode()
        import_test_csv(csv_data, dry_run=False)
        self.assertEqual(Record.objects.get(legacy_id="P-1").data["work_zip"], "01067")


class SessionTests(ApiTestBase):
    @override_settings(KARTEI_VERSION="9.9.9-test", KARTEI_SOURCE_URL="https://example.org/fork")
    def test_version_and_source_only_when_signed_in(self):
        self.assertNotIn("version", self.client.get("/api/session").json())
        self.as_("read")
        s = self.client.get("/api/session").json()
        self.assertEqual((s["version"], s["source"]), ("9.9.9-test", "https://example.org/fork"))

    def test_a_deactivated_user_loses_an_open_session(self):
        for backend in ("kartei.auth.DbLDAPBackend", "django.contrib.auth.backends.ModelBackend"):
            u = self.users["maintainer"]
            User.objects.filter(pk=u.pk).update(is_active=True)
            self.client.force_login(u, backend=backend)
            self.j("get", "/api/bootstrap")
            User.objects.filter(pk=u.pk).update(is_active=False)  # e.g. by the LDAP user import
            self.j("get", "/api/bootstrap", status=401)


class ContactFieldTests(ApiTestBase):
    """Managing director: a person of the company (stored as uid) or free text."""
    def setUp(self):
        super().setUp()
        self.as_("maintainer")
        self.co = self.j("post", "/api/records", {"kind": "company", "name": "Firma"}, status=201)
        self.p = self.j("post", "/api/records", {"kind": "person", "first_name": "Gerda", "last_name": "Chefin",
                                                 "company": self.co["uid"]}, status=201)

    def set_ceo(self, value, status=200):
        co = self.j("get", f"/api/records/{self.co['uid']}")
        return self.j("patch", f"/api/records/{self.co['uid']}", {"version": co["version"], "data": {"ceo": value}},
                      status=status)

    def test_link_free_text_and_rendering(self):
        self.assertEqual(self.set_ceo(self.p["uid"])["data"]["ceo"], self.p["uid"])
        rec = Record.objects.get(uid=self.co["uid"])
        fields = list(FieldDef.objects.all())
        self.assertIn("Gerda Chefin", render(rec, fields, "ios", names=ref_names()))
        Record.objects.filter(uid=self.p["uid"]).update(first_name="Gerda M.")
        self.assertIn("Gerda M. Chefin", render(rec, fields, "android", names=ref_names()))
        d = tempfile.mkdtemp()
        with mock.patch.object(exports, "EXPORT_ROOT", d):
            cols = [{"header": "GF", "person": "", "company": "field:ceo"}]
            p = ExportProfile.objects.create(name="gf", format="csv", path=f"{d}/gf.csv", columns=cols, kinds="company")
            self.assertEqual(exports.run(p), "ok", p.last_message)
            self.assertIn("Gerda M. Chefin", open(f"{d}/gf.csv", encoding="utf-8-sig").read())
        self.assertEqual(self.set_ceo("  Dr.  Hans   Extern ")["data"]["ceo"], "Dr. Hans Extern")
        rec.refresh_from_db()
        self.assertIn("Dr. Hans Extern", render(rec, fields, "ios", names=ref_names()))

    def test_closed_contact_field_rejects_text(self):
        FieldDef.objects.filter(key="ceo").update(open_options=False)
        self.assertEqual(self.set_ceo("Jemand", status=400)["fields"]["ceo"], "invalid_contact")
        self.assertEqual(self.set_ceo(self.co["uid"], status=400)["fields"]["ceo"], "invalid_contact")  # not a person

    def test_text_field_converts_with_free_text(self):
        FieldDef.objects.filter(key="ceo").update(type="text", open_options=False)
        self.set_ceo("Alter Wert")
        self.as_("admin")
        f = FieldDef.objects.get(key="ceo")
        r = self.j("patch", f"/api/admin/fields/{f.id}", {"type": "contact"})
        self.assertTrue(r["open_options"])
        self.as_("maintainer")
        self.set_ceo("Alter Wert")  # still valid


class BatchTests(ApiTestBase):
    """Company changes carried over to its persons."""
    def setUp(self):
        super().setUp()
        self.as_("maintainer")
        self.co = self.j("post", "/api/records", {"kind": "company", "name": "Alt GmbH", "data": {
            "company_phone": "0511 1000", "work_street": "Altweg 1", "hotline": "0800 123456"}}, status=201)
        mk = lambda last, data: self.j("post", "/api/records", {"kind": "person", "last_name": last,
                                                                "company": self.co["uid"], "data": data}, status=201)
        self.a = mk("A", {"phone_work": "0511 1000-12", "fax": "0511 1000-99", "work_street": "Altweg 1",
                          "mobile": "0170 1000123"})
        self.b = mk("B", {"phone_work": "+49 511 1000 13", "work_street": "Eigener Weg 5"})  # deviating
        self.changes = {"company_phone": ["0511 1000", "0511 4711"], "work_street": ["Altweg 1", "Neuweg 2"],
                        "hotline": ["0800 123456", "0800 654321"]}

    def test_swap_prefix(self):
        self.assertEqual(batch.swap_prefix("0511 1000-12", "0511 1000", "0511 4711"), "0511 4711-12")
        self.assertEqual(batch.swap_prefix("(0511) 1000 12", "05111000", "0511 4711"), "0511 4711 12")
        self.assertIsNone(batch.swap_prefix("0511 1001-12", "0511 1000", "0511 4711"))
        self.assertIsNone(batch.swap_prefix("110", "110", "112"))  # too short to be safe
        self.assertEqual(batch.swap_prefix("+49 511 1000 13", "0511 1000", "0511 4711"), "0511 4711 13")
        self.assertEqual(batch.swap_prefix("0049-511-1000-14", "0511 1000", "0511 4711"), "0511 4711-14")

    def test_preview_and_apply(self):
        items = self.j("post", f"/api/records/{self.co['uid']}/propagate", {"changes": self.changes})["items"]
        got = {(i["name"], i["key"]): i["new"] for i in items}
        self.assertEqual(got, {("A", "phone_work"): "+49 511 4711-12", ("A", "fax"): "+49 511 4711-99",
                               ("A", "work_street"): "Neuweg 2", ("B", "phone_work"): "+49 511 4711 13"})  # B keeps its street
        sel = [{"uid": self.a["uid"], "key": "phone_work"}, {"uid": self.a["uid"], "key": "work_street"}]
        res = self.j("post", f"/api/records/{self.co['uid']}/propagate", {"changes": self.changes, "selected": sel})
        self.assertEqual(res, {"updated": 1, "errors": []})
        a = Record.objects.get(uid=self.a["uid"])
        self.assertEqual((a.data["phone_work"], a.data["fax"], a.data["work_street"], a.version),
                         ("+49 511 4711-12", "+49 511 1000-99", "Neuweg 2", self.a["version"] + 1))
        e = AuditEntry.objects.filter(target_id=self.a["uid"], action="update").first()
        self.assertEqual(e.changes["batch_from"], [None, "Alt GmbH"])
        self.as_("read")
        self.j("post", f"/api/records/{self.co['uid']}/propagate", {"changes": self.changes}, status=403)


class ListPrefsTests(ApiTestBase):
    def test_list_order_and_name_display_are_per_user(self):
        self.as_("read")
        u = self.j("get", "/api/session")["user"]
        self.assertEqual((u["list_sort"], u["name_order"]), ("last_name", "first_last"))
        u = self.j("patch", "/api/me", {"list_sort": "updated", "name_order": "last_first"})["user"]
        self.assertEqual((u["list_sort"], u["name_order"]), ("updated", "last_first"))
        self.assertEqual(self.j("patch", "/api/me", {"list_sort": "random"}, status=400)["fields"], {"list_sort": "invalid"})
        self.j("patch", "/api/me", {"name_order": "x"}, status=400)
        self.as_("maintainer")  # someone else keeps the defaults
        self.assertEqual(self.j("get", "/api/session")["user"]["list_sort"], "last_name")
