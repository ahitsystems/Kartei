"""Session: sign-in (local, LDAP, SSO), two-factor, push subscriptions, sign-out, bootstrap and changes."""
import json
import time
from datetime import timedelta

from django.conf import settings as dj
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseRedirect, JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import ensure_csrf_cookie

from .. import audit, oidc, push, totp
from ..models import Profile, Record, Settings, user_options
from ..net import client_ip, count_failure
from .core import (
    ADMIN,
    READ,
    err,
    needs,
    profile_of,
    sections_list,
    ser_field,
    ser_records,
    ser_user,
    totp_due,
    totp_policy,
    visible_fields,
)
from .records import print_logo
from .workflow import dashboard_prefs, workflow

# ---------------------------------------------------------------- session

@ensure_csrf_cookie
def session(request):
    s = Settings.get()
    out = {"default_language": s.default_language, "authenticated": request.user.is_authenticated,
           "sso": oidc.configured(s), "sso_label": s.oidc_label}
    if request.user.is_authenticated:
        out |= {"user": ser_user(request.user) | {"totp_due": totp_due(request.user, request)},
                "version": dj.KARTEI_VERSION, "source": dj.KARTEI_SOURCE_URL}
    return JsonResponse(out)


def login_view(request):
    if request.method != "POST":
        return err("method_not_allowed", 405)
    try:
        body = json.loads(request.body or b"{}")
    except (ValueError, RecursionError):
        return err("invalid_json")
    if not isinstance(body, dict):
        return err("invalid_json")
    username, password = str(body.get("username", "")).strip(), str(body.get("password", ""))
    # after single sign-on a user with two-factor sign-in only sends the code (see oidc_callback)
    sso = request.session.get("sso_pending") if not username else None
    if sso:
        if time.time() - sso["at"] > 300:
            request.session.pop("sso_pending")
            _signin_failed(User.objects.filter(pk=sso["user"]).values_list("username", flat=True).first() or "", "sso_expired")
            return err("sso_expired", 401)
        user = User.objects.filter(pk=sso["user"], is_active=True).first()
        username = user.username if user else ""
    key = f"login-fail:{client_ip(request)}:{username.lower()}"
    if cache.get(key, 0) >= 10:
        if cache.get(key, 0) == 10:  # once per lockout, not for every further try
            cache.set(key, 11, 15 * 60)
            _signin_failed(username, "too_many_attempts")
        return err("too_many_attempts", 429)
    if not sso:
        user = authenticate(request, username=username, password=password)
    if user is None or not user.is_active:
        count_failure(key)
        _signin_failed(username, "inactive" if user else "invalid_credentials", user)
        return err("invalid_credentials", 401)
    p = profile_of(user)
    if p.totp_secret_enc:  # two-factor sign-in: the code from the authenticator app (or a recovery code) as well
        if not str(body.get("code") or "").strip():
            return err("totp_required", 401)
        if totp_locked(p):
            _signin_failed(username, "too_many_attempts", user)
            return err("too_many_attempts", 429)
        if not _second_factor(user, p, str(body["code"])):
            count_failure(key)
            _signin_failed(username, "invalid_code", user)
            return err("invalid_code", 401)
    cache.delete(key)
    if sso:
        request.session.pop("sso_pending")
        _sso_login(request, user)
    else:
        login(request, user)
        method = "ldap" if "LDAP" in getattr(user, "backend", "") else "password"
        audit.log(user, "login", "session", user.id, user.username, {"method": [None, method]})
    return JsonResponse({"user": ser_user(user)})


def _signin_failed(username, reason, user=None):
    """Audit entry for a refused sign-in; the attempted name is the label (there may be no such user)."""
    audit.log(None, "login_failed", "session", user.id if user else "", username[:150],
              {"reason": [None, reason]}, username=username[:150] or "?")


def _sso_login(request, user):
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    request.session["sso_at"] = int(time.time())  # stands in for the password when a SCIM user sets up 2FA
    audit.log(user, "login", "session", user.id, user.username, {"method": [None, "oidc"]})


