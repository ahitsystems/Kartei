"""Admin: fields and field sections, field sets as JSON."""
import re

from django.db import transaction
from django.http import JsonResponse
from django.utils.text import slugify

from .. import audit
from ..models import USER_TYPES, FieldDef, FieldSection, FieldType, Record, Role, user_options
from ..validation import REGEX_TYPES
from ..vcard import TARGETS as VCARD_TARGETS
from .core import ADMIN, err, needs, sections_list, ser_field, ser_section

# ---------------------------------------------------------------- admin: fields

FIELD_ATTRS = ["label_de", "label_en", "help_de", "help_en", "regex", "applies_to", "type", "options", "open_options",
               "required",
               "min_value", "max_value",
               "decimals", "visible_to", "editable_by", "searchable", "vcard",
               "vcard_label", "import_aliases", "quick_filter"]
COMPATIBLE = {("text", "longtext"), ("longtext", "text"), ("select", "multiselect"), ("phone", "text"),
              ("email", "text"), ("url", "text"), ("integer", "decimal"), ("user", "users"),
              ("text", "contact")}
TO_LIST = {("select", "multiselect"), ("user", "users")}  # stored values are wrapped into lists


def _field_from_body(f, body):
    """Flatten the SPA's {label: {de,en}} shape and copy attributes. Returns errors."""
    b = dict(body)
    for nested in ("label", "help"):
        if isinstance(b.get(nested), dict):
            b[f"{nested}_de"], b[f"{nested}_en"] = b[nested].get("de", ""), b[nested].get("en", "")
    if "min" in b:
        b["min_value"] = b["min"] if b["min"] not in ("", None) else None
    if "max" in b:
        b["max_value"] = b["max"] if b["max"] not in ("", None) else None
    old_type = f.type if f.pk else None
    for a in FIELD_ATTRS:
        if a in b:
            setattr(f, a, b[a])
    errors = {}
    if not (f.label_de or "").strip() or not (f.label_en or "").strip():
        errors["label"] = "required"
    if f.type not in FieldType.values:
        errors["type"] = "invalid"
    if f.applies_to not in ("person", "company", "both"):
        errors["applies_to"] = "invalid"
    if f.visible_to not in Role.values or f.editable_by not in (Role.MAINTAINER, Role.ADMIN):
        errors["visible_to"] = "invalid"
    f.regex = (f.regex or "").strip()
    if f.regex:
        if f.type not in REGEX_TYPES:
            f.regex = ""
        else:
            try:
                re.compile(f.regex)
            except re.error:
                errors["regex"] = "invalid_regex"
    if f.type in ("select", "multiselect"):
        opts = f.options if isinstance(f.options, list) else []
        values = [str(o.get("value", "")).strip() for o in opts if isinstance(o, dict)]
        if len(values) != len(opts) or "" in values or len(set(values)) != len(values):
            errors["options"] = "invalid"
        else:
            f.options = [{"value": v, "de": o.get("de") or v, "en": o.get("en") or v} for v, o in zip(values, opts, strict=True)]
    else:
        f.options = []
    aliases = f.import_aliases.split(",") if isinstance(f.import_aliases, str) else f.import_aliases
    if isinstance(aliases, list) and all(isinstance(a, str) for a in aliases):
        f.import_aliases = list(dict.fromkeys(a.strip() for a in aliases if a.strip()))
    else:
        errors["import_aliases"] = "invalid"
    if (old_type, f.type) == ("text", "contact"):
        f.open_options = True  # existing text values stay valid as free text
    if old_type and old_type != f.type and (old_type, f.type) not in COMPATIBLE \
            and Record.objects.filter(data__has_key=f.key).exists():
        errors["type"] = "incompatible_type"
    if "section" in b:  # section id, None = "Other"
        sec = FieldSection.objects.filter(pk=b["section"]).first() if isinstance(b["section"], int) else None
        if b["section"] not in (None, "") and not sec:
            errors["section"] = "invalid"
        f.section = sec
    if "copy_from" in b:
        src = FieldDef.objects.filter(pk=b["copy_from"]).first() if b["copy_from"] else None
        if b["copy_from"] and (not src or not src.applies("company")):
            errors["copy_from"] = "invalid"
        f.copy_from = src
    try:
        if f.min_value is not None and f.max_value is not None and float(f.min_value) > float(f.max_value):
            errors["min"] = "invalid"
    except (TypeError, ValueError):
        errors["min"] = "invalid_number"
    return errors


def _field_snapshot(f):
    return {**{a: getattr(f, a) for a in FIELD_ATTRS}, "copy_from": f.copy_from_id, "section": f.section_id,
            "archived": f.archived,
            "min_value": str(f.min_value) if f.min_value is not None else None,
            "max_value": str(f.max_value) if f.max_value is not None else None}


