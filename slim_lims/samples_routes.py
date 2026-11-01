"""Staff pages and JSON API for the sample module (slim-samples). Staff only.

    /samples                          the bottles in the lab: ?show=active|due|held|finished
    /samples/containers/{id}          one bottle: its history and what can be done with it
    POST /samples/containers/{id}/{action}
                                      move | condition | correct | hold | release | dispose | return
    /samples/log/{tr}                 log the bottles of samples checked in without them (S-10)
    /samples/labels/{tr}              the request's bottle labels, printable
    /samples/rules                    the lab's lists and periods; a supervisor publishes a new version
    GET  /api/samples/due             ?within=days: bottles whose retention ends by then
    GET  /api/samples/{sample}        a sample's bottles and what the conditions touch
    POST /api/samples/{sample}/containers   another bottle arrived (needs a reason)
    POST /api/samples/containers/{id}/{action}
    PUT  /api/samples/rules           publish the lab's rules (supervisor)

Errors: the rules refuse -> 422 with every problem; not allowed -> 403; unknown -> 404.
"""

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field, ValidationError
from slim_samples.domain.containers import Container, ContainerIn, EventKind, flags, label_for
from slim_samples.domain.problems import Refused
from slim_samples.domain.rules import SamplesRules
from slim_samples.persistence._rows import NotAllowed, NotFound
from slim_samples.persistence.rules import publish_rules
from slim_samples.service import custody
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from slim_lims.api import get_reference
from slim_lims.auth import RequireStaff
from slim_lims.db import get_db
from slim_lims.labtime import LabClock, lab_clock
from slim_lims.reference import ReferenceData
from slim_lims.samples import (
    BOTTLE_ROWS,
    TrSampleLookup,
    bottle_field,
    date_received,
    parse_bottles,
    receive_bottles,
    rules,
    samples_actor_for,
    unlogged_positions,
)
from slim_lims.security import csrf_protect
from slim_lims.web import flash, render

router = APIRouter(dependencies=[Depends(csrf_protect)])
api_router = APIRouter(prefix="/api/samples", tags=["samples"], dependencies=[Depends(csrf_protect)])
DB = Annotated[Session, Depends(get_db)]
Reference = Annotated[ReferenceData, Depends(get_reference)]
Clock = Annotated[LabClock, Depends(lab_clock)]

SHOWS = {
    "active": "In the lab",
    "due": "Due for disposal",
    "held": "On hold",
    "finished": "Disposed or returned",
}
ACTIONS = ("move", "condition", "correct", "hold", "release", "dispose", "return")


class Failed(Exception):
    def __init__(self, status: int, problems: list[str]) -> None:
        self.status, self.problems = status, problems


def _guard(db: Session, action):
    """Runs a write; commits, or rolls back and raises Failed with what to tell the person."""
    try:
        body = action()
    except Refused as error:
        db.rollback()
        raise Failed(422, [f"{p.where}: {p.message}" for p in error.problems]) from None
    except NotAllowed as error:
        db.rollback()
        raise Failed(403, [str(error)]) from None
    except NotFound as error:
        db.rollback()
        raise Failed(404, [str(error)]) from None
    except (ValidationError, ValueError) as error:
        db.rollback()
        raise Failed(422, [str(error).splitlines()[0]]) from None
    except IntegrityError:  # two people logged the same bottles at once: the second loses
        db.rollback()
        raise Failed(409, ["someone else just recorded that; reload and look again"]) from None
    db.commit()
    return body


def container_json(c: Container) -> dict[str, Any]:
    return {
        "id": c.id,
        "sample": c.sample,
        "bottle": c.position,
        "type": c.current_type[0],
        "count": c.current_count,
        "analyses": sorted(c.current_analyses),
        "received_on": c.received_on.isoformat(),
        "keep_until": c.retention_due.isoformat(),
        "location": c.current_location,
        "conditions": [
            {"code": x.code, "label": x.label, "effect": x.effect.name.lower()} for x in c.current_conditions
        ],
        "held": c.held,
        "hold_reason": c.hold_reason,
        "outcome": None if c.outcome is None else c.outcome.name.lower(),
        "events": [
            {
                "kind": e.kind.name.lower(),
                "at": e.at.isoformat(),
                "by": e.by,
                "location": e.location,
                "method": e.method,
                "reason": e.reason,
            }
            for e in c.events
        ],
    }


# ---------------------------------------------------------------- the actions, shared by pages and API


def _flag(name: str, value: Any) -> bool:
    return bool(value) and str(value).lower() not in ("0", "false", "off", "no")


