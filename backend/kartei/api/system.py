"""Admin: settings, tokens and passwords, connection tests and syncs."""
import hashlib
import re
import secrets

from django.contrib.auth.hashers import make_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db.models import Count
from django.http import JsonResponse
from django.utils import timezone

from .. import adsync, audit, backup, exports, graph, ldapusers, monitoring, oidc, relaypush, reviews, syslog
from ..models import ApiToken, ScimGroup, Settings
from ..msg import msg
from ..validation import CONTROL
from .core import ADMIN, MAX_IMAGE, err, needs
from .records import _sniff

# ---------------------------------------------------------------- admin: settings

SETTINGS_ATTRS = ["default_language", "totp_required", "base_url", "trash_days", "purge_anonymize", "export_interval_minutes", "oidc_enabled", "ldap_uri",
                  "ldap_start_tls", "ldap_ca_cert", "ldap_bind_dn", "ldap_user_base", "ldap_user_filter",
                  "ldap_group_admin", "ldap_group_maintainer", "ldap_group_read", "carddav_enabled",
                  "carddav_username", "graph_enabled", "graph_tenant_id", "graph_client_id", "graph_group_id",
                  "graph_folder", "graph_folder_responsible", "graph_folder_internal", "graph_filter_prefix", "review_mode", "review_interval_months", "review_campaign_month",
                  "review_campaign_day", "smtp_host", "smtp_port", "smtp_security", "smtp_username", "smtp_from",
                  "review_remind_days", "proposal_notify", "proposal_notify_to",
                  "ad_enabled", "ad_ou", "ad_bind_dn", "ad_kinds", "ad_columns", "ad_max_shrink_percent"]
SETTINGS_ATTRS += ["phone_normalize", "phone_country_code", "tel_outside", "tel_plus"]  # quality, tel: links
SETTINGS_ATTRS += ["map_url", "ldap_users_max_deactivate_percent", "print_logo_mode"]
SETTINGS_ATTRS += ["backup_enabled", "backup_time", "backup_keep_days", "restore_test_days"]
SETTINGS_ATTRS += ["retention_months", "phonebook_enabled", "phonebook_username", "phonebook_allowlist", "audit_signin_days", "monitoring_allowlist", "syslog_enabled", "syslog_host", "syslog_port",
                   "syslog_protocol", "syslog_ca_cert", "syslog_tls_verify", "syslog_facility", "syslog_content"]
SETTINGS_ATTRS += ["mail_templates", "mail_transport", "mail_graph_sender"]
SETTINGS_ATTRS += ["oidc_issuer", "oidc_client_id", "oidc_scopes", "oidc_claim", "oidc_label", "oidc_mfa_trusted",
                   "scim_group_admin", "scim_group_maintainer", "scim_group_read"]
SETTINGS_ATTRS += ["carddav_relay_enabled", "carddav_relay_url", "carddav_relay_tls_verify", "carddav_relay_ca_cert",
                   "carddav_relay_max_delete_percent"]


def ser_settings(s):
    return {**{a: getattr(s, a) for a in SETTINGS_ATTRS},
            "ldap_bind_password_set": bool(s.ldap_bind_password_enc),
            "carddav_password_set": bool(s.carddav_password_hash), "phonebook_password_set": bool(s.phonebook_password_hash),
            "print_logo_v": hashlib.sha256(bytes(s.print_logo)).hexdigest()[:12] if s.print_logo_type else "", "export_root": exports.EXPORT_ROOT,
            "graph_client_secret_set": bool(s.graph_client_secret_enc), "graph_status": s.graph_status,
            "smtp_password_set": bool(s.smtp_password_enc), "mail_template_defaults": reviews.DEFAULT_TEMPLATES,
            "graph_sync_requested": s.graph_sync_requested, "ldap_users_status": s.ldap_users_status,
            "ad_bind_password_set": bool(s.ad_bind_password_enc), "ad_status": s.ad_status,
            "ad_sync_requested": s.ad_sync_requested, **relaypush.ser(s),
            "oidc_client_secret_set": bool(s.oidc_client_secret_enc), "scim_token_set": bool(s.scim_token_hash),
            "monitoring_token_set": bool(s.monitoring_token_hash), "syslog_status": s.syslog_status,
            "backup_status": s.backup_status, "backup_requested": s.backup_requested, "backup_root": str(backup.BACKUP_ROOT),
            "restore_test_status": s.restore_test_status, "restore_test_requested": s.restore_test_requested,
            "scim_seen_at": s.scim_seen_at.isoformat() if s.scim_seen_at else None,
            "scim_groups": [{"id": str(g.id), "name": g.display_name, "members": g.n}
                            for g in ScimGroup.objects.annotate(n=Count("members"))]}


