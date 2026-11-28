from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from . import graph
from .models import FieldDef, Record, Settings
from .tests import FakeGraph, sample_records


class GraphSyncTests(TestCase):
    def setUp(self):
        self.person, self.company = sample_records()
        s = Settings.get()
        s.graph_enabled, s.graph_tenant_id, s.graph_client_id, s.graph_group_id = True, "t", "c", "g"
        s.graph_client_secret = "secret"
        s.save()
        self.fake = FakeGraph({"u1": "a@x.de", "u2": "b@x.de"})
        patcher = mock.patch.object(graph, "http", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def by_name(self, user, name):
        return next(c for c in self.fake.contacts(user) if c["displayName"] == name)

    def test_mapping(self):
        c = graph.contact(self.person, list(FieldDef.objects.all()))
        self.assertEqual((c["givenName"], c["surname"], c["companyName"]), ("Alexander", "Meier", "Muster IT GmbH"))
        self.assertEqual(c["mobilePhone"], "01701234567")
        self.assertEqual(c["businessPhones"], ["05111234567"])
        self.assertEqual(c["homePhones"], ["050311234567"])
        self.assertEqual(c["businessAddress"], {"street": "Empfang\nMuster Straße 1", "city": "Hannover", "state": "",
                                                "postalCode": "30159", "countryOrRegion": "Deutschland"})
        self.assertEqual(c["birthday"], "1994-01-01T00:00:00Z")
        self.assertEqual(len(c["emailAddresses"]), 3)
        self.assertIn("Fax: 051112345671117", c["personalNotes"])
        self.assertEqual(c["department"], "Informatik")
        self.assertEqual(c["categories"], [])
        co = graph.contact(self.company, list(FieldDef.objects.all()))
        self.assertEqual((co["displayName"], co["companyName"], co["givenName"]), ("ACME GmbH", "ACME GmbH", ""))

    def test_sync_create_update_delete(self):
        st = graph.run()
        self.assertEqual((st["mailboxes"], st["created"], st["failed"]), (2, 6, []))
        self.assertEqual(len(self.fake.contacts("u1")), 3)
        # unchanged: no writes
        before = self.fake.calls
        st = graph.run()
        self.assertEqual((st["created"], st["updated"], st["deleted"]), (0, 0, 0))
        self.assertLess(self.fake.calls - before, 10)
        # edit -> PATCH, foreign contact on the phone -> removed, record deleted -> removed
        self.person.first_name = "Alex"
        self.person.save()
        folder = next(d for (u, _), d in self.fake.folders.items() if u == "u1")
        folder["contacts"]["foreign"] = {"displayName": "added on phone"}
        Record.objects.filter(pk=self.person.company_id).update(deleted_at=timezone.now())
        st = graph.run()
        self.assertEqual((st["updated"], st["deleted"]), (2, 3))
        self.assertEqual(sorted(c["displayName"] for c in self.fake.contacts("u1")), ["ACME GmbH", "Alex Meier"])
        self.assertEqual(self.by_name("u1", "Alex Meier")["companyName"], "")

    def test_leaver_folder_removed_and_guards(self):
        graph.run()
        del self.fake.users["u2"]
        st = graph.run()
        self.assertEqual(st["mailboxes"], 1)
        self.assertEqual(self.fake.contacts("u2"), [])
        self.assertEqual(Settings.get().graph_mailboxes, ["u1"])
        Record.objects.update(deleted_at=timezone.now())
        st = graph.run()
        self.assertIn("@graph_no_records", st["error"])
        self.assertEqual(len(self.fake.contacts("u1")), 3)  # untouched

    def test_missing_user_read_permission(self):
        # without User.Read.All Graph returns members with id and type only
        with mock.patch.object(self.fake, "_one", return_value={"status": 200, "body": {"value": [
                {"@odata.type": "#microsoft.graph.user", "id": "u1", "mail": None, "userPrincipalName": None}]}}):
            self.assertIn("@graph_no_user_read", graph.test(Settings.get())["error"])

    def test_personal_folders(self):
        FieldDef.objects.create(key="owner", label_de="Zuständig", label_en="Owner", type="user", applies_to="both")
        FieldDef.objects.create(key="internal", label_de="Intern", label_en="Internal", type="users", applies_to="both")
        User.objects.create(username="anna", email="A@x.de")  # mailbox u1 by mail
        User.objects.create(username="b")  # mailbox u2 by the local part
        Record.objects.filter(pk=self.person.pk).update(data={**self.person.data, "owner": "anna", "internal": ["b"]})
        Record.objects.filter(pk=self.company.pk).update(data={**self.company.data, "internal": ["anna", "b"]})
        s = Settings.get()
        s.graph_folder_responsible, s.graph_folder_internal = "Meine", "Intern"
        s.save()
        self.assertEqual(graph.run()["failed"], [])
        folder = lambda u, n: sorted(c["displayName"] for (uu, _), d in self.fake.folders.items()
                                     if uu == u and d["name"] == n for c in d["contacts"].values())
        self.assertEqual(len(folder("u1", "Kartei")), 3)
        self.assertEqual(folder("u1", "Meine"), ["Alexander Meier"])
        self.assertEqual(folder("u1", "Intern"), ["ACME GmbH"])
        self.assertEqual(folder("u2", "Meine"), [])
        self.assertEqual(folder("u2", "Intern"), ["ACME GmbH", "Alexander Meier"])
        Record.objects.filter(pk=self.company.pk).update(data={})  # no longer an internal contact
        graph.run()
        self.assertEqual(folder("u1", "Intern"), [])
        User.objects.filter(username="anna").update(is_active=False)  # F-6: no Kartei user any more, mailbox stays
        self.assertEqual(graph.run()["failed"], [])
        self.assertEqual(sorted(d["name"] for (u, _), d in self.fake.folders.items() if u == "u1"), ["Kartei"])
        self.assertEqual(folder("u2", "Intern"), ["Alexander Meier"])  # others keep theirs
        del self.fake.users["u1"]  # leaver: all three folders go
        graph.run()
        self.assertEqual([d["name"] for (u, _), d in self.fake.folders.items() if u == "u1"], [])

    def test_owner_by_local_part_only_for_the_same_person(self):
        """F-7: mailbox kim@x.de must not get the folders of Kartei user 'kim' whose email is another mailbox's."""
        User.objects.create(username="kim", email="kim.kramer@x.de")
        User.objects.create(username="lea")  # no email: the AD account name fallback still works
        users = graph.kartei_users()
        self.assertIsNone(graph.owner({"mail": "kim@x.de", "userPrincipalName": "u1@x.de"}, users))
        self.assertEqual(graph.owner({"mail": "kim.kramer@x.de"}, users), "kim")
        self.assertEqual(graph.owner({"mail": "Kim@x.de", "userPrincipalName": "kim.kramer@x.de"}, users), "kim")
        self.assertEqual(graph.owner({"mail": "lea@x.de"}, users), "lea")

    def test_photos(self):
        jpeg = b"\xff\xd8\xff" + b"x" * 50
        Record.objects.filter(pk=self.person.pk).update(image=jpeg, image_type="jpeg")
        self.fake.photo_fails = True
        st = graph.run()
        self.assertEqual(st["photos"], 0)
        self.assertEqual(len(st["failed"]), 2)
        self.fake.photo_fails = False
        st = graph.run()  # retried, data unchanged
        self.assertEqual((st["photos"], st["created"], st["updated"], st["failed"]), (2, 0, 0, []))
        self.assertEqual(self.by_name("u1", "Alexander Meier")["photo"], jpeg)
        self.assertEqual(graph.run()["photos"], 0)  # nothing to do
        Record.objects.filter(pk=self.person.pk).update(image=None, image_type="")
        st = graph.run()  # removal: contact recreated without photo
        self.assertEqual(st["created"], 2)
        self.assertNotIn("photo", self.by_name("u1", "Alexander Meier"))
        self.assertEqual(len(self.fake.contacts("u1")), 3)

    def test_sync_now(self):
        Settings.objects.update(graph_sync_requested=timezone.now())
        graph.run()
        self.assertIsNone(Settings.get().graph_sync_requested)

    def test_disabled_and_secret_masked(self):
        self.assertEqual(Settings.get().graph_client_secret, "secret")
        self.assertNotIn("secret", Settings.get().graph_client_secret_enc)
        self.assertEqual(graph.test(Settings.get())["mailboxes"], 2)
        Settings.objects.update(graph_group_id="")
        self.assertIn("@graph_no_group", graph.test(Settings.get())["error"])
        Settings.objects.update(graph_client_id="")
        self.assertIn("@graph_not_configured", graph.test(Settings.get())["error"])
        Settings.objects.update(graph_enabled=False)
        self.assertIsNone(graph.run())
