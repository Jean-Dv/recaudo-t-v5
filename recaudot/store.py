"""SQLite persistence for balances, challenges, operations, and audit events."""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .crypto import test_key_for_card, verify_proof


@dataclass(frozen=True)
class ValidationResult:
    accepted: bool
    reason: str
    operation_id: str
    card_id: str
    balance_cents: int | None
    idempotent_replay: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "reason": self.reason,
            "operation_id": self.operation_id,
            "card_id": self.card_id,
            "balance_cents": self.balance_cents,
            "idempotent_replay": self.idempotent_replay,
        }


class Store:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA synchronous = FULL;

                CREATE TABLE IF NOT EXISTS cards (
                    card_id TEXT PRIMARY KEY,
                    secret_hex TEXT NOT NULL,
                    last_counter INTEGER NOT NULL DEFAULT 0,
                    balance_cents INTEGER NOT NULL,
                    blocked INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS challenges (
                    challenge_id TEXT PRIMARY KEY,
                    card_id TEXT NOT NULL,
                    nonce TEXT NOT NULL,
                    consumed INTEGER NOT NULL DEFAULT 0,
                    created_ns INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS operations (
                    operation_id TEXT PRIMARY KEY,
                    card_id TEXT NOT NULL,
                    fare_cents INTEGER NOT NULL,
                    accepted INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    balance_cents INTEGER,
                    node TEXT NOT NULL,
                    created_ns INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    card_id TEXT,
                    operation_id TEXT,
                    details_json TEXT NOT NULL,
                    created_ns INTEGER NOT NULL
                );
                """
            )

    def seed_cards(
        self,
        card_ids: Iterable[str],
        balance_cents: int = 1_000_000,
    ) -> None:
        rows = [
            (card_id, test_key_for_card(card_id).hex(), balance_cents)
            for card_id in card_ids
        ]
        with self.connect() as connection:
            connection.executemany(
                """
                INSERT OR REPLACE INTO cards
                    (card_id, secret_hex, last_counter, balance_cents, blocked)
                VALUES (?, ?, 0, ?, 0)
                """,
                rows,
            )

    def issue_challenge(self, card_id: str) -> dict[str, str]:
        challenge_id = uuid.uuid4().hex
        nonce = secrets.token_hex(24)
        with self.connect() as connection:
            card = connection.execute(
                "SELECT blocked FROM cards WHERE card_id = ?", (card_id,)
            ).fetchone()
            if card is None:
                raise KeyError("unknown_card")
            if card["blocked"]:
                raise PermissionError("blocked_card")
            connection.execute(
                """
                INSERT INTO challenges
                    (challenge_id, card_id, nonce, consumed, created_ns)
                VALUES (?, ?, ?, 0, ?)
                """,
                (challenge_id, card_id, nonce, time.time_ns()),
            )
        return {"challenge_id": challenge_id, "nonce": nonce}

    def validate(
        self,
        *,
        card_id: str,
        operation_id: str,
        fare_cents: int,
        node: str,
        auth_enabled: bool,
        challenge_id: str | None = None,
        counter: int | None = None,
        proof: str | None = None,
    ) -> ValidationResult:
        if fare_cents < 0:
            return ValidationResult(
                False, "invalid_fare", operation_id, card_id, None
            )

        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                """
                SELECT accepted, reason, balance_cents
                FROM operations WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
            if previous is not None:
                return ValidationResult(
                    bool(previous["accepted"]),
                    previous["reason"],
                    operation_id,
                    card_id,
                    previous["balance_cents"],
                    True,
                )

            card = connection.execute(
                "SELECT * FROM cards WHERE card_id = ?", (card_id,)
            ).fetchone()
            if card is None:
                return self._reject(
                    connection, card_id, operation_id, fare_cents, node,
                    "unknown_card", None
                )
            if card["blocked"]:
                return self._reject(
                    connection, card_id, operation_id, fare_cents, node,
                    "blocked_card", card["balance_cents"]
                )

            if auth_enabled:
                auth_failure = self._verify_authentication(
                    connection,
                    card,
                    challenge_id=challenge_id,
                    counter=counter,
                    proof=proof,
                )
                if auth_failure is not None:
                    connection.execute(
                        "UPDATE cards SET blocked = 1 WHERE card_id = ?",
                        (card_id,),
                    )
                    return self._reject(
                        connection,
                        card_id,
                        operation_id,
                        fare_cents,
                        node,
                        auth_failure,
                        card["balance_cents"],
                        audit_event="authentication_attack",
                    )

            if card["balance_cents"] < fare_cents:
                return self._reject(
                    connection, card_id, operation_id, fare_cents, node,
                    "insufficient_balance", card["balance_cents"]
                )

            new_balance = card["balance_cents"] - fare_cents
            if auth_enabled:
                connection.execute(
                    """
                    UPDATE cards
                    SET balance_cents = ?, last_counter = ?
                    WHERE card_id = ?
                    """,
                    (new_balance, counter, card_id),
                )
            else:
                connection.execute(
                    "UPDATE cards SET balance_cents = ? WHERE card_id = ?",
                    (new_balance, card_id),
                )
            connection.execute(
                """
                INSERT INTO operations
                    (operation_id, card_id, fare_cents, accepted, reason,
                     balance_cents, node, created_ns)
                VALUES (?, ?, ?, 1, 'accepted', ?, ?, ?)
                """,
                (
                    operation_id,
                    card_id,
                    fare_cents,
                    new_balance,
                    node,
                    time.time_ns(),
                ),
            )
            return ValidationResult(
                True, "accepted", operation_id, card_id, new_balance
            )

    def _verify_authentication(
        self,
        connection: sqlite3.Connection,
        card: sqlite3.Row,
        *,
        challenge_id: str | None,
        counter: int | None,
        proof: str | None,
    ) -> str | None:
        if not challenge_id or counter is None or not proof:
            return "missing_proof"
        challenge = connection.execute(
            "SELECT * FROM challenges WHERE challenge_id = ?",
            (challenge_id,),
        ).fetchone()
        if challenge is None or challenge["card_id"] != card["card_id"]:
            return "invalid_challenge"
        if challenge["consumed"]:
            return "replayed_challenge"

        connection.execute(
            "UPDATE challenges SET consumed = 1 WHERE challenge_id = ?",
            (challenge_id,),
        )
        if counter <= card["last_counter"]:
            return "replayed_counter"
        if not verify_proof(
            bytes.fromhex(card["secret_hex"]),
            card["card_id"],
            challenge["nonce"],
            counter,
            proof,
        ):
            return "invalid_proof"
        return None

    def _reject(
        self,
        connection: sqlite3.Connection,
        card_id: str,
        operation_id: str,
        fare_cents: int,
        node: str,
        reason: str,
        balance_cents: int | None,
        audit_event: str = "validation_rejected",
    ) -> ValidationResult:
        connection.execute(
            """
            INSERT INTO operations
                (operation_id, card_id, fare_cents, accepted, reason,
                 balance_cents, node, created_ns)
            VALUES (?, ?, ?, 0, ?, ?, ?, ?)
            """,
            (
                operation_id,
                card_id,
                fare_cents,
                reason,
                balance_cents,
                node,
                time.time_ns(),
            ),
        )
        connection.execute(
            """
            INSERT INTO audit_events
                (event_type, card_id, operation_id, details_json, created_ns)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                audit_event,
                card_id,
                operation_id,
                json.dumps({"reason": reason}, sort_keys=True),
                time.time_ns(),
            ),
        )
        return ValidationResult(
            False, reason, operation_id, card_id, balance_cents
        )

    def counts(self) -> dict[str, int]:
        with self.connect() as connection:
            accepted = connection.execute(
                "SELECT COUNT(*) FROM operations WHERE accepted = 1"
            ).fetchone()[0]
            rejected = connection.execute(
                "SELECT COUNT(*) FROM operations WHERE accepted = 0"
            ).fetchone()[0]
            blocked = connection.execute(
                "SELECT COUNT(*) FROM cards WHERE blocked = 1"
            ).fetchone()[0]
        return {"accepted": accepted, "rejected": rejected, "blocked": blocked}