def _act(
    db: Session, user, container_id: int, action: str, values: dict[str, Any], reference, clock: LabClock
):
    """One action on one bottle. `values` is the posted form or the JSON body."""
    if action not in ACTIONS:
        raise NotFound(f"no action {action!r}")
    published = rules(db)
    actor = samples_actor_for(user)
    reason = str(values.get("reason") or values.get("note") or "")
    if action == "move":
        return custody.move(
            db, container_id, str(values.get("location") or ""), actor=actor, rules=published, reason=reason
        )
    if action == "condition":
        codes = values.get("conditions")
        if codes is None:  # the form: one checkbox per condition
            codes = [
                c.code for c in published.rules.conditions if _flag(c.code, values.get(f"cond_{c.code}"))
            ]
        return custody.note_conditions(
            db, container_id, frozenset(codes), actor=actor, rules=published, reason=reason
        )
    if action == "correct":
        count = values.get("count")
        analyses = values.get("analyses")
        return custody.correct(
            db,
            TrSampleLookup(db, reference),
            container_id,
            actor=actor,
            rules=published,
            reason=reason,
            container_type=str(values.get("container_type") or "") or None,
            count=int(count) if str(count or "").strip().isdigit() else None,
            analyses=None if analyses is None else frozenset(analyses),
        )
    if action == "hold":
        return custody.hold(db, container_id, actor=actor, reason=reason)
    if action == "release":
        return custody.release_hold(db, container_id, actor=actor, reason=reason)
    if action == "dispose":
        return custody.dispose(
            db,
            container_id,
            actor=actor,
            rules=published,
            method=str(values.get("method") or ""),
            today=clock.today(),
            reason=reason,
        )
    return custody.return_to_customer(db, container_id, actor=actor, note=reason)


# ---------------------------------------------------------------- pages


@router.get("/samples", name="samples_home")
def samples_home(request: Request, user: RequireStaff, db: DB, clock: Clock, show: str = "active"):
    show = show if show in SHOWS else "active"
    today = clock.today()
    everything = custody.all_containers(db) if show == "finished" else custody.active_containers(db)
    if show == "due":
        found = [c for c in everything if c.due(today)]
    elif show == "held":
        found = [c for c in everything if c.held]
    elif show == "finished":
        found = [c for c in everything if not c.active]
    else:
        found = everything
    found.sort(key=lambda c: (c.retention_due, c.sample, c.position))
    published = rules(db)
    return render(
        request,
        "samples/index.html",
        {
            "containers": found,
            "show": show,
            "shows": SHOWS,
            "today": today,
            "rules": published.rules,
            "counts": {
                "due": sum(1 for c in everything if c.due(today)),
                "overdue": sum(1 for c in everything if c.overdue(today)),
                "held": sum(1 for c in everything if c.held),
            },
        },
    )


@router.get("/samples/containers/{container_id}", name="samples_container")
def samples_container(request: Request, container_id: int, user: RequireStaff, db: DB, clock: Clock):
    try:
        container = custody.get_container(db, container_id)
    except NotFound:
        return render(request, "error.html", {"status": 404, "message": "No such bottle."}, 404)
    siblings = custody.containers_of(db, container.sample)
    return render(
        request,
        "samples/container.html",
        {
            "c": container,
            "label": label_for(container, len(siblings)),
            "siblings": siblings,
            "flags": flags(siblings),
            "rules": rules(db).rules,
            "today": clock.today(),
            "EventKind": EventKind,
        },
    )


async def _form(request: Request) -> dict[str, str]:
    return {k: v for k, v in (await request.form()).items() if isinstance(v, str)}


Form_ = Annotated[dict[str, str], Depends(_form)]


@router.post("/samples/containers/{container_id}/{action}", name="samples_action")
def samples_action(
    request: Request,
    container_id: int,
    action: str,
    user: RequireStaff,
    db: DB,
    reference: Reference,
    clock: Clock,
    form: Form_,
):
    try:
        done = _guard(db, lambda: _act(db, user, container_id, action, form, reference, clock))
    except Failed as failed:
        flash(request, "Not done: " + "; ".join(failed.problems), "error")
    else:
        flash(request, f"{done.sample} {done.title}: {action} recorded.", "success")
    return RedirectResponse(request.url_for("samples_container", container_id=container_id), status_code=303)


@router.get("/samples/log/{tr_number}", name="samples_log")
def samples_log(
    request: Request, tr_number: str, user: RequireStaff, db: DB, reference: Reference, clock: Clock
):
    lookup = TrSampleLookup(db, reference)
    samples = lookup.for_tr(tr_number)
    if not samples:
        return render(
            request, "error.html", {"status": 404, "message": f"Testing Request {tr_number} not found."}, 404
        )
    missing = unlogged_positions(db, lookup, tr_number)
    return render(
        request,
        "samples/log.html",
        {
            "tr_number": tr_number,
            "samples": [s for s in samples if int(s.sample.rsplit("-", 1)[1]) in missing],
            "rules": rules(db).rules,
            "rows": range(BOTTLE_ROWS),
            "today": clock.today(),
            "field": bottle_field,
        },
    )


