import base64
import re
from datetime import timedelta

from django.contrib.auth.hashers import make_password
from django.test import TestCase
from django.utils import timezone

from .models import AuditEntry, FieldDef, Record, Settings
from .tests import (
    PERSON_UID,
    sample_records,
)
from .vcard import fold, render

# Reference vCards for iOS and Android (PHOTO/LOGO/SOURCE excluded: data-dependent)
ANDROID_PERSON = r"""UID:urn:uuid:ad081c47-96f6-4b88-9d9b-67faf688772d
N:Meier;Alexander;Ralph;Prof. Dr. Dr.;
FN:Prof. Dr. Dr. Alexander Meier
X-PHONETIC-FIRST-NAME:Ahlecksanda
X-PHONETIC-MIDDLE-NAME:Ralf
X-PHONETIC-LAST-NAME:Maiher
NICKNAME:Alex
TITLE:Rechenzentrums- & Systembetrieb
ORG:Muster IT GmbH
ROLE:Informatik
EMAIL;WORK:a.meier@acme.com
EMAIL;HOME:alex.meier@gmx.de
EMAIL;Sonstige:a.meier@gmail.com
TEL;CELL:01701234567
TEL;HOME:050311234567
TEL;WORK:05111234567
TEL;X-Assistenz:05111234568
TEL;WORK;FAX:051112345671117
TEL;HOME;FAX:0267612312311
URL:https://acme.com
BDAY:1994-01-01
ADR;WORK:;Empfang;Muster Straße 1;Hannover;;30159;Deutschland
ADR;HOME:;3. OG;Muster Straße 24a;Garbsen;;30823;Deutschland
X-ANDROID-CUSTOM:vnd.android.cursor.item/relation;Friedrich März;1;;;;;;;;;;;;;
X-ANDROID-CUSTOM:vnd.android.cursor.item/relation;Donald Trumpet;7;;;;;;;;;;;;;
NOTE:Trink seinen Kaffe mit Milch\nAbteilung: Informatik\n\nAnsprechpartner intern: KK\ninterne Kontakte: KA\,ZZ"""


IOS_PERSON = r"""UID:urn:uuid:ad081c47-96f6-4b88-9d9b-67faf688772d
N:Meier;Alexander;Ralph;Prof. Dr. Dr.;
FN:Prof. Dr. Dr. Alexander Meier
NICKNAME:Alex
X-PHONETIC-FIRST-NAME:Ahlecksanda
X-PRONUNCIATION-FIRST-NAME:Ahlecksanda
X-PHONETIC-MIDDLE-NAME:Ralf
X-PHONETIC-LAST-NAME:Maiher
X-PRONUNCIATION-LAST-NAME:Maiher
TITLE:Rechenzentrums- & Systembetrieb
ORG:Muster IT GmbH;Informatik
EMAIL;type=WORK;type=pref:a.meier@acme.com
EMAIL;type=HOME:alex.meier@gmx.de
EMAIL;type=Sonstige:a.meier@gmail.com
TEL;type=CELL;type=VOICE:01701234567
TEL;type=HOME;type=VOICE:050311234567
TEL;type=WORK;type=VOICE;type=pref:05111234567
item1.TEL:05111234568
item1.X-ABLabel:_$!<AssistantPhone>!$_
TEL;type=WORK;type=FAX:051112345671117
TEL;type=HOME;type=FAX:0267612312311
item2.URL;type=pref:https://acme.com
item2.X-ABLabel:_$!<HomePage>!$_
BDAY:1994-01-01
ADR;WORK:;Empfang;Muster Straße 1;Hannover;;30159;Deutschland
ADR;HOME:;3. OG;Muster Straße 24a;Garbsen;;30823;Deutschland
item3.X-ABRELATEDNAMES;type=pref:Friedrich März
item3.X-ABLabel:_$!<Assistant>!$_
item4.X-ABRELATEDNAMES;type=pref:Donald Trumpet
item4.X-ABLabel:_$!<Manager>!$_
NOTE:Trink seinen Kaffe mit Milch\n\nAnsprechpartner intern: KK\ninterne Kontakte: KA\,ZZ"""