def _settings_snapshot(s):
    return {**{a: getattr(s, a) for a in SETTINGS_ATTRS}, "ldap_bind_password_enc": s.ldap_bind_password_enc,
            "graph_client_secret_enc": s.graph_client_secret_enc, "smtp_password_enc": s.smtp_password_enc,
            "ad_bind_password_enc": s.ad_bind_password_enc, "oidc_client_secret_enc": s.oidc_client_secret_enc}


@needs(ADMIN)
def settings_get(request):
    return JsonResponse(ser_settings(Settings.get()))


@needs(ADMIN)
def settings_put(request):
    s, b = Settings.get(), request.json
    before = _settings_snapshot(s)
    errors = {}
    for a in SETTINGS_ATTRS:
        if a in b:
            setattr(s, a, b[a])
    if "ldap_bind_password" in b:
        s.ldap_bind_password = str(b["ldap_bind_password"] or "")
    if "graph_client_secret" in b:
        s.graph_client_secret = str(b["graph_client_secret"] or "")
    if "smtp_password" in b:
        s.smtp_password = str(b["smtp_password"] or "")
    if "ad_bind_password" in b:
        s.ad_bind_password = str(b["ad_bind_password"] or "")
    if "oidc_client_secret" in b:
        s.oidc_client_secret = str(b["oidc_client_secret"] or "")
    errors |= adsync.validate(s) | relaypush.validate(s)
    if s.review_mode not in ("off", "rolling", "campaign"):
        errors["review_mode"] = "invalid"
    for a, lo, hi in [("review_interval_months", 1, 120), ("review_campaign_month", 1, 12), ("review_campaign_day", 1, 31),
                      ("smtp_port", 1, 65535), ("review_remind_days", 0, 365)]:
        if not isinstance(getattr(s, a), int) or not lo <= getattr(s, a) <= hi:
            errors[a] = "invalid_number"
    s.proposal_notify, s.proposal_notify_to = bool(s.proposal_notify), str(s.proposal_notify_to or "").strip()[:500]
    try:
        for a in filter(None, map(str.strip, s.proposal_notify_to.split(","))):
            validate_email(a)
    except ValidationError:
        errors["proposal_notify_to"] = "invalid_email"
    if s.smtp_security not in ("starttls", "ssl", "none"):
        errors["smtp_security"] = "invalid"
    if s.mail_transport not in ("smtp", "graph"):
        errors["mail_transport"] = "invalid"
    if bad := reviews.check_templates(s.mail_templates):
        errors["mail_templates"] = bad
    if s.default_language not in ("de", "en"):
        errors["default_language"] = "invalid"
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(s.backup_time)):
        errors["backup_time"] = "invalid"
    if not isinstance(s.backup_keep_days, int) or not 1 <= s.backup_keep_days <= 365:
        errors["backup_keep_days"] = "invalid_number"
    if not isinstance(s.restore_test_days, int) or not 0 <= s.restore_test_days <= 365:
        errors["restore_test_days"] = "invalid_number"
    if not isinstance(s.ldap_users_max_deactivate_percent, int) or not 0 <= s.ldap_users_max_deactivate_percent <= 100:
        errors["ldap_users_max_deactivate_percent"] = "invalid_number"
    for a in ("trash_days", "export_interval_minutes", "audit_signin_days", "retention_months"):
        if not isinstance(getattr(s, a), int) or getattr(s, a) < 0:
            errors[a] = "invalid_number"
    if s.print_logo_mode not in ("kartei", "custom", "none"):
        errors["print_logo_mode"] = "invalid"
    elif s.print_logo_mode == "custom" and not s.print_logo_type:
        errors["print_logo_mode"] = "logo_missing"
    s.map_url = str(s.map_url or "").strip()
    if s.map_url and not (re.fullmatch(r"https?://\S+", s.map_url) and "{address}" in s.map_url):
        errors["map_url"] = "map_url_invalid"
    if not re.fullmatch(r"\+[1-9]\d{0,3}", str(s.phone_country_code)):
        errors["phone_country_code"] = "invalid"
    for a in ("tel_outside", "tel_plus"):
        setattr(s, a, str(getattr(s, a) or "").strip())
        if not re.fullmatch(r"[\d*#]{0,4}", getattr(s, a)):
            errors[a] = "invalid"
    if s.export_interval_minutes == 0:
        errors["export_interval_minutes"] = "too_small"
    if s.ldap_user_filter and "%(user)s" not in s.ldap_user_filter:
        errors["ldap_user_filter"] = "missing_user_placeholder"
    if s.totp_required not in ("none", "admins", "all"):
        errors["totp_required"] = "invalid"
    s.graph_folder, s.graph_folder_responsible, s.graph_folder_internal = (
        str(x or "").strip() for x in (s.graph_folder, s.graph_folder_responsible, s.graph_folder_internal))
    # not stripped: "Kartei: " keeps its space before the filter's name
    s.graph_filter_prefix = re.sub(r"\s", " ", CONTROL.sub("", str(s.graph_filter_prefix or "")))[:50].lstrip()
    if not s.graph_folder:
        errors["graph_folder"] = "required"
    seen = {s.graph_folder.casefold()}
    for k in ("graph_folder_responsible", "graph_folder_internal"):
        if (v := getattr(s, k).casefold()) and v in seen:
            errors[k] = "duplicate"
        seen.add(v)
    if s.graph_enabled and not (s.graph_tenant_id and s.graph_client_id and s.graph_group_id
                                and s.graph_client_secret_enc):
        errors["graph_enabled"] = "graph_incomplete"
    s.oidc_issuer = str(s.oidc_issuer or "").strip()
    if s.oidc_issuer and not re.fullmatch(r"https://[^\s?#]+", s.oidc_issuer):
        errors["oidc_issuer"] = "https_required"
    if s.oidc_claim not in oidc.CLAIMS:
        errors["oidc_claim"] = "invalid"
    if "openid" not in str(s.oidc_scopes).split():
        errors["oidc_scopes"] = "openid_required"
    if s.oidc_enabled and not (s.oidc_issuer and s.oidc_client_id and s.oidc_client_secret_enc and s.base_url):
        errors["oidc_enabled"] = "oidc_incomplete"  # provider and the public URL (for the redirect URI) are needed
    for a in ("monitoring_allowlist", "phonebook_allowlist"):
        if not isinstance(getattr(s, a), list):
            errors[a] = "invalid"
            continue
        setattr(s, a, [str(x).strip() for x in getattr(s, a) if str(x).strip()])
        if bad := monitoring.valid_allowlist(getattr(s, a)):
            errors[a] = "invalid_network:" + ", ".join(bad)[:200]
    s.phonebook_username = str(s.phonebook_username or "").strip()
    if not s.phonebook_username or ":" in s.phonebook_username:
        errors["phonebook_username"] = "invalid"
    s.syslog_host = str(s.syslog_host or "").strip()
    if s.syslog_protocol not in ("udp", "tcp", "tls"):
        errors["syslog_protocol"] = "invalid"
    if s.syslog_facility not in syslog.FACILITIES:
        errors["syslog_facility"] = "invalid"
    if s.syslog_content not in ("audit", "audit_and_app"):
        errors["syslog_content"] = "invalid"
    if not isinstance(s.syslog_port, int) or not 1 <= s.syslog_port <= 65535:
        errors["syslog_port"] = "invalid_number"
    if s.syslog_enabled and not s.syslog_host:
        errors["syslog_host"] = "required"
    groups = {str(i) for i in ScimGroup.objects.values_list("id", flat=True)}
    for a in ("scim_group_admin", "scim_group_maintainer", "scim_group_read"):
        setattr(s, a, str(getattr(s, a) or ""))
        if getattr(s, a) and getattr(s, a) not in groups:
            errors[a] = "invalid"
    if errors:
        return err("validation", fields=errors)
    s.save()
    syslog.reload(s)
    if change := audit.diff(before, _settings_snapshot(s)):
        audit.log(request.user, "update", "settings", 1, "settings", change)
    if any(a.startswith("scim_group_") for a in change or {}):
        from .. import scim
        scim.sync_all(s)  # roles and active of SCIM users follow the new mapping
    return JsonResponse(ser_settings(s))


