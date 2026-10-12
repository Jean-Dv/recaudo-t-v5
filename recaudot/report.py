"""Render the final report from measured JSON evidence."""

from __future__ import annotations

import json
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "docs" / "part-2-report-template.md"
OUTPUT = ROOT / "docs" / "part-2-report.md"
RESULTS = ROOT / "evidence" / "results"


def _load(name: str) -> dict[str, Any]:
    path = RESULTS / name
    if not path.exists():
        raise SystemExit(
            f"Missing {path}. Run `make evidence` (or `make quick`) first."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _number(value: float | int | None, digits: int = 3) -> str:
    if value is None:
        return "no disponible"
    if isinstance(value, int):
        return str(value)
    return f"{value:.{digits}f}"


def _yes_no(value: bool) -> str:
    return "Sí" if value else "No"


def main() -> None:
    security = _load("s1-security.json")
    performance = _load("p1-performance.json")
    availability = _load("d1-availability.json")
    tradeoff = _load("tradeoff-security-performance.json")

    s1_base = security["without_tactic"]
    s1_tactic = security["with_tactic"]
    p1_base = performance["without_tactic"]
    p1_tactic = performance["with_tactic"]
    p1_capacity_base = performance["capacity_without_tactic"]
    p1_capacity_tactic = performance["capacity_with_tactic"]
    d1_base = availability["without_tactic"]
    d1_tactic = availability["with_tactic"]
    trade_base = tradeoff["without_authentication"]
    trade_tactic = tradeoff["with_challenge_response"]

    s1_full = security["sample_size_per_variant"] >= 1_000
    p1_full = p1_tactic["validations"] >= 6_000
    trade_full = tradeoff["sample_size_per_variant"] >= 1_000
    full_run = s1_full and p1_full and trade_full

    s1_pass = all(security["criteria"].values())
    p1_pass = all(performance["criteria"].values())
    d1_pass = all(availability["criteria"].values())
    cpu_base = p1_base["service_cpu_pct_of_one_core"]
    cpu_tactic = p1_tactic["service_cpu_pct_of_one_core"]

    replacements = {
        "RUN_KIND": "evidencia completa" if full_run else "calibración rápida (no entregable)",
        "GENERATED_AT": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "ENVIRONMENT": f"Python {platform.python_version()}, {platform.system()} {platform.release()}",
        "S1_SAMPLE": str(security["sample_size_per_variant"]),
        "S1_BASE_BLOCK_RATE": _number(s1_base["attack_block_rate_pct"]),
        "S1_TACTIC_BLOCK_RATE": _number(s1_tactic["attack_block_rate_pct"]),
        "S1_BASE_FRAUD": str(s1_base["fraudulent_discounts"]),
        "S1_TACTIC_FRAUD": str(s1_tactic["fraudulent_discounts"]),
        "S1_BASE_P95": _number(s1_base["p95_latency_ms"]),
        "S1_TACTIC_P95": _number(s1_tactic["p95_latency_ms"]),
        "S1_BASE_MAX": _number(s1_base["maximum_reaction_ms"]),
        "S1_TACTIC_MAX": _number(s1_tactic["maximum_reaction_ms"]),
        "S1_RESULT": "cumple" if s1_pass else "no cumple todavía",
        "P1_SAMPLE": str(p1_tactic["validations"]),
        "P1_BASE_TPM": _number(p1_base["throughput_per_min"]),
        "P1_TACTIC_TPM": _number(p1_tactic["throughput_per_min"]),
        "P1_BASE_P95": _number(p1_base["p95_latency_ms"]),
        "P1_TACTIC_P95": _number(p1_tactic["p95_latency_ms"]),
        "P1_BASE_JITTER": _number(p1_base["jitter_stddev_ms"]),
        "P1_TACTIC_JITTER": _number(p1_tactic["jitter_stddev_ms"]),
        "P1_BASE_UNPROCESSED": str(p1_base["unprocessed"]),
        "P1_TACTIC_UNPROCESSED": str(p1_tactic["unprocessed"]),
        "P1_BASE_CPU": _number(cpu_base),
        "P1_TACTIC_CPU": _number(cpu_tactic),
        "P1_BASE_HTTP": str(p1_base["http_requests"]),
        "P1_TACTIC_HTTP": str(p1_tactic["http_requests"]),
        "P1_MEAN_BATCH": _number(p1_tactic["mean_batch_size"]),
        "P1_CAP_BASE_TPM": _number(p1_capacity_base["throughput_per_min"]),
        "P1_CAP_TACTIC_TPM": _number(p1_capacity_tactic["throughput_per_min"]),
        "P1_CAP_BASE_P95": _number(p1_capacity_base["p95_latency_ms"]),
        "P1_CAP_TACTIC_P95": _number(p1_capacity_tactic["p95_latency_ms"]),
        "P1_CAP_BASE_HTTP": str(p1_capacity_base["http_requests"]),
        "P1_CAP_TACTIC_HTTP": str(p1_capacity_tactic["http_requests"]),
        "P1_CAP_GAIN": _number(performance["capacity_throughput_gain_pct"]),
        "P1_HTTP_REDUCTION": _number(performance["sla_http_request_reduction_pct"]),
        "P1_RESULT": "cumple" if p1_pass else "no cumple todavía",
        "D1_FAILURE_AT": str(availability["injected_failure_after_requests"]),
        "D1_REQUESTS": str(d1_tactic["requests"]),
        "D1_BASE_SUCCESS": str(d1_base["successful"]),
        "D1_TACTIC_SUCCESS": str(d1_tactic["successful"]),
        "D1_BASE_AVAIL": _number(d1_base["availability_pct"]),
        "D1_TACTIC_AVAIL": _number(d1_tactic["availability_pct"]),
        "D1_FAILOVER": _number(d1_tactic["recovery_ms"]),
        "D1_BASE_LOST": str(d1_base["confirmed_transactions_lost"]),
        "D1_TACTIC_LOST": str(d1_tactic["confirmed_transactions_lost"]),
        "D1_RESULT": "cumple" if d1_pass else "no cumple todavía",
        "TRADEOFF_SAMPLE": str(tradeoff["sample_size_per_variant"]),
        "TRADEOFF_BASE_MEAN": _number(trade_base["mean_latency_ms"]),
        "TRADEOFF_TACTIC_MEAN": _number(trade_tactic["mean_latency_ms"]),
        "TRADEOFF_BASE_P95": _number(trade_base["p95_latency_ms"]),
        "TRADEOFF_TACTIC_P95": _number(trade_tactic["p95_latency_ms"]),
        "TRADEOFF_INCREASE_MS": _number(tradeoff["p95_increase_ms"]),
        "TRADEOFF_INCREASE_PCT": _number(tradeoff["p95_increase_pct"]),
        "TRADEOFF_ACCEPTABLE": "aceptable" if tradeoff["cost_acceptable_for_v5"] else "no aceptable",
        "TRADEOFF_LIMIT_TEXT": "permanece bajo" if tradeoff["cost_acceptable_for_v5"] else "supera",
        "VERIFY_S1_MEASURE": (
            f"{s1_tactic['attacks_blocked']}/{s1_tactic['attempts']}; "
            f"{s1_tactic['fraudulent_discounts']} descuentos; "
            f"máx. {_number(s1_tactic['maximum_reaction_ms'])} ms"
        ),
        "VERIFY_S1": _yes_no(s1_pass),
        "VERIFY_S1_NOTE": "Ejecución completa." if s1_full else "La muestra rápida no contiene 1.000 ataques.",
        "VERIFY_P1_MEASURE": (
            f"{_number(p1_tactic['throughput_per_min'])} val/min; "
            f"p95 {_number(p1_tactic['p95_latency_ms'])} ms; "
            f"{p1_tactic['unprocessed']} sin procesar; CPU {_number(cpu_tactic)} %"
        ),
        "VERIFY_P1": _yes_no(p1_pass),
        "VERIFY_P1_NOTE": "Ejecución completa." if p1_full else "La muestra rápida no contiene 6.000 validaciones.",
        "VERIFY_D1_MEASURE": (
            f"{_number(d1_tactic['recovery_ms'])} ms; "
            f"{d1_tactic['confirmed_transactions_lost']} confirmadas perdidas"
        ),
        "VERIFY_D1": _yes_no(d1_pass),
    }

    report = TEMPLATE.read_text(encoding="utf-8")
    for key, value in replacements.items():
        report = report.replace("{{" + key + "}}", value)
    if "{{" in report:
        raise SystemExit("The report template contains unresolved placeholders")
    OUTPUT.write_text(report, encoding="utf-8")
    print(f"Generated {OUTPUT}")


if __name__ == "__main__":
    main()
