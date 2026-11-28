from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.utils import timezone

from .models import AuditEntry, FieldDef, Record, Review, Settings
from .tests import ApiTestBase


class ReviewTests(ApiTestBase):
    """Reviews go to the responsible user, who confirms or proposes; maintainers decide."""
    def setUp(self):
        super().setUp()
        FieldDef.objects.filter(key="internal_contact").update(required=True)
        User.objects.filter(username="read").update(email="read@example.com", first_name="Rita")
        self.as_("maintainer")
        self.co = self.j("post", "/api/records", {"kind": "company", "name": "Alt GmbH",
                                                  "data": {"internal_contact": "read"}}, status=201)
        self.p = self.j("post", "/api/records", {"kind": "person", "last_name": "Meier", "company": self.co["uid"],
                                                 "data": {"internal_contact": "read", "email": "m@alt.de"}}, status=201)
        self.orphan = Record.objects.create(kind="person", last_name="Ohne")  # imported without responsible

    def test_responsible_required(self):
        bad = self.j("post", "/api/records", {"kind": "person", "last_name": "X"}, status=400)
        self.assertEqual(bad["fields"]["internal_contact"], "required")

    def test_manual_start_mail_and_confirm(self):
        from django.core import mail
        self.as_("admin")
        res = self.j("post", "/api/admin/reviews", {"scope": "all"})
        self.assertEqual(res, {"created": 2, "already_open": 0, "no_responsible": 1})
        self.assertEqual(self.j("post", "/api/admin/reviews", {"scope": "all"})["already_open"], 2)
        stats = self.j("get", "/api/admin/reviews")
        self.assertEqual((stats["open"], stats["no_responsible"]), (2, 1))
        # digest: one mail per user, only once
        Settings.objects.update(smtp_host="smtp.example", smtp_from="kartei@example.com", base_url="https://kartei.example")
        with mock.patch("kartei.reviews.mail_connection", lambda s: mail.get_connection()):
            from kartei import reviews
            self.assertEqual(reviews.notify(Settings.get()), 1)
            self.assertEqual(reviews.notify(Settings.get()), 0)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Alt GmbH: https://kartei.example/company/", mail.outbox[0].body)
        self.assertEqual(mail.outbox[0].to, ["read@example.com"])
        # the read-only responsible sees and confirms
        self.as_("read")
        boot = self.j("get", "/api/bootstrap")
        self.assertEqual({rv["record"] for rv in boot["reviews"]}, {self.co["uid"], self.p["uid"]})
        since = timezone.now() - timedelta(seconds=1)
        Record.objects.filter(uid=self.co["uid"]).update(updated_at=timezone.now() - timedelta(days=1))
        r = self.j("post", f"/api/records/{self.co['uid']}/review/confirm")
        self.assertTrue(r["reviewed_at"])
        self.assertLess(Record.objects.get(uid=self.co["uid"]).updated_at, since)  # a review is not a change ...
        ch = self.j("get", f"/api/changes?since={since.isoformat().replace('+', '%2B')}")
        self.assertIn(self.co["uid"], [x["uid"] for x in ch["records"]])  # ... but clients still get it
        self.assertEqual(r["reviewed_by"], "read")
        self.assertEqual(len(self.j("get", "/api/bootstrap")["reviews"]), 1)
        self.as_("maintainer")  # someone else's review cannot be confirmed by a read-only user
        self.as_("read")
        self.j("post", f"/api/records/{self.co['uid']}/review/confirm", status=403)

    def test_schedule_rolling_and_campaign(self):
        from datetime import date as d

        from kartei import reviews
        s = Settings.get()
        s.review_mode, s.review_interval_months = "rolling", 12
        s.save()
        self.assertEqual(reviews.schedule(s)["created"], 0)  # all fresh
        Record.objects.filter(pk=self.orphan.pk).update(created_at=timezone.now() - timedelta(days=400))
        Record.objects.filter(uid=self.p["uid"]).update(created_at=timezone.now() - timedelta(days=400))
        self.assertEqual(reviews.schedule(s), {"created": 1, "already_open": 0, "no_responsible": 1})
        Review.objects.update(done_at=timezone.now())
        s.review_mode, s.review_campaign_month, s.review_campaign_day = "campaign", 3, 1
        s.save()
        self.assertIsNone(reviews.schedule(s, today=d(2030, 2, 28)))
        self.assertEqual(reviews.schedule(Settings.get(), today=d(2030, 3, 1))["created"], 2)
        Review.objects.update(done_at=timezone.now())
        self.assertIsNone(reviews.schedule(Settings.get(), today=d(2030, 6, 1)))  # once per year
        self.assertEqual(reviews.schedule(Settings.get(), today=d(2031, 3, 2))["created"], 2)

    def test_change_proposal_accept(self):
        self.as_("admin")
        self.j("post", f"/api/records/{self.p['uid']}/review/request")
        self.as_("read")
        self.j("patch", f"/api/records/{self.p['uid']}", {"version": 1, "last_name": "X"}, status=403)  # can't edit
        bad = self.j("post", f"/api/records/{self.p['uid']}/proposals", {"kind": "change", "data": {"email": "nope"}},
                     status=400)
        self.assertEqual(bad["fields"]["email"], "invalid_email")
        self.j("post", f"/api/records/{self.p['uid']}/proposals", {"kind": "change", "data": {"email": "m@alt.de"}},
               status=400)  # no change
        res = self.j("post", f"/api/records/{self.p['uid']}/proposals",
                     {"kind": "change", "last_name": "Meyer", "data": {"email": "meyer@alt.de", "mobile": ""},
                      "comment": "neue Adresse"}, status=201)["proposal"]
        self.assertEqual(res["changes"], {"core": {"last_name": "Meyer"}, "data": {"email": "meyer@alt.de"}})
        self.assertEqual(res["base"], {"core": {"last_name": "Meier"}, "data": {"email": "m@alt.de"}})
        boot = self.j("get", "/api/bootstrap")
        self.assertEqual(boot["reviews"], [])  # proposing closes the review
        self.assertEqual(len(boot["proposals"]), 1)
        self.assertTrue(Record.objects.get(uid=self.p["uid"]).reviewed_at)
        self.j("post", f"/api/proposals/{res['id']}/accept", status=403)
        self.as_("maintainer")
        self.assertEqual(len(self.j("get", "/api/bootstrap")["proposals"]), 1)
        out = self.j("post", f"/api/proposals/{res['id']}/accept", {})
        self.assertEqual((out["record"]["last_name"], out["record"]["data"]["email"]), ("Meyer", "meyer@alt.de"))
        self.assertEqual(out["proposal"]["status"], "accepted")
        entry = AuditEntry.objects.filter(target_id=self.p["uid"], action="update").first()
        self.assertEqual(entry.changes["proposed_by"], [None, "read"])
        self.assertEqual(self.j("get", "/api/bootstrap")["proposals"], [])
        self.j("post", f"/api/proposals/{res['id']}/accept", status=404)

    def test_proposer_edits_and_withdraws(self):
        self.as_("read")
        p = self.j("post", f"/api/records/{self.p['uid']}/proposals",
                   {"kind": "change", "data": {"email": "x@alt.de"}}, status=201)["proposal"]
        p = self.j("patch", f"/api/proposals/{p['id']}", {"last_name": "Maier", "data": {"email": "y@alt.de"},
                                                          "comment": "Tippfehler"})["proposal"]
        self.assertEqual(p["changes"], {"core": {"last_name": "Maier"}, "data": {"email": "y@alt.de"}})
        self.assertEqual(p["comment"], "Tippfehler")
        self.as_("maintainer")
        self.j("patch", f"/api/proposals/{p['id']}", {"data": {"email": "z@alt.de"}}, status=404)  # not theirs
        self.as_("read")
        self.j("delete", f"/api/proposals/{p['id']}")
        self.assertEqual(self.j("get", "/api/bootstrap")["proposals"], [])
        self.j("delete", f"/api/proposals/{p['id']}", status=404)

    def test_reviewer_adjusts_before_accepting(self):
        self.as_("read")
        p = self.j("post", f"/api/records/{self.p['uid']}/proposals",
                   {"kind": "change", "data": {"email": "mayer@alt.dee"}}, status=201)["proposal"]
        self.as_("maintainer")
        bad = self.j("post", f"/api/proposals/{p['id']}/accept", {"override": {"data": {"email": "kaputt"}}}, status=400)
        self.assertEqual(bad["fields"]["email"], "invalid_email")
        out = self.j("post", f"/api/proposals/{p['id']}/accept", {"override": {"data": {"email": "mayer@alt.de"}}})
        self.assertEqual(out["record"]["data"]["email"], "mayer@alt.de")
        e = AuditEntry.objects.filter(target_id=self.p["uid"], action="update").first()
        self.assertEqual((e.changes["email"][1], e.changes["adjusted"]), ("mayer@alt.de", [None, True]))

    def test_new_contact_proposal(self):
        self.as_("read")
        self.j("post", "/api/records", {"kind": "person", "last_name": "Neu"}, status=403)  # can't create
        self.j("post", "/api/proposals", {"kind": "robot"}, status=400)
        bad = self.j("post", "/api/proposals", {"kind": "person", "data": {"email": "nina@alt.de"}}, status=400)
        self.assertEqual(bad["fields"]["last_name"], "required")
        p = self.j("post", "/api/proposals", {"kind": "person", "first_name": "Nina", "last_name": "Neu",
                                              "company": self.co["uid"], "data": {"email": "nina@alt.de", "internal_contact": "read"},
                                              "comment": "neue Kollegin"}, status=201)["proposal"]
        self.assertEqual((p["kind"], p["record"], p["record_kind"], p["label"]), ("create", None, "person", "Nina Neu"))
        self.assertEqual(p["changes"], {"core": {"first_name": "Nina", "last_name": "Neu", "company": self.co["uid"]},
                                        "data": {"email": "nina@alt.de", "internal_contact": "read"}})
        self.assertEqual([x["id"] for x in self.j("get", "/api/bootstrap")["proposals"]], [p["id"]])
        p = self.j("patch", f"/api/proposals/{p['id']}", {"first_name": "Nina", "last_name": "Neumann",
                                                          "company": self.co["uid"], "data": {"internal_contact": "read"}})["proposal"]
        self.assertEqual(p["label"], "Nina Neumann")
        e = AuditEntry.objects.filter(target_type="proposal", target_id=str(p["id"])).first()
        self.assertEqual((e.action, e.target_label, e.changes["company"]), ("propose", "Nina Neumann", [None, "Alt GmbH"]))
        self.j("post", f"/api/proposals/{p['id']}/accept", status=403)
        self.as_("maintainer")
        out = self.j("post", f"/api/proposals/{p['id']}/accept", {"note": "willkommen"})
        r = out["record"]
        self.assertEqual((r["last_name"], r["company"], out["proposal"]["record"]), ("Neumann", self.co["uid"], r["uid"]))
        created = AuditEntry.objects.get(target_id=r["uid"], action="create")
        self.assertEqual((created.changes["proposed_by"], created.changes["note"]), ([None, "read"], [None, "willkommen"]))
        self.assertEqual(self.j("get", "/api/bootstrap")["proposals"], [])

    def test_new_company_proposal_adjusted_rejected_withdrawn(self):
        self.as_("read")
        new = lambda name: self.j("post", "/api/proposals", {"kind": "company", "name": name, "data": {"internal_contact": "read"}},
                                   status=201)["proposal"]
        a, b, c = new("Neu AG"), new("Unsinn GmbH"), new("Doppelt KG")
        self.j("delete", f"/api/proposals/{c['id']}")
        self.as_("maintainer")
        bad = self.j("post", f"/api/proposals/{a['id']}/accept", {"override": {"name": ""}}, status=400)
        self.assertEqual(bad["fields"]["name"], "required")
        out = self.j("post", f"/api/proposals/{a['id']}/accept", {"override": {"name": "Neu AG & Co. KG", "data": {"internal_contact": "read"}}})
        self.assertEqual(out["record"]["name"], "Neu AG & Co. KG")
        self.assertTrue(AuditEntry.objects.get(target_id=out["record"]["uid"], action="create").changes["adjusted"])
        self.assertIsNone(self.j("post", f"/api/proposals/{b['id']}/reject", {"note": "gibt es nicht"})["record"])
        self.assertTrue(AuditEntry.objects.filter(action="reject", target_type="proposal", target_id=str(b["id"])).exists())
        self.assertEqual(Record.objects.filter(name__in=["Unsinn GmbH", "Doppelt KG"]).count(), 0)
        self.assertEqual(self.j("get", "/api/bootstrap")["proposals"], [])

    def test_delete_proposal_and_reject(self):
        self.as_("read")
        p1 = self.j("post", f"/api/records/{self.co['uid']}/proposals", {"kind": "delete", "comment": "insolvent"},
                    status=201)["proposal"]
        p2 = self.j("post", f"/api/records/{self.p['uid']}/proposals", {"kind": "delete", "reason": "left"},
                    status=201)["proposal"]
        self.as_("maintainer")
        self.j("post", f"/api/proposals/{p2['id']}/reject", {"note": "noch aktiv"})
        self.assertIsNone(Record.objects.get(uid=self.p["uid"]).deleted_at)
        self.j("post", f"/api/proposals/{p1['id']}/accept", {})
        self.assertIsNotNone(Record.objects.get(uid=self.co["uid"]).deleted_at)  # in the recycle bin
        self.assertTrue(AuditEntry.objects.filter(action="reject", target_id=self.p["uid"]).exists())

    def test_delete_reasons(self):
        self.as_("read")
        url = f"/api/records/{self.p['uid']}/proposals"
        bad = self.j("post", url, {"kind": "delete", "reason": "other"}, status=400)
        self.assertEqual(bad["fields"], {"comment": "required"})
        bad = self.j("post", f"/api/records/{self.co['uid']}/proposals", {"kind": "delete", "reason": "left"}, status=400)
        self.assertEqual(bad["fields"], {"reason": "invalid"})
        orphan = f"/api/records/{self.orphan.uid}/proposals"  # no company
        self.j("post", orphan, {"kind": "delete", "reason": "company_gone"}, status=400)

    def test_company_gone_deletes_company_and_persons(self):
        other = self.j("post", "/api/records", {"kind": "person", "last_name": "Zweite", "company": self.co["uid"],
                                                "data": {"internal_contact": "read"}}, status=201)
        self.as_("admin")
        self.j("post", "/api/admin/reviews", {"scope": "all"})
        self.as_("read")
        p = self.j("post", f"/api/records/{self.p['uid']}/proposals", {"kind": "delete", "reason": "company_gone"},
                   status=201)["proposal"]
        self.assertEqual((p["record"], p["reason"]), (self.co["uid"], "company_gone"))  # proposed on the company
        self.assertEqual(self.j("get", "/api/bootstrap")["reviews"], [])  # company and its persons count as answered
        again = self.j("post", f"/api/records/{other['uid']}/proposals", {"kind": "delete", "reason": "company_gone"})
        self.assertEqual((again["existing"], again["proposal"]["id"]), (True, p["id"]))  # no duplicate
        self.as_("maintainer")
        self.j("post", f"/api/proposals/{p['id']}/accept", {})
        gone = Record.objects.filter(uid__in=[self.co["uid"], self.p["uid"], other["uid"]], deleted_at__isnull=False)
        self.assertEqual(gone.count(), 3)
        e = AuditEntry.objects.filter(target_id=self.p["uid"], action="delete").first()
        self.assertEqual((e.changes["reason"], e.changes["proposed_by"]), ([None, "company_gone"], [None, "read"]))

    def test_editing_answers_own_review(self):
        self.as_("admin")
        rec = self.j("get", f"/api/records/{self.p['uid']}")
        self.j("patch", f"/api/records/{self.p['uid']}", {"version": rec["version"], "data": {"internal_contact": "maintainer"}})
        self.j("post", f"/api/records/{self.p['uid']}/review/request")
        self.as_("maintainer")
        rec = self.j("get", f"/api/records/{self.p['uid']}")
        self.j("patch", f"/api/records/{self.p['uid']}", {"version": rec["version"], "data": {"email": "neu@alt.de"}})
        rv = Review.objects.get(record__uid=self.p["uid"], assignee__username="maintainer")
        self.assertEqual(rv.outcome, "edited")
        self.assertEqual(Record.objects.get(uid=self.p["uid"]).reviewed_by.username, "maintainer")

    def test_reassign_and_cancel(self):
        self.as_("admin")
        self.j("post", f"/api/records/{self.p['uid']}/review/request")
        rec = self.j("get", f"/api/records/{self.p['uid']}")
        self.j("patch", f"/api/records/{self.p['uid']}", {"version": rec["version"], "data": {"internal_contact": "maintainer"}})
        rv = Review.objects.get(record__uid=self.p["uid"])
        self.assertEqual((rv.assignee.username, rv.notified_at), ("maintainer", None))
        self.j("delete", f"/api/records/{self.p['uid']}")
        rv.refresh_from_db()
        self.assertEqual(rv.outcome, "cancelled")