@needs(ADMIN)
def fields_list(request):
    return JsonResponse({"fields": [ser_field(f) for f in FieldDef.objects.all()], "sections": sections_list(),
                         "vcard_targets": VCARD_TARGETS})


@needs(ADMIN)
def field_create(request):
    f = FieldDef(applies_to="person", label_de="", label_en="")
    if errors := _field_from_body(f, request.json):
        return err("validation", fields=errors)
    base = slugify(request.json.get("key") or f.label_en or f.label_de).replace("-", "_")[:50] or "field"
    key, i = base, 2
    while FieldDef.objects.filter(key=key).exists():
        key, i = f"{base}_{i}", i + 1
    f.key = key
    f.order = (FieldDef.objects.order_by("-order").values_list("order", flat=True).first() or 0) + 10
    f.save()
    audit.log(request.user, "create", "field", f.id, f.label_de, audit.diff({}, _field_snapshot(f)))
    return JsonResponse(ser_field(f), status=201)


def _wrap_lists(user, f, old_type):
    if (old_type, f.type) in TO_LIST:
        for r in Record.objects.filter(data__has_key=f.key):
            old = r.data[f.key]
            if not isinstance(old, list):
                r.data[f.key] = [old]
                r.version += 1
                r.save()
                audit.log(user, "update", "record", r.uid, r.display_name, {f.key: [old, [old]]})


@needs(ADMIN)
def field_update(request, pk):
    f = FieldDef.objects.filter(pk=pk).first()
    if not f:
        return err("not_found", 404)
    before = _field_snapshot(f)
    if "archived" in request.json:
        f.archived = bool(request.json["archived"])
    if errors := _field_from_body(f, request.json):
        return err("validation", fields=errors)
    with transaction.atomic():
        f.save()
        if change := audit.diff(before, _field_snapshot(f)):
            audit.log(request.user, "update", "field", f.id, f.label_de, change)
        _wrap_lists(request.user, f, before["type"])
    return JsonResponse(ser_field(f, user_options() if f.type in USER_TYPES else None))


@needs(ADMIN)
def field_delete(request, pk):
    f = FieldDef.objects.filter(pk=pk).first()
    if not f:
        return err("not_found", 404)
    if Record.objects.filter(data__has_key=f.key).exists():
        return err("field_has_data", 409)  # archive instead
    audit.log(request.user, "delete", "field", f.id, f.label_de, audit.diff(_field_snapshot(f), {}))
    f.delete()
    return JsonResponse({"ok": True})


@needs(ADMIN)
def fields_reorder(request):
    """{ids: [field ids in the new order], moves: {field id: section id or null}} (drag & drop between sections)."""
    b = request.json if isinstance(request.json, dict) else {}
    ids, moves = b.get("ids", []), b.get("moves") or {}
    if not isinstance(moves, dict):
        return err("validation", fields={"moves": "invalid"})
    with transaction.atomic():
        for pk, sid in moves.items():
            f = FieldDef.objects.filter(pk=pk).first()
            if not f:
                continue
            if sid is not None and not (isinstance(sid, int) and FieldSection.objects.filter(pk=sid).exists()):
                transaction.set_rollback(True)
                return err("validation", fields={"section": "invalid"})
            if f.section_id != sid:
                audit.log(request.user, "update", "field", f.id, f.label_de, {"section": [f.section_id, sid]})
                f.section_id = sid
                f.save(update_fields=["section"])
        for i, pk in enumerate(ids):
            FieldDef.objects.filter(pk=pk).update(order=(i + 1) * 10)
    audit.log(request.user, "reorder", "field", "-", "", {"order": [None, ids]})
    return fields_list(request)


# ---------------------------------------------------------------- admin: field sections

def _section_from_body(s, body):
    """{label: {de, en}}; one language is enough, the other one gets the same text. Returns errors."""
    label = body.get("label") if isinstance(body.get("label"), dict) else {}
    de, en = str(label.get("de") or "").strip()[:100], str(label.get("en") or "").strip()[:100]
    if "label" in body:
        if not (de or en):
            return {"label": "required"}
        s.label_de, s.label_en = de or en, en or de
    return {}


def _new_section_key(label):
    base = slugify(label).replace("-", "_")[:50] or "section"
    key, i = base, 2
    while FieldSection.objects.filter(key=key).exists():
        key, i = f"{base}_{i}", i + 1
    return key


def _next_section_order():
    return (FieldSection.objects.order_by("-order").values_list("order", flat=True).first() or 0) + 10


def _section_snapshot(s):
    return {"key": s.key, "label_de": s.label_de, "label_en": s.label_en, "order": s.order}


