import sys
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

from input_control.action_proposal import build_action_proposal


def aim(x=100, y=120):
    return {"canvasX": x, "canvasY": y}


def bounds(x=10, y=20, width=30, height=40):
    return {"x": x, "y": y, "width": width, "height": height}


def status_for(
    *,
    phase="target_selected",
    active_intent="select_target",
    active_target=None,
    inventory_full=False,
    free_slots=15,
    service=None,
    pathing=None,
    bank_ui=None,
    bank_operation=None,
    close_bank=None,
    resource_return=None,
    overlay=None,
):
    active_target = active_target or {"targetName": "Oak tree", "classId": "tree", "aimPoint": aim(110, 130)}
    return {
        "brain": {
            "genericTaskState": {
                "phase": phase,
                "activeIntent": active_intent,
                "activeIntentTarget": active_target,
                "blockingConditions": [],
            },
            "inventoryContext": {"inventoryFull": inventory_full, "freeSlots": free_slots},
            "serviceContext": service or {},
            "pathingContext": pathing or {},
            "bankUiContext": bank_ui or {"bankOpen": False, "bankReadable": False, "bankPinOpen": False},
            "bankOperationContext": bank_operation or {},
            "closeBankContext": close_bank or {},
            "resourceReturnContext": resource_return or {},
            "intentOverlayContext": overlay or {"selectedMarker": active_target},
            "missingRequiredContextDomains": [],
            "warnings": [],
        }
    }


class ActionProposalTest(unittest.TestCase):
    def test_collecting_resource_target_proposes_select_resource_target(self):
        proposal = build_action_proposal(status_for())

        self.assertEqual(proposal.proposed_action, "select_resource_target")
        self.assertEqual(proposal.target_kind, "resource")
        self.assertEqual(proposal.target_name, "Oak tree")
        self.assertEqual(proposal.suggested_click_point, {"x": 110, "y": 130})
        self.assertEqual(proposal.status, "PASS")

    def test_service_ready_bank_closed_proposes_open_service(self):
        proposal = build_action_proposal(
            status_for(
                phase="service_available",
                active_intent="service_available",
                inventory_full=True,
                free_slots=0,
                active_target=None,
                service={
                    "serviceNeeded": True,
                    "serviceReady": True,
                    "bestServiceCandidate": {"targetName": "Bank booth", "classId": "bank_booth", "aimPoint": aim(220, 240)},
                },
                bank_ui={"bankOpen": False},
            )
        )

        self.assertEqual(proposal.proposed_action, "open_service")
        self.assertEqual(proposal.target_kind, "service")
        self.assertEqual(proposal.target_name, "Bank booth")
        self.assertEqual(proposal.suggested_click_point, {"x": 220, "y": 240})

    def test_inventory_full_with_service_path_needed_proposes_navigate_to_service(self):
        proposal = build_action_proposal(
            status_for(
                phase="needs_service",
                active_intent="needs_service",
                inventory_full=True,
                free_slots=0,
                active_target=None,
                service={
                    "serviceNeeded": True,
                    "serviceReady": False,
                    "bestServiceCandidate": {"targetName": "Bank booth", "classId": "bank_booth"},
                },
                pathing={
                    "pathingNeeded": True,
                    "pathCompleted": False,
                    "nextWaypointTile": {"worldX": 3201, "worldY": 3200, "plane": 0},
                    "nextWaypointAimPoint": aim(260, 280),
                },
            )
        )

        self.assertEqual(proposal.proposed_action, "navigate_to_service")
        self.assertEqual(proposal.target_kind, "path_tile")
        self.assertEqual(proposal.suggested_click_point, {"x": 260, "y": 280})

    def test_bank_readable_resources_held_deposit_inventory_available(self):
        proposal = build_action_proposal(
            status_for(
                phase="service_open",
                active_intent="bank_operation_pending",
                active_target=None,
                bank_ui={
                    "bankOpen": True,
                    "bankReadable": True,
                    "depositInventoryButtonVisible": True,
                    "depositInventoryButtonBounds": bounds(300, 400, 20, 10),
                },
                bank_operation={
                    "operationNeeded": True,
                    "operationType": "deposit_inventory",
                    "resourceItemsHeld": 18,
                    "depositInventoryAvailable": True,
                },
            )
        )

        self.assertEqual(proposal.proposed_action, "deposit_inventory")
        self.assertEqual(proposal.target_kind, "bank_ui")
        self.assertEqual(proposal.suggested_click_point, {"x": 310, "y": 405})

    def test_bank_readable_resources_held_without_deposit_inventory_deposit_resources(self):
        proposal = build_action_proposal(
            status_for(
                phase="service_open",
                active_intent="bank_operation_pending",
                active_target=None,
                bank_ui={"bankOpen": True, "bankReadable": True},
                bank_operation={
                    "operationNeeded": True,
                    "operationType": "deposit_resources",
                    "resourceItemsHeld": 3,
                    "depositInventoryAvailable": False,
                    "resourceItemSlotBounds": [bounds(500, 600, 28, 28)],
                },
            )
        )

        self.assertEqual(proposal.proposed_action, "deposit_resources")
        self.assertEqual(proposal.target_kind, "bank_ui")
        self.assertEqual(proposal.suggested_click_point, {"x": 514, "y": 614})

    def test_banking_complete_close_ready_proposes_close_bank_keyboard_first(self):
        proposal = build_action_proposal(
            status_for(
                phase="waiting_for_world_view",
                active_intent="close_service_context",
                active_target=None,
                bank_ui={"bankOpen": True, "bankReadable": True},
                bank_operation={"bankingComplete": True, "resourceItemsHeld": 0},
                close_bank={"closeBankNeeded": True, "closeBankReady": True, "keyboardClosePossible": True},
            )
        )

        self.assertEqual(proposal.proposed_action, "close_bank")
        self.assertEqual(proposal.key_action, {"type": "key_press", "key": "escape"})
        self.assertIsNone(proposal.suggested_click_point)

    def test_valid_return_destination_proposes_return_to_resource_area(self):
        proposal = build_action_proposal(
            status_for(
                phase="return_to_resource",
                active_intent="return_to_resource_area",
                active_target={"targetName": "Resource return", "classId": "resource_return", "aimPoint": aim(700, 710)},
                bank_ui={"bankOpen": False},
                bank_operation={"bankingComplete": True, "resourceItemsHeld": 0},
                resource_return={
                    "returnDestinationAvailable": True,
                    "returnDestinationTile": {"worldX": 3156, "worldY": 3237, "plane": 0},
                    "reason": "using_remembered_resource_area",
                },
            )
        )

        self.assertEqual(proposal.proposed_action, "return_to_resource_area")
        self.assertEqual(proposal.target_kind, "path_tile")
        self.assertEqual(proposal.suggested_click_point, {"x": 700, "y": 710})

    def test_missing_click_point_warns_and_prevents_execution(self):
        proposal = build_action_proposal(
            status_for(
                active_target={"targetName": "Oak tree", "classId": "tree"},
                overlay={"selectedMarker": {"targetName": "Oak tree", "classId": "tree"}},
            )
        )

        self.assertEqual(proposal.status, "WARN")
        self.assertEqual(proposal.proposed_action, "select_resource_target")
        self.assertIn("click_point", proposal.missing_capabilities)
        self.assertFalse(proposal.executable)


if __name__ == "__main__":
    unittest.main()
