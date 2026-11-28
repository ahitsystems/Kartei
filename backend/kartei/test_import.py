import json

from django.test import TestCase

from .models import FieldDef, Record
from .tests import ApiTestBase, import_test_csv, load_fields


class ImportTests(TestCase):
    CSV = ("ID;Anrede;Vorname;Nachname;Funktion;Abteilung;Firma;PLZ;Telefon;E-Mail;Schlagworte;Notizen\n"
           "F-5;;ACME;;;;ACME GmbH;10115;030 123;info@acme.com;Kunde |##| Partner;\"zwei\nZeilen\"\n"
           "P-7;Herr;Max;Muster;CEO;IT;ACME GmbH;10115;030 124;max@acme.com;Kunde;\n"
           "P-8;;Ohne;Firma;;;Neue Firma;;kaputt!;;;\n").encode("cp1252")

    def setUp(self):
        load_fields()

    def test_import_idempotent(self):
        dry = import_test_csv(self.CSV, dry_run=True)
        self.assertEqual(dry["persons_created"], 2)
        self.assertFalse(Record.objects.exists())
        res = import_test_csv(self.CSV, dry_run=False)
        self.assertEqual((res["companies_created"], res["persons_created"]), (2, 2))
        self.assertTrue(any("invalid_phone" in w for w in res["warnings"]))
        max_ = Record.objects.get(legacy_id="P-7")
        self.assertEqual(max_.company.legacy_id, "F-5")
        self.assertEqual(max_.data["salutation"], "Herr")
        acme = Record.objects.get(legacy_id="F-5")
        self.assertEqual(acme.data["keywords"], ["Kunde", "Partner"])
        self.assertEqual(acme.data["description"], "zwei\nZeilen")
        again = import_test_csv(self.CSV, dry_run=False)
        self.assertEqual(again["persons_updated"] + again["persons_created"], 0)


class CsvMappingTests(ApiTestBase):
    """Generic CSV: suggested mapping, kind column, new fields."""
    CSV = ("Art;Vorname;Nachname;Firma;E-Mail;Telefon Zentrale;Lieblingsfarbe;Notiz\n"
           "Firma;;;Ärzte AG;;0511 1;;x\n"
           "Person;Eva;Huber;Ärzte AG;eva@aerzte.de;;blau;y\n"
           "Kontakt;Tom;;;;;grün;\n"
           "Alien;Ute;Ufo;;;;;\n").encode()

    def upload(self, url, **data):
        from django.core.files.uploadedfile import SimpleUploadedFile
        return self.client.post(url, {"file": SimpleUploadedFile("x.csv", self.CSV), **data})

    def test_preview_suggests(self):
        self.as_("admin")
        r = self.upload("/api/admin/import/preview").json()
        cols = r["mapping"]["columns"]
        self.assertEqual(r["mapping"]["kind"]["column"], "Art")
        self.assertEqual(cols["Vorname"], {"person": "core:first_name", "company": ""})
        self.assertEqual(cols["Firma"], {"person": "core:company", "company": "core:name"})
        self.assertEqual(cols["E-Mail"], {"person": "field:email", "company": "field:company_email"})  # label / alias
        self.assertEqual(cols["Telefon Zentrale"], {"person": "", "company": "field:company_phone"})
        self.assertEqual(cols["Lieblingsfarbe"], {"person": "", "company": ""})
        self.assertEqual((r["rows"], r["samples"][1][1]), (4, "Eva"))

    def test_import_with_mapping(self):
        self.as_("admin")
        mapping = {"columns": {"Lieblingsfarbe": {"person": "new", "company": ""},
                               "Notiz": {"person": "field:description", "company": "field:description"}}}
        dry = self.upload("/api/admin/import", dry_run="1", mapping=json.dumps(mapping)).json()
        self.assertEqual((dry["companies_created"], dry["persons_created"]), (1, 3))
        self.assertFalse(FieldDef.objects.filter(key="lieblingsfarbe").exists())
        res = self.upload("/api/admin/import", dry_run="0", mapping=json.dumps(mapping)).json()
        self.assertTrue(any(w.startswith("@import_kind_unknown") and "Alien" in w for w in res["warnings"]))
        eva = Record.objects.get(first_name="Eva")
        self.assertEqual((eva.company.name, eva.data["lieblingsfarbe"], eva.data["description"]), ("Ärzte AG", "blau", "y"))
        self.assertEqual(Record.objects.get(kind="company").data, {"company_phone": "+49 511 1", "description": "x"})
        self.assertEqual(Record.objects.get(first_name="Tom").kind, "person")
        self.assertEqual(FieldDef.objects.get(key="lieblingsfarbe").applies_to, "person")
        bad = {"columns": {"Notiz": {"person": "field:company_phone", "company": ""}}}
        r = self.upload("/api/admin/import", dry_run="1", mapping=json.dumps(bad))
        self.assertEqual(r.status_code, 400)
        self.assertIn("@import_bad_target", r.json()["detail"])


