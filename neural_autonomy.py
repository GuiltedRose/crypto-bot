
"""Prospective qualification for frozen neural entry models.

Training may use eligible paper or followed-wallet observations.
Qualification uses later confirmed bot closes whose prediction
was captured before order submission.

The incumbent strategy remains active while candidates qualify.
"""
import copy
import hashlib
import json
import math
import random

MIN_TRAINING = 30
MIN_VALIDATION = 30
MIN_SELECTED = 20
MIN_REJECTED = 5
MIN_DAYS = 14
THRESHOLD = 2 / 3
MAX_AGE = 60 * 86400


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, allow_nan=False
        ).encode()
    ).hexdigest()


def qualification(candidate, trades, failures=()):
    """Measure incremental selection improvement using confirmed trades."""
    eligible = []
    seen = set()

    for row in trades:
        context = row.get("entry_context") or {}
        prediction = context.get("neural_selection") or {}
        score = prediction.get("score")
        pnl = row.get("net_pnl_usd")
        signature = row.get("exit_signature")

        if (
            row.get("source") != "bot_live"
            or prediction.get("candidate_id") != candidate["id"]
            or row.get("entry_at", 0) <= candidate["created_at"]
            or context.get("adaptive_selection", {}).get("status")
               in ("learning", "active_inference")
            or context.get("recorded_before_order") is not True
            or type(score) not in (int, float)
            or not math.isfinite(score)
            or not 0 <= score <= 1
            or type(pnl) not in (int, float)
            or not math.isfinite(pnl)
            or not signature
            or signature in seen
        ):
            continue

        seen.add(signature)
        eligible.append(row)

    eligible.sort(
        key=lambda row: (
            row["exit_at"], row["exit_signature"]
        )
    )

    selected = [
        row for row in eligible
        if row["entry_context"]["neural_selection"]["score"]
        >= THRESHOLD
    ]

    days = {}
    selected_signatures = {
        row["exit_signature"] for row in selected
    }
    for row in eligible:
        day = int(row["exit_at"] // 86400)
        improvement = (
            0.0 if row["exit_signature"] in selected_signatures
            else -row["net_pnl_usd"]
        )
        days[day] = days.get(day, 0.0) + improvement

    failed_cost = 0.0
    for failure in failures:
        at = failure.get("observed_at", 0)
        cost = failure.get("network_cost_usd")

        if at <= candidate["created_at"]:
            continue
        if (
            type(cost) not in (int, float)
            or not math.isfinite(cost)
            or cost < 0
        ):
            return {
                "status": "collecting",
                "reason": "Invalid failed-order costs",
            }

        failed_cost += cost
        day = int(at // 86400)
        days[day] = days.get(day, 0.0) - cost

    lower = None
    if len(days) >= MIN_DAYS:
        values = list(days.values())
        rng = random.Random(42)
        bootstrap = sorted(
            sum(
                rng.choice(values) for _ in values
            ) / len(values)
            for _ in range(1000)
        )
        lower = bootstrap[24]

    pnl = (
        sum(row["net_pnl_usd"] for row in selected)
        - failed_cost
    )
    ready = (
        len(eligible) >= MIN_VALIDATION
        and len(selected) >= MIN_SELECTED
        and len(eligible) - len(selected) >= MIN_REJECTED
        and len(days) >= MIN_DAYS
    )

    if ready:
        status = (
            "qualified"
            if pnl > 0 and lower is not None and lower > 0
            else "rejected"
        )
    else:
        status = "collecting"

    return {
        "status": status,
        "confirmed_validation_trades": len(eligible),
        "selected_trades": len(selected),
        "rejected_trades": len(eligible) - len(selected),
        "observed_days": len(days),
        "selected_net_pnl_usd": pnl,
        "failed_order_cost_usd": failed_cost,
        "daily_improvement_bootstrap_lower": lower,
    }


def advance(
    previous, model, trades, binding, now,
    can_activate=True, failures=()
):
    """Maintain an immutable candidate and its promotion lifecycle."""
    state = copy.deepcopy(previous or {})
    candidate = state.get("candidate")
    current_ids = set(model.get("training_ids", []))
    binding_hash = digest(binding)

    if candidate and (
        candidate["model"].get("version") != model.get("version")
        or candidate["binding_hash"] != binding_hash
        or not set(
            candidate["model"].get("training_ids", [])
        ).issubset(current_ids)
        or now - candidate["created_at"] > MAX_AGE
    ):
        state = {
            "status": "invalidated",
            "reason": "Training provenance, policy or model age changed",
        }
        candidate = None

    if candidate:
        if state.get("status") == "active":
            recent = sorted(
                [
                    row for row in trades
                    if row.get("source") == "bot_live"
                    and row.get("entry_at", 0)
                    > state["activated_at"]
                    and (
                        row.get("entry_context") or {}
                    ).get("neural_selection", {}).get("candidate_id")
                    == candidate["id"]
                ],
                key=lambda row: row["exit_at"],
            )
            pnls = [row["net_pnl_usd"] for row in recent]

            rollback = (
                len(pnls) >= 2
                and all(value < 0 for value in pnls[-2:])
            ) or (
                len(pnls) >= 5
                and sum(pnls[-5:]) <= 0
            )

            if rollback:
                state = {
                    "status": "rolled_back",
                    "reason": (
                        "Two consecutive losses or "
                        "nonpositive last five closes"
                    ),
                }
                candidate = None
            else:
                state["post_activation_closes"] = len(pnls)
                return state
        else:
            result = qualification(
                candidate, trades, failures
            )
            state["qualification"] = result

            if result["status"] == "qualified" and can_activate:
                state.update(
                    status="active",
                    activated_at=now,
                )
                return state

            if result["status"] == "rejected":
                state = {
                    "status": "rejected",
                    "last_qualification": result,
                }
                candidate = None
            else:
                return state

    if not candidate:
        if model.get("samples", 0) < MIN_TRAINING:
            state.update(
                status="waiting_for_training",
                training_samples=model.get("samples", 0),
            )
            return state

        candidate = {
            "id": digest({
                "model": model,
                "created_at": now,
                "binding": binding_hash,
            }),
            "created_at": now,
            "binding_hash": binding_hash,
            "model": copy.deepcopy(model),
        }
        state.update(
            status="collecting",
            candidate=candidate,
        )

    return state


def entry_decision(state, context):
    """Return incumbent neural decision without relaxing risk gates."""
    candidate = (state or {}).get("candidate")
    if not candidate:
        return {
            "status": "unqualified",
            "allow": True,
            "candidate_id": None,
        }

    from meme_learning import weighted_prediction

    prediction = weighted_prediction(
        candidate["model"], context
    )
    active = state.get("status") == "active"

    return {
        "candidate_id": candidate["id"],
        "score": prediction["score"],
        "threshold": THRESHOLD,
        "features": prediction["features"],
        "status": "active" if active else "shadow",
        "allow": (
            not active
            or prediction["score"] >= THRESHOLD
        ),
    }


def learning_decision(model, context):
    """Existing experimental learning mode; not a qualification claim."""
    if not model or model.get("samples", 0) < MIN_TRAINING:
        return {
            "allow": True,
            "status": "warming_up",
            "training_samples": (
                model or {}
            ).get("samples", 0),
        }

    from meme_learning import weighted_prediction

    prediction = weighted_prediction(model, context)
    return {
        "allow": prediction["score"] >= THRESHOLD,
        "score": prediction["score"],
        "threshold": THRESHOLD,
        "status": "learning",
        "qualified": False,
        "training_samples": model["samples"],
        "features": prediction["features"],
    }
