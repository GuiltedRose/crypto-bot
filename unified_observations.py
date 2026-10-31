
"""Versioned, append-only observation cache and historical retrieval.

This is not a trading ledger. Original bot and wallet ledgers remain authoritative.
No source approval is inferred. Missing labels are never fabricated.
"""
from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time

from data_policy import require_learning_rights, SourceAccessBlocked

APP_ID = 0x554D4C31
MAX_STORE_BYTES = 512_000_000


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def feature_schema():
    from meme_learning import MODEL_FEATURES, CONTEXT_SCALES
    return digest([list(MODEL_FEATURES), CONTEXT_SCALES])


def validate(row):
    for key in (
        "identity", "position_id", "group_id", "source",
        "mint", "feature_schema"
    ):
        value = row.get(key)
        if not isinstance(value, str) or not 0 < len(value) <= 256:
            raise ValueError("Missing or invalid " + key)

    for key in ("feature_at", "known_at"):
        if not number(row.get(key)) or row[key] < 0:
            raise ValueError("Invalid timestamp: " + key)

    values = row.get("features")
    if (
        not isinstance(values, list)
        or not 1 <= len(values) <= 64
        or any(
            x is not None and (not number(x) or abs(x) > 8)
            for x in values
        )
    ):
        raise ValueError("Invalid feature vector")

    rights = row.get("rights") or {}
    if rights.get("storage_verified") is not True or not rights.get("evidence"):
        raise ValueError("Explicit storage rights required")

    if row["source"] == "followed_wallet":
        proof = row.get("provenance") or {}
        group = (
            proof.get("wallet_cluster_id")
            if proof.get("wallet_cluster_verified") is True
            else "unverified_wallet_cluster"
        )
        if row["group_id"] != group:
            raise ValueError("Wallet correlation group mismatch")

    for key in (
        "net_pnl_usd", "fees_usd", "risk_label_at", "label_known_at"
    ):
        if row.get(key) is not None and not number(row[key]):
            raise ValueError("Invalid " + key)

    risk_label = row.get("risk_label")
    if risk_label is not None and (
        type(risk_label) is not int or risk_label not in (0, 1)
    ):
        raise ValueError("Risk label must be 0 or 1")

    if len(canonical(row).encode()) > 65536:
        raise ValueError("Observation exceeds byte limit")
    return row


def eligible(row):
    rights = row.get("rights") or {}
    proof = row.get("provenance") or {}
    if (
        rights.get("ml_verified") is not True
        or rights.get("evaluation_verified") is not True
        or proof.get("verified") is not True
        or not proof.get("evidence")
        or row.get("training_eligible") is not True
        or row.get("synthetic") is True
        or row["known_at"] > row["feature_at"]
    ):
        return False
    try:
        require_learning_rights(row)
    except SourceAccessBlocked:
        return False
    return True


def tensor(row):
    result = []
    for value in row["features"]:
        result.extend([
            0.0 if value is None else value / 8.0,
            1.0 if value is None else 0.0,
        ])
    return result