@needs(ADMIN)
def scim_token(request):
    """New bearer token for the identity provider's SCIM client; only its SHA-256 is stored, the token is shown once."""
    s, token = Settings.get(), secrets.token_urlsafe(32)
    s.scim_token_hash = ApiToken.digest(token)
    s.save(update_fields=["scim_token_hash"])
    audit.log(request.user, "update", "settings", 1, "settings", {"scim_token_hash": ["***", "***"]})
    return JsonResponse({"token": token})


@needs(ADMIN)
def carddav_password(request):
    s = Settings.get()
    pw = secrets.token_urlsafe(18)
    s.carddav_password_hash = make_password(pw)
    s.save()
    audit.log(request.user, "update", "settings", 1, "settings", {"carddav_password_hash": ["***", "***"]})
    return JsonResponse({"password": pw})  # shown once


@needs(ADMIN)
def phonebook_password(request):
    """A new password for the desk-phone phonebook, shown once; the old one stops working."""
    pw = secrets.token_urlsafe(18)
    Settings.objects.filter(pk=1).update(phonebook_password_hash=make_password(pw))
    audit.log(request.user, "update", "settings", 1, "settings", {"phonebook_password_hash": ["***", "***"]})
    return JsonResponse({"password": pw})  # shown once


@needs(ADMIN)
def monitoring_token(request):
    """A new monitoring token, shown once; the old one stops working."""
    token, digest = monitoring.new_token()
    Settings.objects.filter(pk=1).update(monitoring_token_hash=digest)
    audit.log(request.user, "update", "settings", 1, "settings", {"monitoring_token_hash": ["***", "***"]})
    return JsonResponse({"token": token})