@needs(ADMIN)
def sections_get(request):
    return JsonResponse({"sections": sections_list()})


@needs(ADMIN)
def section_create(request):
    s = FieldSection(label_de="", label_en="")
    if errors := _section_from_body(s, {"label": None, **(request.json if isinstance(request.json, dict) else {})}):
        return err("validation", fields=errors)
    s.key = _new_section_key(s.label_en or s.label_de)
    s.order = _next_section_order()
    s.save()
    audit.log(request.user, "create", "section", s.id, s.label_de, audit.diff({}, _section_snapshot(s)))
    return JsonResponse(ser_section(s), status=201)


@needs(ADMIN)
def section_update(request, pk):
    s = FieldSection.objects.filter(pk=pk).first()
    if not s:
        return err("not_found", 404)
    before = _section_snapshot(s)
    if errors := _section_from_body(s, request.json if isinstance(request.json, dict) else {}):
        return err("validation", fields=errors)
    s.save()
    if change := audit.diff(before, _section_snapshot(s)):
        audit.log(request.user, "update", "section", s.id, s.label_de, change)
    return JsonResponse(ser_section(s))


@needs(ADMIN)
def section_delete(request, pk):
    """Only when no active field uses it (409 section_in_use), unless ?move_to=<section id>|other moves its fields."""
    s = FieldSection.objects.filter(pk=pk).first()
    if not s:
        return err("not_found", 404)
    move_to = request.GET.get("move_to", "")
    target = None
    if move_to and move_to != "other":
        target = FieldSection.objects.filter(pk=int(move_to) if move_to.isdigit() else 0).exclude(pk=s.pk).first()
        if not target:
            return err("validation", fields={"move_to": "invalid"})
    in_use = s.fields.filter(archived=False).count()
    if in_use and not move_to:
        return err("section_in_use", 409, count=in_use)
    with transaction.atomic():
        for f in s.fields.all():  # archived ones too; without move_to they fall back to "Other"
            new = target.id if target else None
            audit.log(request.user, "update", "field", f.id, f.label_de, {"section": [s.id, new]})
            f.section_id = new
            f.save(update_fields=["section"])
        audit.log(request.user, "delete", "section", s.id, s.label_de, audit.diff(_section_snapshot(s), {}))
        s.delete()
    return JsonResponse({"ok": True})


@needs(ADMIN)
def sections_reorder(request):
    ids = request.json.get("ids", []) if isinstance(request.json, dict) else []
    with transaction.atomic():
        for i, pk in enumerate(ids):
            FieldSection.objects.filter(pk=pk).update(order=(i + 1) * 10)
    audit.log(request.user, "reorder", "section", "-", "", {"order": [None, ids]})
    return sections_get(request)


FIELDS_FORMAT = "kartei-fields"
FIELDS_VERSION = 2
KEY_RE = re.compile(r"^[a-z0-9_]{1,64}$")


def fields_document():
    """All sections and field definitions (archived too) as a versioned file. List order = order; fields refer to
    their section and copy_from source by key."""
    sections = list(FieldSection.objects.all())
    skeys = {s.id: s.key for s in sections}
    fields = list(FieldDef.objects.all())
    keys = {f.id: f.key for f in fields}
    out = []
    for f in fields:
        d = ser_field(f)
        del d["id"], d["order"]
        d["section"] = skeys.get(f.section_id)
        d["copy_from"] = keys.get(f.copy_from_id)
        out.append(d)
    return {"format": FIELDS_FORMAT, "version": FIELDS_VERSION,
            "sections": [{"key": s.key, "label": {"de": s.label_de, "en": s.label_en}} for s in sections],
            "fields": out}


def _import_sections(items, user):
    """v2 sections, matched by key: new ones are appended in file order, existing ones get the file's labels
    (their order stays). Returns ({key: section}, results)."""
    out, by_key, order = [], {}, _next_section_order()
    for item in items:
        item = item if isinstance(item, dict) else {}
        key = item.get("key")
        res = {"key": str(key or ""), "label": item.get("label"), "errors": {}}
        out.append(res)
        if not isinstance(key, str) or not KEY_RE.match(key) or key in by_key:
            res.update(status="invalid", errors={"key": "invalid"})
            continue
        s = FieldSection.objects.filter(key=key).first() or FieldSection(key=key, label_de="", label_en="")
        before = _section_snapshot(s) if s.pk else {}
        if errors := _section_from_body(s, {"label": item.get("label")}):
            res.update(status="invalid", errors=errors)
            continue
        if not s.pk:
            s.order, order = order, order + 10
        s.save()
        by_key[key] = s
        change = audit.diff(before, _section_snapshot(s))
        res["status"] = "new" if not before else "changed" if change else "unchanged"
        if change:
            audit.log(user, "update" if before else "create", "section", s.id, s.label_de, change)
    return by_key, out


