import os
import tempfile
from unittest import mock

from django.contrib.auth.models import User

from . import exports
from .models import AuditEntry, ExportProfile, FieldDef, Record, Settings, ref_names
from .tests import ApiTestBase, example, import_test_csv
from .vcard import render


class UserFieldTests(ApiTestBase):
    """Internal responsible (user) and internal contacts (users) reference Kartei users by username."""
    def setUp(self):
        super().setUp()
        User.objects.filter(username="read").update(first_name="Rita", last_name="Reader")
        User.objects.filter(username="admin").update(first_name="Anna", last_name="Admin")
        self.as_("maintainer")

    def create(self, **data):
        return self.j("post", "/api/records", {"kind": "person", "last_name": "X", "data": data}, status=201)

    def test_options_validation_and_deactivation(self):
        boot = self.j("get", "/api/bootstrap")
        f = next(f for f in boot["fields"] if f["key"] == "internal_contact")
        self.assertEqual(f["type"], "user")
        self.assertIn({"value": "read", "de": "Rita Reader", "en": "Rita Reader"}, f["options"])
        r = self.create(internal_contact="read", internal_contacts=["maintainer", "read", "maintainer"])
        self.assertEqual(r["data"]["internal_contacts"], ["maintainer", "read"])
        # admins are listed (for existing values) but can't be picked
        self.assertIn({"value": "admin", "de": "Anna Admin", "en": "Anna Admin", "pick": False}, f["options"])
        for key, v in (("internal_contact", "admin"), ("internal_contacts", ["admin"])):
            bad = self.j("post", "/api/records", {"kind": "person", "last_name": "A", "data": {key: v}}, status=400)
            self.assertEqual(bad["fields"][key], "invalid_user")
        rec = Record.objects.get(uid=r["uid"])
        rec.data["internal_contacts"] = ["admin", "read"]  # assigned before the rule: stays on unrelated edits
        rec.save()
        r = self.j("patch", f"/api/records/{r['uid']}", {"version": r["version"], "first_name": "Ann"})
        self.assertEqual(r["data"]["internal_contacts"], ["admin", "read"])
        bad = self.j("post", "/api/records", {"kind": "person", "last_name": "Y", "data": {"internal_contact": "nobody"}},
                     status=400)
        self.assertEqual(bad["fields"]["internal_contact"], "invalid_user")
        User.objects.filter(username="read").update(is_active=False)
        url = f"/api/records/{r['uid']}"
        # unchanged values of a deactivated user may stay, new assignments may not
        r = self.j("patch", url, {"version": r["version"], "data": {"internal_contact": "read",
                                                                    "internal_contacts": ["read"]}})
        bad = self.j("post", "/api/records", {"kind": "person", "last_name": "Z", "data": {"internal_contacts": ["read"]}},
                     status=400)
        self.assertEqual(bad["fields"]["internal_contacts"], "invalid_user")
        f = next(f for f in self.j("get", "/api/bootstrap")["fields"] if f["key"] == "internal_contact")
        self.assertIn({"value": "read", "de": "Rita Reader (inaktiv)", "en": "Rita Reader (inactive)", "pick": False},
                      f["options"])

    def test_names_in_vcard_and_export(self):
        r = self.create(internal_contact="read", internal_contacts=["maintainer"])
        rec = Record.objects.get(uid=r["uid"])
        rec.data["internal_contacts"] = ["admin", "maintainer"]  # an admin assigned before admins were excluded
        rec.save()
        vcf = render(rec, list(FieldDef.objects.all()), "ios", names=ref_names()).replace("\r\n ", "")
        self.assertIn("Ansprechpartner intern: Rita Reader", vcf)
        self.assertIn(r"interne Kontakte: Anna Admin\, maintainer", vcf)
        d = tempfile.mkdtemp()
        with mock.patch.object(exports, "EXPORT_ROOT", d):
            cols = [{"header": "U1", "person": "field:internal_contact", "company": ""},
                    {"header": "U2", "person": "field:internal_contacts", "company": ""}]
            p = ExportProfile.objects.create(name="u", format="csv", path=f"{d}/u.csv", columns=cols)
            self.assertEqual(exports.run(p), "ok", p.last_message)
            self.assertIn("Rita Reader;Anna Admin, maintainer", open(f"{d}/u.csv", encoding="utf-8-sig").read())

    def test_type_change_wraps_values(self):
        r = self.create(internal_contact="read")
        self.as_("admin")
        f = FieldDef.objects.get(key="internal_contact")
        self.j("patch", f"/api/admin/fields/{f.id}", {"type": "users"})
        rec = Record.objects.get(uid=r["uid"])
        self.assertEqual((rec.data["internal_contact"], rec.version), (["read"], r["version"] + 1))
        self.j("patch", f"/api/admin/fields/{f.id}", {"type": "text"}, status=400)

    def test_import_matches_users(self):
        csv_data = ("ID;Vorname;Nachname;Firma;Ansprechpartner intern;Kontakte intern\n"
                    "P-1;A;B;;RR;Anna Admin |##| maintainer, Unbekannt\n").encode()  # admins can't be assigned
        res = import_test_csv(csv_data, dry_run=False)
        data = Record.objects.get(legacy_id="P-1").data
        self.assertEqual((data["internal_contact"], data["internal_contacts"]), ("read", ["maintainer"]))
        self.assertTrue(any(w.startswith("@import_unknown_user") and "Unbekannt" in w and "Anna Admin" in w
                            for w in res["warnings"]))


