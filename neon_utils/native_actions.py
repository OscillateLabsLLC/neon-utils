# NEON AI (TM) SOFTWARE, Software Development Kit & Application Framework
# All trademark and other rights reserved by their respective owners
# Copyright 2008-2026 Neongecko.com Inc.
# Contributors: Daniel McKnight, Guy Daniels, Elon Gasper, Richard Leeds,
# Regina Bloomstine, Casimiro Ferreira, Andrii Pernatii, Kirill Hrymailo
# BSD-3 License
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
# 3. Neither the name of the copyright holder nor the names of its
#    contributors may be used to endorse or promote products derived from this
#    software without specific prior written permission.
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO,
# THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
# PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR
# CONTRIBUTORS  BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
# EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
# PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA,
# OR PROFITS;  OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
# LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
# NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE,  EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import time

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from ovos_bus_client import Message
from ovos_utils.log import LOG

from neon_data_models.enum import NodeNativeAction, NativeActionErrorCode

# Default response timeout in seconds, per action.
_DEFAULT_TIMEOUTS = {
    NodeNativeAction.LAUNCH_CAMERA_APP.value: 10,
    NodeNativeAction.LAUNCH_VOICE_RECORDER_APP.value: 10,
    NodeNativeAction.LAUNCH_REMINDERS_APP.value: 5,
    NodeNativeAction.LAUNCH_CLOCK_APP.value: 5,
    NodeNativeAction.LAUNCH_SMS_APP.value: 5,
    NodeNativeAction.LAUNCH_EMAIL_APP.value: 5,
}


class NativeActionOutcome(Enum):
    SUCCESS = "success"
    NOT_SUPPORTED = "not_supported"
    ERROR = "error"
    TIMEOUT = "timeout"


@dataclass
class NativeActionResult:
    outcome: NativeActionOutcome
    error_code: Optional[NativeActionErrorCode] = None
    error_message: str = ""


def invoke_native_action(skill, message: Message, action: NodeNativeAction,
                         params: Optional[dict] = None) -> NativeActionResult:
    """
    Capability-gate, dispatch, and await a `node.invoke_native` request.
    Shared by every skill's `if node:` branch so the flow (gate, emit,
    await, settings) lives in one place.
    :param skill: the calling OVOSSkill/NeonSkill instance
    :param message: the triggering Message (must carry `context.node`)
    :param action: `NodeNativeAction` to invoke
    :param params: optional payload (only meaningful for sms/email actions)
    :returns: `NativeActionResult` describing what happened
    """
    action_value = action.value if isinstance(action, NodeNativeAction) \
        else action
    node_ctx = message.context.get("node") or {}
    capabilities = node_ctx.get("capabilities") or {}

    if not capabilities.get(action_value):
        LOG.debug(f"Node does not support {action_value}; "
                  f"capabilities={capabilities}")
        _speak_not_supported(skill, message, action_value)
        return NativeActionResult(NativeActionOutcome.NOT_SUPPORTED)

    session_id = message.context.get("session", {}).get("session_id") or \
        node_ctx.get("node_id")

    invoke_msg = message.reply("node.invoke_native",
                               {"action": action_value,
                                "params": params or {}})
    skill.bus.emit(invoke_msg)

    timeout = _resolve_timeout(skill, action_value)
    response = _await_response(skill, session_id, action_value, timeout)

    if response is None:
        LOG.warning(f"Timed out awaiting node.invoke_native.response for "
                    f"action={action_value} session={session_id}")
        _speak_timeout(skill, message, action_value)
        return NativeActionResult(NativeActionOutcome.TIMEOUT)

    return _interpret_response(skill, message, action_value, response)


