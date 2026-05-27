from __future__ import annotations

import json
import re
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "input_integrity_status.v1"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def wall_time_millis() -> int:
    return int(time.time() * 1000)


def normalize_usb_token(value: str | None, prefix: str) -> str | None:
    if value is None:
        return None
    text = str(value).strip().upper()
    if not text:
        return None
    expected_prefix = prefix.upper() + "_"
    if text.startswith(expected_prefix):
        return text
    if text.startswith(prefix.upper()):
        return text
    return expected_prefix + text


def read_status_payload(source: str | Path | None) -> dict[str, Any] | None:
    if not source:
        return None
    text_source = str(source)
    try:
        if text_source.lower().startswith(("http://", "https://")):
            with urllib.request.urlopen(text_source, timeout=1.0) as response:
                text = response.read().decode("utf-8")
        else:
            text = Path(text_source).read_text(encoding="utf-8")
        decoded = json.loads(text)
    except Exception:  # noqa: BLE001
        return None
    return decoded if isinstance(decoded, dict) else None


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "pass", "ok", "present", "matched"}:
            return True
        if lowered in {"false", "0", "no", "fail", "missing", "mismatch"}:
            return False
    return None


def _first_bool(payload: dict[str, Any], *keys: str) -> bool | None:
    for key in keys:
        value = payload.get(key)
        parsed = _as_bool(value)
        if parsed is not None:
            return parsed
    return None


def _first_int(payload: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool):
            continue
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            pass
    return 0


def _first_nullable_int(payload: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool):
            continue
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            pass
    return None


def _payload_generated_ms(payload: dict[str, Any]) -> int | None:
    for key in ("generatedAtMillis", "generatedAtMs", "wallTimeMillis", "wallTimeMs"):
        value = _first_nullable_int(payload, key)
        if value is not None:
            return value
    stamp = payload.get("generatedAtUtc") or payload.get("generatedAt")
    if isinstance(stamp, str) and stamp.strip():
        text = stamp.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return int(parsed.timestamp() * 1000)
        except ValueError:
            return None
    return None


def _contains_token(payload: dict[str, Any], token: str | None) -> bool | None:
    if not token:
        return None
    try:
        text = json.dumps(payload, default=str).upper()
    except TypeError:
        text = str(payload).upper()
    return token.upper() in text


