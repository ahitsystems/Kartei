"""Kerberos sign-in (kerberos.py, api.kerberos_view) with the GSS library stubbed out; test_kerberos_live runs it for real."""
import base64
import time
from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client

from . import kerberos, totp
from .models import AuditEntry, Profile, Settings
from .tests import ApiTestBase

KEYTAB = base64.b64encode(b"\x05\x02" + b"\0" * 40).decode()
TICKET = "Negotiate " + base64.b64encode(b"\x60\x82ticket").decode()


def accepts(principal, realm="CORP.EXAMPLE", out=b"reply"):
    return mock.patch("kartei.kerberos._accept", return_value=(principal, realm, out))


class KerberosTests(ApiTestBase):
    def setUp(self):
        super().setUp()
        cache.clear()
        Settings.objects.update(ldap_uri="ldap://dc.corp.example", kerberos_enabled=True)
        s = Settings.get()
        s.kerberos_keytab = KEYTAB
        s.save()
        # "read" comes from LDAP; its Kerberos name differs from the username (sign-in filter on the UPN)
        User.objects.filter(username="read").update(username="read@corp.example")
        Profile.objects.filter(user__username="read@corp.example").update(source="ldap", kerberos_name="rdr")
        self.csrf = Client(enforce_csrf_checks=True)

    def post(self, header=None, status=200, client=None):
        r = (client or self.client).post("/api/kerberos", **({"HTTP_AUTHORIZATION": header} if header else {}))
        self.assertEqual(r.status_code, status, r.content[:300])
        return r

    def test_challenge_then_sign_in_by_the_stored_name(self):
        self.assertFalse(self.client.get("/api/session").json()["authenticated"])
        self.assertTrue(self.client.get("/api/session").json()["kerberos"])
        r = self.post(status=401)
        self.assertEqual((r["WWW-Authenticate"], r.json()["error"]), ("Negotiate", "kerberos_challenge"))
        with accepts("rdr@CORP.EXAMPLE"):
            r = self.post(TICKET)
        self.assertEqual(r.json()["user"]["username"], "read@corp.example")
        self.assertEqual(r["WWW-Authenticate"], "Negotiate " + base64.b64encode(b"reply").decode())  # mutual authentication
        self.assertTrue(self.client.get("/api/session").json()["authenticated"])
        e = AuditEntry.objects.filter(action="login").latest("id")
        self.assertEqual((e.target_label, e.changes), ("read@corp.example", {"method": [None, "kerberos"]}))
        self.assertNotIn("sso_at", self.client.session)  # never stands in for the password or the provider's MFA

    def test_needs_the_csrf_token(self):
        self.post(TICKET, status=403, client=self.csrf)
        self.csrf.get("/api/session")
        with accepts("rdr@CORP.EXAMPLE"):
            self.csrf.post("/api/kerberos", HTTP_AUTHORIZATION=TICKET, HTTP_X_CSRFTOKEN=self.csrf.cookies["csrftoken"].value)
        self.assertTrue(self.csrf.get("/api/session").json()["authenticated"])

    def test_users_imported_before_the_name_was_kept(self):
        Profile.objects.filter(user__username="maintainer").update(source="ldap")
        with accepts("Maintainer@CORP.EXAMPLE"):
            self.assertEqual(self.post(TICKET).json()["user"]["username"], "maintainer")

    def test_refusals_never_challenge_again(self):
        Profile.objects.filter(user__username="maintainer").update(source="ldap", kerberos_name="gone")
        User.objects.filter(username="maintainer").update(is_active=False)
        Profile.objects.filter(user__username="admin").update(kerberos_name="adm")  # local account
        cases = [("rdr@OTHER.FOREST", "kerberos_realm"), ("gone@CORP.EXAMPLE", "sso_unknown_user"),
                 ("adm@CORP.EXAMPLE", "sso_unknown_user"), ("nobody@CORP.EXAMPLE", "sso_unknown_user")]
        for principal, error in cases:
            with accepts(principal):
                r = self.post(TICKET, status=403)
            self.assertEqual(r.json()["error"], error)
            self.assertNotIn("WWW-Authenticate", r)
        ntlm = "Negotiate " + base64.b64encode(b"NTLMSSP\0\x01\0\0\0").decode()
        self.assertEqual(self.post(ntlm, status=403).json()["error"], "kerberos_ntlm")
        with mock.patch("kartei.kerberos._accept", side_effect=kerberos.SignInError("kerberos_failed")):
            self.assertEqual(self.post(TICKET, status=403).json()["error"], "kerberos_failed")
        self.assertEqual(self.post("Negotiate !!", status=403).json()["error"], "kerberos_failed")
        self.assertFalse(self.client.get("/api/session").json()["authenticated"])
        labels = list(AuditEntry.objects.filter(action="login_failed").order_by("id").values_list("target_label", flat=True))
        self.assertEqual(labels[:2], ["rdr@OTHER.FOREST", "gone@CORP.EXAMPLE"])

    def test_rate_limited(self):
        with mock.patch("kartei.kerberos._accept", side_effect=kerberos.SignInError("kerberos_failed")):
            for _ in range(30):
                self.post(TICKET, status=403)
            self.post(TICKET, status=429)

    def test_two_factor_still_asked(self):
        p = Profile.objects.get(user__username="read@corp.example")
        p.totp_secret = secret = totp.new_secret()
        p.save()
        Settings.objects.update(oidc_mfa_trusted=True)  # trusts the OIDC provider, not Windows
        with accepts("rdr@CORP.EXAMPLE"):
            self.assertEqual(self.post(TICKET, status=401).json()["error"], "totp_required")
        self.assertFalse(self.client.get("/api/session").json()["authenticated"])
        self.j("post", "/api/login", {"username": "", "code": totp.code_at(secret, int(time.time() // totp.STEP))})
        self.assertTrue(self.client.get("/api/session").json()["authenticated"])
        self.assertEqual(AuditEntry.objects.filter(action="login").latest("id").changes, {"method": [None, "kerberos"]})
        self.assertNotIn("sso_at", self.client.session)

    def test_off_or_incomplete(self):
        Settings.objects.update(kerberos_enabled=False)
        self.post(TICKET, status=404)
        self.assertFalse(self.client.get("/api/session").json()["kerberos"])
        Settings.objects.update(kerberos_enabled=True, ldap_uri="")
        self.post(TICKET, status=404)

    def test_settings(self):
        self.as_("admin")
        s = self.j("get", "/api/admin/settings")
        self.assertTrue(s["kerberos_keytab_set"])
        self.assertNotIn("kerberos_keytab_enc", s)
        Settings.objects.update(kerberos_keytab_enc="", kerberos_enabled=False)
        with mock.patch("kartei.kerberos.check", return_value=True):
            r = self.j("put", "/api/admin/settings", {"kerberos_enabled": True}, status=400)
            self.assertEqual(r["fields"], {"kerberos_enabled": "kerberos_incomplete"})
            for bad in ["not base64!", base64.b64encode(b"PK\x03\x04zip").decode()]:
                r = self.j("put", "/api/admin/settings", {"kerberos_keytab": bad}, status=400)
                self.assertEqual(r["fields"], {"kerberos_keytab": "invalid"})
            self.j("put", "/api/admin/settings", {"kerberos_keytab": KEYTAB, "kerberos_enabled": True})
        with mock.patch("kartei.kerberos.check", return_value=False):  # the GSS library can't use it
            r = self.j("put", "/api/admin/settings", {"kerberos_keytab": KEYTAB}, status=400)
            self.assertEqual(r["fields"], {"kerberos_keytab": "invalid"})
        self.assertEqual(Settings.get().kerberos_keytab, KEYTAB)
        e = AuditEntry.objects.filter(changes__has_key="kerberos_keytab_enc").latest("id")
        self.assertEqual(e.changes["kerberos_keytab_enc"], ["***", "***"])
        # "Remove": only together with switching it off
        r = self.j("put", "/api/admin/settings", {"kerberos_keytab": ""}, status=400)
        self.assertEqual(r["fields"], {"kerberos_enabled": "kerberos_incomplete"})
        self.assertFalse(self.j("put", "/api/admin/settings", {"kerberos_keytab": "", "kerberos_enabled": False})["kerberos_keytab_set"])
        self.assertEqual(Settings.get().kerberos_keytab_enc, "")


class KeytabFileTests(ApiTestBase):
    def test_written_once_private(self):
        raw = b"\x05\x02" + b"key" * 10
        path = kerberos.keytab_path(raw)
        self.assertEqual((path.read_bytes(), path.stat().st_mode & 0o777), (raw, 0o600))
        self.assertEqual(kerberos.keytab_path(raw), path)
        self.assertIsNone(kerberos.decode_keytab(base64.b64encode(b"\x05\x02" + b"x" * kerberos.KEYTAB_MAX).decode()))
