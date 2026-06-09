# Decisions

| ID | Decision | Reason |
| --- | --- | --- |
| record_everything_default | Record Everything Simple Mode is the default workflow. | Record broadly first; analyzer decides what matters later. |
| no_required_arduino | Arduino is optional for recording and route monitoring. | Human recordings must still be useful without hardware. |
| map_only_default | map_only is the safe default when Arduino mapping evidence is enabled. | Avoid duplicate live Arduino clicks. |
| route_segments_primary | Route templates compare routeSegments, not raw clicks. | Raw clicks include support/review evidence and normal route variants. |
| bank_to_wc_rev3 | Door/Open is optional for Bank_to_Woodcutting_area revision 3. | Walk here Large door can be navigation support; required segments are start, walk, stair, walk, arrival. |
| bank_ui_direct_proof | Banking must consume direct bank_ui when available. | Inventory-only inference is useful but weaker than direct widget/container evidence. |
| promote_useful_data | Useful telemetry should not stay recorder-only when scripts need it. | Promote through analyzer, context_service, MCP where useful, and task_script_api. |
| human_click_profile_reference | Human click/camera behavior is a profile reference, not an execution shortcut. | Use it to shape tolerances and recommendations while preserving existing guarded input paths. |
| human_click_planning_advisory | Human-profile click planning is dry-run/advisory until replay validation proves it. | Target readiness, hover/menu proof, and task state gates must remain stronger than a profile offset. |
| knowledge_repo_owned | Project state belongs in docs/knowledge and telemetry-viewer/knowledge_base. | Do not rely on chat memory alone. |

## Manual Notes

<!-- BEGIN MANUAL NOTES -->
<!-- END MANUAL NOTES -->