class CsvReadTests(ApiTestBase):
    """Files as real exports produce them: none of these may end in a server error."""
    def preview(self, data, status=200):
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.as_("admin")
        r = self.client.post("/api/admin/import/preview", {"file": SimpleUploadedFile("k.csv", data, content_type="text/csv")})
        self.assertEqual(r.status_code, status, r.content[:300])
        return r.json()

    def test_line_endings_encodings_and_long_fields(self):
        base = "Vorname;Nachname;Firma\nJürgen;Groß;Müller GmbH\nAnna;Bär;\n"
        for data in (base.replace("\n", "\r").encode("cp1252"),  # "CSV (Macintosh)"
                     base.replace("\n", "\r\n").encode("utf-8-sig"), base.encode("utf-16"), base.encode("cp1252")):
            p = self.preview(data)
            self.assertEqual(p["headers"], ["Vorname", "Nachname", "Firma"])
            self.assertEqual(p["samples"][0][:2], ["Jürgen", "Groß"])
        self.assertEqual(self.preview(("A;B\nx;" + "y" * 200000 + "\n").encode())["headers"], ["A", "B"])

    def test_excel_and_broken_files_get_a_message(self):
        self.assertIn("import_excel", self.preview(b"PK\x03\x04rest", status=400)["detail"])
        self.assertIn("import_unreadable", self.preview(b'A;B\n"x;y\n' + b"z" * 20 * 1024 * 1024, status=400)["detail"])

    def test_columns_guessed_from_content(self):
        csv_ = ("ID;Vorname;Nachname;Typ;Satzart;Kontakt;Durchwahl;Nr2;Netz;Geb;Gruss;Wer;Plz2\n"
                "X-1;Anna;Bär;X-1;Person;anna@x.de;0511 1234567;0170 7654321;www.x.de;01.02.1990;Frau;maintainer;30159\n"
                "X-2;;;X-2;Firma;info@y.de;+49 511 99999;0171 1111111;https://y.de;1995-03-04;Herr;maintainer;30160\n")
        p = self.preview(csv_.encode())
        cols = p["mapping"]["columns"]
        self.assertEqual(p["mapping"]["kind"]["column"], "Satzart")  # "Typ" holds IDs, not kinds
        self.assertEqual(cols["Kontakt"], {"person": "field:email", "company": "field:company_email"})
        self.assertEqual(cols["Durchwahl"], {"person": "field:phone_work", "company": "field:company_phone"})
        self.assertEqual(cols["Nr2"]["person"], "field:mobile")  # the next free phone field
        self.assertEqual(cols["Netz"], {"person": "field:website", "company": "field:website"})
        self.assertEqual(cols["Geb"], {"person": "field:birthday", "company": ""})
        self.assertEqual(cols["Gruss"]["person"], "field:salutation")
        self.assertEqual(cols["Wer"], {"person": "field:internal_contact", "company": "field:internal_contact"})
        for h in ("Typ", "Plz2", "Satzart"):
            self.assertEqual(cols[h], {"person": "", "company": ""}, h)
        self.assertEqual(p["guessed"], ["Kontakt", "Durchwahl", "Nr2", "Netz", "Geb", "Gruss", "Wer"])

    def test_new_field_types(self):
        from .importer import new_field
        self.assertEqual(new_field(["a@x.de", "b@y.de"])["type"], "email")
        self.assertEqual(new_field(["zwei\nZeilen", "x"])["type"], "longtext")
        self.assertEqual(new_field(["12", "-3"])["type"], "integer")
        self.assertEqual(new_field(["1,5", "2"])["type"], "decimal")
        self.assertEqual(new_field(["01234", "30159"])["type"], "text")  # leading zero: a postcode, not a number
        self.assertEqual(new_field(["Gold", "Silber", "Gold", "Gold"]),
                         {"type": "select", "options": [{"value": v, "de": v, "en": v} for v in ("Gold", "Silber")]})
        self.assertEqual(new_field(["A, B", "B"])["type"], "multiselect")
        self.assertEqual(new_field(["Freitext eins", "Freitext zwei"])["type"], "text")
        p = self.preview("Name;Rabatt\nx;5\ny;7\n".encode())
        self.assertEqual(p["new_fields"]["Rabatt"]["type"], "integer")

    def test_large_upload(self):
        rows = "".join(f"Max{i};Muster{i};m{i}@example.com;{'x' * 120}\n" for i in range(80000))  # ~12 MB
        self.assertEqual(len(self.preview(("Vorname;Nachname;E-Mail;Notiz\n" + rows).encode())["headers"]), 4)