def _v1_section(label, user, results):
    """v1 files carry {de, en} per field: find the section by its German or English label, or create it."""
    if not isinstance(label, dict):
        return None
    de, en = str(label.get("de") or "").strip()[:100], str(label.get("en") or "").strip()[:100]
    if not (de or en):
        return None
    s = (FieldSection.objects.filter(label_de__iexact=de).first() if de else None) \
        or (FieldSection.objects.filter(label_en__iexact=en).first() if en else None)
    if not s:
        s = FieldSection(key=_new_section_key(en or de), label_de=de or en, label_en=en or de, order=_next_section_order())
        s.save()
        audit.log(user, "create", "section", s.id, s.label_de, audit.diff({}, _section_snapshot(s)))
        results.append({"key": s.key, "label": {"de": s.label_de, "en": s.label_en}, "status": "new", "errors": {}})
    return s.id


def import_fields(doc, user=None, apply=False):
    """Preview (apply=False, rolled back) or apply a field file (v1 or v2), matched by key. Sections and fields not in
    the file stay untouched. Returns {"sections": [...], "fields": [...]}, each [{key, label, status:
    new|changed|unchanged|invalid, errors}] in file order; fields also list the changed attributes."""
    ok = isinstance(doc, dict) and doc.get("format") == FIELDS_FORMAT and doc.get("version", 1) in (1, 2)
    items = doc.get("fields") if ok else None
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        raise ValueError("invalid_format")
    v2 = doc.get("version") == 2
    if v2 and not isinstance(doc.get("sections", []), list):
        raise ValueError("invalid_format")
    out, saved, seen = [], [], set()
    company_keys = set(FieldDef.objects.filter(applies_to__in=["company", "both"]).values_list("key", flat=True))
    company_keys |= {i.get("key") for i in items if i.get("applies_to") in ("company", "both")}
    with transaction.atomic():
        sections, section_out = _import_sections(doc.get("sections", []), user) if v2 else ({}, [])
        order = (FieldDef.objects.order_by("-order").values_list("order", flat=True).first() or 0) + 10
        for item in items:
            key = item.get("key")
            res = {"key": str(key or ""), "label": item.get("label"), "changes": [], "errors": {}}
            out.append(res)
            if not isinstance(key, str) or not KEY_RE.match(key) or key in seen:
                res.update(status="invalid", errors={"key": "invalid"})
                continue
            seen.add(key)
            f = FieldDef.objects.filter(key=key).first() or FieldDef(key=key, applies_to="person", label_de="", label_en="")
            before = _field_snapshot(f) if f.pk else {}
            body = {k: v for k, v in item.items() if k not in ("id", "key", "order", "copy_from", "section")}
            unknown_section = False
            if "section" in item:
                if not v2:
                    body["section"] = _v1_section(item["section"], user, section_out)
                elif item["section"] in (None, ""):
                    body["section"] = None
                else:
                    sec = sections.get(item["section"]) or FieldSection.objects.filter(key=str(item["section"])).first()
                    body["section"], unknown_section = (sec.id if sec else None), not sec
            errors = _field_from_body(f, body)
            if unknown_section:
                errors["section"] = "invalid"
            if item.get("copy_from") and item["copy_from"] not in company_keys:
                errors["copy_from"] = "invalid"
            if errors:
                res.update(status="invalid", errors=errors)
                continue
            if "archived" in item:
                f.archived = bool(item["archived"])
            if not f.pk:
                f.order, order = order, order + 10
            f.save()
            saved.append((res, f, item, before))
        for res, f, item, before in saved:  # copy_from once every field of the file exists
            if "copy_from" in item:
                f.copy_from = FieldDef.objects.filter(key=item["copy_from"]).first() if item["copy_from"] else None
                f.save(update_fields=["copy_from"])
            f.refresh_from_db()
            change = audit.diff(before, _field_snapshot(f))
            res["status"] = "new" if not before else "changed" if change else "unchanged"
            if before:
                res["changes"] = sorted(change)
            if change:
                audit.log(user, "update" if before else "create", "field", f.id, f.label_de, change)
            if before:
                _wrap_lists(user, f, before["type"])
        if not apply:
            transaction.set_rollback(True)
    return {"sections": section_out, "fields": out}


@needs(ADMIN)
def fields_export(request):
    return JsonResponse(fields_document())


@needs(ADMIN)
def fields_import(request):
    b = request.json if isinstance(request.json, dict) else {}
    try:
        result = import_fields(b.get("document"), request.user, apply=bool(b.get("apply")))
    except ValueError as e:
        return err(str(e))
    return JsonResponse({"applied": bool(b.get("apply")), **result})
