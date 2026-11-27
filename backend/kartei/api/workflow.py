"""Reviews and proposals."""

from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone

from .. import audit, reviews
from ..models import FieldDef, Kind, Proposal, Record, Review, Settings
from ..msg import msg
from .core import ADMIN, MAINT, READ, err, needs, records_qs, ser_record, snapshot, visible_fields
from .records import _add_new_options, _apply, _soft_delete

# ---------------------------------------------------------------- reviews & proposals

def ser_proposal(p, rank=ADMIN):
    """rank: whose eyes. A proposal by an admin can hold admin-only fields; others see only fields visible to them."""
    def visible(part):
        if rank >= ADMIN or not isinstance(part, dict) or not isinstance(part.get("data"), dict):
            return part
        keys = {f.key for f in visible_fields(rank)}
        return {**part, "data": {k: v for k, v in part["data"].items() if k in keys}}
    return {"id": p.id, "record": str(p.record.uid) if p.record_id else None,
            "record_kind": p.record.kind if p.record_id else p.record_kind, "label": p.label,
            "kind": p.kind, "changes": visible(p.changes), "base": visible(p.base),
            "comment": p.comment, "reason": p.reason, "proposer": p.proposer_name,
            "created_at": p.created_at.isoformat(), "status": p.status}


def workflow(request):
    """Part of bootstrap/changes: my open reviews; pending proposals (maintainers: all, others: their own)."""
    mine = reviews.open_reviews().filter(assignee=request.user, record__deleted_at__isnull=True).select_related("record")
    props = Proposal.objects.filter(Q(record__isnull=True) | Q(record__deleted_at__isnull=True), status="pending") \
        .select_related("record")
    if request.rank < MAINT:
        props = props.filter(proposer=request.user)
    return {"reviews": [{"id": rv.id, "record": str(rv.record.uid), "created_at": rv.created_at.isoformat(),
                         "reason": rv.reason} for rv in mine],
            "proposals": [ser_proposal(p, request.rank) for p in props.order_by("created_at")]}


def dashboard_prefs(request):
    from ..dashboard import prefs  # dashboard builds on this module
    return prefs(request)


def _core(r):
    return {"first_name": r.first_name, "last_name": r.last_name, "name": r.name,
            "company": str(r.company.uid) if r.company_id and r.company else None}


@needs(READ)
def propose(request, uid):
    """Anyone may propose; changes are validated as if a maintainer made them, but only on fields they can see."""
    b = request.json
    r = records_qs().filter(uid=uid, deleted_at__isnull=True).first()
    if not r:
        return err("not_found", 404)
    p = Proposal(record=r, kind=b.get("kind"), proposer=request.user, proposer_name=request.user.username)
    logged, error = _fill_proposal(request, r, p, b)
    if error:
        return error
    target = p.record  # "company gone" is proposed on the company, also when started from a person
    with transaction.atomic():
        if p.reason == "company_gone":
            same = Proposal.objects.filter(record=target, kind="delete", reason="company_gone", status="pending").first()
            if same:  # someone already reported it
                reviews.finish(_company_reviews(target, request.user), "proposed", request.user)
                return JsonResponse({"proposal": ser_proposal(same, request.rank), "existing": True})
        p.save()
        audit.log(request.user, "propose", "record", target.uid, target.display_name, logged)
        reviews.finish(r.reviews.filter(assignee=request.user), "proposed", request.user)
        if p.reason == "company_gone":
            reviews.finish(_company_reviews(target, request.user), "proposed", request.user)
    return JsonResponse({"proposal": ser_proposal(p, request.rank)}, status=201)


@needs(READ)
def propose_new(request):
    """A new person or company for a maintainer to create. Body like POST /api/records, plus `comment`."""
    b = request.json
    if b.get("kind") not in Kind.values:
        return err("invalid_kind")
    p = Proposal(kind="create", record_kind=b["kind"], proposer=request.user, proposer_name=request.user.username)
    logged, error = _fill_proposal(request, None, p, b)
    if error:
        return error
    p.save()
    audit.log(request.user, "propose", "proposal", p.id, p.label, logged)
    return JsonResponse({"proposal": ser_proposal(p, request.rank)}, status=201)


def _company_reviews(company, user):
    return Review.objects.filter(Q(record=company) | Q(record__company=company), assignee=user)


DELETE_REASONS = {"person": ("left", "company_gone", "other"), "company": ("company_gone", "other")}