@needs(ADMIN)
def monitoring_status(request):
    """The monitoring status for the settings page (admin session instead of token and allowlist)."""
    return JsonResponse(monitoring.collect())


@needs(ADMIN)
def syslog_test(request):
    error = syslog.send_test(Settings.get())
    return JsonResponse({"error": msg(error) if error == "syslog_no_host" else error} if error else {"ok": True})


@needs(ADMIN)
def ldap_test(request):
    import ldap

    from ..auth import ldap_settings, role_of
    s, b = Settings.get(), request.json
    conf = ldap_settings(s)
    out = {"bind": False, "user": None}
    try:
        conn = ldap.initialize(conf.SERVER_URI)
        for k, v in conf.CONNECTION_OPTIONS.items():
            conn.set_option(k, v)
        if conf.START_TLS:
            conn.start_tls_s()
        conn.simple_bind_s(conf.BIND_DN, conf.BIND_PASSWORD)
        out["bind"] = True
        if b.get("username"):
            res = conf.USER_SEARCH.execute(conn, {"user": b["username"]})
            if res:
                dn, attrs = res[0]
                groups = attrs.get("memberOf", attrs.get("memberof", []))
                # same rule as sign-in and the user import, nested groups included
                out["user"] = {"dn": dn, "groups": groups, "role": role_of(conn, s, dn, groups)}
    except ldap.LDAPError as e:
        out["error"] = str(e.args[0].get("desc", e) if e.args and isinstance(e.args[0], dict) else e)
    return JsonResponse(out)


