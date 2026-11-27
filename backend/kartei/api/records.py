"""Contacts: create, read, update, delete, recycle bin, history, images, own preferences."""
import hashlib
import uuid

from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.utils import timezone

from .. import audit, batch, gdpr, reviews
from ..models import AuditEntry, FieldDef, Kind, Record, Settings
from ..validation import clean_core, merge_data
from .core import (
    ADMIN,
    MAINT,
    MAX_IMAGE,
    READ,
    err,
    needs,
    records_qs,
    ser_record,
    ser_records,
    snapshot,
    visible_fields,
)

# ---------------------------------------------------------------- records

def _resolve_company(value):
    if not value:
        return None, None
    try:
        c = Record.objects.filter(uid=value, kind=Kind.COMPANY, deleted_at__isnull=True).first()
    except ValidationError:  # not a UUID
        c = None
    return (c, None) if c else (None, "invalid_company")


def _apply(rank, r, body, fields):
    core, errors = clean_core(r.kind, body)
    for k, v in core.items():
        setattr(r, k, v)
    if r.kind == Kind.PERSON:
        if "company" in body:
            r.company, e = _resolve_company(body["company"])
            if e:
                errors["company"] = e
        if not (r.first_name or r.last_name):
            errors["last_name"] = "required"
    elif not r.name:
        errors["name"] = "required"
    data = body.get("data", {})
    if not isinstance(data, dict):
        return {"data": "invalid"}
    r.data, ferrors = merge_data(r.kind, r.data, data, fields, rank)
    errors.update(ferrors)
    return errors


def _add_new_options(user, fields, r, username=None):
    """Values typed into open select fields become options of the field (audited like an admin edit)."""
    for f in fields:
        v = r.data.get(f.key)
        if not f.open_options or f.type not in ("select", "multiselect") or v is None:
            continue
        vals = v if isinstance(v, list) else [v]
        if not set(vals) - {o["value"] for o in f.options}:
            continue
        with transaction.atomic():
            f = FieldDef.objects.select_for_update().get(pk=f.pk)
            before = list(f.options)
            known = {o["value"] for o in before}
            f.options = before + [{"value": x, "de": x, "en": x} for x in dict.fromkeys(vals) if x not in known]
            if len(f.options) > len(before):
                f.save(update_fields=["options"])
                audit.log(user, "update", "field", f.id, f.label_de, {"options": [before, f.options]}, username)


@needs(MAINT)
def create_record(request):
    body = request.json
    if body.get("kind") not in Kind.values:
        return err("invalid_kind")
    fields = list(FieldDef.objects.all())
    r = Record(kind=body["kind"], updated_by=request.user)
    if errors := _apply(request.rank, r, body, fields):
        return err("validation", fields=errors)
    r.save()
    audit.log(request.user, "create", "record", r.uid, r.display_name, audit.diff({}, snapshot(r)))
    _add_new_options(request.user, fields, r)
    return JsonResponse(ser_record(r, visible_fields(request.rank)), status=201)


@needs(READ)
def get_record(request, uid):
    r = records_qs().filter(uid=uid).first()
    target = r
    for _ in range(10):  # merged away (maybe repeatedly): the SPA follows to the record it lives on in
        if not (target and target.deleted_at and target.merged_into_id):
            break
        target = records_qs().filter(pk=target.merged_into_id).first()
    if target is not r and target and not target.deleted_at:
        return err("merged", 410, uid=str(target.uid), kind=target.kind)
    if not r or (r.deleted_at and request.rank < MAINT):
        return err("not_found", 404)
    return JsonResponse(ser_record(r, visible_fields(request.rank)))


@needs(MAINT)
def update_record(request, uid):
    body = request.json
    fields = list(FieldDef.objects.all())
    with transaction.atomic():
        r = Record.objects.select_for_update().filter(uid=uid, deleted_at__isnull=True).first()
        if not r:
            return err("not_found", 404)
        if body.get("version") != r.version:
            return err("version_conflict", 409, record=ser_record(r, visible_fields(request.rank)))
        before = snapshot(r)
        if errors := _apply(request.rank, r, body, fields):
            return err("validation", fields=errors)
        change = audit.diff(before, snapshot(r))
        if change:
            r.version += 1
            r.updated_by = request.user
            r.save()
            audit.log(request.user, "update", "record", r.uid, r.display_name, change)
            _add_new_options(request.user, fields, r)
            # editing answers a review that was yours before the edit; then open ones follow a new responsible
            reviews.finish(r.reviews.filter(assignee=request.user), "edited", request.user)
            reviews.reassign(r)
    return JsonResponse(ser_record(r, visible_fields(request.rank)))


def _flag(request, name):
    """A yes/no option given as query parameter (?name=1) or JSON body key."""
    v = request.GET.get(name)
    if v is None and isinstance(getattr(request, "json", None), dict):
        v = request.json.get(name)
    return v in (True, 1, "1", "true", "yes")


