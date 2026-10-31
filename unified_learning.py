
"""Unified SVM, LSTM and retrieval candidate training and evaluation.

Offline training, frozen candidate artifacts, chronological testing,
prospective forecast capture, and evidence-backed qualification.

Does not authorize orders or alter the incumbent trading policy.
"""
import copy
import json
import math
import time

from neural_autonomy import MIN_TRAINING, qualification
from neural_network import initialize, fit_balanced, forward
from shadow_ml_models import (
    fit_svm, calibrate_svm, fit_lstm, predict, MAX_STEPS
)
from unified_observations import (
    canonical, digest, eligible, tensor, number
)


def grouped_split(records):
    """Chronological group-purged train/validation/test split."""
    positions = {}
    groups = {}
    for row in records:
        position = row["position_id"]
        group = row["group_id"]
        if position in positions and positions[position] != group:
            raise ValueError("Position correlation group changed")
        positions[position] = group
        groups.setdefault(group, []).append(row)

    ordered = sorted(
        groups.values(),
        key=lambda rows: (
            min(r["feature_at"] for r in rows),
            rows[0]["group_id"]
        )
    )
    first = int(len(ordered) * 0.6)
    second = int(len(ordered) * 0.8)

    def flatten(items):
        return [row for group in items for row in group]

    train = flatten(ordered[:first])

    def latest_known(rows):
        return max(
            (
                max(
                    row["known_at"],
                    row.get("label_known_at") or 0,
                    row.get("risk_label_at") or 0,
                )
                for row in rows
            ),
            default=0,
        )

    cutoff = latest_known(train)
    validation = [
        row for row in flatten(ordered[first:second])
        if row["feature_at"] > cutoff
    ]
    cutoff = max(cutoff, latest_known(validation))
    test = [
        row for row in flatten(ordered[second:])
        if row["feature_at"] > cutoff
    ]
    return train, validation, test


def windows(records):
    """Produce bounded, nonoverlapping chronological LSTM windows."""
    groups = {}
    for row in records:
        groups.setdefault(row["position_id"], []).append(row)

    result = []
    for position, items in groups.items():
        ordered = sorted(
            items, key=lambda row: (row["feature_at"], row["identity"])
        )
        labels = {
            r["net_pnl_usd"]
            for r in ordered if r.get("net_pnl_usd") is not None
        }
        if len(labels) > 1:
            raise ValueError("Conflicting completed position outcomes")

        for end in range(MAX_STEPS, len(ordered) + 1, MAX_STEPS):
            sequence = ordered[end - MAX_STEPS:end]
            timestamps = [r["feature_at"] for r in sequence]
            if any(
                a >= b for a, b in zip(timestamps, timestamps[1:])
            ):
                continue
            last = sequence[-1]
            if (
                last.get("net_pnl_usd") is None
                or last.get("net_of_costs_verified") is not True
                or not number(last.get("label_known_at"))
                or last["label_known_at"] <= last["feature_at"]
            ):
                continue
            result.append((
                [tensor(row) for row in sequence],
                int(last["net_pnl_usd"] > 0),
                last,
            ))
    return result


def metrics(predictions):
    """Position-aggregated classification and observed-outcome statistics."""
    grouped = {}
    for probability, label, row in predictions:
        if not number(probability) or not 0 <= probability <= 1:
            raise ValueError("Invalid prediction probability")
        grouped.setdefault(row["position_id"], []).append(
            (probability, label, row)
        )

    if not grouped:
        return {
            "positions": 0,
            "windows": 0,
            "brier": None,
            "accuracy": None,
            "estimated_selected_net_usd": None,
            "max_net_drawdown_usd": None,
        }

    aggregated = []
    for values in grouped.values():
        if len({value[1] for value in values}) != 1:
            raise ValueError("Conflicting targets for position")
        probability = sum(v[0] for v in values) / len(values)
        aggregated.append((probability, values[0][1], values[0][2]))

    ordered = sorted(
        aggregated,
        key=lambda value: value[2].get("label_known_at") or 0
    )
    cumulative, peak, max_drawdown = 0.0, 0.0, 0.0
    for probability, label, row in ordered:
        if probability >= 2 / 3 and number(row.get("net_pnl_usd")):
            cumulative += row["net_pnl_usd"]
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)

    return {
        "positions": len(aggregated),
        "windows": len(predictions),
        "brier": sum(
            (probability - label) ** 2
            for probability, label, _ in aggregated
        ) / len(aggregated),
        "accuracy": sum(
            int((probability >= 0.5) == bool(label))
            for probability, label, _ in aggregated
        ) / len(aggregated),
        "estimated_selected_net_usd": cumulative,
        "max_net_drawdown_usd": max_drawdown,
        "scope": (
            "Observed position outcomes only; not a simulated "
            "or executable counterfactual trading result"
        ),
    }


