from __future__ import annotations

import argparse
import json
from typing import Any

import task_policy
import task_state


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
        f"  process inventory analyzer runs: {'yes' if payload.get('processInventoryAnalyzerShouldRun') else 'no'}",
        f"  noActionEmitted: {str(payload.get('noActionEmitted')).lower()}",
    ]
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