class OpenOptionsTests(ApiTestBase):
    """Keywords: maintainers add new options inline; closed selects stay closed."""
    def test_inline_options(self):
        self.as_("maintainer")
        kw = FieldDef.objects.get(key="keywords")
        self.assertTrue(kw.open_options)
        r = self.j("post", "/api/records", {"kind": "person", "last_name": "A", "data": {"keywords": ["  Neues   Wort "]}},
                   status=201)
        self.assertEqual(r["data"]["keywords"], ["Neues Wort"])
        kw.refresh_from_db()
        self.assertIn({"value": "Neues Wort", "de": "Neues Wort", "en": "Neues Wort"}, kw.options)
        self.assertTrue(AuditEntry.objects.filter(target_type="field", target_id=str(kw.id)).exists())
        # same text in another case maps onto the existing option
        r = self.j("post", "/api/records", {"kind": "person", "last_name": "B", "data": {"keywords": ["neues wort"]}},
                   status=201)
        self.assertEqual(r["data"]["keywords"], ["Neues Wort"])
        kw.refresh_from_db()
        self.assertEqual(sum(o["value"] == "Neues Wort" for o in kw.options), 1)
        boot = self.j("get", "/api/bootstrap")
        self.assertTrue(next(f for f in boot["fields"] if f["key"] == "keywords")["open_options"])

    def test_closed_select_rejects_new(self):
        FieldDef.objects.create(key="prio", label_de="Prio", label_en="Prio", applies_to="both", type="select",
                                options=[{"value": "A", "de": "A", "en": "A"}])
        self.as_("maintainer")
        bad = self.j("post", "/api/records", {"kind": "person", "last_name": "C", "data": {"prio": "B"}}, status=400)
        self.assertEqual(bad["fields"]["prio"], "invalid_option")
        self.assertEqual(len(FieldDef.objects.get(key="prio").options), 1)


class FieldFileTests(ApiTestBase):
    """Field definitions as a JSON file: export, preview, apply."""
    def test_roundtrip_matches_example(self):
        self.as_("admin")
        FieldDef.objects.filter(key="internal_contact").update(required=True)  # as in the file
        doc = self.j("get", "/api/admin/fields/export")
        self.assertEqual(doc, example("fields-example.json"))
        res = self.j("post", "/api/admin/fields/import", {"document": doc})["fields"]
        self.assertEqual({r["status"] for r in res}, {"unchanged"})

    def test_preview_and_apply(self):
        self.as_("admin")
        Record.objects.create(kind="person", last_name="A", data={"email": "a@x.de"})
        doc = example("fields-example.json")
        fields = {f["key"]: f for f in doc["fields"]}
        fields["mobile"]["label"]["de"] = "Handy"
        fields["mobile"]["import_aliases"] = "Handynummer, Mobil"
        fields["email"]["type"] = "integer"  # has data: incompatible
        doc["fields"] = [fields["mobile"], fields["email"], {"key": "Bad Key"},
                         {**fields["fax"], "key": "fax2", "copy_from": "company_fax"},
                         {**fields["fax"], "key": "fax3", "copy_from": "nope"}]
        res = {r["key"]: r for r in self.j("post", "/api/admin/fields/import", {"document": doc})["fields"]}
        self.assertEqual((res["mobile"]["status"], res["mobile"]["changes"]), ("changed", ["import_aliases", "label_de"]))
        self.assertEqual((res["email"]["status"], res["email"]["errors"]), ("invalid", {"type": "incompatible_type"}))
        self.assertEqual(res["Bad Key"]["status"], "invalid")
        self.assertEqual((res["fax2"]["status"], res["fax3"]["errors"]), ("new", {"copy_from": "invalid"}))
        self.assertFalse(FieldDef.objects.filter(key="fax2").exists())  # preview only
        self.j("post", "/api/admin/fields/import", {"document": doc, "apply": True})
        m = FieldDef.objects.get(key="mobile")
        self.assertEqual((m.label_de, m.import_aliases), ("Handy", ["Handynummer", "Mobil"]))
        self.assertEqual(FieldDef.objects.get(key="fax2").copy_from.key, "company_fax")
        self.assertEqual(FieldDef.objects.get(key="email").type, "email")
        self.assertTrue(FieldDef.objects.filter(key="website").exists())  # not in the file: untouched
        self.assertTrue(AuditEntry.objects.filter(target_type="field", action="update", target_label="Handy").exists())
        self.j("post", "/api/admin/fields/import", {"document": {"fields": []}}, status=400)

    def test_init_app_loads_initial_fields(self):
        from django.core.management import call_command
        FieldDef.objects.all().delete()
        Settings.objects.all().delete()
        call_command("init_app", stdout=open(os.devnull, "w"))
        self.assertEqual(list(FieldDef.objects.values_list("key", flat=True)),
                         [f["key"] for f in example("fields-default.json")["fields"]])
        FieldDef.objects.all().delete()
        Settings.objects.all().delete()
        with mock.patch.dict(os.environ, {"INITIAL_FIELDS": ""}):
            call_command("init_app", stdout=open(os.devnull, "w"))
        self.assertFalse(FieldDef.objects.exists())