def build_candidate(store, family, now=None):
    """Build or reuse a frozen candidate from versioned eligible evidence."""
    if family not in ("svm", "lstm", "rag"):
        raise ValueError("Unknown model family")

    now = time.time() if now is None else now
    records = [
        row for row in store.observations()
        if eligible(row)
        and row["feature_at"] <= now
        and row["known_at"] <= now
        and max(
            row.get("label_known_at") or 0,
            row.get("risk_label_at") or 0,
        ) <= now
    ]

    fingerprint = digest([
        "unified-v2", family, [digest(row) for row in records]
    ])
    with store.connect(readonly=True) as db:
        old = db.execute(
            "SELECT body FROM models WHERE fingerprint=?",
            (fingerprint,)
        ).fetchone()
    if old:
        return json.loads(old[0])

    if not records:
        return {
            "status": "blocked",
            "reason": "No eligible verified learning observations",
        }

    schemas = {row["feature_schema"] for row in records}
    sizes = {len(tensor(row)) for row in records}
    if len(schemas) != 1 or len(sizes) != 1:
        raise ValueError("Mixed feature schemas or feature widths")

    train, validation, test = grouped_split(records)
    model = None
    report = {}

    if family == "svm":
        def valid_risk(rows):
            return [
                row for row in rows
                if row.get("risk_label_verified") is True
                and row.get("risk_label") in (0, 1)
                and number(row.get("risk_label_at"))
                and row["risk_label_at"] >= row["feature_at"]
            ]

        training = valid_risk(train)
        calibration = valid_risk(validation)
        testing = valid_risk(test)

        if (
            len({r["group_id"] for r in training}) < MIN_TRAINING
            or len({r["group_id"] for r in calibration}) < 10
            or len({r["risk_label"] for r in training}) < 2
            or len({r["risk_label"] for r in calibration}) < 2
        ):
            return {
                "status": "blocked",
                "reason": (
                    "Verified two-class risk labels and independent "
                    "calibration observations required"
                ),
            }

        model = fit_svm([
            (tensor(row), row["risk_label"]) for row in training
        ])
        model = calibrate_svm(model, [
            (tensor(row), row["risk_label"]) for row in calibration
        ])
        report = metrics([
            (predict(model, tensor(row)), row["risk_label"], row)
            for row in testing
        ])
        report.pop("estimated_selected_net_usd", None)
        report.pop("max_net_drawdown_usd", None)
        report["target"] = "verified_token_risk"

        rules = [
            row for row in testing
            if number(row.get("rule_risk_score"))
            and 0 <= row["rule_risk_score"] <= 1
        ]
        report["rule_baseline"] = metrics([
            (row["rule_risk_score"], row["risk_label"], row)
            for row in rules
        ])
        lineage = training + calibration

    elif family == "lstm":
        training = windows(train)
        testing = windows(test)

        if (
            len({row["group_id"] for _, _, row in training})
            < MIN_TRAINING
        ):
            return {
                "status": "blocked",
                "reason": (
                    "Insufficient independent verified "
                    "chronological LSTM windows"
                ),
            }

        model = fit_lstm([
            (sequence, label)
            for sequence, label, _ in training
        ])

        baseline = fit_balanced(
            initialize(len(training[0][0][-1])),
            [
                (sequence[-1], label)
                for sequence, label, _ in training
            ],
        )
        report = metrics([
            (predict(model, sequence), label, row)
            for sequence, label, row in testing
        ])
        report["feedforward_baseline"] = metrics([
            (forward(baseline, sequence[-1])[0], label, row)
            for sequence, label, row in testing
        ])
        lineage = train + validation

    else:
        training = [
            row for row in train
            if row.get("net_pnl_usd") is not None
            and row.get("net_of_costs_verified") is True
        ]
        if len({r["group_id"] for r in training}) < MIN_TRAINING:
            return {
                "status": "blocked",
                "reason": "Insufficient independent retrieval outcomes",
            }

        training = training[:512]
        model = {
            "family": "rag",
            "corpus_ids": [
                digest([row["source"], row["identity"]])
                for row in training
            ],
        }
        predictions = []
        for row in test:
            if (
                row.get("net_pnl_usd") is None
                or row.get("net_of_costs_verified") is not True
            ):
                continue
            result = store.retrieve(
                tensor(row),
                row["feature_at"],
                row["group_id"],
                allow_ids=set(model["corpus_ids"]),
                feature_schema_id=next(iter(schemas)),
            )
            if len(result["neighbors"]) >= 3:
                predictions.append((
                    result["score"],
                    int(row["net_pnl_usd"] > 0),
                    row,
                ))
        report = metrics(predictions)
        report["retrieval_scope"] = (
            "Frozen historical corpus, decision-time eligible labels"
        )
        lineage = train

    identifiers = [
        digest([row["source"], row["identity"]])
        for row in lineage
    ]
    fingerprints = {
        digest([row["source"], row["identity"]]): digest(row)
        for row in lineage
    }

    artifact = {
        "family": family,
        "version": "unified-v2",
        "model": model,
        "created_at": now,
        "training_ids": identifiers,
        "training_fingerprints": fingerprints,
        "feature_schema": next(iter(schemas)),
        "feature_size": next(iter(sizes)),
        "historical_evaluation": report,
        "status": "collecting",
        "live_eligible": False,
    }
    artifact["id"] = digest(artifact)
    with store.connect() as db:
        db.execute(
            "INSERT OR IGNORE INTO models VALUES(?,?,?)",
            (artifact["id"], fingerprint, canonical(artifact)),
        )
    return artifact


