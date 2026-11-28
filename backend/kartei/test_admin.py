import json
import os
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, TransactionTestCase, override_settings

from .models import AuditEntry, FieldDef, Profile, Proposal, Record, Review, Settings
from .tests import ApiTestBase


class AdminApiTests(ApiTestBase):
    def test_fields_crud(self):
        self.as_("admin")
        f = self.j("post", "/api/admin/fields", {"label": {"de": "Größe", "en": "Size"}, "applies_to": "person",
                                                 "type": "select", "options": [{"value": "S"}, {"value": "M"}]},
                   status=201)
        self.assertEqual(f["key"], "size")
        self.j("patch", f"/api/admin/fields/{f['id']}", {"type": "multiselect"})
        Record.objects.create(kind="person", last_name="Z", data={"size": ["S"]})
        bad = self.j("patch", f"/api/admin/fields/{f['id']}", {"type": "date"}, status=400)
        self.assertEqual(bad["fields"]["type"], "incompatible_type")
        self.j("delete", f"/api/admin/fields/{f['id']}", status=409)
        self.j("patch", f"/api/admin/fields/{f['id']}", {"archived": True})
        self.assertTrue(AuditEntry.objects.filter(target_type="field", action="update").exists())

    def test_field_regex_and_help(self):
        self.as_("admin")
        bad = self.j("post", "/api/admin/fields", {"label": {"de": "Kd-Nr", "en": "Cust no"}, "applies_to": "both",
                                                   "type": "text", "regex": "([A-Z"}, status=400)
        self.assertEqual(bad["fields"], {"regex": "invalid_regex"})
        f = self.j("post", "/api/admin/fields", {
            "label": {"de": "Kd-Nr", "en": "Cust no"}, "applies_to": "both", "type": "text", "regex": r"K-\d{5}",
            "help": {"de": "Format K-12345", "en": "Format K-12345"}}, status=201)
        self.assertEqual((f["regex"], f["help"]["de"]), (r"K-\d{5}", "Format K-12345"))
        n = self.j("post", "/api/admin/fields", {"label": {"de": "Zahl", "en": "Num"}, "applies_to": "both",
                                                 "type": "integer", "regex": "x"}, status=201)
        self.assertEqual(n["regex"], "")  # regex only applies to text-like types
        self.as_("maintainer")
        err = self.j("post", "/api/records", {"kind": "company", "name": "A", "data": {f["key"]: "K-123"}}, status=400)
        self.assertEqual(err["fields"], {f["key"]: "pattern_mismatch"})
        self.j("post", "/api/records", {"kind": "company", "name": "A", "data": {f["key"]: "K-12345"}}, status=201)

    def test_users_and_last_admin(self):
        self.as_("admin")
        u = self.j("post", "/api/admin/users", {"username": "neu", "password": "long-enough-pw", "role": "read"},
                   status=201)
        self.j("patch", f"/api/admin/users/{u['id']}", {"role": "maintainer"})
        self.assertEqual(Profile.objects.get(user_id=u["id"]).role, "maintainer")
        self.j("patch", f"/api/admin/users/{self.users['admin'].id}", {"role": "read"}, status=409)
        self.j("delete", f"/api/admin/users/{self.users['admin'].id}", status=409)

    def test_settings_secrets(self):
        self.as_("admin")
        s = self.j("put", "/api/admin/settings", {"ldap_uri": "ldaps://dc", "ldap_bind_password": "geheim"})
        self.assertTrue(s["ldap_bind_password_set"])
        self.assertNotIn("geheim", json.dumps(s))
        self.assertEqual(Settings.get().ldap_bind_password, "geheim")
        self.assertNotIn("geheim", json.dumps(list(AuditEntry.objects.values_list("changes", flat=True))))
        r = self.j("put", "/api/admin/settings", {"graph_enabled": True, "graph_tenant_id": "t"}, status=400)
        self.assertEqual(r["fields"], {"graph_enabled": "graph_incomplete"})
        r = self.j("put", "/api/admin/settings", {"graph_folder_responsible": " kartei "}, status=400)
        self.assertEqual(r["fields"], {"graph_folder_responsible": "duplicate"})
        self.j("put", "/api/admin/settings", {"graph_folder_responsible": " Meine ", "graph_folder_internal": "Mine"})
        self.assertEqual(Settings.get().graph_folder_responsible, "Meine")
        r = self.j("put", "/api/admin/settings", {"graph_folder_responsible": "mine "}, status=400)
        self.assertEqual(r["fields"], {"graph_folder_internal": "duplicate"})
        s = self.j("put", "/api/admin/settings", {"graph_enabled": True, "graph_tenant_id": "t", "graph_client_id": "c",
                                                  "graph_group_id": "g", "graph_client_secret": "graphpw"})
        self.assertTrue(s["graph_client_secret_set"])
        self.assertNotIn("graphpw", json.dumps(s) + json.dumps(list(AuditEntry.objects.values_list("changes", flat=True))))
        self.assertEqual(Settings.get().graph_client_secret, "graphpw")
        self.assertTrue(self.j("post", "/api/admin/graph/sync")["graph_sync_requested"])
        self.j("put", "/api/admin/settings", {"graph_enabled": False})
        self.j("post", "/api/admin/graph/sync", status=409)
        self.as_("maintainer")
        self.j("post", "/api/admin/graph/sync", status=403)

    def test_login_and_language(self):
        self.client.logout()
        self.j("post", "/api/login", {"username": "read", "password": "wrong"}, status=401)
        self.j("post", "/api/login", {"username": "read", "password": "pw-" + "read" * 3})
        me = self.j("patch", "/api/me", {"language": "en"})
        self.assertEqual(me["user"]["language"], "en")


