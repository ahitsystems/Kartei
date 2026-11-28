import os
import tempfile
from unittest import mock

from django.test import TestCase

from . import exports
from .models import ExportProfile, Record, Settings
from .tests import sample_records

# A CSV export layout: (header, person source, company source)
EXPORT_COLUMNS = [
    ("ID", "calc:legacy_or_uid", "calc:legacy_or_uid"),
    ("Anrede", "field:salutation", ""),
    ("Vorname", "core:first_name", "field:short_name"),
    ("Nachname", "core:last_name", ""),
    ("Funktion", "field:job_title", ""),
    ("Abteilung", "field:department", ""),
    ("Firma", "calc:company_name", "core:name"),
    ("Straße", "field:work_street", "field:work_street"),
    ("PLZ", "field:work_zip", "field:work_zip"),
    ("Ort", "field:work_city", "field:work_city"),
    ("Land", "field:work_country", "field:work_country"),
    ("Telefon", "field:phone_work", "field:company_phone"),
    ("Fax", "field:fax", "field:company_fax"),
    ("Mobil", "field:mobile", ""),
    ("E-Mail", "field:email", "field:company_email"),
    ("Ansprechpartner intern", "field:internal_contact", "field:internal_contact"),
    ("Kontakte intern", "field:internal_contacts", "field:internal_contacts"),
    ("Schlagworte", "field:keywords", "field:keywords"),
    ("Notizen", "field:description", "field:description"),
]


class ExportTests(TestCase):
    def setUp(self):
        sample_records()
        self.dir = tempfile.mkdtemp()
        patcher = mock.patch.object(exports, "EXPORT_ROOT", self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def profile(self, fmt="csv", **kw):
        cols = [{"header": h, "person": p, "company": c} for h, p, c in EXPORT_COLUMNS]
        return ExportProfile.objects.create(name="t", format=fmt, path=f"{self.dir}/out.{fmt}", columns=cols, **kw)

    def test_csv_layout(self):
        p = self.profile(options={"delimiter": ",", "encoding": "utf-8", "line_ending": "lf"})
        self.assertEqual(exports.run(p), "ok", p.last_message)
        text = open(p.path, encoding="utf-8").read()
        header, *rows = text.strip().split("\n")
        self.assertEqual(header.split(","), [h for h, _, _ in EXPORT_COLUMNS])
        self.assertIn("ACME,,,,ACME GmbH", text)  # company: firstname = short name
        self.assertEqual(exports.run(p), "skipped")
        self.assertEqual(os.stat(p.path).st_mode & 0o777, 0o644)

    def test_multiline_and_cp1252(self):
        Record.objects.filter(kind="company", name="ACME GmbH").update(data={"description": "Zeile 1\nZeile 2 €"})
        p = self.profile(options={"encoding": "cp1252", "newlines": "keep"})
        exports.run(p)
        import csv
        with open(p.path, encoding="cp1252", newline="") as fh:
            rows = list(csv.reader(fh, delimiter=";"))
        self.assertIn("Zeile 1\nZeile 2 €", [r[-1] for r in rows])

    def test_guard(self):
        p = self.profile()
        self.assertEqual(exports.run(p), "ok")
        Record.objects.filter(kind="person").update(deleted_at="2026-01-01T00:00Z")
        Record.objects.filter(name="Muster IT GmbH").update(deleted_at="2026-01-01T00:00Z")
        before = open(p.path, "rb").read()
        self.assertEqual(exports.run(p), "error")
        self.assertIn("@export_dropped", p.last_message)
        self.assertEqual(open(p.path, "rb").read(), before)  # file untouched
        Record.objects.update(deleted_at="2026-01-01T00:00Z")
        self.assertEqual(exports.run(p), "error")
        self.assertIn("@export_empty", p.last_message)
        self.assertEqual(exports.run(p, force=True), "ok")

    def test_path_outside_root_rejected(self):
        p = self.profile()
        p.path = "/etc/passwd.csv"
        self.assertEqual(exports.run(p), "error")
        self.assertTrue(os.path.exists("/etc/passwd"))

    def test_xlsx(self):
        from openpyxl import load_workbook
        p = self.profile("xlsx")
        self.assertEqual(exports.run(p), "ok", p.last_message)
        ws = load_workbook(p.path).active
        self.assertEqual(ws["A1"].value, "ID")
        self.assertEqual(ws.freeze_panes, "A2")
        self.assertEqual((ws["A1"].font.name, ws["A1"].font.b, ws["A2"].font.name), ("Tahoma", True, "Tahoma"))


class PhonebookExportTests(TestCase):
    """Swyx-style phonebook: one row per number, a suffix for unique names, canonical numbers."""
    setUp = ExportTests.setUp

    def phonebook(self, **opts):
        cols = [{"header": "Name", "person": "calc:display_name", "company": "core:name", "suffix_target": True},
                {"header": "Nummer", "split": [{"person": "field:phone_work", "company": "field:company_phone"},
                                               {"person": "field:mobile", "suffix": " (Mobil)"}]},
                {"header": "Beschreibung", "person": "field:department", "company": ""},
                {"header": "Show", "person": "const:Show", "company": "const:Show"}]
        return ExportProfile.objects.create(name="swyx", path=f"{self.dir}/pb.csv", columns=cols,
                                            options={"quoting": "all", "header": False, "encoding": "utf-8", **opts})

    def test_split_column_one_row_per_number(self):
        Settings.objects.update(phone_country_code="+49")
        p = self.phonebook(phone_format="canonical")
        self.assertEqual(exports.validate_profile(p), {})
        self.assertEqual(exports.run(p), "ok", p.last_message)
        lines = open(p.path, encoding="utf-8").read().splitlines()
        person = Record.objects.get(kind="person").display_name
        self.assertIn(f'"{person}";"+495111234567";', "\n".join(lines))
        self.assertIn(f'"{person} (Mobil)";"+491701234567";', "\n".join(lines))
        self.assertTrue(all(l.endswith(';"Show"') for l in lines))
        companies = Record.objects.filter(kind="company", data__company_phone__isnull=False).count()
        self.assertEqual(len(lines), 2 + companies)  # contacts without any number are left out
        self.assertNotIn("@export_duplicate_names", p.last_message)

    def test_local_numbers_and_duplicate_names_reported(self):
        Settings.objects.update(phone_country_code="+49")
        r = Record.objects.get(kind="person")
        r.data["phone_work"] = "123"
        r.save()
        Record.objects.create(kind="person", first_name=r.first_name, last_name=r.last_name, data={"phone_work": "0511 99"})
        p = self.phonebook(phone_format="canonical")
        self.assertEqual(exports.run(p), "ok", p.last_message)
        self.assertIn('"123"', open(p.path, encoding="utf-8").read())
        self.assertIn('@export_not_canonical {"n": 1}', p.last_message)
        self.assertIn('@export_duplicate_names {"n": 1}', p.last_message)

    def test_one_split_column(self):
        p = self.phonebook()
        p.columns.append({**p.columns[1]})
        self.assertEqual(exports.validate_profile(p)["columns"], "one_split_column")