def oidc_start(request):
    """Top-level navigation from the login page to the identity provider."""
    s = Settings.get()
    if not oidc.configured(s):
        return err("not_found", 404)
    try:
        return HttpResponseRedirect(oidc.start(request.session, s))
    except oidc.SignInError as e:
        return HttpResponseRedirect(f"/?login_error={e}")


def oidc_callback(request):
    """The provider sends the browser back here; on success the user is signed in, else the login page shows why."""
    s = Settings.get()
    if not oidc.configured(s):
        return err("not_found", 404)
    if request.GET.get("error"):  # cancelled or refused at the provider
        return HttpResponseRedirect("/?login_error=sso_cancelled")
    try:
        user = oidc.finish(request.session, s, request.GET.get("code", ""), request.GET.get("state", ""))
    except oidc.SignInError as e:
        _signin_failed("", str(e))  # ponytail: the provider's name for the user isn't known at this point
        return HttpResponseRedirect(f"/?login_error={e}")
    if profile_of(user).totp_secret_enc and not s.oidc_mfa_trusted:  # unless the provider's MFA counts, the login page asks
        request.session["sso_pending"] = {"user": user.id, "at": int(time.time())}
        return HttpResponseRedirect("/?login_step=totp")
    _sso_login(request, user)
    return HttpResponseRedirect("/")


TOTP_MAX_FAILURES, TOTP_LOCK = 5, timedelta(minutes=15)


def totp_locked(p):
    return p.totp_failures >= TOTP_MAX_FAILURES and p.totp_failed_at and p.totp_failed_at > timezone.now() - TOTP_LOCK


def _second_factor(user, p, code):
    """Check a TOTP code (each step only once) or use up a recovery code. Under a row lock, so parallel requests
    can't use one code twice; 5 wrong codes in a row block the check for 15 minutes (in the database, for all workers)."""
    with transaction.atomic():
        p = Profile.objects.select_for_update().get(pk=p.pk)
        if totp_locked(p):
            return False
        if (step := totp.verify(p.totp_secret, code, p.totp_last_counter)) is not None:
            p.totp_last_counter, p.totp_failures = step, 0
            p.save(update_fields=["totp_last_counter", "totp_failures"])
            return True
        if h := totp.match_recovery(p.totp_recovery, code):
            p.totp_recovery, p.totp_failures = [x for x in p.totp_recovery if x != h], 0
            p.save(update_fields=["totp_recovery", "totp_failures"])
            audit.log(user, "update", "user", user.id, user.username, {"totp_recovery_used": [None, len(p.totp_recovery)]})
            return True
        p.totp_failures, p.totp_failed_at = p.totp_failures + 1, timezone.now()
        p.save(update_fields=["totp_failures", "totp_failed_at"])
        return False


@needs(READ)
def my_push(request):
    """Web Push on this device. GET: the server key to subscribe with. POST: keep the browser's subscription
    (PushSubscription.toJSON()), or {"test": true} to send a test to all own devices. DELETE {endpoint}: stop."""
    if request.method == "GET":
        return JsonResponse({"key": push.public_key()})
    if request.method == "DELETE":
        push.unsubscribe(request.user, request.json.get("endpoint", ""))
        return JsonResponse({})
    if request.json.get("test"):
        return JsonResponse({"sent": push.send_test(request.user)})
    if not push.subscribe(request.user, request.json):
        return err("validation", fields={"endpoint": "invalid"})
    return JsonResponse({})


