import json
import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import analyze_manual_recording
import context_service
import route_template
import telemetry_ui


def world(x, y, plane=0):
    return {"worldX": x, "worldY": y, "plane": plane}


def segment(index, segment_type, label, *, option=None, target=None, quality=None, post="movement", result="success", start=None, end=None):
    start = start or world(3200 + index, 3200 + index, 0)
    end = end or world(3201 + index, 3201 + index, 0)
    return {
        "segmentIndex": index,
        "segmentType": segment_type,
        "label": label,
        "startWorld": start,
        "endWorld": end,
        "startPlane": start.get("plane"),
        "endPlane": end.get("plane"),
        "primaryAction": {"option": option, "target": target, "targetQuality": quality},
        "postcondition": {"type": post, "result": result},
        "evidenceRefs": [f"raw_step_{index:03d}"],
        "confidence": 0.9,
        "warnings": [],
    }


def lifecycle(route="Bank_to_Woodcutting_area", *, end_area="woodcutting_area", route_segments=None, review=None):
    segments = route_segments or [
        segment(1, "area_start", "Start: bank_area", post="area_start", start=world(3208, 3220, 2), end=world(3208, 3220, 2)),
        segment(2, "walk_segment", "Walk", option="Walk", post="movement", start=world(3208, 3220, 2), end=world(3205, 3209, 2)),
        segment(3, "stair_transition", "Climb-down Staircase", option="Climb-down", target="Staircase", post="plane_change", start=world(3205, 3209, 2), end=world(3206, 3208, 0)),
        segment(4, "door_transition", "Open Door", option="Open", target="Door", quality="strong", post="movement", start=world(3198, 3218, 0), end=world(3196, 3236, 0)),
        segment(5, "area_arrival", f"Arrive: {end_area}", post="area_arrival", start=world(3193, 3243, 0), end=world(3193, 3243, 0)),
    ]
    return {
        "schema": "traversal_lifecycle.v1",
        "status": "PASS",
        "routeName": route,
        "recordingPath": "synthetic",
        "start": {"areaLabel": "bank_area", "world": world(3208, 3220, 2), "plane": 2},
        "end": {"areaLabel": end_area, "world": world(3193, 3243, 0), "plane": 0},
        "routeSegments": segments,
        "reviewEvidence": review or [],
    }


def reverse_lifecycle(*, route="woodcutting_area_to_bank", route_segments=None, review=None):
    segments = route_segments or [
        segment(1, "area_start", "Start: woodcutting_area", post="area_start", start=world(3195, 3243, 0), end=world(3195, 3243, 0)),
        segment(2, "walk_segment", "Walk", option="Walk", post="movement", start=world(3195, 3243, 0), end=world(3205, 3209, 0)),
        segment(3, "stair_transition", "Climb-up Staircase", option="Climb-up", target="Staircase", post="plane_change", start=world(3205, 3209, 0), end=world(3205, 3209, 2)),
        segment(4, "walk_segment", "Walk", option="Walk", post="movement", start=world(3205, 3209, 2), end=world(3208, 3220, 2)),
        segment(5, "area_arrival", "Arrive: bank_area", post="area_arrival", start=world(3208, 3220, 2), end=world(3208, 3220, 2)),
    ]
    return {
        "schema": "traversal_lifecycle.v1",
        "status": "PASS",
        "routeName": route,
        "recordingPath": "synthetic_reverse",
        "start": {"areaLabel": "woodcutting_area", "world": world(3195, 3243, 0), "plane": 0},
        "end": {"areaLabel": "bank_area", "world": world(3208, 3220, 2), "plane": 2},
        "routeSegments": segments,
        "reviewEvidence": review or [],
    }


def navigation_variant_lifecycle(*, end_area="woodcutting_area", quality="strong", distance=10.0, extras=False):
    base = lifecycle(end_area=end_area)
    segments = list(base["routeSegments"])
    segments[3] = segment(
        4,
        "walk_segment",
        "Walk here Large door",
        option="Walk here",
        target="Large door",
        quality=quality,
        post="movement",
        start=world(3198, 3218, 0),
        end=world(3198 + int(distance), 3218, 0),
    )
    if extras:
        segments.insert(4, segment(40, "stair_transition", "Climb-up Staircase", option="Climb-up", target="Staircase", quality="strong", result="partial", start=world(3193, 3240, 0), end=world(3193, 3243, 0)))
    return lifecycle(end_area=end_area, route_segments=segments)


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")