@router.post("/samples/log/{tr_number}", name="samples_log_form")
def samples_log_form(
    request: Request,
    tr_number: str,
    user: RequireStaff,
    db: DB,
    reference: Reference,
    clock: Clock,
    form: Form_,
):
    lookup = TrSampleLookup(db, reference)
    published = rules(db)

    def run():
        missing = unlogged_positions(db, lookup, tr_number)
        by_position = parse_bottles(form, [s for s in lookup.for_tr(tr_number)], published)
        return receive_bottles(
            db,
            lookup,
            tr_number,
            by_position,
            user,
            date_received(db, tr_number, clock.today()),
            only=missing,
        )

    try:
        made = _guard(db, run)
    except Failed as failed:
        flash(request, "Not logged: " + "; ".join(failed.problems), "error")
        return RedirectResponse(request.url_for("samples_log", tr_number=tr_number), status_code=303)
    flash(request, f"{len(made)} bottle row(s) logged for {tr_number}.", "success")
    return RedirectResponse(request.url_for("request_detail", tr_number=tr_number), status_code=303)


@router.get("/samples/labels/{tr_number}", name="samples_labels")
def samples_labels(request: Request, tr_number: str, user: RequireStaff, db: DB, reference: Reference):
    infos = TrSampleLookup(db, reference).for_tr(tr_number)
    found = custody.containers_for(db, [i.sample for i in infos])
    labels = [label_for(c, len(found[i.sample])) for i in infos for c in found[i.sample]]
    if not labels:
        return render(
            request, "error.html", {"status": 404, "message": f"No bottles logged for {tr_number}."}, 404
        )
    return render(request, "samples/labels.html", {"tr_number": tr_number, "labels": labels})


@router.get("/samples/rules", name="samples_rules")
def samples_rules(request: Request, user: RequireStaff, db: DB):
    published = rules(db)
    return render(
        request,
        "samples/rules.html",
        {
            "rules": published.rules,
            "version": published.id,
            "body": json.dumps(published.rules.model_dump(mode="json"), indent=2),
            "may_publish": samples_actor_for(user).may_publish_rules,
        },
    )


def _publish(db: Session, user, body: dict[str, Any]):
    new = SamplesRules.model_validate(body)
    if new.name != rules(db).rules.name:
        raise ValueError(f"the lab's rules are named {rules(db).rules.name!r}; keep the name")
    return publish_rules(db, new, samples_actor_for(user))


@router.post("/samples/rules", name="samples_rules_form")
def samples_rules_form(
    request: Request, user: RequireStaff, db: DB, body: Annotated[str, Form(max_length=60000)] = ""
):
    try:
        published = _guard(db, lambda: _publish(db, user, json.loads(body)))
    except json.JSONDecodeError as error:
        flash(request, f"Not published: that isn't valid JSON ({error.msg}).", "error")
    except Failed as failed:
        flash(request, "Not published: " + "; ".join(failed.problems), "error")
    else:
        flash(
            request,
            f"The lab's rules are now version {published.id}. Bottles already received keep theirs.",
            "success",
        )
    return RedirectResponse(request.url_for("samples_rules"), status_code=303)


# ---------------------------------------------------------------- JSON API


def _fail(failed: Failed) -> JSONResponse:
    return JSONResponse({"detail": "Refused.", "problems": failed.problems}, status_code=failed.status)


@api_router.get("/due", summary="Bottles whose retention ends within `within` days (held ones left out)")
def api_due(user: RequireStaff, db: DB, clock: Clock, within: int = 0):
    return [container_json(c) for c in custody.due_for_disposal(db, clock.today(), max(0, min(within, 3650)))]


@api_router.get("/{sample}", summary="A sample's bottles, and what their conditions touch")
def api_sample(sample: str, user: RequireStaff, db: DB):
    found = custody.containers_of(db, sample)
    return {
        "sample": sample,
        "containers": [container_json(c) for c in found],
        "warnings": {a: [f.text for f in fl] for a, fl in flags(found).items()},
    }


class AddBottleIn(BaseModel):
    containers: list[ContainerIn] = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=500)


@api_router.post("/{sample}/containers", summary="Another bottle arrived for a received sample")
def api_add(sample: str, data: AddBottleIn, user: RequireStaff, db: DB, reference: Reference, clock: Clock):
    try:
        made = _guard(
            db,
            lambda: custody.add_container(
                db,
                TrSampleLookup(db, reference),
                sample,
                data.containers,
                actor=samples_actor_for(user),
                rules=rules(db),
                received_on=clock.today(),
                reason=data.reason,
            ),
        )
    except Failed as failed:
        return _fail(failed)
    return [container_json(c) for c in made]


@api_router.post(
    "/containers/{container_id}/{action}",
    summary="move | condition | correct | hold | release | dispose | return",
)
def api_action(
    container_id: int,
    action: str,
    body: dict[str, Any],
    user: RequireStaff,
    db: DB,
    reference: Reference,
    clock: Clock,
):
    try:
        done = _guard(db, lambda: _act(db, user, container_id, action, body, reference, clock))
    except Failed as failed:
        return _fail(failed)
    return container_json(done)


@api_router.put(
    "/rules", summary="Publish the lab's rules (a supervisor); bottles already received keep theirs"
)
def api_rules(body: dict[str, Any], user: RequireStaff, db: DB):
    try:
        published = _guard(db, lambda: _publish(db, user, body))
    except Failed as failed:
        return _fail(failed)
    return {"version": published.id, "rules": published.rules.model_dump(mode="json")}