# Companies use the same note layout as persons.
ANDROID_COMPANY = r"""ORG:ACME GmbH
NICKNAME:ACME
N:ACME GmbH;;;;
FN:ACME GmbH
EMAIL;Zentrale:info@acme.com
EMAIL;Datenschutz:datenschutz@acme.com
EMAIL;Rechnung:einkauf@acme.com
TEL;Zentrale:05111231232
TEL;Zentrale;FAX:0511123123123
URL:https://acme.com
ADR;WORK:;;Muster Straße 1;Hannover;;30159;Deutschland
X-ANDROID-CUSTOM:vnd.android.cursor.item/relation;Angela Merklin;0;Geschäftsführer;;;;;;;;;;;;
UID:urn:uuid:cb114125-b78f-43fd-85a0-4fcdea9a8cac
NOTE:Test\n\nAnsprechpartner intern: KK\ninterne Kontakte: KA\,UI"""


IOS_COMPANY = r"""ORG:ACME GmbH
NICKNAME:ACME
N:;;;;
FN:ACME GmbH
EMAIL;type=Zentrale;type=pref:info@acme.com
EMAIL;type=Datenschutz:datenschutz@acme.com
EMAIL;type=Rechnung:einkauf@acme.com
TEL;type=Zentrale;type=VOICE;type=pref:05111231232
TEL;type=Zentrale;type=FAX:0511123123123
item1.URL;type=pref:https://acme.com
item1.X-ABLabel:_$!<HomePage>!$_
ADR;WORK:;;Muster Straße 1;Hannover;;30159;Deutschland
item2.X-ABRELATEDNAMES;type=pref:Angela Merklin
item2.X-ABLabel:Geschäftsführer
UID:urn:uuid:cb114125-b78f-43fd-85a0-4fcdea9a8cac
NOTE:Test\n\nAnsprechpartner intern: KK\ninterne Kontakte: KA\,UI"""


def card_lines(vcf):
    lines = vcf.replace("\r\n ", "").split("\r\n")
    return {l for l in lines if l and not l.startswith(("BEGIN:", "VERSION:", "END:", "SOURCE:", "PHOTO", "LOGO"))}


class VCardTests(TestCase):
    def setUp(self):
        self.person, self.company = sample_records()
        self.fields = list(FieldDef.objects.all())

    def test_reference_cards(self):
        for rec, flavour, expected in [(self.person, "android", ANDROID_PERSON), (self.person, "ios", IOS_PERSON),
                                       (self.company, "android", ANDROID_COMPANY), (self.company, "ios", IOS_COMPANY)]:
            with self.subTest(rec=rec.display_name, flavour=flavour):
                got = card_lines(render(rec, self.fields, flavour, "https://kartei.example"))
                self.assertEqual(got, set(expected.splitlines()))

    def test_fold_and_escape(self):
        line = "NOTE:" + "ä" * 100
        folded = fold(line)
        self.assertTrue(all(len(l.encode()) <= 75 for l in folded.split("\r\n")))
        self.assertEqual(folded.replace("\r\n ", ""), line)
        self.person.data["description"] = "a;b,c\\d\nzweite Zeile"
        vcf = render(self.person, self.fields, "ios")
        self.assertIn(r"NOTE:a\;b\,c\\d\nzweite Zeile", vcf.replace("\r\n ", ""))


