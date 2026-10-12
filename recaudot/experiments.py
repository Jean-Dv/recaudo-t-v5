"""Reproducible experiments and CSV/JSON evidence for S1, P1, and D1."""

from __future__ import annotations

import csv
import json
import math
import os
import statistics
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable

from .http_utils import TransportError, post_json
from .runtime import (
    free_port,
    start_gateway,
    start_service,
    stop_process,
)
from .simulator import CardSimulator
from .store import Store


def percentile(values: list[float], percentile_value: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil((percentile_value / 100) * len(ordered)) - 1)
    return ordered[index]


def adaptive_batch_size(
    *,
    remaining: int,
    max_batch_size: int,
    offered_rate_per_s: float | None,
    max_batch_wait_ms: float,
) -> int:
    """Choose a batch bounded by both size and oldest-event waiting time."""

    if remaining <= 0 or max_batch_size <= 0:
        raise ValueError("remaining and max_batch_size must be positive")
    if offered_rate_per_s is None:
        return min(max_batch_size, remaining)
    arrivals_within_wait = (
        math.floor(offered_rate_per_s * (max_batch_wait_ms / 1_000)) + 1
    )
    return min(max_batch_size, arrivals_within_wait, remaining)


def _prepare_evidence(root: Path) -> tuple[Path, Path]:
    raw = root / "raw"
    results = root / "results"
    raw.mkdir(parents=True, exist_ok=True)
    results.mkdir(parents=True, exist_ok=True)
    return raw, results


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"cannot write empty evidence file: {path}")
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_summary(path: Path, summary: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as output:
        json.dump(summary, output, indent=2, sort_keys=True)
        output.write("\n")


def _temporary_db(prefix: str) -> tuple[tempfile.TemporaryDirectory[str], Path]:
    temporary = tempfile.TemporaryDirectory(prefix=f"recaudot-{prefix}-")
    return temporary, Path(temporary.name) / "experiment.db"


def _process_cpu_seconds(pid: int) -> float | None:
    """Read Linux process CPU time without adding a monitoring dependency."""

    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()
        clock_ticks = int(os.sysconf("SC_CLK_TCK"))
        return (int(fields[13]) + int(fields[14])) / clock_ticks
    except (FileNotFoundError, IndexError, OSError, ValueError):
        return None


def _summarize_attack_variant(
    rows: list[dict[str, Any]], elapsed_s: float
) -> dict[str, Any]:
    latencies = [float(row["latency_ms"]) for row in rows]
    blocked = sum(not bool(row["accepted"]) for row in rows)
    return {
        "attempts": len(rows),
        "attacks_blocked": blocked,
        "attack_block_rate_pct": (blocked / len(rows)) * 100,
        "fraudulent_discounts": sum(bool(row["accepted"]) for row in rows),
        "p95_latency_ms": percentile(latencies, 95),
        "maximum_reaction_ms": max(latencies),
        "elapsed_s": elapsed_s,
    }


def _run_attack_variant(
    *, auth_enabled: bool, attempts: int, variant: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    temporary, db_path = _temporary_db(f"security-{variant}")
    process = None
    try:
        store = Store(db_path)
        store.initialize()
        card_ids = [f"CLONE-{index:05d}" for index in range(attempts)]
        store.seed_cards(card_ids)
        port = free_port()
        process = start_service(
            port=port,
            db_path=db_path,
            node="primary",
            auth_enabled=auth_enabled,
        )
        url = f"http://127.0.0.1:{port}"
        rows: list[dict[str, Any]] = []
        started = time.perf_counter()
        for index, card_id in enumerate(card_ids):
            response, latency_ms = CardSimulator.clone(card_id).validate(
                url,
                fare_cents=100,
                authenticate=auth_enabled,
            )
            rows.append(
                {
                    "variant": variant,
                    "attempt": index + 1,
                    "card_id": card_id,
                    "accepted": bool(response["accepted"]),
                    "reason": response["reason"],
                    "latency_ms": round(latency_ms, 6),
                }
            )
        elapsed_s = time.perf_counter() - started
        return rows, _summarize_attack_variant(rows, elapsed_s)
    finally:
        stop_process(process)
        temporary.cleanup()


def run_security(evidence_root: Path, quick: bool = False) -> dict[str, Any]:
    raw, results = _prepare_evidence(evidence_root)
    attempts = 50 if quick else 1_000
    baseline_rows, baseline = _run_attack_variant(
        auth_enabled=False, attempts=attempts, variant="without_tactic"
    )
    tactic_rows, tactic = _run_attack_variant(
        auth_enabled=True, attempts=attempts, variant="challenge_response"
    )
    _write_csv(raw / "s1-clone-attacks.csv", baseline_rows + tactic_rows)
    summary = {
        "scenario": "S1",
        "sample_size_per_variant": attempts,
        "without_tactic": baseline,
        "with_tactic": tactic,
        "criteria": {
            "sample_contains_1000_attempts": attempts >= 1_000,
            "100_percent_clones_rejected": tactic["attack_block_rate_pct"] == 100.0,
            "zero_fraudulent_discounts": tactic["fraudulent_discounts"] == 0,
            "reaction_under_60_seconds": tactic["maximum_reaction_ms"] < 60_000,
        },
    }
    _write_summary(results / "s1-security.json", summary)
    return summary


def _performance_variant(
    *,
    count: int,
    variant: str,
    max_batch_size: int,
    offered_rate_per_s: float | None,
    max_batch_wait_ms: float = 50.0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    temporary, db_path = _temporary_db(f"performance-{variant}")
    process = None
    try:
        card_count = max(100, max_batch_size)
        card_ids = [f"PERF-{index:04d}" for index in range(card_count)]
        cards = {card_id: CardSimulator.legitimate(card_id) for card_id in card_ids}
        store = Store(db_path)
        store.initialize()
        store.seed_cards(card_ids, balance_cents=10_000_000)
        port = free_port()
        process = start_service(
            port=port,
            db_path=db_path,
            node="primary",
            auth_enabled=True,
        )
        url = f"http://127.0.0.1:{port}"
        rows: list[dict[str, Any]] = []
        http_requests = 0
        observed_batch_sizes: list[int] = []
        cpu_before = _process_cpu_seconds(process.pid)
        started = time.perf_counter()
        if max_batch_size == 1:
            for index in range(count):
                if offered_rate_per_s is not None:
                    due = started + (index / offered_rate_per_s)
                    remaining = due - time.perf_counter()
                    if remaining > 0:
                        time.sleep(remaining)
                card = cards[card_ids[index % card_count]]
                response, latency_ms = card.validate(url, fare_cents=0)
                http_requests += 2
                observed_batch_sizes.append(1)
                rows.append(
                    {
                        "variant": variant,
                        "validation": index + 1,
                        "accepted": bool(response["accepted"]),
                        "latency_ms": round(latency_ms, 6),
                        "batch_size": 1,
                    }
                )
        else:
            offset = 0
            while offset < count:
                if offered_rate_per_s is None:
                    chunk_size = adaptive_batch_size(
                        remaining=count - offset,
                        max_batch_size=max_batch_size,
                        offered_rate_per_s=None,
                        max_batch_wait_ms=max_batch_wait_ms,
                    )
                else:
                    chunk_size = adaptive_batch_size(
                        remaining=count - offset,
                        max_batch_size=max_batch_size,
                        offered_rate_per_s=offered_rate_per_s,
                        max_batch_wait_ms=max_batch_wait_ms,
                    )
                    # Dispatch when the batch is full or its oldest event has
                    # waited the configured maximum, whichever happens first.
                    full_at = started + (
                        (offset + chunk_size - 1) / offered_rate_per_s
                    )
                    timeout_at = started + (
                        (offset / offered_rate_per_s)
                        + (max_batch_wait_ms / 1_000)
                    )
                    dispatch_due = min(full_at, timeout_at)
                    remaining = dispatch_due - time.perf_counter()
                    if remaining > 0:
                        time.sleep(remaining)
                selected = [
                    cards[card_ids[(offset + index) % card_count]]
                    for index in range(chunk_size)
                ]
                challenges, _challenge_ms = post_json(
                    url,
                    "/challenges/batch",
                    {"card_ids": [card.card_id for card in selected]},
                )
                validations = []
                for card, challenge in zip(selected, challenges["challenges"], strict=True):
                    validations.append(
                        {
                            **card.proof_for(challenge),
                            "operation_id": uuid.uuid4().hex,
                            "fare_cents": 0,
                        }
                    )
                response, _validation_ms = post_json(
                    url, "/validations/batch", {"validations": validations}
                )
                http_requests += 2
                observed_batch_sizes.append(chunk_size)
                completed = time.perf_counter()
                for index, result in enumerate(response["results"]):
                    if offered_rate_per_s is None:
                        logical_latency_ms = (
                            _challenge_ms + _validation_ms
                        )
                    else:
                        arrival = started + (
                            (offset + index) / offered_rate_per_s
                        )
                        logical_latency_ms = (completed - arrival) * 1_000
                    rows.append(
                        {
                            "variant": variant,
                            "validation": offset + index + 1,
                            "accepted": bool(result["accepted"]),
                            "latency_ms": round(logical_latency_ms, 6),
                            "batch_size": chunk_size,
                        }
                    )
                offset += chunk_size
        elapsed_s = time.perf_counter() - started
        cpu_after = _process_cpu_seconds(process.pid)
        cpu_pct = (
            ((cpu_after - cpu_before) / elapsed_s) * 100
            if cpu_before is not None and cpu_after is not None and elapsed_s > 0
            else None
        )
        latencies = [float(row["latency_ms"]) for row in rows]
        accepted = sum(bool(row["accepted"]) for row in rows)
        summary = {
            "validations": len(rows),
            "accepted": accepted,
            "unprocessed": len(rows) - accepted,
            "elapsed_s": elapsed_s,
            "throughput_per_s": accepted / elapsed_s,
            "throughput_per_min": (accepted / elapsed_s) * 60,
            "p95_latency_ms": percentile(latencies, 95),
            "mean_latency_ms": statistics.fmean(latencies),
            "jitter_stddev_ms": statistics.pstdev(latencies),
            "service_cpu_pct_of_one_core": cpu_pct,
            "max_batch_size": max_batch_size,
            "mean_batch_size": statistics.fmean(observed_batch_sizes),
            "max_batch_wait_ms": max_batch_wait_ms if max_batch_size > 1 else 0.0,
            "offered_rate_per_min": (
                offered_rate_per_s * 60
                if offered_rate_per_s is not None
                else None
            ),
            "http_requests": http_requests,
        }
        return rows, summary
    finally:
        stop_process(process)
        temporary.cleanup()


def run_performance(evidence_root: Path, quick: bool = False) -> dict[str, Any]:
    raw, results = _prepare_evidence(evidence_root)
    count = 300 if quick else 6_000
    baseline_rows, baseline = _performance_variant(
        count=count,
        variant="sla_individual_requests",
        max_batch_size=1,
        offered_rate_per_s=102.0,
    )
    tactic_rows, tactic = _performance_variant(
        count=count,
        variant="sla_adaptive_batches",
        max_batch_size=25,
        offered_rate_per_s=102.0,
        max_batch_wait_ms=50.0,
    )
    capacity_baseline_rows, capacity_baseline = _performance_variant(
        count=count,
        variant="capacity_individual_requests",
        max_batch_size=1,
        offered_rate_per_s=None,
    )
    capacity_tactic_rows, capacity_tactic = _performance_variant(
        count=count,
        variant="capacity_batches",
        max_batch_size=25,
        offered_rate_per_s=None,
    )
    _write_csv(
        raw / "p1-load.csv",
        baseline_rows
        + tactic_rows
        + capacity_baseline_rows
        + capacity_tactic_rows,
    )
    capacity_gain_pct = (
        (
            capacity_tactic["throughput_per_min"]
            - capacity_baseline["throughput_per_min"]
        )
        / capacity_baseline["throughput_per_min"]
        * 100
    )
    request_reduction_pct = (
        (baseline["http_requests"] - tactic["http_requests"])
        / baseline["http_requests"]
        * 100
    )
    summary = {
        "scenario": "P1",
        "stimulus": f"{count} cryptographic validations",
        "without_tactic": baseline,
        "with_tactic": tactic,
        "capacity_without_tactic": capacity_baseline,
        "capacity_with_tactic": capacity_tactic,
        "capacity_throughput_gain_pct": capacity_gain_pct,
        "sla_http_request_reduction_pct": request_reduction_pct,
        "criteria": {
            "sample_contains_6000_validations": count >= 6_000,
            "at_least_6000_per_min": tactic["throughput_per_min"] >= 6_000,
            "p95_under_500_ms": tactic["p95_latency_ms"] < 500,
            "zero_unprocessed": tactic["unprocessed"] == 0,
            "service_cpu_under_70_pct": (
                tactic["service_cpu_pct_of_one_core"] is not None
                and tactic["service_cpu_pct_of_one_core"] < 70
            ),
            "capacity_throughput_improved": capacity_gain_pct > 0,
            "http_requests_reduced": request_reduction_pct > 0,
        },
    }
    _write_summary(results / "p1-performance.json", summary)
    return summary


def _record_availability_row(
    rows: list[dict[str, Any]],
    *,
    variant: str,
    index: int,
    operation_id: str,
    success: bool,
    latency_ms: float,
    node: str,
    gateway_switch_ms: float,
    recovery_ms: float,
    error: str,
) -> None:
    rows.append(
        {
            "variant": variant,
            "request": index + 1,
            "operation_id": operation_id,
            "success": success,
            "latency_ms": round(latency_ms, 6),
            "node": node,
            "gateway_switch_ms": round(gateway_switch_ms, 6),
            "recovery_ms": round(recovery_ms, 6),
            "error": error,
        }
    )


def _run_availability_variant(
    *, requests: int, failure_at: int, with_tactic: bool, event_log: Path
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    variant = "passive_failover" if with_tactic else "primary_only"
    temporary, db_path = _temporary_db(f"availability-{variant}")
    primary = standby = gateway = None
    try:
        card_id = "AVAILABLE-1"
        store = Store(db_path)
        store.initialize()
        store.seed_cards([card_id], balance_cents=10_000_000)
        primary_port = free_port()
        primary = start_service(
            port=primary_port,
            db_path=db_path,
            node="primary",
            auth_enabled=True,
        )
        primary_url = f"http://127.0.0.1:{primary_port}"
        target_url = primary_url
        if with_tactic:
            standby_port = free_port()
            gateway_port = free_port()
            standby = start_service(
                port=standby_port,
                db_path=db_path,
                node="standby",
                auth_enabled=True,
            )
            gateway = start_gateway(
                port=gateway_port,
                primary_url=primary_url,
                standby_url=f"http://127.0.0.1:{standby_port}",
                timeout_s=0.25,
                event_log=event_log,
            )
            target_url = f"http://127.0.0.1:{gateway_port}"

        card = CardSimulator.legitimate(card_id)
        rows: list[dict[str, Any]] = []
        successful_operation_ids: list[str] = []
        measured_gateway_switch_ms = 0.0
        measured_recovery_ms = 0.0
        for index in range(requests):
            operation_id = uuid.uuid4().hex
            started_ns = time.perf_counter_ns()
            failure_injected_ns: int | None = None
            try:
                if with_tactic and index == failure_at:
                    challenge, _ = post_json(
                        target_url, "/challenges", {"card_id": card_id}
                    )
                    failure_injected_ns = time.perf_counter_ns()
                    stop_process(primary)
                    primary = None
                    payload = {
                        **card.proof_for(challenge),
                        "operation_id": operation_id,
                        "fare_cents": 1,
                    }
                    response, _ = post_json(target_url, "/validations", payload)
                else:
                    if index == failure_at:
                        stop_process(primary)
                        primary = None
                    response, _ = card.validate(
                        target_url,
                        fare_cents=1,
                        operation_id=operation_id,
                    )
                elapsed_ms = (time.perf_counter_ns() - started_ns) / 1_000_000
                gateway_data = response.get("_gateway", {})
                failover_ms = float(gateway_data.get("failover_ms", 0.0))
                measured_gateway_switch_ms = max(
                    measured_gateway_switch_ms, failover_ms
                )
                recovery_ms = (
                    (time.perf_counter_ns() - failure_injected_ns) / 1_000_000
                    if failure_injected_ns is not None
                    else 0.0
                )
                measured_recovery_ms = max(measured_recovery_ms, recovery_ms)
                success = bool(response["accepted"])
                if success:
                    successful_operation_ids.append(operation_id)
                _record_availability_row(
                    rows,
                    variant=variant,
                    index=index,
                    operation_id=operation_id,
                    success=success,
                    latency_ms=elapsed_ms,
                    node=str(response.get("node", "unknown")),
                    gateway_switch_ms=failover_ms,
                    recovery_ms=recovery_ms,
                    error="" if success else str(response.get("reason", "rejected")),
                )
            except TransportError as exc:
                elapsed_ms = (time.perf_counter_ns() - started_ns) / 1_000_000
                _record_availability_row(
                    rows,
                    variant=variant,
                    index=index,
                    operation_id=operation_id,
                    success=False,
                    latency_ms=elapsed_ms,
                    node="unavailable",
                    gateway_switch_ms=0.0,
                    recovery_ms=0.0,
                    error=str(exc),
                )

        with store.connect() as connection:
            recorded = {
                row[0]
                for row in connection.execute(
                    "SELECT operation_id FROM operations WHERE accepted = 1"
                )
            }
        lost_confirmed = len(set(successful_operation_ids) - recorded)
        successes = sum(bool(row["success"]) for row in rows)
        summary = {
            "requests": requests,
            "successful": successes,
            "availability_pct": (successes / requests) * 100,
            "recovery_ms": measured_recovery_ms if with_tactic else None,
            "gateway_switch_ms": (
                measured_gateway_switch_ms if with_tactic else None
            ),
            "confirmed_transactions_lost": lost_confirmed,
        }
        return rows, summary
    finally:
        stop_process(gateway)
        stop_process(standby)
        stop_process(primary)
        temporary.cleanup()


def run_availability(evidence_root: Path, quick: bool = False) -> dict[str, Any]:
    raw, results = _prepare_evidence(evidence_root)
    requests = 30 if quick else 200
    failure_at = 10 if quick else 50
    event_log = raw / "d1-gateway-events.jsonl"
    if event_log.exists():
        event_log.unlink()
    baseline_rows, baseline = _run_availability_variant(
        requests=requests,
        failure_at=failure_at,
        with_tactic=False,
        event_log=event_log,
    )
    tactic_rows, tactic = _run_availability_variant(
        requests=requests,
        failure_at=failure_at,
        with_tactic=True,
        event_log=event_log,
    )
    _write_csv(raw / "d1-failover.csv", baseline_rows + tactic_rows)
    summary = {
        "scenario": "D1",
        "injected_failure_after_requests": failure_at,
        "without_tactic": baseline,
        "with_tactic": tactic,
        "criteria": {
            "failover_under_30_seconds": (
                tactic["recovery_ms"] is not None and tactic["recovery_ms"] < 30_000
            ),
            "zero_confirmed_transactions_lost": (
                tactic["confirmed_transactions_lost"] == 0
            ),
        },
        "monthly_availability_note": (
            "The 99.9% monthly target requires a month of operational data; "
            "this controlled experiment verifies recovery time and data loss."
        ),
    }
    _write_summary(results / "d1-availability.json", summary)
    return summary


def _run_legitimate_latency_variant(
    *, count: int, auth_enabled: bool, variant: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    temporary, db_path = _temporary_db(f"tradeoff-{variant}")
    process = None
    try:
        card_id = f"TRADEOFF-{variant}"
        store = Store(db_path)
        store.initialize()
        store.seed_cards([card_id])
        port = free_port()
        process = start_service(
            port=port,
            db_path=db_path,
            node="primary",
            auth_enabled=auth_enabled,
        )
        card = CardSimulator.legitimate(card_id)
        url = f"http://127.0.0.1:{port}"
        rows: list[dict[str, Any]] = []
        for index in range(count):
            response, latency_ms = card.validate(
                url, fare_cents=0, authenticate=auth_enabled
            )
            rows.append(
                {
                    "variant": variant,
                    "validation": index + 1,
                    "accepted": bool(response["accepted"]),
                    "latency_ms": round(latency_ms, 6),
                }
            )
        latencies = [float(row["latency_ms"]) for row in rows]
        return rows, {
            "validations": count,
            "p95_latency_ms": percentile(latencies, 95),
            "mean_latency_ms": statistics.fmean(latencies),
            "accepted": sum(bool(row["accepted"]) for row in rows),
        }
    finally:
        stop_process(process)
        temporary.cleanup()


def run_tradeoff(evidence_root: Path, quick: bool = False) -> dict[str, Any]:
    raw, results = _prepare_evidence(evidence_root)
    count = 100 if quick else 1_000
    baseline_rows, baseline = _run_legitimate_latency_variant(
        count=count, auth_enabled=False, variant="without_authentication"
    )
    tactic_rows, tactic = _run_legitimate_latency_variant(
        count=count, auth_enabled=True, variant="challenge_response"
    )
    _write_csv(raw / "tradeoff-security-performance.csv", baseline_rows + tactic_rows)
    increase_ms = tactic["p95_latency_ms"] - baseline["p95_latency_ms"]
    increase_pct = (
        (increase_ms / baseline["p95_latency_ms"]) * 100
        if baseline["p95_latency_ms"]
        else None
    )
    summary = {
        "tradeoff": "security_vs_performance",
        "sample_size_per_variant": count,
        "without_authentication": baseline,
        "with_challenge_response": tactic,
        "p95_increase_ms": increase_ms,
        "p95_increase_pct": increase_pct,
        "cost_acceptable_for_v5": tactic["p95_latency_ms"] < 500,
    }
    _write_summary(results / "tradeoff-security-performance.json", summary)
    return summary


EXPERIMENTS: dict[str, Callable[[Path, bool], dict[str, Any]]] = {
    "security": run_security,
    "performance": run_performance,
    "availability": run_availability,
    "tradeoff": run_tradeoff,
}


def run_requested(scenario: str, evidence_root: Path, quick: bool) -> None:
    selected: Iterable[str] = EXPERIMENTS if scenario == "all" else [scenario]
    summaries: dict[str, Any] = {}
    for name in selected:
        print(f"Running {name} experiment...", flush=True)
        summaries[name] = EXPERIMENTS[name](evidence_root, quick)
    print(json.dumps(summaries, indent=2, sort_keys=True))