def _interpret_response(skill, message: Message, action_value: str,
                        response: Message) -> NativeActionResult:
    if response.data.get("status") == "success":
        if skill.settings.get("confirm_on_success", False):
            _speak_success(skill, message, action_value)
        return NativeActionResult(NativeActionOutcome.SUCCESS)

    error = response.data.get("error") or {}
    try:
        error_code = NativeActionErrorCode(error.get("code"))
    except ValueError:
        error_code = NativeActionErrorCode.INTERNAL_ERROR
    error_message = error.get("message", "")
    _speak_error(skill, message, action_value, error_code, error_message)
    return NativeActionResult(NativeActionOutcome.ERROR, error_code,
                              error_message)


def _resolve_timeout(skill, action_value: str) -> int:
    overrides = skill.settings.get("per_action_timeouts") or {}
    if action_value in overrides:
        try:
            return int(overrides[action_value])
        except (TypeError, ValueError):
            LOG.warning(f"Invalid per_action_timeouts override for "
                       f"{action_value}: {overrides[action_value]!r}")
    return _DEFAULT_TIMEOUTS.get(action_value, 5)


def _await_response(skill, session_id: Optional[str], action_value: str,
                    timeout: float) -> Optional[Message]:
    """
    `wait_for_message` filters only by `msg_type`, not content. Loop with
    one shared deadline and drop any response for a different session or
    action.
    """
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        response = skill.bus.wait_for_message("node.invoke_native.response",
                                              timeout=remaining)
        if response is None:
            return None
        resp_action = response.data.get("action")
        resp_session = response.context.get("session", {}).get("session_id")
        if resp_action == action_value and (
                session_id is None or resp_session == session_id):
            return response
        LOG.debug(f"Discarding non-matching node.invoke_native.response: "
                  f"action={resp_action} session={resp_session}")


def _speak_not_supported(skill, message: Message, action_value: str):
    _speak(skill, message, "native_action_not_supported",
          {"action": action_value, "description": _describe(action_value)})


def _speak_success(skill, message: Message, action_value: str):
    _speak(skill, message, "native_action_success",
          {"action": action_value, "description": _describe(action_value)})


def _speak_timeout(skill, message: Message, action_value: str):
    _speak(skill, message, "native_action_timeout",
          {"action": action_value, "description": _describe(action_value)})


def _speak_error(skill, message: Message, action_value: str,
                 error_code: NativeActionErrorCode, error_message: str):
    _speak(skill, message, "native_action_error",
          {"action": action_value, "code": error_code.value,
           "message": error_message, "description": _describe(action_value)})


def _speak(skill, message: Message, dialog_key: str, data: dict):
    """
    Speak the skill's own dialog file when it exists, so a skill can still
    override the default text through the normal OVOS dialog convention.
    Otherwise speak the canonical fallback text. Checks `templates`
    directly rather than calling `speak_dialog` blind: a missing dialog
    file is the expected case here, not an error to log.
    """
    try:
        if skill.dialog_renderer and \
                dialog_key in skill.dialog_renderer.templates:
            skill.speak_dialog(dialog_key, data, message=message)
            return
    except Exception as e:
        LOG.warning(f"Failed checking dialog_renderer for {dialog_key}: {e}")
    skill.speak(_fallback_text(dialog_key, data), message=message)


def _fallback_text(dialog_key: str, data: dict) -> str:
    """
    Canonical plain-English text for a skill with no matching dialog file.
    A skill can still override any of these keys with its own dialog file;
    this text is the default, not a template for one.
    """
    action = data.get("action", "")
    if dialog_key == "native_action_not_supported":
        return f"This device cannot open {_describe(action)}."
    if dialog_key == "native_action_success":
        return "Done."
    if dialog_key == "native_action_timeout":
        return "I did not reach your device."
    if dialog_key == "native_action_error":
        message = data.get("message")
        return message or f"I could not open {_describe(action)}."
    return ""


def _describe(action_value: str) -> str:
    name = action_value.replace("launch_", "").replace("_app", "") \
        .replace("_", " ")
    return f"the {name} app" if name else "that app"
