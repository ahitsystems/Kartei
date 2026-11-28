"""Kerberos sign-in (SPNEGO, "Negotiate"): a browser on a domain computer signs in without a password.

Kartei only checks the ticket the browser sends against the service's keytab (uploaded under Single sign-on); it never
talks to the KDC, so the image needs no krb5.conf. The name in the ticket is the AD sAMAccountName, which the user import
and LDAP sign-in store as Profile.kerberos_name: only users Kartei already knows from the directory can sign in this way.
"""
import base64
import hashlib
import os
import tempfile
from pathlib import Path

from django.contrib.auth.models import User
from django.db.models import Q

KEYTAB_MAX = 64 * 1024


class SignInError(Exception):
    """The code is shown on the login page and kept in the audit log, with the ticket's name if known."""
    def __init__(self, code, principal=""):
        super().__init__(code)
        self.principal = principal


def configured(s):
    return bool(s.kerberos_enabled and s.kerberos_keytab_enc and s.ldap_uri)


def _dir():
    # a directory only this user can write, as for the LDAP CA (auth.ldap_settings); /tmp is tmpfs in compose
    d = Path(tempfile.gettempdir()) / f"kartei-{os.getuid()}"
    d.mkdir(mode=0o700, exist_ok=True)
    return d


def keytab_path(raw):
    """The keytab as a file for the GSS library, written once per content (a new upload works without a restart)."""
    path = _dir() / f"krb5-{hashlib.sha256(raw).hexdigest()[:16]}.keytab"
    if not path.exists():
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_bytes(raw)
        tmp.chmod(0o600)
        os.replace(tmp, path)
    return path


def decode_keytab(b64):
    """bytes of an uploaded keytab, or None if it isn't one (MIT keytab format version 2)."""
    try:
        raw = base64.b64decode(b64, validate=True)
    except ValueError:
        return None
    return raw if 2 < len(raw) <= KEYTAB_MAX and raw[:2] == b"\x05\x02" else None


def check(raw):
    """True if the GSS library can accept with this keytab."""
    import gssapi
    try:
        gssapi.Credentials(usage="accept", store={"keytab": str(keytab_path(raw))})
        return True
    except gssapi.exceptions.GSSError:
        return False


def _accept(raw, token):
    """-> (client principal "name@REALM", the service's realm, token for the browser or None). Raises SignInError."""
    import gssapi
    # the replay cache in our own directory: /var/tmp (the default) is read-only in the container; one file for all workers
    store = {"keytab": str(keytab_path(raw)), "rcache": f"file2:{_dir() / 'krb5.rcache'}"}
    try:  # a replayed ticket only shows when the context is asked whether it is complete
        ctx = gssapi.SecurityContext(creds=gssapi.Credentials(usage="accept", store=store), usage="accept")
        out = ctx.step(token)
        if not ctx.complete:  # Kerberos needs one round trip; anything else is not for us
            raise SignInError("kerberos_failed")
        return str(ctx.initiator_name), str(ctx.target_name).rpartition("@")[2], out
    except gssapi.exceptions.GSSError:
        raise SignInError("kerberos_failed") from None


def authenticate(s, header):
    """The Kartei user for an "Authorization: Negotiate <token>" header -> (user, principal, token for the browser)."""
    try:
        token = base64.b64decode(header.split(" ", 1)[1].strip(), validate=True)
    except (IndexError, ValueError):
        raise SignInError("kerberos_failed") from None
    if token.startswith(b"NTLMSSP\0"):  # not on the domain or the name isn't the SPN's: Windows falls back to NTLM
        raise SignInError("kerberos_ntlm")
    principal, realm, out = _accept(base64.b64decode(s.kerberos_keytab), token)
    name, _, user_realm = principal.rpartition("@")
    if not name or user_realm.upper() != realm.upper():  # e.g. a trusted forest: its "alice" is not ours
        raise SignInError("kerberos_realm", principal)
    name = name.lower()
    eligible = User.objects.filter(is_active=True, is_superuser=False, profile__source="ldap")
    # users signed in or imported before Kartei kept the name: their username is it, with the default sign-in filter
    users = list(eligible.filter(Q(profile__kerberos_name=name) | Q(profile__kerberos_name="", username__iexact=name))[:2])
    if len(users) != 1:
        raise SignInError("sso_unknown_user" if not users else "sso_ambiguous", principal)
    return users[0], principal, out
