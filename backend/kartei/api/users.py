"""Admin: users."""

from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse

from .. import audit, handover
from ..models import Profile, Role, assignable
from .core import ADMIN, err, needs, profile_of, rank_of, ser_user

# ---------------------------------------------------------------- admin: users

def _user_snapshot(u):
    d = ser_user(u)
    d.pop("last_login")
    return d


def _admins_left(excluding):
    """Call inside a transaction: the other admins' rows stay locked until it ends, so two admins demoting each
    other at the same time can't both pass."""
    with transaction.atomic():  # a savepoint inside the caller's transaction; there the locks last until its end
        admins = User.objects.select_for_update(of=("self",)).filter(
            Q(profile__role=Role.ADMIN) | Q(is_superuser=True), is_active=True)
        return any(pk != excluding.pk for pk in admins.values_list("pk", flat=True))


@needs(ADMIN)
def users_list(request):
    refs = handover.refs()  # contacts naming each user: left people still named there need a handover
    return JsonResponse({"users": [ser_user(u) | {"refs": refs[u.username]} for u in User.objects.order_by("username")]})


@needs(ADMIN)
def user_handover(request, pk):
    """GET: what a handover would change. POST {to}: the successor takes the user's place in all contacts."""
    u = User.objects.filter(pk=pk).first()
    if not u:
        return err("not_found", 404)
    if request.method == "GET":
        return JsonResponse(handover.plan(u))
    to = User.objects.filter(username=str(request.json.get("to", ""))).select_related("profile").first()
    if not to or to == u or not assignable(to):
        return err("validation", fields={"to": "invalid_user"})
    return JsonResponse({"records": handover.apply(u, to, request.user)})


@needs(ADMIN)
def user_create(request):
    b = request.json
    username = str(b.get("username", "")).strip()
    errors = {}
    if not username:
        errors["username"] = "required"
    elif User.objects.filter(username__iexact=username).exists():
        errors["username"] = "taken"
    if not isinstance(b.get("password"), str) or len(b["password"]) < 10:
        errors["password"] = "too_short"
    if b.get("role") not in Role.values:
        errors["role"] = "invalid"
    if errors:
        return err("validation", fields=errors)
    u = User.objects.create_user(username, b.get("email", ""), b["password"],
                                 first_name=b.get("first_name", ""), last_name=b.get("last_name", ""))
    Profile.objects.update_or_create(user=u, defaults={"role": b["role"], "source": "local",
                                                       "language": b.get("language", "")})
    audit.log(request.user, "create", "user", u.id, u.username, audit.diff({}, _user_snapshot(u)))
    return JsonResponse(ser_user(u), status=201)


@needs(ADMIN)
@transaction.atomic  # the last-admin check holds until the change is saved
def user_update(request, pk):
    u = User.objects.filter(pk=pk).first()
    if not u:
        return err("not_found", 404)
    b, p = request.json, profile_of(u)
    before = _user_snapshot(u)
    demoting = ("role" in b and b["role"] != Role.ADMIN) or ("is_active" in b and not b["is_active"])
    if demoting and rank_of(u) == ADMIN and not _admins_left(u):
        return err("last_admin", 409)
    if "role" in b:
        if b["role"] not in Role.values:
            return err("validation", fields={"role": "invalid"})
        if p.source != "local":  # LDAP or SCIM groups decide
            return err("validation", fields={"role": "ldap_managed"})
        p.role = b["role"]
    if "language" in b and b["language"] in ("", "de", "en"):
        p.language = b["language"]
    for a in ("first_name", "last_name", "email"):
        if a in b and p.source == "local":
            setattr(u, a, str(b[a]))
    if "is_active" in b and bool(b["is_active"]) != u.is_active:
        if p.source != "local":  # the directory decides, see ldapusers and scim
            return err("validation", fields={"is_active": "ldap_managed"})
        u.is_active = bool(b["is_active"])
    if b.get("password"):
        if p.source != "local":
            return err("validation", fields={"password": "ldap_managed"})
        if not isinstance(b["password"], str) or len(b["password"]) < 10:
            return err("validation", fields={"password": "too_short"})
        u.set_password(b["password"])
    u.save()
    p.save()
    change = audit.diff(before, _user_snapshot(u))
    if b.get("password"):
        change["password"] = ["***", "***"]
    if change:
        audit.log(request.user, "update", "user", u.id, u.username, change)
    return JsonResponse(ser_user(u))


@needs(ADMIN)
@transaction.atomic
def user_delete(request, pk):
    u = User.objects.filter(pk=pk).first()
    if not u:
        return err("not_found", 404)
    if u == request.user:
        return err("cannot_delete_self", 409)
    if rank_of(u) == ADMIN and not _admins_left(u):
        return err("last_admin", 409)
    if handover.refs()[u.username]:  # contacts would keep pointing at a user that no longer exists
        return err("handover_first", 409)
    audit.log(request.user, "delete", "user", u.id, u.username, audit.diff(_user_snapshot(u), {}))
    u.delete()
    return JsonResponse({"ok": True})