class ObservationStore:
    def __init__(self, path, create=False):
        self.path = Path(path)
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("xb"):
                pass
            self.path.chmod(0o600)
            with self.connect(check=False) as db:
                db.execute("PRAGMA application_id=" + str(APP_ID))
                db.executescript("""
                    CREATE TABLE observations (
                        id TEXT PRIMARY KEY,
                        position_id TEXT,
                        group_id TEXT,
                        feature_at REAL,
                        known_at REAL,
                        label_known_at REAL,
                        body TEXT NOT NULL
                    );
                    CREATE INDEX historical_neighbors
                    ON observations(feature_at,known_at,label_known_at);
                    CREATE TABLE models (
                        id TEXT PRIMARY KEY,
                        fingerprint TEXT UNIQUE,
                        body TEXT NOT NULL
                    );
                    CREATE TABLE revocations (
                        id TEXT PRIMARY KEY,
                        reason TEXT,
                        recorded REAL
                    );
                    CREATE TABLE evaluations (
                        id TEXT PRIMARY KEY,
                        candidate_id TEXT,
                        body TEXT
                    );
                    CREATE TABLE forecasts (
                        candidate_id TEXT,
                        position_id TEXT,
                        recorded REAL,
                        body TEXT,
                        PRIMARY KEY(candidate_id,position_id)
                    );
                """)
        elif not self.path.is_file():
            raise ValueError("Existing cache not found; use --initialize")
        with self.connect(readonly=True):
            pass

    @contextmanager
    def connect(self, readonly=False, check=True):
        if not readonly and check:
            files = [
                self.path,
                Path(str(self.path) + "-journal"),
                Path(str(self.path) + "-wal"),
            ]
            used = sum(p.stat().st_size for p in files if p.exists())
            if used + 1_000_000 > MAX_STORE_BYTES:
                raise ValueError("Cache storage cap reached")
        uri = self.path.resolve().as_uri()
        uri += "?mode=" + ("ro" if readonly else "rw")
        db = sqlite3.connect(uri, uri=True, timeout=10)
        try:
            if check and db.execute(
                "PRAGMA application_id"
            ).fetchone()[0] != APP_ID:
                raise ValueError("Not a unified ML database")
            if readonly:
                db.execute("PRAGMA query_only=ON")
            else:
                db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        finally:
            db.close()

    def add(self, row):
        row = validate(row)
        key = digest([row["source"], row["identity"]])
        body = canonical(row)
        with self.connect() as db:
            existing = db.execute(
                "SELECT body FROM observations WHERE id=?", (key,)
            ).fetchone()
            if existing and existing[0] != body:
                raise ValueError("Conflicting evidence identity")
            if db.execute(
                "SELECT 1 FROM revocations WHERE id=?", (key,)
            ).fetchone():
                raise ValueError("Revoked observation identity")
            db.execute(
                "INSERT OR IGNORE INTO observations VALUES(?,?,?,?,?,?,?)",
                (
                    key, row["position_id"], row["group_id"],
                    row["feature_at"], row["known_at"],
                    row.get("label_known_at"), body
                )
            )
        return key

    def revoke(self, source, identity, reason):
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("Explicit revocation reason required")
        key = digest([source, identity])
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO revocations VALUES(?,?,?)",
                (key, reason, time.time())
            )

    def observations(self, limit=1024):
        if type(limit) is not int or not 1 <= limit <= 1024:
            raise ValueError("Invalid observation limit")
        recent_count = max(1, limit // 2)
        with self.connect(readonly=True) as db:
            recent = db.execute("""
                SELECT id,body FROM observations
                WHERE id NOT IN (SELECT id FROM revocations)
                ORDER BY feature_at DESC,id LIMIT ?
            """, (recent_count,)).fetchall()
            historical = db.execute("""
                SELECT id,body FROM observations
                WHERE id NOT IN (SELECT id FROM revocations)
                ORDER BY id LIMIT ?
            """, (limit - recent_count,)).fetchall()
        chosen = {
            key: json.loads(body)
            for key, body in recent + historical
        }
        return sorted(
            chosen.values(), key=lambda x: (x["feature_at"], x["identity"])
        )

    def by_ids(self, identifiers):
        identifiers = sorted(set(identifiers))
        if len(identifiers) > 1024:
            raise ValueError("Lineage read limit exceeded")
        result = []
        with self.connect(readonly=True) as db:
            for offset in range(0, len(identifiers), 128):
                batch = identifiers[offset:offset + 128]
                if not batch:
                    continue
                placeholders = ",".join("?" for _ in batch)
                sql = (
                    "SELECT body FROM observations "
                    "WHERE id IN (" + placeholders + ") "
                    "AND id NOT IN (SELECT id FROM revocations)"
                )
                result.extend(
                    json.loads(r[0]) for r in db.execute(sql, batch)
                )
        return result

    def retrieve(
        self, features, asof, exclude_group, k=8,
        allow_ids=None, feature_schema_id=None
    ):
        from shadow_ml_models import vector
        vector(features)
        if (
            type(k) is not int or not 1 <= k <= 8
            or not number(asof) or asof < 0
        ):
            raise ValueError("Invalid retrieval arguments")

        args = [asof, asof, asof, exclude_group]
        clause = ""
        if allow_ids is not None:
            if len(allow_ids) > 512:
                raise ValueError("Corpus too large")
            if not allow_ids:
                return {
                    "neighbors": [], "score": None,
                    "market_eligible": False
                }
            ids = sorted(allow_ids)
            clause = " AND id IN (" + ",".join("?" for _ in ids) + ")"
            args += ids

        sql = """
            SELECT body FROM observations
            WHERE feature_at < ?
              AND known_at <= ?
              AND label_known_at <= ?
              AND group_id != ?
              AND id NOT IN (SELECT id FROM revocations)
        """ + clause + " ORDER BY feature_at DESC,id LIMIT 512"

        with self.connect(readonly=True) as db:
            candidates = [
                json.loads(r[0]) for r in db.execute(sql, args)
            ]

        neighbors = []
        for row in candidates:
            if (
                feature_schema_id is not None
                and row["feature_schema"] != feature_schema_id
            ):
                continue
            if (
                not eligible(row)
                or row.get("net_pnl_usd") is None
                or row.get("net_of_costs_verified") is not True
            ):
                continue
            values = tensor(row)
            if len(values) != len(features):
                continue
            distance = sum(
                (a - b) ** 2 for a, b in zip(features, values)
            ) / len(values)
            neighbors.append({
                "identity": row["identity"],
                "position_id": row["position_id"],
                "group_id": row["group_id"],
                "distance": distance,
                "net_pnl_usd": row["net_pnl_usd"],
                "source": row["source"],
                "provenance": row["provenance"],
            })

        selected, seen_groups = [], set()
        for row in sorted(
            neighbors, key=lambda x: (x["distance"], x["identity"])
        ):
            if row["group_id"] in seen_groups:
                continue
            seen_groups.add(row["group_id"])
            selected.append(row)
            if len(selected) >= k:
                break

        return {
            "neighbors": selected,
            "score": (
                sum(r["net_pnl_usd"] > 0 for r in selected) / len(selected)
                if selected else None
            ),
            "independent_human_verified": False,
            "market_eligible": False,
        }


def import_bot_ledger(
    store, path, source, rights, provenance, offset=0, limit=128
):
    from meme_learning import (
        paper_outcomes, live_outcomes, model_features, _paper_correction
    )
    if source not in ("bot_paper", "bot_live"):
        raise ValueError("Invalid bot ledger source")
    if (
        type(offset) is not int or offset < 0
        or type(limit) is not int or not 1 <= limit <= 128
    ):
        raise ValueError("Invalid import page")

    outcomes = (
        paper_outcomes if source == "bot_paper" else live_outcomes
    )(path)[0]
    count = 0

    for row in outcomes[offset:offset + limit]:
        if (
            row.get("training_eligible", True) is not True
            or _paper_correction(row)
            or row.get("entry_data_kind") == "synthetic"
            or not number(row.get("net_pnl_usd"))
            or not row.get("exit_signature")
        ):
            continue

        context = row.get("entry_context") or {}
        position = (
            str(row["run_id"]) + ":" + row["mint"] + ":" +
            str(row["entry_at"])
            if source == "bot_paper"
            else str(row.get("entry_signature", row["exit_signature"]))
        )
        record = {
            "identity": row["exit_signature"],
            "position_id": position,
            "group_id": position,
            "source": source,
            "mint": row["mint"],
            "feature_at": row["entry_at"],
            "known_at": context.get("observed_at", time.time()),
            "label_known_at": row["exit_at"],
            "features": list(model_features(context).values()),
            "feature_schema": feature_schema(),
            "net_pnl_usd": row["net_pnl_usd"],
            "fees_usd": row.get("fees_usd"),
            "net_of_costs_verified": (
                provenance.get("net_outcomes_verified") is True
            ),
            "label_kind": row.get("label_kind", source),
            "rights": rights,
            "provenance": provenance,
            "training_eligible": True,
            "original_context": context,
        }
        store.add(record)
        count += 1

    return {
        "imported": count,
        "next_offset": (
            offset + limit if offset + limit < len(outcomes) else None
        ),
        "source_rows": len(outcomes),
    }


def import_wallet_outcomes(store, outcomes, rights, provenance):
    from meme_learning import model_features
    if len(outcomes) > 128:
        raise ValueError("Wallet import batch exceeded")
    count = 0
    for row in outcomes:
        if (
            row.get("status") != "closed"
            or row.get("receipt_backed") is not True
            or not row.get("entry_signature")
            or not row.get("exit_signature")
            or not row.get("wallet")
        ):
            continue
        context = row.get("entry_context") or {}
        group = (
            provenance.get("wallet_cluster_id")
            if provenance.get("wallet_cluster_verified") is True
            else "unverified_wallet_cluster"
        )
        usd_verified = row.get("usd_conversion_verified") is True
        store.add({
            "identity": row["exit_signature"],
            "position_id": row["entry_signature"],
            "group_id": group,
            "source": "followed_wallet",
            "mint": row["mint"],
            "feature_at": row["entry_at"],
            "known_at": row["feature_known_at"],
            "label_known_at": row["exit_at"],
            "features": list(model_features(context).values()),
            "feature_schema": feature_schema(),
            "net_pnl_usd": (
                row.get("net_pnl_usd") if usd_verified else None
            ),
            "fees_usd": row.get("fees_usd"),
            "net_of_costs_verified": (
                row.get("net_of_costs_verified") is True
                and usd_verified
            ),
            "rights": rights,
            "provenance": provenance,
            "training_eligible": True,
            "wallet": row["wallet"],
            "original_context": context,
            "label_kind": "receipt_backed_wallet_net",
        })
        count += 1
    return count


def add_market_context(
    store, context, *, identity, position_id, group_id,
    mint, feature_at, known_at, rights, provenance
):
    from meme_learning import model_features
    return store.add({
        "identity": identity,
        "position_id": position_id,
        "group_id": group_id,
        "source": "market",
        "mint": mint,
        "feature_at": feature_at,
        "known_at": known_at,
        "label_known_at": None,
        "features": list(model_features(context).values()),
        "feature_schema": feature_schema(),
        "net_pnl_usd": None,
        "rights": rights,
        "provenance": provenance,
        "training_eligible": True,
        "original_context": context,
    })


def import_position_quotes(store, paths, rights, provenance):
    from neural_exits import load_groups
    from meme_learning import model_features

    completed = {
        row["position_id"]: row
        for row in store.observations()
        if row.get("net_pnl_usd") is not None
    }
    count = 0
    for (path, position), quotes in load_groups(paths).items():
        target = completed.get(position)
        if target is None:
            continue
        for at, context, _ in quotes:
            if count >= 1024:
                return {"imported": count, "status": "batch_limit"}
            if at >= target["label_known_at"]:
                continue
            verified_timestamp = (
                provenance.get("quote_timestamp_verified") is True
            )
            store.add({
                "identity": "quote:" + digest([path, position, at]),
                "position_id": position,
                "group_id": target["group_id"],
                "source": target["source"],
                "mint": target["mint"],
                "feature_at": at,
                "known_at": at if verified_timestamp else time.time(),
                "label_known_at": target["label_known_at"],
                "features": list(model_features(context).values()),
                "feature_schema": feature_schema(),
                "net_pnl_usd": target["net_pnl_usd"],
                "net_of_costs_verified": (
                    target.get("net_of_costs_verified") is True
                ),
                "fees_usd": target.get("fees_usd"),
                "rights": rights,
                "provenance": provenance,
                "training_eligible": True,
                "original_context": context,
                "label_kind": "completed_position_net",
            })
            count += 1
    return {"imported": count, "status": "complete"}