@needs(READ)
def my_totp(request):
    """Two-factor sign-in for the signed-in user. action: setup (new secret, kept in the session until confirmed),
    confirm (code → on, returns recovery codes once), recovery (code → new recovery codes), disable (code → off)."""
    b, u, p = request.json, request.user, profile_of(request.user)
    action = b.get("action")
    if action in ("setup", "confirm") and p.totp_secret_enc:  # replacing an active secret needs disabling (with a code) first
        return err("totp_already_on", 409)
    if action == "setup":
        secret = totp.new_secret()
        request.session["totp_pending"] = secret
        return JsonResponse({"secret": secret, "uri": totp.uri(secret, u.username)})
    if action == "confirm":
        # the password as well: a session alone (e.g. on an unlocked computer) must not be able to lock the owner out;
        # users without one (SCIM) instead need a single sign-on in the last 10 minutes
        if u.has_usable_password() or p.source != "scim":
            ok = _stepup(u, lambda: authenticate(request, username=u.username, password=str(b.get("password") or "")) == u)
            if ok is None:
                return err("too_many_attempts", 429)
            if not ok:
                return err("validation", fields={"password": "invalid"})
        elif time.time() - request.session.get("sso_at", 0) > 600:
            return err("sso_reauth", 403)
        secret = request.session.get("totp_pending", "")
        step = totp.verify(secret, b.get("code"))
        if step is None:
            return err("validation", fields={"code": "invalid_code"})
        codes, hashes = totp.new_recovery_codes()
        p.totp_secret, p.totp_last_counter, p.totp_recovery = secret, step, hashes
        p.save()
        del request.session["totp_pending"]
        end_sessions(u, keep=request.session.session_key)  # others opened with the password alone end
        audit.log(u, "update", "user", u.id, u.username, {"totp": [False, True]})
        return JsonResponse({"user": ser_user(u), "recovery": codes})
    if action in ("recovery", "disable"):
        if not p.totp_secret_enc:
            return err("not_found", 404)
        if action == "disable" and totp_policy(u):
            return err("totp_policy", 409)
        if totp_locked(p):
            return err("too_many_attempts", 429)
        if not _second_factor(u, p, str(b.get("code") or "")):
            return err("validation", fields={"code": "invalid_code"})
        if action == "recovery":
            codes, p.totp_recovery = totp.new_recovery_codes()
            p.save(update_fields=["totp_recovery"])
            audit.log(u, "update", "user", u.id, u.username, {"totp_recovery_new": [None, len(codes)]})
            return JsonResponse({"recovery": codes, "user": ser_user(u)})
        p.totp_secret, p.totp_last_counter, p.totp_recovery = "", None, []
        p.save()
        audit.log(u, "update", "user", u.id, u.username, {"totp": [True, False]})
        return JsonResponse({"user": ser_user(u)})
    return err("invalid", 400)


@needs(ADMIN)
def user_totp_reset(request, pk):
    """An admin switches off a user's two-factor sign-in (lost phone); the user can set it up again."""
    u = User.objects.filter(pk=pk).first()
    if not u:
        return err("not_found", 404)
    p = profile_of(u)
    if p.totp_secret_enc:
        p.totp_secret, p.totp_last_counter, p.totp_recovery = "", None, []
        p.save()
        end_sessions(u, keep=request.session.session_key)  # the lost phone's sessions too (not the admin's own)
        audit.log(request.user, "update", "user", u.id, u.username, {"totp": [True, False]})
    return JsonResponse(ser_user(u))


def end_sessions(user, keep=None):
    """Sign the user out everywhere except the session `keep`."""
    # ponytail: decodes every live session; fine for a directory's users, a user → session index if it ever isn't
    from django.contrib.sessions.models import Session
    for s in Session.objects.filter(expire_date__gt=timezone.now()).exclude(session_key=keep or ""):
        if s.get_decoded().get("_auth_user_id") == str(user.pk):
            s.delete()


def logout_view(request):
    if request.method != "POST":
        return err("method_not_allowed", 405)
    if request.user.is_authenticated:
        audit.log(request.user, "logout", "session", request.user.id, request.user.username)
    logout(request)
    return JsonResponse({"ok": True})


@needs(READ)
def bootstrap(request):
    now = timezone.now()
    fields = visible_fields(request.rank)
    records = ser_records(Record.objects.filter(deleted_at__isnull=True), fields)
    s, users = Settings.get(), user_options()
    return JsonResponse({"cursor": now.isoformat(), "user": ser_user(request.user),
                         "default_language": s.default_language, "map_url": s.map_url, "print_logo": print_logo(s),
                         "tel": {"outside": s.tel_outside, "plus": s.tel_plus},
                         "fields": [ser_field(f, users) for f in fields], "sections": sections_list(),
                         "records": records, **workflow(request),
                         **dashboard_prefs(request)})