def _fill_proposal(request, r, p, b):
    """Set comment, changes and base of `p` from a form body. Returns (audit changes, error response or None)."""
    p.comment = str(b.get("comment") or "").strip()[:5000]
    if p.kind == "change":
        fields = [f for f in FieldDef.objects.all() if f.visible(request.rank)]
        probe = Record(kind=r.kind, first_name=r.first_name, last_name=r.last_name, name=r.name,
                       company_id=r.company_id, data=dict(r.data))
        if errors := _apply(max(request.rank, MAINT), probe, b, fields):
            return None, err("validation", fields=errors)
        old_core, new_core = _core(r), _core(probe)
        core = [k for k in old_core if old_core[k] != new_core[k]]
        data = [k for k in dict.fromkeys([*r.data, *probe.data]) if r.data.get(k) != probe.data.get(k)]
        if not core and not data:
            return None, err("no_changes")
        p.changes = {"core": {k: new_core[k] for k in core}, "data": {k: probe.data.get(k) for k in data}}
        p.base = {"core": {k: old_core[k] for k in core}, "data": {k: r.data.get(k) for k in data}}
        logged = {k: [p.base[s][k], p.changes[s][k]] for s in ("core", "data") for k in p.changes[s]}
        if "company" in logged:  # history shows names, like record snapshots do
            logged["company"] = [Record.objects.filter(uid=v).values_list("name", flat=True).first() if v else None
                                 for v in logged["company"]]
    elif p.kind == "create":  # a new contact: validated like a maintainer creating it, on the fields the proposer sees
        fields = [f for f in FieldDef.objects.all() if f.visible(request.rank)]
        probe = Record(kind=p.record_kind)
        if errors := _apply(max(request.rank, MAINT), probe, b, fields):
            return None, err("validation", fields=errors)
        core = {k: v for k, v in _core(probe).items() if v}
        p.changes, p.base = {"core": core, "data": probe.data}, {}
        logged = {"kind": [None, p.record_kind]} | {k: [None, v] for k, v in (core | probe.data).items()}
        if "company" in logged:
            logged["company"] = [None, probe.company.name]
    elif p.kind == "delete":
        p.reason = str(b.get("reason") or "other")
        if p.reason not in DELETE_REASONS[r.kind]:
            return None, err("validation", fields={"reason": "invalid"})
        if p.reason == "other" and not p.comment:
            return None, err("validation", fields={"comment": "required"})
        if p.reason == "company_gone" and r.kind == Kind.PERSON:
            if not (r.company_id and r.company and not r.company.deleted_at):
                return None, err("validation", fields={"reason": "invalid"})
            p.record = r.company
        logged = {"delete": [None, True], "reason": [None, p.reason]}
    else:
        return None, err("invalid_kind")
    if p.comment:
        logged["comment"] = [None, p.comment]
    return logged, None


@needs(READ)
def own_proposal(request, pk):
    """The proposer edits (PATCH, same body as proposing) or withdraws (DELETE) a pending proposal."""
    with transaction.atomic():
        p = Proposal.objects.select_for_update().filter(pk=pk, status="pending", proposer=request.user).first()
        if not p:
            return err("not_found", 404)
        r = records_qs().filter(pk=p.record_id, deleted_at__isnull=True).first() if p.record_id else None
        if p.record_id and not r:  # in the recycle bin: like the contact itself, not for the proposer
            return err("not_found", 404)
        target = ("record", r.uid) if r else ("proposal", p.id)
        if request.method == "DELETE":
            p.status, p.decided_at = "withdrawn", timezone.now()
            p.save()
            audit.log(request.user, "withdraw", *target, p.label, {"kind": [None, p.kind]})
            return JsonResponse({"ok": True})
        logged, error = _fill_proposal(request, r, p, request.json)
        if error:
            return error
        p.save()
        audit.log(request.user, "propose", *target, p.label, logged)
    return JsonResponse({"proposal": ser_proposal(p, request.rank)})


