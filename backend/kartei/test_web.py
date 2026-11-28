import tempfile
from unittest import mock

from django.test import TestCase


class PwaTests(TestCase):
    def test_service_worker_from_root(self):
        from django.conf import settings as dj
        with tempfile.TemporaryDirectory() as d, mock.patch.object(dj, "FRONTEND_DIST", __import__("pathlib").Path(d)):
            self.assertEqual(self.client.get("/sw.js").status_code, 404)
            open(f"{d}/sw.js", "w").write("// sw")
            r = self.client.get("/sw.js")
            self.assertEqual((r.status_code, r["Content-Type"]), (200, "text/javascript"))
            self.assertIn("no-cache", r["Cache-Control"])


class RenameTests(TestCase):
    def test_old_crm_links_redirect(self):
        r = self.client.get("/crm/person/ad081c47-96f6-4b88-9d9b-67faf688772d?x=1")
        self.assertEqual((r.status_code, r["Location"]), (301, "/person/ad081c47-96f6-4b88-9d9b-67faf688772d?x=1"))
        self.assertEqual(self.client.get("/crm")["Location"], "/")


class ShareTargetTests(TestCase):
    def test_post_without_service_worker_asks_again(self):
        r = self.client.post("/share", {"text": "Erika Muster"})  # no CSRF token: the phone's share sheet has none
        self.assertEqual((r.status_code, r["Location"]), (302, "/share?retry=1"))