class CardDavTests(TestCase):
    def setUp(self):
        self.person, self.company = sample_records()
        s = Settings.get()
        s.carddav_enabled, s.carddav_password_hash = True, make_password("phone-secret")
        s.save()
        self.auth = "Basic " + base64.b64encode(b"carddav:phone-secret").decode()

    def dav(self, method, path, body="", **headers):
        return self.client.generic(method, path, body, content_type="application/xml",
                                   HTTP_AUTHORIZATION=self.auth, **headers)

    def test_auth_and_readonly(self):
        self.assertEqual(self.client.generic("PROPFIND", "/carddav/").status_code, 401)
        self.assertEqual(self.dav("PUT", f"/carddav/kontakte/{PERSON_UID}.vcf", "x").status_code, 403)
        self.assertEqual(self.dav("DELETE", f"/carddav/kontakte/{PERSON_UID}.vcf").status_code, 403)

    def sync(self, token=""):
        body = ('<d:sync-collection xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:carddav"><d:sync-token>' + token +
                '</d:sync-token><d:sync-level>1</d:sync-level><d:prop><d:getetag/></d:prop></d:sync-collection>')
        return self.dav("REPORT", "/carddav/kontakte/", body)

    def test_sync_collection(self):
        r = self.dav("PROPFIND", "/carddav/kontakte/", HTTP_DEPTH="0")
        self.assertIn(b"<d:sync-collection />", r.content.replace(b"<d:sync-collection/>", b"<d:sync-collection />"))
        self.assertIn(b"<d:sync-token>urn:kartei:sync:", r.content)
        r = self.sync()
        self.assertEqual((r.status_code, r.content.count(b".vcf</d:href>")), (207, 3))
        Record.objects.update(updated_at=timezone.now() - timedelta(hours=1))
        token = re.findall(rb"<d:sync-token>([^<]+)</d:sync-token>", r.content)[-1].decode()
        self.assertEqual(self.sync(token).content.count(b"<d:response>"), 0)
        Record.objects.filter(uid=self.company.uid).update(version=2, updated_at=timezone.now())  # person's card changes too
        Record.objects.filter(uid=PERSON_UID).update(deleted_at=timezone.now())
        r = self.sync(token)
        self.assertIn(f"{self.company.uid}.vcf</d:href><d:propstat>".encode(), r.content)
        self.assertIn(f"{PERSON_UID}.vcf</d:href><d:status>HTTP/1.1 404 Not Found".encode(), r.content)
        # a changed vCard mapping or purged contacts: the token is no longer usable, the phone starts over
        s = Settings.get()
        s.base_url = "https://kartei.example.org"
        s.save()
        self.assertEqual(self.sync(token).status_code, 403)
        token = re.findall(rb"<d:sync-token>([^<]+)</d:sync-token>", self.sync().content)[-1].decode()
        self.assertEqual(self.sync(token).status_code, 207)
        AuditEntry.objects.create(action="purge", target_type="record", target_id=PERSON_UID)
        r = self.sync(token)
        self.assertEqual(r.status_code, 403)
        self.assertIn(b"valid-sync-token", r.content)

    def test_new_password_ends_cached_login(self):
        depth0 = lambda: self.dav("PROPFIND", "/carddav/kontakte/", HTTP_DEPTH="0").status_code
        self.assertEqual(depth0(), 207)  # the successful check is now cached
        Settings.objects.filter(pk=1).update(carddav_password_hash=make_password("new-secret"))
        self.assertEqual(depth0(), 401)

    def test_discovery_and_sync(self):
        r = self.dav("PROPFIND", "/carddav/", HTTP_DEPTH="0", body=
                     '<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:carddav"><d:prop>'
                     '<d:current-user-principal/><c:addressbook-home-set/></d:prop></d:propfind>')
        self.assertEqual(r.status_code, 207)
        self.assertIn(b"/carddav/", r.content)
        r = self.dav("PROPFIND", "/carddav/kontakte/", HTTP_DEPTH="1")
        self.assertEqual(r.content.count(b".vcf</d:href>"), 3)
        self.assertIn(b"getctag", r.content)
        body = ('<c:addressbook-multiget xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:carddav"><d:prop>'
                f'<d:getetag/><c:address-data/></d:prop><d:href>/carddav/kontakte/{PERSON_UID}.vcf</d:href>'
                '</c:addressbook-multiget>')
        r = self.dav("REPORT", "/carddav/kontakte/", body, HTTP_USER_AGENT="iOS/17.0 (21A329) dataaccessd/1.0")
        self.assertEqual(r.status_code, 207)
        self.assertIn("ORG:Muster IT GmbH;Informatik".encode(), r.content)
        self.assertEqual(r.content.count(b"BEGIN:VCARD"), 1)
        r = self.dav("GET", f"/carddav/kontakte/{PERSON_UID}.vcf", HTTP_USER_AGENT="DAVx5/4.4")
        self.assertIn(b"ROLE:Informatik", r.content)

    def test_restricted_fields_hidden(self):
        f = FieldDef.objects.get(key="description")
        f.visible_to = "maintainer"
        f.save()
        r = self.dav("GET", f"/carddav/kontakte/{PERSON_UID}.vcf")
        self.assertNotIn(b"Kaffe", r.content)