@needs(MAINT)
def decide_proposal(request, pk, action):
    note = str(request.json.get("note") or "").strip()[:5000]
    with transaction.atomic():
        p = Proposal.objects.select_for_update().filter(pk=pk, status="pending").first()
        if not p:
            return err("not_found", 404)
        extra = {"proposed_by": [None, p.proposer_name]} | ({"note": [None, note]} if note else {})
        if p.kind == "create":
            r = None
            if action == "accept":
                body = {**p.changes.get("core", {}), "data": p.changes.get("data", {})}
                if isinstance(request.json.get("override"), dict):  # the reviewer adjusted the proposal
                    body = request.json["override"]
                    extra["adjusted"] = [None, True]
                fields = list(FieldDef.objects.all())
                r = Record(kind=p.record_kind, updated_by=request.user)
                if errors := _apply(request.rank, r, body, fields):
                    return err("validation", fields=errors)
                r.save()
                audit.log(request.user, "create", "record", r.uid, r.display_name, audit.diff({}, snapshot(r)) | extra)
                _add_new_options(request.user, fields, r)
                p.record = r
            else:
                audit.log(request.user, "reject", "proposal", p.id, p.label, extra)
            p.status = "accepted" if action == "accept" else "rejected"
            p.decided_by, p.decided_at, p.decision_note = request.user, timezone.now(), note
            p.save()
            return JsonResponse({"proposal": ser_proposal(p, request.rank),
                                 "record": ser_record(records_qs().get(pk=r.pk), visible_fields(request.rank)) if r else None})
        # of=self: Postgres can't lock the nullable side of the company join
        r = Record.objects.select_for_update(of=("self",)).select_related("company").get(pk=p.record_id)
        if action == "accept":
            if r.deleted_at:
                return err("not_found", 404)
            if p.kind == "delete":
                extra["reason"] = [None, p.reason]
                if p.reason == "company_gone":  # the company and everyone working there
                    for person in Record.objects.select_for_update().filter(company=r, deleted_at__isnull=True):
                        _soft_delete(request.user, person, extra)
                _soft_delete(request.user, r, extra)
            else:
                fields = list(FieldDef.objects.all())
                before = snapshot(r)
                body = {**p.changes.get("core", {}), "data": p.changes.get("data", {})}
                if isinstance(request.json.get("override"), dict):  # the reviewer adjusted the proposal
                    body = request.json["override"]
                    extra["adjusted"] = [None, True]
                if errors := _apply(request.rank, r, body, fields):
                    return err("validation", fields=errors)
                if change := audit.diff(before, snapshot(r)):
                    r.version += 1
                    r.updated_by = request.user
                    r.save()
                    audit.log(request.user, "update", "record", r.uid, r.display_name, change | extra)
                    _add_new_options(request.user, fields, r)
                    reviews.reassign(r)
        else:
            audit.log(request.user, "reject", "record", r.uid, r.display_name, extra)
        p.status = "accepted" if action == "accept" else "rejected"
        p.decided_by, p.decided_at, p.decision_note = request.user, timezone.now(), note
        p.save()
    return JsonResponse({"proposal": ser_proposal(p, request.rank), "record": ser_record(records_qs().get(pk=r.pk),
                                                                           visible_fields(request.rank))})


@needs(READ)
def review_confirm(request, uid):
    r = records_qs().filter(uid=uid, deleted_at__isnull=True).first()
    if not r:
        return err("not_found", 404)
    if request.rank < MAINT and not r.reviews.filter(assignee=request.user, done_at__isnull=True).exists():
        return err("forbidden", 403)
    reviews.confirm(r, request.user)
    return JsonResponse(ser_record(records_qs().get(pk=r.pk), visible_fields(request.rank)))


@needs(ADMIN)
def review_request(request, uid):
    r = Record.objects.filter(uid=uid, deleted_at__isnull=True).first()
    if not r:
        return err("not_found", 404)
    return JsonResponse(reviews.start([r], "manual", request.user))


@needs(ADMIN)
def reviews_admin(request):
    if request.method == "POST":
        scope = request.json.get("scope")
        if scope == "due":
            recs = reviews.due(max(Settings.get().review_interval_months, 1))
        elif scope == "all":
            recs = reviews.live()
        else:
            return err("invalid")
        return JsonResponse(reviews.start(recs, "manual", request.user))
    return JsonResponse(reviews.stats())


@needs(ADMIN)
def mail_test(request):
    from django.core.mail import EmailMessage
    s = Settings.get()
    to = str(request.json.get("to") or request.user.email or "").strip()
    if not reviews.mail_ready(s):
        return JsonResponse({"error": msg("mail_not_configured")})
    if not to:
        return JsonResponse({"error": msg("mail_no_recipient")})
    try:
        with reviews.mail_connection(s) as conn:
            EmailMessage("Kartei test", "This is a test message from Kartei.", reviews.mail_from(s), [to],
                         connection=conn).send()
    except Exception as e:  # report anything the mail server or Microsoft 365 says
        return JsonResponse({"error": str(e) or e.__class__.__name__})
    return JsonResponse({"ok": True, "to": to})