@needs(READ)
def changes(request):
    try:
        since = parse_datetime(request.GET.get("since", "") or "")
    except ValueError:  # well-formed but impossible, e.g. month 13
        since = None
    if since and timezone.is_naive(since):
        since = timezone.make_aware(since)
    now = timezone.now()
    s = Settings.get()
    if since is None or (s.trash_days and since < now - timedelta(days=s.trash_days)):
        return JsonResponse({"reset": True})
    fields, users = visible_fields(request.rank), user_options()
    # ponytail: 5 s overlap covers transactions committing with an older updated_at; client dedupes by version
    cut = since - timedelta(seconds=5)
    changed = ser_records(Record.objects.filter(Q(updated_at__gte=cut) | Q(reviewed_at__gte=cut)), fields)
    return JsonResponse({
        "cursor": now.isoformat(), "user": ser_user(request.user), "default_language": s.default_language, "map_url": s.map_url, "print_logo": print_logo(s),
        "tel": {"outside": s.tel_outside, "plus": s.tel_plus},
        "fields": [ser_field(f, users) for f in fields], "sections": sections_list(),
        "records": [r for r in changed if not r["deleted_at"]],
        "deleted": [r["uid"] for r in changed if r["deleted_at"]],
        **workflow(request), **dashboard_prefs(request),
    })


# ---------------------------------------------------------------- me

LIST_SORTS = ("last_name", "first_name", "updated", "created")
NAME_ORDERS = ("first_last", "last_first")


def _stepup(u, check):
    """Re-check of the own password from inside a session. Counts failures per user like the sign-in does:
    a stolen session must not be able to guess the password. -> True, False, or None (locked for 15 minutes)."""
    key = f"stepup-fail:{u.id}"
    if cache.get(key, 0) >= 10:
        return None
    if check():
        return True
    if count_failure(key) == 10:
        _signin_failed(u.username, "too_many_attempts", u)  # in the sign-in log, like a lockout at the login
    return False


@needs(READ)
def update_me(request):
    body, p, u = request.json, profile_of(request.user), request.user
    if "language" in body:
        if body["language"] not in ("", "de", "en"):
            return err("validation", fields={"language": "invalid"})
        p.language = body["language"]
        p.save()
    for key, allowed in (("list_sort", LIST_SORTS), ("name_order", NAME_ORDERS)):
        if key in body:
            if body[key] not in allowed:
                return err("validation", fields={key: "invalid"})
            setattr(p, key, body[key])
            p.save(update_fields=[key])
    if "push_off" in body:
        if not isinstance(body["push_off"], list) or any(k not in push.KINDS for k in body["push_off"]):
            return err("validation", fields={"push_off": "invalid"})
        p.push_off = sorted(set(body["push_off"]))
        p.save(update_fields=["push_off"])
    if "password" in body:
        if p.source != "local":
            return err("forbidden", 403)
        ok = _stepup(u, lambda: u.check_password(str(body.get("old_password", ""))))
        if ok is None:
            return err("too_many_attempts", 429)
        if not ok:
            return err("validation", fields={"old_password": "invalid"})
        if p.totp_secret_enc:  # with two-factor sign-in, a guessed password alone must not take the account
            if totp_locked(p):
                return err("too_many_attempts", 429)
            if not _second_factor(u, p, str(body.get("code") or "")):
                return err("validation", fields={"code": "invalid_code"})
        if not isinstance(body["password"], str) or len(body["password"]) < 10:
            return err("validation", fields={"password": "too_short"})
        u.set_password(body["password"])
        u.save()
        login(request, u, backend="django.contrib.auth.backends.ModelBackend")
        audit.log(u, "password", "user", u.id, u.username)
    return JsonResponse({"user": ser_user(u)})
