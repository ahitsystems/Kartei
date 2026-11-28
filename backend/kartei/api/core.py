"""Plumbing shared by the API modules: errors, roles and permission checks, serializers."""
import json
from functools import wraps

from django.http import JsonResponse

from .. import audit, totp
from ..models import ROLE_RANK, USER_TYPES, FieldDef, FieldSection, Kind, Profile, Record, Role, Settings

READ, MAINT, ADMIN = 0, 1, 2
MAX_IMAGE = 512 * 1024


# ---------------------------------------------------------------- plumbing

def err(code, status=400, **extra):
    return JsonResponse({"error": code, **extra}, status=status)


def profile_of(user):
    return Profile.objects.get_or_create(user=user, defaults={
        "role": Role.ADMIN if user.is_superuser else Role.READ})[0]


def rank_of(user):
    return 2 if user.is_superuser else ROLE_RANK[profile_of(user).role]


def needs(min_rank):
    def deco(fn):
        @wraps(fn)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return err("unauthenticated", 401)
            request.rank = rank_of(request.user)
            if request.rank < min_rank:
                audit.log(request.user, "denied", "session", request.user.id, request.path[:255],
                          {"needs": [None, ["read", "maintainer", "admin"][min_rank]]})
                return err("forbidden", 403)
            if fn.__name__ not in ("my_totp", "update_me") and totp_due(request.user, request):  # set up the required 2FA first
                return err("totp_setup_required", 403)
            request.json = {}
            if request.content_type == "application/json" and request.body:  # not for uploads: no body size limit there
                try:
                    request.json = json.loads(request.body)
                except (ValueError, RecursionError):  # RecursionError: nested thousands of levels deep
                    return err("invalid_json")
                if not isinstance(request.json, dict):  # every endpoint takes a JSON object
                    return err("invalid_json")
            return fn(request, *args, **kwargs)
        return wrapper
    return deco


def methods(**handlers):
    def view(request, *args, **kwargs):
        h = handlers.get(request.method)
        return h(request, *args, **kwargs) if h else err("method_not_allowed", 405)
    return view


# ---------------------------------------------------------------- serializers

def ser_field(f, users=None):
    """users: user_options(), filled in as the options of user fields so the SPA treats them like selects."""
    return {
        "id": f.id, "key": f.key, "label": {"de": f.label_de, "en": f.label_en}, "applies_to": f.applies_to,
        "type": f.type, "options": users if f.type in USER_TYPES and users is not None else f.options,
        "open_options": f.open_options, "quick_filter": f.quick_filter,
        "required": f.required,
        "min": None if f.min_value is None else str(f.min_value),
        "max": None if f.max_value is None else str(f.max_value), "decimals": f.decimals,
        "section": f.section_id, "order": f.order,
        "visible_to": f.visible_to, "editable_by": f.editable_by, "searchable": f.searchable,
        "regex": f.regex, "help": {"de": f.help_de, "en": f.help_en},
        "vcard": f.vcard, "vcard_label": f.vcard_label, "copy_from": f.copy_from_id, "archived": f.archived,
        "import_aliases": f.import_aliases,
    }


def ser_section(s):
    return {"id": s.id, "key": s.key, "label": {"de": s.label_de, "en": s.label_en}, "order": s.order}


def sections_list():
    return [ser_section(s) for s in FieldSection.objects.all()]


RECORD_COLUMNS = ("uid", "kind", "legacy_id", "first_name", "last_name", "name", "company__uid", "image_type", "data", "version",
                  "created_at", "updated_at", "updated_by__username", "deleted_at", "reviewed_at", "reviewed_by__username")


def _ser_row(v, keys):
    iso = lambda d: d.isoformat() if d else None
    return {
        "uid": str(v["uid"]), "kind": v["kind"], "legacy_id": v["legacy_id"],
        "first_name": v["first_name"], "last_name": v["last_name"], "name": v["name"],
        "company": str(v["company__uid"]) if v["company__uid"] else None,
        "image": v["version"] if v["image_type"] else None,
        "data": {k: x for k, x in v["data"].items() if k in keys[v["kind"]]},
        "version": v["version"], "created_at": iso(v["created_at"]), "updated_at": iso(v["updated_at"]),
        "updated_by": v["updated_by__username"] or "", "deleted_at": iso(v["deleted_at"]),
        "reviewed_at": iso(v["reviewed_at"]), "reviewed_by": v["reviewed_by__username"] or "",
    }


def _keys(fields):
    return {kind: {f.key for f in fields if f.applies(kind)} for kind in Kind.values}


def ser_records(qs, fields):
    """Many records at once: plain column values instead of model instances (bootstrap of 10k+ contacts)."""
    keys = _keys(fields)
    return [_ser_row(v, keys) for v in qs.values(*RECORD_COLUMNS)]


def ser_record(r, fields):
    rel = lambda o: o.username if o else None
    return _ser_row({c: getattr(r, c) for c in RECORD_COLUMNS if "__" not in c} | {
        "company__uid": r.company.uid if r.company_id and r.company else None,
        "updated_by__username": rel(r.updated_by if r.updated_by_id else None),
        "reviewed_by__username": rel(r.reviewed_by if r.reviewed_by_id else None)}, _keys(fields))


def totp_policy(u):
    """Two-factor sign-in is required for this user (Settings.totp_required: none / admins / all)."""
    need = Settings.get().totp_required
    return need == "all" or (need == "admins" and rank_of(u) == 2)


def provider_mfa(request):
    """Signed in by single sign-on, and the admin trusts the provider's multi-factor authentication."""
    return bool(request.session.get("sso_at")) and Settings.get().oidc_mfa_trusted


def totp_due(u, request=None):
    """Required but not set up yet: the user can't do anything else until it is (unless the provider's MFA counts)."""
    return totp_policy(u) and not profile_of(u).totp_secret_enc and not (request and provider_mfa(request))


def ser_user(u):
    p = profile_of(u)
    return {"id": u.id, "username": u.username, "first_name": u.first_name, "last_name": u.last_name,
            "email": u.email, "role": Role.ADMIN if u.is_superuser else p.role, "language": p.language,
            "list_sort": p.list_sort, "name_order": p.name_order, "push_off": p.push_off,
            "source": p.source, "is_active": u.is_active, "is_superuser": u.is_superuser, "totp": bool(p.totp_secret_enc),
            "totp_recovery_legacy": bool(p.totp_secret_enc) and totp.legacy_recovery(p.totp_recovery),
            "totp_due": totp_due(u), "last_login": u.last_login.isoformat() if u.last_login else None}


def visible_fields(rank):
    return [f for f in FieldDef.objects.filter(archived=False) if f.visible(rank)]


def records_qs():
    return Record.objects.select_related("company", "updated_by", "reviewed_by").defer("image")


def snapshot(r):
    """Flat view of a record for audit diffs."""
    return {"first_name": r.first_name, "last_name": r.last_name, "name": r.name,
            "company": r.company.display_name if r.company_id and r.company else None, **r.data}