@needs(ADMIN)
def backup_run(request):
    """"Back up now" (also with the daily backup switched off) or "test restore": the exporter picks it up."""
    field = "restore_test_requested" if request.path.endswith("restore-test") else "backup_requested"
    Settings.objects.filter(pk=1).update(**{field: timezone.now()})
    audit.log(request.user, "sync", "settings", 1, "restore_test" if field == "restore_test_requested" else "backup")
    return JsonResponse(ser_settings(Settings.get()))


@needs(ADMIN)
def graph_sync(request):
    if not Settings.objects.filter(pk=1, graph_enabled=True).update(graph_sync_requested=timezone.now()):
        return err("graph_disabled", 409)
    audit.log(request.user, "sync", "settings", 1, "outlook")
    return JsonResponse(ser_settings(Settings.get()))


@needs(ADMIN)
def graph_test(request):
    return JsonResponse(graph.test(Settings.get()))


@needs(ADMIN)
def relay_token(request):
    """New push token for the CardDAV relay. Only its SHA-256 is shown: that is what the relay host needs."""
    s = Settings.get()
    s.carddav_relay_token = secrets.token_urlsafe(32)
    s.save()
    audit.log(request.user, "update", "settings", 1, "settings", {"carddav_relay_token": ["***", "***"]})
    return JsonResponse(ser_settings(s))


@needs(ADMIN)
def relay_test(request):
    return JsonResponse(relaypush.test(Settings.get()))


@needs(ADMIN)
def relay_sync(request):
    if not Settings.objects.filter(pk=1, carddav_relay_enabled=True).update(carddav_relay_requested=timezone.now()):
        return err("relay_disabled", 409)
    audit.log(request.user, "sync", "settings", 1, "carddav_relay")
    return JsonResponse(ser_settings(Settings.get()))


@needs(ADMIN)
def ldap_import_users(request):
    return JsonResponse(ldapusers.run(request.user))


@needs(ADMIN)
def ad_dry_run(request):
    return JsonResponse(adsync.dry_run(Settings.get()))


@needs(ADMIN)
def ad_sync(request):
    if not Settings.objects.filter(pk=1, ad_enabled=True).update(ad_sync_requested=timezone.now()):
        return err("ad_disabled", 409)
    audit.log(request.user, "sync", "settings", 1, "ad")
    return JsonResponse(ser_settings(Settings.get()))


@needs(ADMIN)
def put_print_logo(request):
    """Upload (PUT, the image as body) or remove (DELETE) the organisation's own logo for the phone list."""
    s = Settings.get()
    if request.method == "DELETE":
        Settings.objects.filter(pk=1).update(print_logo=None, print_logo_type="",
                                             print_logo_mode="kartei" if s.print_logo_mode == "custom" else s.print_logo_mode)
        audit.log(request.user, "update", "settings", 1, "settings", {"print_logo": ["…", None]})
        return JsonResponse(ser_settings(Settings.get()))
    data = request.body
    if len(data) > MAX_IMAGE:
        return err("image_too_large", 413)
    typ = _sniff(data)
    if not typ:
        return err("invalid_image")
    Settings.objects.filter(pk=1).update(print_logo=data, print_logo_type=typ, print_logo_mode="custom")
    audit.log(request.user, "update", "settings", 1, "settings", {"print_logo": [None, f"{typ}, {len(data)} B"]})
    return JsonResponse(ser_settings(Settings.get()))
