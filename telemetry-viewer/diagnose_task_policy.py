from __future__ import annotations

import argparse
import json
from typing import Any

import task_policy
import task_state
from analyzers import process_inventory_analyzer, service_analyzer
from analyzers.live_state import InventoryContext


def bool_arg(value: str) -> bool:
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "on"}:
        return True
    if text in {"false", "0", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError("expected true or false")


def sample_target(task: str) -> dict[str, Any]:
    if task == "combat":
        return {"targetKey": "combat-target", "name": "Target", "id": 1, "directReachability": "reachable"}
    return {"targetKey": "tree-target", "name": "Tree", "id": 1278, "directReachability": "reachable"}


def policy_label(policy: task_policy.TaskPolicy) -> str:
    disposition = task_policy.enum_value(policy.resourceDisposition)
    strategy = task_policy.enum_value(policy.fullInventoryStrategy)
    if strategy == "needs_service" and disposition == "bank":
        return "bank resources"
    if strategy == "process_inventory" and disposition == "burn":
        return "burn resources"
    if strategy == "process_inventory" and disposition == "drop":
        return "drop resources"
    if strategy == "continue_task":
        return "continue task"
    if strategy == "observe_only":
        return "observe only"
    return str(strategy or "unknown")


def build_policy_diagnostic(
    *,
    policy_name: str,
    task: str,
    inventory_full: bool,
    resource_count: int | None,
    goal_count: int | None,
    service_candidates: list[dict[str, Any]] | None = None,
    inventory_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    policy = task_policy.resolve_task_policy(policy_name, task=task, profile=task)
    best = sample_target(task)
    decision = {
        "task": task,
        "phase": "inventory_full" if inventory_full else "target_available",
        "confidence": 1.0,
        "blockingConditions": ["inventory is full"] if inventory_full else [],
        "currentContextSummary": {"bestTarget": best},
        "goalProgress": {
            "currentHeldCount": resource_count,
            "goalCount": goal_count,
        },
        "taskPolicy": policy.to_dict(),
        "noActionEmitted": True,
    }
    generic = task_state.from_brain_decision(decision, policy=policy).to_dict()
    inventory = {"items": inventory_items} if inventory_items is not None else {}
    inventory_context = InventoryContext(inventory=inventory, progress={"currentHeldCount": resource_count})
    service_context = service_analyzer.analyze_service_context(
        policy,
        candidates=service_candidates or [],
        source_tick=None,
    )
    process_context = process_inventory_analyzer.analyze_process_inventory_context(
        policy,
        inventory_context,
        source_tick=None,
    )
    target_should_clear = best is not None and generic.get("activeIntentTarget") is None
    strategy = task_policy.enum_value(policy.fullInventoryStrategy)
    return {
        "schema": "task_policy_diagnostic.v1",
        "selectedPolicy": policy.name,
        "task": task,
        "inventoryFull": bool(inventory_full),
        "resourceCount": resource_count,
        "goalCount": goal_count,
        "inventoryExpectation": task_policy.enum_value(policy.inventoryExpectation),
        "fullInventoryStrategy": strategy,
        "resourceDisposition": task_policy.enum_value(policy.resourceDisposition),
        "serviceTypeNeeded": policy.serviceTypeNeeded if inventory_full and strategy == "needs_service" else None,
        "processTypeNeeded": policy.processTypeNeeded if inventory_full and strategy == "process_inventory" else None,
        "expectedGenericPhase": generic.get("phase"),
        "expectedActiveIntent": generic.get("activeIntent"),
        "targetShouldBeCleared": bool(target_should_clear),
        "serviceAnalyzerShouldRun": bool(inventory_full and strategy == "needs_service"),
        "processInventoryAnalyzerShouldRun": bool(inventory_full and strategy == "process_inventory"),
        "serviceCandidateExists": bool(service_context.best_service_candidate),
        "serviceContext": service_context.to_dict(),
        "processInventoryContext": process_context.to_dict(),
        "policyLabel": policy_label(policy),
        "noActionEmitted": True,
    }


def format_human(payload: dict[str, Any]) -> str:
    lines = [
        "TASK POLICY DIAGNOSTIC",
        "",
        f"Policy: {payload.get('selectedPolicy')}",
        f"Task: {payload.get('task')}",
        f"Inventory full: {str(payload.get('inventoryFull')).lower()}",
        f"Resource count: {payload.get('resourceCount')}",
        f"Goal count: {payload.get('goalCount')}",
        "",
        "Policy:",
        f"  inventory expectation: {payload.get('inventoryExpectation')}",
        f"  full inventory strategy: {payload.get('fullInventoryStrategy')}",
        f"  resource disposition: {payload.get('resourceDisposition')}",
        f"  service type needed: {payload.get('serviceTypeNeeded') or 'none'}",
        f"  process type needed: {payload.get('processTypeNeeded') or 'none'}",
        "",
        "Expected generic state:",
        f"  phase: {payload.get('expectedGenericPhase')}",
        f"  active intent: {payload.get('expectedActiveIntent')}",
        f"  target cleared: {'yes' if payload.get('targetShouldBeCleared') else 'no'}",
        f"  service analyzer runs: {'yes' if payload.get('serviceAnalyzerShouldRun') else 'no'}",
        f"  service candidate exists: {'yes' if payload.get('serviceCandidateExists') else 'no'}",
        f"  process inventory analyzer runs: {'yes' if payload.get('processInventoryAnalyzerShouldRun') else 'no'}",
        f"  noActionEmitted: {str(payload.get('noActionEmitted')).lower()}",
    ]
    service_context = payload.get("serviceContext") if isinstance(payload.get("serviceContext"), dict) else {}
    if payload.get("serviceAnalyzerShouldRun"):
        candidate = service_context.get("bestServiceCandidate") if isinstance(service_context.get("bestServiceCandidate"), dict) else {}
        lines.extend(
            [
                "",
                "Service context:",
                f"  needed: {'yes' if service_context.get('serviceNeeded') else 'no'}",
                f"  type: {service_context.get('serviceTypeNeeded') or 'none'}",
                f"  candidates: {service_context.get('candidateCount')}",
                f"  candidates by type: {service_context.get('candidateCountsByType') or {}}",
                f"  best: {candidate.get('targetName') or candidate.get('name') or candidate.get('classId') or 'none'}",
                f"  nearest: {(service_context.get('nearestServiceCandidate') or {}).get('targetName') or (service_context.get('nearestServiceCandidate') or {}).get('name') or (service_context.get('nearestServiceCandidate') or {}).get('classId') or 'none'}",
                f"  reachable: {service_context.get('reachableCount')}",
                f"  unknown reachability: {service_context.get('unknownReachabilityCount')}",
            ]
        )
    process_context = payload.get("processInventoryContext") if isinstance(payload.get("processInventoryContext"), dict) else {}
    if payload.get("processInventoryAnalyzerShouldRun"):
        lines.extend(
            [
                "",
                "Process inventory context:",
                f"  needed: {'yes' if process_context.get('processRequired') else 'no'}",
                f"  type: {process_context.get('processTypeNeeded') or 'none'}",
                f"  disposition: {process_context.get('resourceDisposition') or 'none'}",
                f"  resources available: {'yes' if process_context.get('resourcesAvailable') else 'no'}",
                f"  tinderbox: {process_context.get('tinderboxStatus') or 'not_required'}",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only task policy diagnostic. Prints to stdout only.")
    parser.add_argument("--policy", choices=task_policy.policy_names(), default="woodcutting_bank")
    parser.add_argument("--task", default="woodcutting")
    parser.add_argument("--inventory-full", type=bool_arg, default=False)
    parser.add_argument("--resource-count", type=int, default=0)
    parser.add_argument("--goal-count", type=int)
    parser.add_argument("--json", action="store_true", help="Print JSON to stdout only.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload = build_policy_diagnostic(
        policy_name=args.policy,
        task=args.task,
        inventory_full=args.inventory_full,
        resource_count=args.resource_count,
        goal_count=args.goal_count,
    )
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=False))
    else:
        print(format_human(payload), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
