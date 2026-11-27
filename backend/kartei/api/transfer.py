"""Admin: export profiles, audit log, CSV import."""
import json

from django.http import JsonResponse

from .. import audit, exports, importer
from ..models import AuditEntry, ExportProfile, FieldDef
from .core import ADMIN, err, needs
from .records import record_kinds, ser_audit

# ---------------------------------------------------------------- admin: exports

EXPORT_ATTRS = ["name", "enabled", "format", "path", "schedule", "kinds", "filter_values", "columns", "options",
                "max_shrink_percent"]


def ser_export(p):
    return {"id": p.id, **{a: getattr(p, a) for a in EXPORT_ATTRS}, "filter_field": p.filter_field_id,
            "effective_options": p.effective_options(),
            "last_run_at": p.last_run_at.isoformat() if p.last_run_at else None, "last_status": p.last_status,
            "last_message": p.last_message, "last_rows": p.last_rows}


def _export_snapshot(p):
    return {**{a: getattr(p, a) for a in EXPORT_ATTRS}, "filter_field": p.filter_field_id}


def _export_from_body(p, b):
    for a in EXPORT_ATTRS:
        if a in b:
            setattr(p, a, b[a])
    if "filter_field" in b:
        p.filter_field = FieldDef.objects.filter(pk=b["filter_field"]).first() if b["filter_field"] else None
    return exports.validate_profile(p)


@needs(ADMIN)
def exports_list(request):
    return JsonResponse({"exports": [ser_export(p) for p in ExportProfile.objects.order_by("name")],
                         "sources": exports.available_sources(), "export_root": exports.EXPORT_ROOT})


@needs(ADMIN)
def export_create(request):
    p = ExportProfile(name="", path="")
    if errors := _export_from_body(p, request.json):
        return err("validation", fields=errors)
    p.save()
    audit.log(request.user, "create", "export", p.id, p.name, audit.diff({}, _export_snapshot(p)))
    return JsonResponse(ser_export(p), status=201)


@needs(ADMIN)
def export_update(request, pk):
    p = ExportProfile.objects.filter(pk=pk).first()
    if not p:
        return err("not_found", 404)
    before = _export_snapshot(p)
    if errors := _export_from_body(p, request.json):
        return err("validation", fields=errors)
    p.save()
    if change := audit.diff(before, _export_snapshot(p)):
        audit.log(request.user, "update", "export", p.id, p.name, change)
    return JsonResponse(ser_export(p))


@needs(ADMIN)
def export_delete(request, pk):
    p = ExportProfile.objects.filter(pk=pk).first()
    if not p:
        return err("not_found", 404)
    audit.log(request.user, "delete", "export", p.id, p.name, audit.diff(_export_snapshot(p), {}))
    p.delete()
    return JsonResponse({"ok": True})


@needs(ADMIN)
def export_preview(request):
    """Preview an unsaved profile from the editor."""
    p = ExportProfile(name="preview", path=exports.EXPORT_ROOT + "/preview")
    for a in EXPORT_ATTRS:
        if a in request.json and a not in ("name", "path"):
            setattr(p, a, request.json[a])
    if request.json.get("filter_field"):
        p.filter_field = FieldDef.objects.filter(pk=request.json["filter_field"]).first()
    errors = exports.validate_profile(p, check_path=False)
    if errors:
        return err("validation", fields=errors)
    header, rows = exports.build_rows(p)
    return JsonResponse({"header": header, "rows": rows[:20], "total": len(rows)})


@needs(ADMIN)
def export_run(request, pk):
    p = ExportProfile.objects.filter(pk=pk).first()
    if not p:
        return err("not_found", 404)
    exports.run(p, force=bool(request.json.get("force")), user=request.user)
    return JsonResponse(ser_export(p))


# ---------------------------------------------------------------- admin: audit & import

@needs(ADMIN)
def audit_list(request):
    qs = AuditEntry.objects.all()
    if t := request.GET.get("target_type"):
        qs = qs.filter(target_type=t)
    if q := request.GET.get("q"):
        qs = qs.filter(target_label__icontains=q) | qs.filter(username__icontains=q)
    if (before := request.GET.get("before", "")).isdigit():
        qs = qs.filter(id__lt=before)
    entries = list(qs[:100])
    kinds = record_kinds(entries)
    return JsonResponse({"entries": [ser_audit(e, ADMIN, kinds) for e in entries],
                         "more": len(entries) == 100})


@needs(ADMIN)
def import_preview(request):
    f = request.FILES.get("file")
    if not f:
        return err("validation", fields={"file": "required"})
    fields = list(FieldDef.objects.all())
    try:
        result = importer.preview(f.read(), fields)
    except importer.ImportError_ as e:
        return err("import_failed", detail=str(e))
    return JsonResponse({**result, "fields": [{"key": x.key, "label": {"de": x.label_de, "en": x.label_en},
                                               "applies_to": x.applies_to} for x in fields if not x.archived]})


@needs(ADMIN)
def import_csv(request):
    f = request.FILES.get("file")
    if not f:
        return err("validation", fields={"file": "required"})
    dry = request.POST.get("dry_run", "1") == "1"
    try:
        mapping = json.loads(request.POST.get("mapping") or "null")
    except ValueError:
        return err("invalid_json")
    try:
        result = importer.import_csv(f.read(), mapping=mapping, user=request.user, dry_run=dry)
    except importer.ImportError_ as e:
        return err("import_failed", detail=str(e))
    return JsonResponse(result)