def snap(elapsed, tick, x, y, plane, *, objects=None):
    return {
        "event_type": "source_snapshot",
        "elapsed_seconds": elapsed,
        "latest_tick": tick,
        "high_value_fields": {
            "latest_tick": tick,
            "player": {"worldPoint": {"worldX": x, "worldY": y, "plane": plane}},
            "nearby_objects": objects or [],
            "route_objects": objects or [],
        },
    }


def obj(name, action):
    return {"effectiveName": name, "effectiveActions": [action], "worldPoint": {"worldX": 3200, "worldY": 3200, "plane": 0}}


class RouteTemplateTest(unittest.TestCase):
    def test_resolver_resolves_absolute_template_path(self):
        path = route_template.default_template_path()
        resolution = route_template.resolve_route_template(path)
        self.assertEqual(resolution["status"], "PASS")
        self.assertEqual(Path(resolution["resolvedPath"]), path)
        self.assertEqual(resolution["routeName"], "Bank_to_Woodcutting_area")
        self.assertEqual(resolution["templateRevision"], 3)

    def test_resolver_resolves_relative_template_path(self):
        resolution = route_template.resolve_route_template(r"route_templates\Bank_to_Woodcutting_area.route_template.json")
        self.assertEqual(resolution["status"], "PASS")
        self.assertTrue(str(resolution["resolvedPath"]).endswith("Bank_to_Woodcutting_area.route_template.json"))

    def test_resolver_resolves_template_basename(self):
        resolution = route_template.resolve_route_template("Bank_to_Woodcutting_area.route_template.json")
        self.assertEqual(resolution["status"], "PASS")
        self.assertEqual(resolution["routeName"], "Bank_to_Woodcutting_area")

    def test_resolver_resolves_route_name(self):
        resolution = route_template.resolve_route_template("Bank_to_Woodcutting_area")
        self.assertEqual(resolution["status"], "PASS")
        self.assertEqual(resolution["templateRevision"], 3)

    def test_find_template_for_route_name_finds_forward_template(self):
        found = route_template.find_template_for_route_name("Bank_to_Woodcutting_area")
        self.assertIsNotNone(found)
        self.assertEqual(found["routeName"], "Bank_to_Woodcutting_area")

    def test_find_template_for_start_end_finds_forward_template(self):
        found = route_template.find_template_for_start_end("bank_area", "woodcutting_area")
        self.assertIsNotNone(found)
        self.assertEqual(found["routeName"], "Bank_to_Woodcutting_area")

    def test_reverse_start_end_finds_reverse_template(self):
        found = route_template.find_template_for_start_end("woodcutting_area", "bank_area")
        self.assertIsNotNone(found)
        self.assertEqual(found["routeName"], "woodcutting_area_to_bank")

    def test_resolver_fails_explicit_unknown_template(self):
        resolution = route_template.resolve_route_template("missing_route_template_for_unit_test")
        self.assertEqual(resolution["status"], "FAIL")
        self.assertFalse(resolution["exists"])

    def test_extracts_template_from_successful_route_segments(self):
        template = route_template.extract_template(lifecycle(), created_at_utc="2026-06-06T00:00:00Z")
        self.assertEqual(template["schema"], "route_template.v1")
        self.assertEqual(template["routeName"], "Bank_to_Woodcutting_area")
        self.assertEqual(template["start"]["areaLabel"], "bank_area")
        self.assertEqual(template["end"]["areaLabel"], "woodcutting_area")
        self.assertEqual(template["templateRevision"], 3)
        self.assertEqual(len(template["segments"]), 4)
        self.assertNotIn("Open Door", [segment["label"] for segment in template["segments"]])
        self.assertTrue(any(segment["label"] == "Open Door" and segment["routeRole"] == "navigation_support" for segment in template["optionalSegments"]))

    def test_review_evidence_does_not_become_required_segment(self):
        template = route_template.extract_template(lifecycle(review=[{"evidenceId": "raw_step_999", "type": "object_action"}]))
        self.assertEqual(len(template["reviewEvidenceNotes"]), 1)
        self.assertTrue(all(segment["label"] != "raw_step_999" for segment in template["segments"]))

    def test_identical_route_compares_pass(self):
        life = lifecycle()
        template = route_template.extract_template(life)
        comparison = route_template.compare_template(template, life)
        self.assertEqual(comparison["status"], "PASS")
        self.assertEqual(comparison["statusReason"], "PASS_BASE_TEMPLATE")
        self.assertEqual(comparison["matchedSegmentCount"], comparison["requiredSegmentCount"])
        self.assertGreaterEqual(comparison["score"], 0.95)

    def test_walk_here_large_door_is_navigation_support_not_missing_door(self):
        template = route_template.extract_template(lifecycle())
        comparison = route_template.compare_template(template, navigation_variant_lifecycle(extras=True))
        self.assertEqual(comparison["status"], "PASS")
        self.assertEqual(comparison["statusReason"], "PASS_BASE_TEMPLATE")
        self.assertFalse(comparison["validUnregisteredVariant"])
        self.assertEqual(len(comparison["missingSegments"]), 0)
        self.assertEqual(len(comparison["navigationSupportSubstitutions"]), 0)
        self.assertEqual(len(comparison["navigationSupportEvidence"]), 1)
        self.assertGreaterEqual(len(comparison["allowedExtraSegments"]), 1)

    def test_walk_here_large_door_no_longer_needs_registered_variant(self):
        template = route_template.extract_template(lifecycle())
        variant_life = navigation_variant_lifecycle(extras=True)
        comparison = route_template.compare_template(template, variant_life)
        variant = route_template.extract_variant(template, variant_life, comparison, variant_name="walk_here_large_door")
        template["variants"] = [variant]
        registered = route_template.compare_template(template, variant_life)
        self.assertEqual(registered["status"], "PASS")
        self.assertEqual(registered["statusReason"], "PASS_BASE_TEMPLATE")
        self.assertIsNone(registered["matchedVariantName"])
        self.assertEqual(len(registered["missingSegments"]), 0)
        self.assertEqual(len(variant["segmentOverrides"]), 0)

    def test_weak_walk_here_large_door_still_does_not_make_door_missing(self):
        template = route_template.extract_template(lifecycle())
        weak_move = navigation_variant_lifecycle(distance=1.0)
        comparison = route_template.compare_template(template, weak_move)
        self.assertEqual(comparison["status"], "PASS")
        self.assertEqual(comparison["statusReason"], "PASS_BASE_TEMPLATE")
        self.assertEqual(len(comparison["missingSegments"]), 0)

    def test_walk_here_large_door_does_not_override_wrong_endpoint(self):
        template = route_template.extract_template(lifecycle())
        comparison = route_template.compare_template(template, navigation_variant_lifecycle(end_area="bank_area"))
        self.assertEqual(comparison["status"], "FAIL")
        self.assertEqual(comparison["statusReason"], "FAIL_WRONG_ENDPOINT")

    def test_reverse_route_auto_selects_reverse_template(self):
        selection = route_template.resolve_template_auto(reverse_lifecycle())
        self.assertEqual(selection["status"], "PASS")
        self.assertEqual(selection["selectedTemplateRouteName"], "woodcutting_area_to_bank")
        comparison = route_template.compare_template(selection["template"], reverse_lifecycle())
        self.assertEqual(comparison["status"], "PASS")
        self.assertEqual(comparison["statusReason"], "PASS_BASE_TEMPLATE")

    def test_reverse_route_does_not_auto_compare_to_forward_template(self):
        selection = route_template.resolve_template_auto(reverse_lifecycle())
        self.assertNotEqual(selection["selectedTemplateRouteName"], "Bank_to_Woodcutting_area")

    def test_explicit_wrong_direction_template_reports_direction_mismatch(self):
        forward = route_template.extract_template(lifecycle())
        comparison = route_template.compare_template(forward, reverse_lifecycle())
        self.assertEqual(comparison["status"], "FAIL")
        self.assertTrue(comparison["routeTemplateDirectionMismatch"])
        self.assertIn("route_template_direction_mismatch", json.dumps(comparison))

    def test_untemplated_route_suggests_template_name(self):
        life = reverse_lifecycle(route="new_area_to_other_area")
        life["start"]["areaLabel"] = "new_area"
        life["end"]["areaLabel"] = "other_area"
        selection = route_template.resolve_template_auto(life)
        self.assertEqual(selection["status"], "WARN")
        self.assertTrue(selection["untemplatedRoute"])
        self.assertEqual(selection["suggestedTemplateName"], "new_area_to_other_area")

    def test_deposit_box_review_evidence_not_required_in_reverse_template(self):
        life = reverse_lifecycle(review=[{"evidenceId": "raw_step_004", "type": "bank_interaction", "action": "Deposit", "targetName": "Bank Deposit Box"}])
        template = route_template.extract_template(life)
        self.assertEqual(template["routeName"], "woodcutting_area_to_bank")
        self.assertEqual(len(template["segments"]), 5)
        self.assertFalse(any(_segment.get("primaryAction", {}).get("target") == "Bank Deposit Box" for _segment in template["segments"]))
        self.assertEqual(template["reviewEvidenceNotes"][0]["targetName"], "Bank Deposit Box")

    def test_reverse_trapdoor_variant_satisfies_stair_transition(self):
        template = route_template.extract_template(reverse_lifecycle())
        template["variants"] = [
            {
                "schema": "route_template_variant.v1",
                "variantName": "trapdoor_climb_down_to_bank",
                "routeName": "woodcutting_area_to_bank",
                "segmentOverrides": [
                    {
                        "baseSegmentIndex": 3,
                        "baseSegmentLabel": "Climb-up Staircase",
                        "allowedAlternatives": [
                            {
                                "segmentType": "door_transition",
                                "primaryAction": {"option": "Climb-down", "target": "Trapdoor"},
                                "satisfiesBaseSegment": True,
                                "requiresPostcondition": {"type": "plane_change"},
                                "qualityRequirements": {"minTargetQuality": None},
                            }
                        ],
                    }
                ],
            }
        ]
        life = reverse_lifecycle()
        life["routeSegments"][2] = segment(
            3,
            "door_transition",
            "Climb-down Trapdoor",
            option="Climb-down",
            target="Trapdoor",
            post="plane_change",
            start=world(3205, 3209, 0),
            end=world(3205, 3209, 2),
        )
        comparison = route_template.compare_template(template, life)
        self.assertEqual(comparison["status"], "PASS")
        self.assertEqual(comparison["statusReason"], "PASS_REGISTERED_VARIANT")
        self.assertEqual(comparison["matchedVariantName"], "trapdoor_climb_down_to_bank")

    def test_minimap_style_option_can_satisfy_walk_segment(self):
        life = lifecycle()
        template = route_template.extract_template(life)
        route_segments = list(life["routeSegments"])
        route_segments[1] = segment(2, "walk_segment", "Minimap walk", option="minimap_click", post="movement", start=world(3208, 3220, 2), end=world(3205, 3209, 2))
        comparison = route_template.compare_template(template, lifecycle(route_segments=route_segments))
        self.assertNotIn("expected option Walk", json.dumps(comparison))
        self.assertEqual(comparison["status"], "PASS")

    def test_harmless_navigation_extra_is_allowed(self):
        life = lifecycle()
        template = route_template.extract_template(life)
        route_segments = list(life["routeSegments"])
        route_segments.insert(4, segment(44, "walk_segment", "Minimap nudge", option="minimap_click", post="movement", start=world(3194, 3242, 0), end=world(3193, 3243, 0)))
        comparison = route_template.compare_template(template, lifecycle(route_segments=route_segments))
        self.assertEqual(comparison["status"], "PASS")
        self.assertEqual(len(comparison["allowedExtraSegments"]), 1)

    def test_missing_required_segment_warns(self):
        life = lifecycle()
        template = route_template.extract_template(life)
        missing = lifecycle(route_segments=[life["routeSegments"][0], life["routeSegments"][1], life["routeSegments"][-1]])
        comparison = route_template.compare_template(template, missing)
        self.assertEqual(comparison["status"], "WARN")
        self.assertGreater(len(comparison["missingSegments"]), 0)

    def test_wrong_end_area_fails(self):
        life = lifecycle()
        template = route_template.extract_template(life)
        wrong = lifecycle(end_area="bank_area")
        comparison = route_template.compare_template(template, wrong)
        self.assertEqual(comparison["status"], "FAIL")
        self.assertFalse(comparison["endAreaMatched"])

    def test_out_of_order_strict_segment_warns(self):
        life = lifecycle()
        template = route_template.extract_template(life)
        reordered = lifecycle(route_segments=[life["routeSegments"][0], life["routeSegments"][3], life["routeSegments"][2], life["routeSegments"][4]])
        comparison = route_template.compare_template(template, reordered)
        self.assertEqual(comparison["status"], "WARN")
        self.assertTrue(comparison["outOfOrderSegments"] or comparison["missingSegments"])

    def test_weak_target_quality_below_requirement_warns(self):
        life = lifecycle(route="DoorRoute")
        template = route_template.extract_template(life)
        weak_life = lifecycle(route="DoorRoute")
        weak_life["routeSegments"][3]["primaryAction"]["targetQuality"] = "weak"
        comparison = route_template.compare_template(template, weak_life)
        self.assertEqual(comparison["status"], "WARN")
        self.assertGreater(len(comparison["weakSegments"]), 0)

    def test_extra_review_evidence_does_not_fail_comparison(self):
        life = lifecycle(review=[{"evidenceId": "raw_review"}])
        template = route_template.extract_template(lifecycle())
        comparison = route_template.compare_template(template, life)
        self.assertEqual(comparison["status"], "PASS")
        self.assertEqual(comparison["reviewEvidenceCount"], 1)

    def test_optional_context_segment_does_not_become_extra(self):
        life = lifecycle()
        life["routeSegments"].insert(1, segment(99, "bank_context", "Bank context", option="Bank", target="Bank booth"))
        template = route_template.extract_template(life)
        comparison = route_template.compare_template(template, life)
        self.assertEqual(comparison["status"], "PASS")
        self.assertGreaterEqual(comparison["optionalSegmentCount"], 2)
        self.assertTrue(any(segment["label"] == "Bank context" for segment in template["optionalSegments"]))
        self.assertEqual(len(comparison["extraSegments"]), 0)

    def test_walk_segment_flexible_matching_works(self):
        life = lifecycle()
        template = route_template.extract_template(life)
        without_first_walk = lifecycle(route_segments=[life["routeSegments"][0], life["routeSegments"][2], life["routeSegments"][3], life["routeSegments"][4]])
        comparison = route_template.compare_template(template, without_first_walk)
        self.assertNotEqual(comparison["status"], "FAIL")

    def test_climb_requires_plane_change_postcondition(self):
        life = lifecycle()
        template = route_template.extract_template(life)
        bad = lifecycle()
        bad["routeSegments"][2]["postcondition"] = {"type": "movement", "result": "success"}
        comparison = route_template.compare_template(template, bad)
        self.assertEqual(comparison["status"], "WARN")
        self.assertTrue(comparison["weakSegments"] or comparison["missingSegments"])

    def test_bank_route_door_open_does_not_require_movement_or_route_progress(self):
        life = lifecycle()
        template = route_template.extract_template(life)
        bad = lifecycle()
        bad["routeSegments"][3]["postcondition"] = {"type": "none", "result": "partial"}
        comparison = route_template.compare_template(template, bad)
        self.assertEqual(comparison["status"], "PASS")
        self.assertEqual(comparison["statusReason"], "PASS_BASE_TEMPLATE")
        self.assertEqual(len(comparison["missingSegments"]), 0)
        self.assertTrue(comparison["optionalSegmentMatches"])

    def test_analyzer_writes_route_template_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            recording = root / "recording"
            recording.mkdir()
            write_jsonl(recording / "events.jsonl", [
                snap(0, 1, 3208, 3220, 2, objects=[obj("Bank booth", "Bank")]),
                snap(2, 2, 3205, 3209, 2),
                snap(4, 3, 3193, 3243, 0, objects=[obj("Tree", "Chop down")]),
                snap(5, 4, 3194, 3244, 0, objects=[obj("Tree", "Chop down")]),
                snap(6, 5, 3195, 3245, 0, objects=[obj("Tree", "Chop down")]),
            ])
            out = root / "templates"
            code = analyze_manual_recording.main([str(recording), "--summary", "--schema-gap", "--traversal-lifecycle", "--group-traversal-steps", "--extract-route-template", "--route-template-out", str(out)])
            self.assertEqual(code, 0)
            self.assertTrue(any(out.glob("*.route_template.json")))

    def test_analyzer_writes_route_template_comparison_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            recording = root / "recording"
            recording.mkdir()
            write_jsonl(recording / "events.jsonl", [
                snap(0, 1, 3208, 3220, 2, objects=[obj("Bank booth", "Bank")]),
                snap(2, 2, 3205, 3209, 2),
                snap(4, 3, 3193, 3243, 0, objects=[obj("Tree", "Chop down")]),
                snap(5, 4, 3194, 3244, 0, objects=[obj("Tree", "Chop down")]),
                snap(6, 5, 3195, 3245, 0, objects=[obj("Tree", "Chop down")]),
            ])
            out = root / "templates"
            analyze_manual_recording.main([str(recording), "--summary", "--schema-gap", "--traversal-lifecycle", "--group-traversal-steps", "--extract-route-template", "--route-template-out", str(out)])
            template_path = next(out.glob("*.route_template.json"))
            analyze_manual_recording.main([str(recording), "--summary", "--schema-gap", "--traversal-lifecycle", "--group-traversal-steps", "--compare-route-template", str(template_path), "--print-route-template-comparison"])
            self.assertTrue((recording / "route_template_comparison.json").exists())
            comparison = json.loads((recording / "route_template_comparison.json").read_text(encoding="utf-8"))
            self.assertEqual(comparison["status"], "PASS")

    def test_context_summary_includes_comparison_compact_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            recording = root / "recording"
            recording.mkdir()
            life = lifecycle()
            comparison = route_template.compare_template(route_template.extract_template(life), life)
            (recording / "summary.json").write_text(json.dumps({"route_template_comparison": comparison}), encoding="utf-8")
            payload = context_service.recording_summary_payload("recording", root=root)
            self.assertEqual(payload["routeTemplateStatus"], "PASS")
            self.assertEqual(payload["missingSegmentCount"], 0)

    def test_context_summary_includes_corrected_semantics_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            recording = root / "recording"
            recording.mkdir()
            template = route_template.extract_template(lifecycle())
            variant_life = navigation_variant_lifecycle()
            comparison = route_template.compare_template(template, variant_life)
            (recording / "summary.json").write_text(json.dumps({"route_template_comparison": comparison}), encoding="utf-8")
            payload = context_service.recording_summary_payload("recording", root=root)
            self.assertEqual(payload["routeTemplateStatusReason"], "PASS_BASE_TEMPLATE")
            self.assertIsNone(payload["matchedVariantName"])
            self.assertEqual(payload["routeTemplateRevision"], 3)
            self.assertEqual(payload["navigationSupportEvidenceCount"], 1)

    def test_context_summary_includes_auto_selection_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            recording = root / "recording"
            recording.mkdir()
            life = reverse_lifecycle()
            selection = route_template.resolve_template_auto(life)
            comparison = route_template.compare_template(selection["template"], life)
            (recording / "summary.json").write_text(
                json.dumps(
                    {
                        "detectedRouteName": "woodcutting_area_to_bank",
                        "detectedStartArea": "woodcutting_area",
                        "detectedEndArea": "bank_area",
                        "routeTemplateAutoSelection": {key: value for key, value in selection.items() if key != "template"},
                        "routeTemplatePath": selection["selectedTemplate"],
                        "route_template_comparison": comparison,
                    }
                ),
                encoding="utf-8",
            )
            payload = context_service.recording_summary_payload("recording", root=root)
            self.assertEqual(payload["detectedRouteName"], "woodcutting_area_to_bank")
            self.assertFalse(payload["routeTemplateDirectionMismatch"])
            self.assertFalse(payload["untemplatedRoute"])

    def test_ui_check_commands_include_route_template_controls(self):
        config = telemetry_ui.default_config()
        config["route_template_path"] = "route_templates\\Bank_to_Woodcutting_area.route_template.json"
        command = telemetry_ui.build_compare_route_template_command(Path("recordings/test"), config)
        self.assertIn("--compare-route-template", command)
        extract = telemetry_ui.build_extract_route_template_command(Path("recordings/test"), config)
        self.assertIn("--extract-route-template", extract)
        register = telemetry_ui.build_register_route_variant_command(Path("recordings/test"), config)
        self.assertIn("--extract-route-variant", register)
        self.assertIn("--add-route-variant-to-template", register)


if __name__ == "__main__":
    unittest.main()