def load_candidate(store, identifier):
    with store.connect(readonly=True) as db:
        row = db.execute(
            "SELECT body FROM models WHERE id=?", (identifier,)
        ).fetchone()
    if not row:
        raise ValueError("Unknown candidate ID")
    candidate = json.loads(row[0])
    content = {k: v for k, v in candidate.items() if k != "id"}
    if digest(content) != identifier:
        raise ValueError("Frozen candidate integrity mismatch")
    return candidate


def lineage_valid(store, candidate):
    actual = {
        digest([row["source"], row["identity"]]): digest(row)
        for row in store.by_ids(candidate["training_ids"])
        if eligible(row)
    }
    return actual == candidate["training_fingerprints"]


def forecast(store, identifier, observations, position_id):
    """Persist a real, timestamped prediction before later evaluation."""
    candidate = load_candidate(store, identifier)
    now = time.time()
    if (
        not observations
        or len(observations) > MAX_STEPS
        or any(
            not eligible(row)
            or row["feature_at"] > now
            or row["known_at"] > now
            for row in observations
        )
    ):
        return {
            "score": None,
            "reason": "Verified inference observations required",
            "market_eligible": False,
        }
    if not lineage_valid(store, candidate):
        return {
            "score": None,
            "reason": "Frozen training evidence revoked or changed",
            "market_eligible": False,
        }

    ordered = sorted(observations, key=lambda r: r["feature_at"])
    if (
        any(
            a["feature_at"] >= b["feature_at"]
            for a, b in zip(ordered, ordered[1:])
        )
        or any(
            row["position_id"] != position_id
            or row["feature_schema"] != candidate["feature_schema"]
            for row in ordered
        )
        or len({row["mint"] for row in ordered}) != 1
    ):
        raise ValueError("Invalid inference chronology/schema/position")

    features = tensor(ordered[-1])
    family = candidate["family"]
    neighbors = []

    if family == "svm":
        # SVM is trained on risky=1, so return P(safe).
        score = 1.0 - predict(candidate["model"], features)
    elif family == "lstm":
        if len(ordered) != MAX_STEPS:
            return {
                "score": None,
                "reason": "Complete chronological LSTM window required",
                "market_eligible": False,
            }
        score = predict(
            candidate["model"], [tensor(row) for row in ordered]
        )
    elif family == "rag":
        result = store.retrieve(
            features,
            ordered[-1]["feature_at"],
            ordered[-1]["group_id"],
            allow_ids=set(candidate["model"]["corpus_ids"]),
            feature_schema_id=candidate["feature_schema"],
        )
        neighbors = result["neighbors"]
        score = result["score"] if len(neighbors) >= 3 else None
    else:
        raise ValueError("Unsupported candidate family")

    prediction = {
        "candidate_id": identifier,
        "score": score,
        "recorded_at": now,
        "position_id": position_id,
        "evidence_ids": [row["identity"] for row in ordered],
        "neighbors": neighbors,
        "market_eligible": False,
    }
    if score is not None:
        with store.connect() as db:
            existing = db.execute(
                "SELECT 1 FROM forecasts "
                "WHERE candidate_id=? AND position_id=?",
                (identifier, position_id)
            ).fetchone()
            if existing:
                raise ValueError(
                    "Forecast already frozen for this candidate/position"
                )
            db.execute(
                "INSERT INTO forecasts VALUES(?,?,?,?)",
                (identifier, position_id, now, canonical(prediction)),
            )
    return prediction