@needs(MAINT)
def delete_record(request, uid):
    """Move a record to the recycle bin; ?with_persons=1 on a company takes its live persons along."""
    with transaction.atomic():
        r = Record.objects.select_for_update().filter(uid=uid, deleted_at__isnull=True).first()
        if not r:
            return err("not_found", 404)
        deleted = delete_tree(request.user, r, _flag(request, "with_persons"))
    return JsonResponse({"ok": True, "deleted": deleted})


def delete_with_persons(user, company, changes=None, username=None):
    """Soft-delete a company and all its live persons in one transaction. Each person is audited like a normal
    delete, marked {"with_company": [None, <company name>]}. Returns the deleted uids, the company first."""
    with transaction.atomic():
        persons = list(Record.objects.select_for_update().filter(company=company, kind=Kind.PERSON,
                                                                 deleted_at__isnull=True).order_by("pk"))
        marker = {"with_company": [None, company.display_name]}
        for p in persons:
            _soft_delete(user, p, (changes or {}) | marker, username)
        _soft_delete(user, company, changes, username)
    return [str(company.uid), *(str(p.uid) for p in persons)]


def _soft_delete(user, r, changes=None, username=None):
    r.deleted_at, r.updated_by = timezone.now(), user
    r.version += 1
    r.save()
    reviews.finish(r.reviews.all(), "cancelled")
    audit.log(user, "delete", "record", r.uid, r.display_name, changes, username)


def delete_tree(user, r, with_persons=False, username=None):
    """Move a locked record to the recycle bin; a company with_persons takes its live persons along
    (UI, bulk edit and the REST API share this). Returns the uids moved, the record first."""
    if with_persons and r.kind == Kind.COMPANY:
        return delete_with_persons(user, r, username=username)
    _soft_delete(user, r, None, username)
    return [str(r.uid)]


@needs(MAINT)
def restore_record(request, uid):
    """Restore a record from the recycle bin. A company also takes back the persons listed in {"persons": [uid, …]}
    (the undo of a delete with persons); others in the list are ignored. They are returned in "persons"."""
    with transaction.atomic():
        r = Record.objects.select_for_update().filter(uid=uid, deleted_at__isnull=False).first()
        if not r:
            return err("not_found", 404)
        persons = []
        wanted = request.json.get("persons") if isinstance(request.json, dict) else None
        if r.kind == Kind.COMPANY and isinstance(wanted, list):
            uids = [u for u in wanted if isinstance(u, str) and _is_uuid(u)]
            persons = list(Record.objects.select_for_update().filter(uid__in=uids, company=r, kind=Kind.PERSON,
                                                                     deleted_at__isnull=False))
        for p in persons:
            _restore(request.user, p)
        _restore(request.user, r)
    fields = visible_fields(request.rank)
    if persons:
        return JsonResponse({**ser_record(r, fields), "persons": [ser_record(p, fields) for p in persons]})
    return JsonResponse(ser_record(r, fields))


def _is_uuid(v):
    try:
        uuid.UUID(v)
        return True
    except ValueError:
        return False


def _restore(user, r):
    if r.merged_into_id:  # back as its own contact: the importer updates it again via its legacy id
        t = Record.objects.select_for_update().get(pk=r.merged_into_id)
        if r.legacy_id in t.merged_legacy_ids:
            t.merged_legacy_ids.remove(r.legacy_id)
            t.save(update_fields=["merged_legacy_ids"])
        r.merged_into = None
    r.deleted_at, r.updated_by = None, user
    r.version += 1
    r.save()
    audit.log(user, "restore", "record", r.uid, r.display_name)


@needs(MAINT)
def trash(request):
    fields = visible_fields(request.rank)
    return JsonResponse({"records": ser_records(Record.objects.filter(deleted_at__isnull=False).order_by("-deleted_at"), fields)})


@needs(ADMIN)
def purge_record(request, uid):
    r = Record.objects.filter(uid=uid, deleted_at__isnull=False).first()
    if not r:
        return err("not_found", 404)
    gdpr.purge(request.user, r)
    return JsonResponse({"ok": True})


@needs(ADMIN)
def record_report(request, uid):
    """Data subject access report (GDPR Art. 15): all data, history and recipients of one contact."""
    r = records_qs().filter(uid=uid).first()
    if not r:
        return err("not_found", 404)
    audit.log(request.user, "report", "record", r.uid, r.display_name)
    return JsonResponse(gdpr.report(r, request.user))


@needs(READ)
def history(request, uid):
    # the recycle bin is for maintainers, like the contact itself; a purged one is gone for good
    if request.rank < MAINT and not Record.objects.filter(uid=uid, deleted_at__isnull=True).exists():
        return err("not_found", 404)
    entries = AuditEntry.objects.filter(target_type="record", target_id=str(uid))[:200]
    return JsonResponse({"entries": visible_audit(entries, request.rank)})


# Keys of record audit entries that are not fields: core columns and workflow markers (see SPECIAL in frontend/src/lib/audit.ts).
RECORD_AUDIT_KEYS = frozenset({
    "first_name", "last_name", "name", "company", "image",
    "anonymized", "proposed_by", "note", "comment", "delete", "reviewed", "batch_from", "started", "adjusted", "kind", "reason",
    "bulk", "normalized", "merged_from", "merged_into", "not_duplicate", "with_company", "handover", "renamed"})
