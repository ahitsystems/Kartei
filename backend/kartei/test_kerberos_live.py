"""Kerberos sign-in against a real Samba AD domain controller (.github/kerberos/, CI job "kerberos").

Skipped unless KERBEROS_LIVE=1. The DC gives: realm KARTEI.TEST, users alice (in kartei-read), bob (in kartei-read,
disabled), carol (no Kartei group), all with password Test-pw-2026, and the keytab for HTTP/kartei.kartei.test.
  KERBEROS_LDAP    ldap:// URL of the DC                     KERBEROS_KEYTAB   path of the exported keytab
  KERBEROS_CLIENT  krb5.conf for the client side only: Kartei (this process) has to work without one, as in the image
"""
import base64
import os
import subprocess
import sys
import unittest

from django.contrib.auth.models import User
from django.core.cache import cache

from . import ldapusers
from .models import AuditEntry, Settings
from .tests import ApiTestBase

PW = "Test-pw-2026"
# the browser's part, in its own process with the client's krb5.conf: a password sign-in at the KDC, then the token
CLIENT = """
import base64, sys, gssapi
user, pw = sys.argv[1:3]
name = gssapi.Name(user, gssapi.NameType.user)
creds = gssapi.raw.acquire_cred_with_password(name, pw.encode(), usage="initiate").creds
ctx = gssapi.SecurityContext(name=gssapi.Name("HTTP@kartei.kartei.test", gssapi.NameType.hostbased_service),
                             creds=creds, usage="initiate", flags=gssapi.RequirementFlag.mutual_authentication)
print(base64.b64encode(ctx.step()).decode())
"""


def token(user, pw=PW):
    env = {**os.environ, "KRB5_CONFIG": os.environ["KERBEROS_CLIENT"]}
    return subprocess.run([sys.executable, "-c", CLIENT, user, pw], env=env, capture_output=True, text=True,
                          check=True).stdout.strip()


@unittest.skipUnless(os.environ.get("KERBEROS_LIVE") == "1", "needs the Samba DC (KERBEROS_LIVE=1)")
class SambaKerberosTests(ApiTestBase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.assertNotIn("KRB5_CONFIG", os.environ)  # the service side works with the keytab alone
        s = Settings.get()
        s.ldap_uri, s.ldap_bind_dn, s.ldap_bind_password = os.environ["KERBEROS_LDAP"], "Administrator@KARTEI.TEST", PW
        s.ldap_user_base, s.ldap_group_read = "CN=Users,DC=kartei,DC=test", "CN=kartei-read,CN=Users,DC=kartei,DC=test"
        s.save()
        self.as_("admin")  # the keytab goes through the settings API: decoded, checked by the GSS library, encrypted
        with open(os.environ["KERBEROS_KEYTAB"], "rb") as f:
            self.j("put", "/api/admin/settings", {"kerberos_keytab": base64.b64encode(f.read()).decode(), "kerberos_enabled": True})
        self.client.logout()

    def import_users(self):
        st = ldapusers.run()
        self.assertNotIn("error", st)

    def sign_in(self, tok, status=200):
        r = self.client.post("/api/kerberos", HTTP_AUTHORIZATION=f"Negotiate {tok}")
        self.assertEqual(r.status_code, status, r.content[:300])
        return r

    def test_imported_user_signs_in_once_per_ticket(self):
        self.import_users()
        self.assertEqual(sorted(User.objects.filter(profile__source="ldap").values_list("username", "profile__kerberos_name")),
                         [("alice", "alice")])  # bob is disabled, carol has no group
        tok = token("alice")
        r = self.sign_in(tok)
        self.assertEqual(r.json()["user"]["username"], "alice")
        self.assertTrue(r["WWW-Authenticate"].startswith("Negotiate "))  # mutual authentication
        self.client.logout()
        self.assertEqual(self.sign_in(tok, 403).json()["error"], "kerberos_failed")  # replayed

    def test_sign_in_filter_on_the_upn(self):
        Settings.objects.update(ldap_user_filter="(userPrincipalName=%(user)s)")
        self.import_users()
        self.assertEqual(self.sign_in(token("alice")).json()["user"]["username"], "alice@kartei.test")

    def test_unknown_to_kartei(self):
        self.import_users()
        self.assertEqual(self.sign_in(token("carol"), 403).json()["error"], "sso_unknown_user")
        e = AuditEntry.objects.filter(action="login_failed").latest("id")
        self.assertEqual((e.target_label, e.changes), ("carol@KARTEI.TEST", {"reason": [None, "sso_unknown_user"]}))