def evaluate_candidate(store, identifier, outcomes, failures=()):
    """Evaluate prospective confirmed results without changing live policy."""
    candidate = load_candidate(store, identifier)
    if not lineage_valid(store, candidate):
        return {
            "status": "blocked",
            "reason": "Frozen training evidence unavailable",
            "live_eligible": False,
        }

    with store.connect(readonly=True) as db:
        predictions = {
            position: json.loads(body)
            for position, body in db.execute(
                "SELECT position_id,body FROM forecasts "
                "WHERE candidate_id=?", (identifier,)
            )
        }

    from data_policy import require_learning_rights, SourceAccessBlocked

    verified = []
    for original in outcomes:
        if (
            original.get("source") != "bot_live"
            or original.get("provenance", {}).get("verified") is not True
            or original.get("rights", {}).get("evaluation_verified") is not True
            or original.get("net_of_costs_verified") is not True
        ):
            continue
        try:
            require_learning_rights(original)
        except SourceAccessBlocked:
            continue
        prediction = predictions.get(original.get("position_id"))
        context = original.get("entry_context") or {}
        if (
            not prediction
            or prediction.get("score") is None
            or context.get("recorded_before_order") is not True
            or prediction["recorded_at"] > original.get("entry_at", 0)
        ):
            continue

        row = copy.deepcopy(original)
        row.setdefault("entry_context", {})["neural_selection"] = {
            "candidate_id": identifier,
            "score": prediction["score"],
        }
        verified.append(row)

    # Existing qualification requires an envelope with an immutable ID.
    result = qualification(candidate, verified, failures)
    result.update({
        "live_eligible": False,
        "promotion_history_unchanged": True,
        "evaluation_scope": "offline_prospective_review",
    })

    snapshot = {
        "candidate_id": identifier,
        "qualification": result,
        "evaluated_at": time.time(),
    }
    key = digest([identifier, result])
    with store.connect() as db:
        db.execute(
            "INSERT OR IGNORE INTO evaluations VALUES(?,?,?)",
            (key, identifier, canonical(snapshot)),
        )
    return result