# markers of the operation that changed fields: without a visible field change they'd still tell that one happened
OPERATION_KEYS = ("bulk", "batch_from", "normalized")


def ser_audit(e, rank, kinds=None):
    """kinds: {record uid: person/company} so the UI can name and link the object."""
    changes = e.changes
    if rank < ADMIN:
        fields = {f.key: f.visible(rank) for f in FieldDef.objects.all()}
        if e.target_type == "record":  # allowlist: a deleted field's old values must not resurface
            changes = {k: v for k, v in changes.items() if fields.get(k) or (k in RECORD_AUDIT_KEYS and k not in fields)}
            if not any(fields.get(k) for k in changes):  # markers alone would tell of (or name) a hidden change
                for k in ("handover", "renamed") + (OPERATION_KEYS if any(k in fields or k not in RECORD_AUDIT_KEYS
                                                                         for k in e.changes) else ()):
                    changes.pop(k, None)
        else:
            changes = {k: v for k, v in changes.items() if fields.get(k, True)}
    return {"id": e.id, "at": e.at.isoformat(), "user": e.username, "action": e.action,
            "target_type": e.target_type, "target_id": e.target_id, "target_label": e.target_label,
            "kind": (kinds or {}).get(e.target_id), "changes": changes, **({"ip": e.ip} if rank >= ADMIN else {})}


def visible_audit(entries, rank, kinds=None):
    """ser_audit for lists: an entry whose changes were all hidden is left out (its time alone tells something)."""
    out = [(e, ser_audit(e, rank, kinds)) for e in entries]
    return [s for e, s in out if s["changes"] or not e.changes]


def record_kinds(entries):
    uids = []
    for e in entries:
        if e.target_type == "record":
            try:
                uids.append(uuid.UUID(e.target_id))
            except ValueError:
                pass
    return {str(u): k for u, k in Record.objects.filter(uid__in=uids).values_list("uid", "kind")}


# ---------------------------------------------------------------- images

def _sniff(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    return None


def print_logo(s):
    """What the phone list shows at the top: "kartei", "none", or the version of the organisation's own logo."""
    if s.print_logo_mode == "custom" and s.print_logo_type:
        return hashlib.sha256(bytes(s.print_logo)).hexdigest()[:12]
    return "none" if s.print_logo_mode == "none" else "kartei"


@needs(READ)
def get_print_logo(request):
    s = Settings.get()
    if not s.print_logo_type:
        return HttpResponse(status=404)
    resp = HttpResponse(bytes(s.print_logo), content_type=f"image/{s.print_logo_type}")
    resp["Cache-Control"] = "private, max-age=31536000, immutable"  # URL carries ?v=<version>
    return resp


@needs(READ)
def get_image(request, uid):
    r = Record.objects.filter(uid=uid).only("image", "image_type", "deleted_at").first()
    if not r or not r.image_type or (r.deleted_at and request.rank < MAINT):
        return HttpResponse(status=404)
    resp = HttpResponse(bytes(r.image), content_type=f"image/{r.image_type}")
    resp["Cache-Control"] = "private, max-age=31536000, immutable"  # URL carries ?v=<version>
    return resp


@needs(MAINT)
def put_image(request, uid):
    data = request.body
    if len(data) > MAX_IMAGE:
        return err("image_too_large", 413)
    typ = _sniff(data)
    if not typ:
        return err("invalid_image")
    with transaction.atomic():
        r = Record.objects.select_for_update().filter(uid=uid, deleted_at__isnull=True).first()
        if not r:
            return err("not_found", 404)
        r.image, r.image_type, r.updated_by = data, typ, request.user
        r.version += 1
        r.save()
        audit.log(request.user, "update", "record", r.uid, r.display_name, {"image": [None, f"{typ}, {len(data)} B"]})
    return JsonResponse(ser_record(r, visible_fields(request.rank)))


@needs(MAINT)
def delete_image(request, uid):
    with transaction.atomic():
        r = Record.objects.select_for_update().filter(uid=uid, deleted_at__isnull=True).first()
        if not r:
            return err("not_found", 404)
        r.image, r.image_type, r.updated_by = None, "", request.user
        r.version += 1
        r.save()
        audit.log(request.user, "update", "record", r.uid, r.display_name, {"image": ["image", None]})
    return JsonResponse(ser_record(r, visible_fields(request.rank)))


@needs(MAINT)
def propagate(request, uid):
    """Carry company changes over to its persons: without `selected` a preview, with it the update."""
    b = request.json
    company = Record.objects.filter(uid=uid, kind=Kind.COMPANY, deleted_at__isnull=True).first()
    if not company:
        return err("not_found", 404)
    changes = b.get("changes") if isinstance(b.get("changes"), dict) else {}
    if "selected" not in b:
        return JsonResponse({"items": batch.plan(company, changes, request.rank)})
    with transaction.atomic():
        updated, errors = batch.apply(company, changes, b["selected"] or [], request.user, request.rank)
    return JsonResponse({"updated": updated, "errors": errors})