def _raw_monitor_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    if payload.get("schema") == SCHEMA:
        return payload
    for key in ("inputIntegrityStatus", "inputIntegrity", "arduino"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            return nested
    return payload


def _nested_or_raw(raw: dict[str, Any], key: str) -> dict[str, Any]:
    nested = raw.get(key)
    return nested if isinstance(nested, dict) else raw


def build_firmware_safety(firmware: dict[str, Any] | None = None) -> dict[str, Any]:
    firmware = _safe_dict(firmware)
    protocol = firmware.get("protocol")
    protocol_ok = protocol == "arduino_hid.v1"
    reset_safe = _as_bool(firmware.get("resetSafe"))
    stop_all = _as_bool(firmware.get("stopAll"))
    watchdog = _as_bool(firmware.get("watchdog"))
    watchdog_ms = _first_nullable_int(firmware, "watchdogMs")
    armed = _as_bool(firmware.get("armed"))
    keys_down = _first_int(firmware, "keysDown")
    buttons_down = _first_int(firmware, "mouseButtonsDown")
    blockers: list[str] = []
    warnings: list[str] = []
    if protocol not in (None, "arduino_hid.v1"):
        blockers.append("firmware_protocol_mismatch")
    elif protocol is None:
        warnings.append("firmware_protocol_unknown")
    for value, reason, unknown in (
        (reset_safe, "firmware_reset_not_safe", "firmware_reset_safety_unknown"),
        (stop_all, "firmware_stop_all_unavailable", "firmware_stop_all_unknown"),
        (watchdog, "firmware_watchdog_unavailable", "firmware_watchdog_unknown"),
    ):
        if value is False:
            blockers.append(reason)
        elif value is None:
            warnings.append(unknown)
    if watchdog_ms is not None and watchdog_ms <= 0:
        blockers.append("firmware_watchdog_invalid")
    if armed is True:
        warnings.append("firmware_still_armed")
    if keys_down > 0 or buttons_down > 0:
        blockers.append("firmware_reports_held_input")
    status = "FAIL" if blockers else "WARN" if warnings else "PASS"
    return {
        "schema": "firmware_safety.v1",
        "status": status,
        "armed": bool(armed) if armed is not None else None,
        "keysDown": keys_down,
        "mouseButtonsDown": buttons_down,
        "watchdogOk": bool(watchdog) and (watchdog_ms is None or watchdog_ms > 0),
        "watchdogMs": watchdog_ms,
        "stopAllAvailable": bool(stop_all),
        "resetSafe": bool(reset_safe),
        "protocolOk": bool(protocol_ok),
        "protocol": protocol,
        "blockers": blockers,
        "warnings": warnings,
    }


def build_vm_input_focus_safety(raw: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = _safe_dict(raw)
    focus = _safe_dict(raw.get("vmInputFocusSafety"))
    overlay = _safe_dict(raw.get("overlay"))
    blockers = [str(item) for item in focus.get("blockers", []) if item] if isinstance(focus.get("blockers"), list) else []
    warnings = [str(item) for item in focus.get("warnings", []) if item] if isinstance(focus.get("warnings"), list) else []
    post_state = str(focus.get("postTestInputState") or "not_evaluated")
    recovery = str(focus.get("postTestFocusRecovery") or "not_evaluated")
    if post_state in {"focus_may_be_captured", "overlay_may_be_blocking", "monitor_may_be_blocking", "vmware_capture_may_be_stuck"}:
        warnings.append(post_state)
    if recovery in {"FAIL", "failed"}:
        blockers.append("post_test_focus_recovery_failed")
    elif recovery in {"WARN", "unknown"}:
        warnings.append("post_test_focus_recovery_unknown")
    requested_status = str(focus.get("status") or "").upper()
    status = requested_status if requested_status in {"PASS", "WARN", "FAIL", "NOT_EVALUATED"} else None
    if status is None:
        status = "FAIL" if blockers else "WARN" if warnings else "NOT_EVALUATED"
    return {
        "schema": "vm_input_focus_safety.v1",
        "status": status,
        "overlayFocusable": focus.get("overlayFocusable", overlay.get("focusable")),
        "overlayClickThrough": focus.get("overlayClickThrough", overlay.get("clickThrough")),
        "overlayTopmost": focus.get("overlayTopmost", overlay.get("topmost")),
        "monitorWindowActive": focus.get("monitorWindowActive"),
        "foregroundWindowTitle": focus.get("foregroundWindowTitle"),
        "foregroundProcess": focus.get("foregroundProcess"),
        "postTestFocusTarget": focus.get("postTestFocusTarget"),
        "postTestFocusRecovery": recovery,
        "postTestInputState": post_state,
        "blockers": list(dict.fromkeys(blockers)),
        "warnings": list(dict.fromkeys(warnings)),
    }


def extract_usb_vid_pid(text: str | None) -> dict[str, str | None]:
    haystack = str(text or "").upper()
    vid_match = re.search(r"VID[_&:]?([0-9A-F]{4})", haystack)
    pid_match = re.search(r"PID[_&:]?([0-9A-F]{4})", haystack)
    return {
        "vid": f"VID_{vid_match.group(1)}" if vid_match else None,
        "pid": f"PID_{pid_match.group(1)}" if pid_match else None,
    }


def build_vmware_autoconnect_recommendation(
    *,
    sketch_vid: str | None = None,
    sketch_pid: str | None = None,
    bootloader_vid: str | None = None,
    bootloader_pid: str | None = None,
    broad_vid_ok: bool = False,
) -> dict[str, Any]:
    sketch = normalize_usb_token(sketch_vid, "VID") if sketch_vid else None
    sketch_pid_norm = normalize_usb_token(sketch_pid, "PID") if sketch_pid else None
    boot_vid = normalize_usb_token(bootloader_vid, "VID") if bootloader_vid else sketch
    boot_pid_norm = normalize_usb_token(bootloader_pid, "PID") if bootloader_pid else None
    lines: list[str] = []
    warnings: list[str] = []
    if sketch and sketch_pid_norm:
        lines.append(f'usb.autoConnect.device0 = "vid:{sketch[-4:]} pid:{sketch_pid_norm[-4:]}"')
    if boot_vid and boot_pid_norm and (boot_vid, boot_pid_norm) != (sketch, sketch_pid_norm):
        lines.append(f'usb.autoConnect.device{len(lines)} = "vid:{boot_vid[-4:]} pid:{boot_pid_norm[-4:]}"')
    if not lines and sketch and broad_vid_ok:
        lines.append(f'usb.autoConnect.device0 = "vid:{sketch[-4:]}"')
        warnings.append("broad_vid_autoconnect_may_capture_multiple_arduino_devices")
    elif not lines:
        warnings.append("insufficient_vid_pid_for_exact_autoconnect")
    if boot_vid and not boot_pid_norm:
        warnings.append("bootloader_pid_unknown_reset_may_still_prompt")
    if sketch and not sketch_pid_norm:
        warnings.append("sketch_pid_unknown")
    return {
        "schema": "vmware_autoconnect_recommendation.v1",
        "lines": lines,
        "warnings": list(dict.fromkeys(warnings)),
        "notes": [
            "Shut down the VM before editing the .vmx file.",
            "Back up the .vmx file first.",
            "Do not add rules for the real host mouse or keyboard.",
        ],
    }


def build_input_integrity_status(
    payload: dict[str, Any] | None = None,
    *,
    status_source: str | Path | None = None,
    expected_vid: str | None = None,
    expected_pid: str | None = None,
    expected_serial: str | None = None,
    expected_device_path: str | None = None,
    expected_com_port: str | None = None,
    live_input_backend: str | None = None,
    arduino_backend_selected: bool | None = None,
    arduino_armed: bool | None = None,
    software_input_allowed: bool = False,
    direct_backend_bypass_count: int = 0,
    require_monitor: bool = False,
    require_armed: bool = False,
    fail_on_injected: bool = True,
    fail_on_bypass: bool = True,
    max_age_ms: int = 3000,
    now_ms: int | None = None,
) -> dict[str, Any]:
    now = wall_time_millis() if now_ms is None else int(now_ms)
    source_payload = payload if isinstance(payload, dict) else read_status_payload(status_source)
    raw = _raw_monitor_payload(source_payload)
    expected_vid = normalize_usb_token(expected_vid, "VID")
    expected_pid = normalize_usb_token(expected_pid, "PID")
    expected_com_port = str(expected_com_port).upper() if expected_com_port else None
    source_generated_ms = _payload_generated_ms(raw if raw else source_payload or {})
    monitor_age_ms = max(0, now - source_generated_ms) if source_generated_ms is not None else (0 if source_payload else None)
    monitor_running = _first_bool(raw, "monitorRunning", "monitorAvailable")
    if monitor_running is None:
        monitor_running = source_payload is not None

    expected_device_path_text = str(expected_device_path).upper() if expected_device_path else None
    detected = _nested_or_raw(raw, "arduinoDetected")
    activity = _nested_or_raw(raw, "arduinoActivity")
    injection = _nested_or_raw(raw, "injectionFlags")
    backend = _nested_or_raw(raw, "backend")
    firmware_raw = _safe_dict(raw.get("firmware"))

    raw_input_device_present = _first_bool(
        detected,
        "rawInputDevicePresent",
        "arduinoRawInputSeen",
        "rawInputSeen",
        "rawInput",
    )
    keyboard_present = _first_bool(detected, "keyboardPresent", "arduinoKeyboardSeen", "keyboardSeen", "keyboard")
    mouse_present = _first_bool(detected, "mousePresent", "arduinoMouseSeen", "mouseSeen", "mouse")
    if raw_input_device_present is None:
        raw_input_device_present = bool((activity.get("rawInputMouseCount") or 0) or (activity.get("rawInputKeyboardCount") or 0))
    if keyboard_present is None:
        keyboard_present = bool(activity.get("rawInputKeyboardCount") or activity.get("keyboardEventCount") or 0)
    if mouse_present is None:
        mouse_present = bool(activity.get("rawInputMouseCount") or activity.get("mouseEventCount") or 0)

    vid_matched = _first_bool(detected, "vidPidMatched", "expectedVidPidMatched")
    if vid_matched is None and (expected_vid or expected_pid):
        vid_match = _contains_token(raw, expected_vid) if expected_vid else True
        pid_match = _contains_token(raw, expected_pid) if expected_pid else True
        vid_matched = bool(vid_match and pid_match)
    device_path_matched = _first_bool(detected, "devicePathMatched")
    if device_path_matched is None and expected_device_path_text:
        device_path_matched = _contains_token(raw, expected_device_path_text)
    com_port_matched = _first_bool(detected, "comPortMatched")
    if com_port_matched is None and expected_com_port:
        com_port_matched = _contains_token(raw, expected_com_port)

    aggregate_injected = _first_int(injection, "injectedEvents", "injectedEventCount")
    aggregate_lower = _first_int(injection, "lowerIlInjectedEvents", "lowerILInjectedEvents", "lowerIntegrityInjectedEvents")
    mouse_injected = _first_int(injection, "mouseInjectedCount", "LLMHF_INJECTED", "mouseInjectedEvents")
    keyboard_injected = _first_int(injection, "keyboardInjectedCount", "LLKHF_INJECTED", "keyboardInjectedEvents")
    mouse_lower = _first_int(injection, "mouseLowerIlInjectedCount", "mouseLowerILInjectedCount", "LLMHF_LOWER_IL_INJECTED")
    keyboard_lower = _first_int(injection, "keyboardLowerIlInjectedCount", "keyboardLowerILInjectedCount", "LLKHF_LOWER_IL_INJECTED")
    if aggregate_injected and not (mouse_injected or keyboard_injected):
        mouse_injected = aggregate_injected
    if aggregate_lower and not (mouse_lower or keyboard_lower):
        mouse_lower = aggregate_lower

    live_backend = live_input_backend or backend.get("liveInputBackend")
    backend_selected = arduino_backend_selected
    if backend_selected is None:
        backend_selected = _first_bool(backend, "arduinoBackendSelected")
    if backend_selected is None:
        backend_selected = str(live_backend or "").lower() == "arduino"
    armed = arduino_armed
    if armed is None:
        armed = _first_bool(backend, "arduinoArmed")

    direct_bypass = int(direct_backend_bypass_count or _first_int(backend, "directBackendBypassCount"))
    result = {
        "schema": SCHEMA,
        "generatedAtUtc": utc_now_iso(),
        "generatedAtMillis": now,
        "monitorRunning": bool(monitor_running),
        "monitorAgeMs": monitor_age_ms,
        "statusSource": str(status_source) if status_source else None,
        "arduinoExpected": {
            "vid": expected_vid,
            "pid": expected_pid,
            "serial": expected_serial,
            "devicePath": expected_device_path,
            "comPort": expected_com_port,
        },
        "arduinoDetected": {
            "rawInputDevicePresent": bool(raw_input_device_present),
            "keyboardPresent": bool(keyboard_present),
            "mousePresent": bool(mouse_present),
            "vidPidMatched": vid_matched,
            "devicePathMatched": device_path_matched,
            "comPortMatched": com_port_matched,
        },
        "arduinoActivity": {
            "lastAnyEventAgeMs": _first_nullable_int(activity, "lastAnyEventAgeMs", "lastArduinoEventAgeMs", "lastEventAgeMs"),
            "lastMouseEventAgeMs": _first_nullable_int(activity, "lastMouseEventAgeMs"),
            "lastKeyboardEventAgeMs": _first_nullable_int(activity, "lastKeyboardEventAgeMs"),
            "mouseEventCount": _first_int(activity, "mouseEventCount"),
            "keyboardEventCount": _first_int(activity, "keyboardEventCount"),
            "rawInputMouseCount": _first_int(activity, "rawInputMouseCount"),
            "rawInputKeyboardCount": _first_int(activity, "rawInputKeyboardCount"),
        },
        "injectionFlags": {
            "mouseInjectedCount": mouse_injected,
            "mouseLowerIlInjectedCount": mouse_lower,
            "keyboardInjectedCount": keyboard_injected,
            "keyboardLowerIlInjectedCount": keyboard_lower,
            "lastInjectedEventAgeMs": _first_nullable_int(injection, "lastInjectedEventAgeMs"),
        },
        "backend": {
            "liveInputBackend": live_backend,
            "arduinoBackendSelected": bool(backend_selected),
            "arduinoArmed": bool(armed),
            "softwareInputAllowed": bool(software_input_allowed or _first_bool(backend, "softwareInputAllowed")),
            "directBackendBypassCount": direct_bypass,
        },
        "firmware": {
            "status": firmware_raw.get("status") or "UNKNOWN",
            "name": firmware_raw.get("name"),
            "version": firmware_raw.get("version"),
            "board": firmware_raw.get("board"),
            "protocol": firmware_raw.get("protocol"),
            "resetSafe": firmware_raw.get("resetSafe"),
            "stopAll": firmware_raw.get("stopAll"),
            "watchdog": firmware_raw.get("watchdog"),
            "watchdogMs": firmware_raw.get("watchdogMs"),
            "armed": firmware_raw.get("armed"),
            "keysDown": firmware_raw.get("keysDown"),
            "mouseButtonsDown": firmware_raw.get("mouseButtonsDown"),
            "lastCommandAgeMs": firmware_raw.get("lastCommandAgeMs"),
        },
        "status": "PASS",
        "blockers": [],
        "warnings": [],
    }
    result["firmwareSafety"] = build_firmware_safety(result["firmware"])
    result["vmInputFocusSafety"] = build_vm_input_focus_safety(raw)
    evaluate_input_integrity_status(
        result,
        require_monitor=require_monitor,
        require_armed=require_armed,
        fail_on_injected=fail_on_injected,
        fail_on_bypass=fail_on_bypass,
        max_age_ms=max_age_ms,
    )
    _attach_legacy_monitor_fields(result)
    return result


def _attach_legacy_monitor_fields(status: dict[str, Any]) -> None:
    detected = _safe_dict(status.get("arduinoDetected"))
    activity = _safe_dict(status.get("arduinoActivity"))
    flags = _safe_dict(status.get("injectionFlags"))
    blockers = status.get("blockers") if isinstance(status.get("blockers"), list) else []
    injected = int(flags.get("mouseInjectedCount") or 0) + int(flags.get("keyboardInjectedCount") or 0)
    lower = int(flags.get("mouseLowerIlInjectedCount") or 0) + int(flags.get("keyboardLowerIlInjectedCount") or 0)
    status.update(
        {
            "monitorAvailable": bool(status.get("monitorRunning")),
            "arduinoRawInputSeen": bool(detected.get("rawInputDevicePresent")),
            "arduinoKeyboardSeen": bool(detected.get("keyboardPresent")),
            "arduinoMouseSeen": bool(detected.get("mousePresent")),
            "expectedVidPidMatched": detected.get("vidPidMatched"),
            "injectedEvents": injected,
            "lowerIlInjectedEvents": lower,
            "lastArduinoEventAgeMs": activity.get("lastAnyEventAgeMs"),
            "monitorPass": status.get("status") != "FAIL",
            "monitorBlockReason": blockers[0] if blockers else None,
        }
    )


def evaluate_input_integrity_status(
    status: dict[str, Any],
    *,
    require_monitor: bool = False,
    require_armed: bool = False,
    fail_on_injected: bool = True,
    fail_on_bypass: bool = True,
    max_age_ms: int = 3000,
) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    detected = _safe_dict(status.get("arduinoDetected"))
    activity = _safe_dict(status.get("arduinoActivity"))
    flags = _safe_dict(status.get("injectionFlags"))
    backend = _safe_dict(status.get("backend"))
    firmware = _safe_dict(status.get("firmware"))
    firmware_safety = _safe_dict(status.get("firmwareSafety"))
    vm_focus = _safe_dict(status.get("vmInputFocusSafety"))
    monitor_running = bool(status.get("monitorRunning"))
    if not monitor_running:
        (blockers if require_monitor else warnings).append("monitor_missing")
    age = status.get("monitorAgeMs")
    try:
        if age is not None and int(age) > max(1, int(max_age_ms or 3000)):
            (blockers if require_monitor else warnings).append("monitor_stale")
    except (TypeError, ValueError):
        pass
    if require_monitor:
        if not detected.get("rawInputDevicePresent"):
            blockers.append("arduino_raw_input_not_seen")
        if not detected.get("keyboardPresent"):
            blockers.append("arduino_keyboard_not_seen")
        if not detected.get("mousePresent"):
            blockers.append("arduino_mouse_not_seen")
        if detected.get("vidPidMatched") is False:
            blockers.append("expected_vid_pid_not_matched")
        if detected.get("devicePathMatched") is False:
            blockers.append("expected_device_path_not_matched")
        if detected.get("comPortMatched") is False:
            blockers.append("expected_com_port_not_matched")
    if require_armed and not backend.get("arduinoArmed"):
        blockers.append("arduino_unarmed")
    firmware_status = str(firmware.get("status") or "UNKNOWN").upper()
    if firmware_status == "FAIL":
        blockers.append("firmware_protocol_failed")
    elif firmware_status in {"UNKNOWN", ""}:
        warnings.append("firmware_protocol_unknown")
    protocol = firmware.get("protocol")
    if protocol not in (None, "arduino_hid.v1"):
        blockers.append("firmware_protocol_mismatch")
    for key, reason in (("resetSafe", "firmware_reset_not_safe"), ("stopAll", "firmware_stop_all_unavailable"), ("watchdog", "firmware_watchdog_unavailable")):
        if firmware.get(key) is False:
            blockers.append(reason)
    try:
        if int(firmware.get("keysDown") or 0) > 0 or int(firmware.get("mouseButtonsDown") or 0) > 0:
            blockers.append("firmware_reports_held_input")
    except (TypeError, ValueError):
        pass
    if firmware_safety.get("status") == "FAIL":
        blockers.extend(str(item) for item in firmware_safety.get("blockers", []) if item)
    elif firmware_safety.get("status") == "WARN":
        warnings.extend(str(item) for item in firmware_safety.get("warnings", []) if item)
    if vm_focus.get("status") == "FAIL":
        blockers.append("vm_input_focus_failed")
        blockers.extend(str(item) for item in vm_focus.get("blockers", []) if item)
    elif vm_focus.get("status") == "WARN":
        warnings.append("vm_input_focus_not_confirmed")
        warnings.extend(str(item) for item in vm_focus.get("warnings", []) if item)
    if backend.get("liveInputBackend") not in (None, "arduino") and not backend.get("softwareInputAllowed"):
        blockers.append("non_arduino_live_backend")
    if fail_on_bypass and int(backend.get("directBackendBypassCount") or 0) > 0:
        blockers.append("backend_bypass_detected")
    injected_counts = (
        int(flags.get("mouseInjectedCount") or 0),
        int(flags.get("keyboardInjectedCount") or 0),
        int(flags.get("mouseLowerIlInjectedCount") or 0),
        int(flags.get("keyboardLowerIlInjectedCount") or 0),
    )
    if fail_on_injected and any(count > 0 for count in injected_counts):
        blockers.append("injected_input_detected")
    last_age = activity.get("lastAnyEventAgeMs")
    try:
        event_count = int(activity.get("rawInputMouseCount") or 0) + int(activity.get("rawInputKeyboardCount") or 0)
        if event_count > 0 and last_age is not None and int(last_age) > max(1, int(max_age_ms or 3000)):
            warnings.append("last_arduino_event_stale")
    except (TypeError, ValueError):
        pass
    status["blockers"] = list(dict.fromkeys(str(item) for item in blockers if item))
    status["warnings"] = list(dict.fromkeys(str(item) for item in warnings if item))
    status["status"] = "FAIL" if status["blockers"] else "WARN" if status["warnings"] else "PASS"
    return status


def input_integrity_delta(before: dict[str, Any] | None, after: dict[str, Any] | None) -> dict[str, int]:
    before = before if isinstance(before, dict) else {}
    after = after if isinstance(after, dict) else {}
    before_flags = _safe_dict(before.get("injectionFlags"))
    after_flags = _safe_dict(after.get("injectionFlags"))
    before_activity = _safe_dict(before.get("arduinoActivity"))
    after_activity = _safe_dict(after.get("arduinoActivity"))
    before_backend = _safe_dict(before.get("backend"))
    after_backend = _safe_dict(after.get("backend"))

    def delta(section_before: dict[str, Any], section_after: dict[str, Any], key: str) -> int:
        return int(section_after.get(key) or 0) - int(section_before.get(key) or 0)

    return {
        "mouseInjectedCountDelta": delta(before_flags, after_flags, "mouseInjectedCount"),
        "keyboardInjectedCountDelta": delta(before_flags, after_flags, "keyboardInjectedCount"),
        "mouseLowerIlInjectedCountDelta": delta(before_flags, after_flags, "mouseLowerIlInjectedCount"),
        "keyboardLowerIlInjectedCountDelta": delta(before_flags, after_flags, "keyboardLowerIlInjectedCount"),
        "lowerIlInjectedCountDelta": delta(before_flags, after_flags, "mouseLowerIlInjectedCount")
        + delta(before_flags, after_flags, "keyboardLowerIlInjectedCount"),
        "rawInputMouseCountDelta": delta(before_activity, after_activity, "rawInputMouseCount"),
        "rawInputKeyboardCountDelta": delta(before_activity, after_activity, "rawInputKeyboardCount"),
        "rawInputMouseDxDelta": delta(before_activity, after_activity, "rawInputMouseDxTotal"),
        "rawInputMouseDyDelta": delta(before_activity, after_activity, "rawInputMouseDyTotal"),
        "directBackendBypassCountDelta": delta(before_backend, after_backend, "directBackendBypassCount"),
    }


def overlay_display_state(status: dict[str, Any]) -> dict[str, str]:
    value = str(status.get("status") or "WARN").upper()
    if value == "PASS":
        return {"status": "PASS", "background": "#0f7d32", "foreground": "#ffffff"}
    if value == "FAIL":
        return {"status": "FAIL", "background": "#b00020", "foreground": "#ffffff"}
    return {"status": "WARN", "background": "#b7791f", "foreground": "#111111"}


def compact_status_lines(status: dict[str, Any]) -> list[str]:
    detected = _safe_dict(status.get("arduinoDetected"))
    activity = _safe_dict(status.get("arduinoActivity"))
    flags = _safe_dict(status.get("injectionFlags"))
    backend = _safe_dict(status.get("backend"))
    firmware = _safe_dict(status.get("firmware"))
    firmware_safety = _safe_dict(status.get("firmwareSafety"))
    vm_focus = _safe_dict(status.get("vmInputFocusSafety"))
    lower = int(flags.get("mouseLowerIlInjectedCount") or 0) + int(flags.get("keyboardLowerIlInjectedCount") or 0)
    return [
        f"ARDUINO INPUT: {status.get('status') or 'WARN'}",
        f"Firmware: {firmware.get('status') or 'UNKNOWN'}",
        f"Firmware safety: {firmware_safety.get('status') or 'unknown'}",
        f"VM focus: {vm_focus.get('status') or 'unknown'} / {vm_focus.get('postTestInputState') or 'unknown'}",
        f"Protocol: {firmware.get('protocol') or 'unknown'}",
        f"Reset safe: {'yes' if firmware.get('resetSafe') else 'no'}",
        f"STOP_ALL: {'yes' if firmware.get('stopAll') else 'no'}",
        f"Watchdog: {firmware.get('watchdogMs', 'unknown')} ms",
        f"Backend: {backend.get('liveInputBackend') or 'unknown'}",
        f"Armed: {'yes' if backend.get('arduinoArmed') else 'no'}",
        f"Keys/buttons down: {firmware.get('keysDown', 'n/a')} / {firmware.get('mouseButtonsDown', 'n/a')}",
        f"Raw mouse: {'yes' if detected.get('mousePresent') else 'no'}, age {activity.get('lastMouseEventAgeMs', 'n/a')} ms",
        f"Raw keyboard: {'yes' if detected.get('keyboardPresent') else 'no'}, age {activity.get('lastKeyboardEventAgeMs', 'n/a')} ms",
        f"VID/PID: matched {'yes' if detected.get('vidPidMatched') else 'no'}",
        f"Injected mouse: {flags.get('mouseInjectedCount', 0)}",
        f"Injected keyboard: {flags.get('keyboardInjectedCount', 0)}",
        f"LowerIL: {lower}",
        f"Bypass: {backend.get('directBackendBypassCount', 0)}",
        f"Last event: {activity.get('lastAnyEventAgeMs', 'n/a')} ms",
        f"Live allowed: {'yes' if status.get('status') == 'PASS' else 'no'}",
    ]