class OpsTests(TestCase):
    @override_settings(TRUSTED_PROXIES=["127.0.0.0/8", "172.18.0.0/16"])  # "auto" in a container on that network
    def test_client_ip_behind_proxy(self):
        from django.test import RequestFactory, override_settings

        from .net import client_ip
        rf = RequestFactory()
        req = rf.get("/", REMOTE_ADDR="172.18.0.1", HTTP_X_FORWARDED_FOR="6.6.6.6, 10.0.0.7")
        self.assertEqual(client_ip(req), "10.0.0.7")  # what the proxy appended, not the client's claim
        with override_settings(PROXY_HEADERS=False):
            self.assertEqual(client_ip(req), "172.18.0.1")
        self.assertEqual(client_ip(rf.get("/", REMOTE_ADDR="10.0.0.8")), "10.0.0.8")

    def test_init_app_creates_admin_only_on_fresh_install(self):
        from django.core.management import call_command
        with mock.patch.dict(os.environ, {"ADMIN_USERNAME": "boss", "ADMIN_PASSWORD": "pw-boss-12345"}):
            call_command("init_app", stdout=open(os.devnull, "w"))
            self.assertTrue(User.objects.get(username="boss").is_superuser)
            User.objects.filter(username="boss").update(is_superuser=False)  # an LDAP admin exists instead
            Profile.objects.filter(user__username="boss").update(role="admin")
            call_command("init_app", stdout=open(os.devnull, "w"))  # must not crash or add a second admin
        self.assertEqual(User.objects.count(), 1)


class SeedDemoTests(TestCase):
    def test_seed_demo_once(self):
        import io

        from django.core.management import CommandError, call_command
        out = io.StringIO()
        call_command("seed_demo", password="demo-pass-123", stdout=out)
        self.assertGreater(Record.objects.filter(kind="person").count(), 30)
        self.assertEqual(Record.objects.filter(kind="company").count(), 6)
        self.assertTrue(User.objects.get(username="kkramer").check_password("demo-pass-123"))
        self.assertTrue(Review.objects.exists())
        self.assertEqual(Proposal.objects.filter(status="pending").count(), 4)  # incl. one new person
        new = Proposal.objects.get(kind="create")
        self.assertEqual(new.changes["data"]["job_title"], "Einkauf")  # field values survive the seeding
        self.assertEqual(Record.objects.filter(deleted_at__isnull=False).count(), 3)  # recycle bin has content
        kw = FieldDef.objects.get(key="keywords")  # used keywords are pickable options
        self.assertIn("Kunde", [o["value"] for o in kw.options])
        with self.assertRaises(CommandError):  # never on top of real data
            call_command("seed_demo", stdout=io.StringIO())
        with self.assertRaises(CommandError):  # --reset wipes everything: development only
            call_command("seed_demo", reset=True, stdout=io.StringIO())
        self.assertEqual(Record.objects.filter(kind="company").count(), 6)


class SeedDemoResetTests(TransactionTestCase):
    """not in a TestCase transaction: PostgreSQL refuses flush's TRUNCATE there (pending FK trigger events),
    while the real command runs outside a transaction"""
    def test_seed_demo_reset(self):
        import io

        from django.core.management import call_command
        from django.test import override_settings
        call_command("seed_demo", password="demo-pass-123", stdout=io.StringIO())
        Record.objects.filter(kind="company").first().delete()  # what a test run or a screenshot leaves behind
        User.objects.create_user("leftover")
        with override_settings(DEBUG=True), mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ADMIN_USERNAME", None)
            os.environ.pop("ADMIN_PASSWORD", None)
            call_command("seed_demo", reset=True, password="demo-pass-123", stdout=io.StringIO())
        self.assertEqual(Record.objects.filter(kind="company").count(), 6)  # fresh again
        self.assertFalse(User.objects.filter(username="leftover").exists())
        self.assertTrue(User.objects.get(username="admin").check_password("admin12345"))
        self.assertTrue(User.objects.get(username="kkramer").check_password("demo-pass-123"))


class PrintLogoTests(ApiTestBase):
    PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64

    def test_upload_choose_and_remove(self):
        self.as_("read")
        self.assertEqual(self.j("get", "/api/bootstrap")["print_logo"], "kartei")
        self.assertEqual(self.client.put("/api/admin/print-logo", self.PNG, content_type="image/png").status_code, 403)
        self.as_("admin")
        self.j("put", "/api/admin/settings", {"print_logo_mode": "custom"}, status=400)  # nothing uploaded yet
        r = self.client.put("/api/admin/print-logo", b"GIF89a....", content_type="image/gif")
        self.assertEqual(r.status_code, 400)
        r = self.client.put("/api/admin/print-logo", self.PNG, content_type="image/png").json()
        self.assertEqual(r["print_logo_mode"], "custom")
        v = r["print_logo_v"]
        self.as_("read")
        self.assertEqual(self.j("get", "/api/bootstrap")["print_logo"], v)
        got = self.client.get(f"/api/print-logo?v={v}")
        self.assertEqual((got.status_code, got["Content-Type"], got.content), (200, "image/png", self.PNG))
        self.as_("admin")
        self.j("put", "/api/admin/settings", {"print_logo_mode": "none"})
        self.assertEqual(self.j("get", "/api/bootstrap")["print_logo"], "none")
        self.assertEqual(self.j("get", "/api/admin/settings")["print_logo_v"], v)  # still there to switch back
        self.j("put", "/api/admin/settings", {"print_logo_mode": "custom"})
        self.assertEqual(self.client.delete("/api/admin/print-logo").json()["print_logo_mode"], "kartei")
        self.assertEqual(self.client.get("/api/print-logo").status_code, 404)
