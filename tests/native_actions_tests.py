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

import sys
import os
import threading
import time
import unittest

from unittest.mock import Mock, call, patch
from ovos_bus_client import Message
from ovos_utils.fakebus import FakeBus

sys.path.append(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from neon_utils.native_actions import (invoke_native_action,
                                       NativeActionOutcome)
from neon_data_models.enum import NodeNativeAction, NativeActionErrorCode


def _node_message(action_supported: bool = True, action="launch_camera_app",
                  settings=None, session_id="node-abc123"):
    context = {
        "node": {
            "node_id": "node-abc123",
            "node_name": "Kitchen Phone",
            "capabilities": {action: action_supported}
        },
        "session": {"session_id": session_id}
    }
    return Message("recognizer_loop:utterance", {}, context)


def _response(action="launch_camera_app", status="success", error=None,
             session_id="node-abc123"):
    data = {"action": action, "status": status}
    if error:
        data["error"] = error
    return Message("node.invoke_native.response", data,
                   {"session": {"session_id": session_id}})


def _make_skill(settings=None, wait_for_message_return=None):
    skill = Mock()
    skill.settings = settings or {}
    skill.bus = Mock()
    skill.bus.wait_for_message = Mock(return_value=wait_for_message_return)
    skill.dialog_renderer = None
    return skill


class NativeActionsTests(unittest.TestCase):
    def test_not_supported_speaks_and_emits_nothing(self):
        skill = _make_skill()
        message = _node_message(action_supported=False)

        result = invoke_native_action(skill, message,
                                      NodeNativeAction.LAUNCH_CAMERA_APP)

        self.assertEqual(result.outcome, NativeActionOutcome.NOT_SUPPORTED)
        skill.bus.emit.assert_not_called()
        skill.speak.assert_called_once()

    def test_capabilities_entirely_absent_treated_as_not_supported(self):
        skill = _make_skill()
        message = Message("recognizer_loop:utterance", {},
                          {"node": {"node_id": "node-abc123"},
                           "session": {"session_id": "node-abc123"}})

        result = invoke_native_action(skill, message,
                                      NodeNativeAction.LAUNCH_CAMERA_APP)

        self.assertEqual(result.outcome, NativeActionOutcome.NOT_SUPPORTED)
        skill.bus.emit.assert_not_called()

    def test_absent_capability_key_treated_as_not_supported(self):
        skill = _make_skill()
        message = _node_message(action_supported=True,
                                action="launch_sms_app")
        # Request an action that has no key in capabilities at all
        result = invoke_native_action(skill, message,
                                      NodeNativeAction.LAUNCH_CAMERA_APP)

        self.assertEqual(result.outcome, NativeActionOutcome.NOT_SUPPORTED)
        skill.bus.emit.assert_not_called()

    def test_supported_emits_invoke_native_with_action_and_params(self):
        response = _response(status="success")
        skill = _make_skill(wait_for_message_return=response)
        message = _node_message(action_supported=True)

        result = invoke_native_action(skill, message,
                                      NodeNativeAction.LAUNCH_CAMERA_APP)

        self.assertEqual(result.outcome, NativeActionOutcome.SUCCESS)
        emitted = skill.bus.emit.call_args[0][0]
        self.assertEqual(emitted.msg_type, "node.invoke_native")
        self.assertEqual(emitted.data["action"], "launch_camera_app")
        self.assertEqual(emitted.data["params"], {})

    def test_params_forwarded_for_sms(self):
        response = _response(action="launch_sms_app", status="success")
        skill = _make_skill(wait_for_message_return=response)
        message = _node_message(action_supported=True, action="launch_sms_app")

        invoke_native_action(skill, message, NodeNativeAction.LAUNCH_SMS_APP,
                            params={"to": "5551234567", "body": "On my way"})

        emitted = skill.bus.emit.call_args[0][0]
        self.assertEqual(emitted.data["params"],
                         {"to": "5551234567", "body": "On my way"})

    def test_success_silent_by_default(self):
        response = _response(status="success")
        skill = _make_skill(wait_for_message_return=response)
        message = _node_message(action_supported=True)

        invoke_native_action(skill, message, NodeNativeAction.LAUNCH_CAMERA_APP)

        skill.speak.assert_not_called()
        skill.speak_dialog.assert_not_called()

    def test_success_confirms_when_setting_enabled(self):
        response = _response(status="success")
        skill = _make_skill(settings={"confirm_on_success": True},
                            wait_for_message_return=response)
        message = _node_message(action_supported=True)

        result = invoke_native_action(skill, message,
                                      NodeNativeAction.LAUNCH_CAMERA_APP)

        self.assertEqual(result.outcome, NativeActionOutcome.SUCCESS)
        skill.speak.assert_called_once()

    def test_error_response_speaks_failure_with_code(self):
        response = _response(status="error",
                             error={"code": "unavailable",
                                    "message": "No camera app."})
        skill = _make_skill(wait_for_message_return=response)
        message = _node_message(action_supported=True)

        result = invoke_native_action(skill, message,
                                      NodeNativeAction.LAUNCH_CAMERA_APP)

        self.assertEqual(result.outcome, NativeActionOutcome.ERROR)
        self.assertEqual(result.error_code, NativeActionErrorCode.UNAVAILABLE)
        skill.speak.assert_called_once()

    def test_unknown_error_code_maps_to_internal_error(self):
        response = _response(status="error",
                             error={"code": "made_up_code", "message": "?"})
        skill = _make_skill(wait_for_message_return=response)
        message = _node_message(action_supported=True)

        result = invoke_native_action(skill, message,
                                      NodeNativeAction.LAUNCH_CAMERA_APP)

        self.assertEqual(result.error_code,
                         NativeActionErrorCode.INTERNAL_ERROR)

    def test_timeout_speaks_failure(self):
        # wait_for_message always returns None -> immediate timeout
        skill = _make_skill(wait_for_message_return=None)
        message = _node_message(action_supported=True)

        result = invoke_native_action(skill, message,
                                      NodeNativeAction.LAUNCH_CAMERA_APP)

        self.assertEqual(result.outcome, NativeActionOutcome.TIMEOUT)
        skill.speak.assert_called_once()

    def test_real_bus_times_out_after_deadline_elapses(self):
        # Exercises the actual deadline loop in _await_response, not a
        # mocked wait_for_message returning None immediately.
        # per_action_timeouts is int()-cast, so use a whole-second value —
        # 0.2 truncates to 0 and would pass this test for the wrong reason.
        skill = Mock()
        skill.settings = {"per_action_timeouts": {"launch_camera_app": 1}}
        skill.bus = FakeBus()
        skill.dialog_renderer = None
        message = _node_message(action_supported=True)

        start = time.monotonic()
        result = invoke_native_action(skill, message,
                                      NodeNativeAction.LAUNCH_CAMERA_APP)
        elapsed = time.monotonic() - start

        self.assertEqual(result.outcome, NativeActionOutcome.TIMEOUT)
        self.assertLess(elapsed, 2.0)
        self.assertGreaterEqual(elapsed, 1.0)

    def test_real_bus_discards_wrong_action_then_matches(self):
        # Proves the discard-and-keep-waiting loop can still succeed on a
        # later, matching response within the same deadline.
        skill = Mock()
        skill.settings = {}
        skill.bus = FakeBus()
        skill.dialog_renderer = None
        message = _node_message(action_supported=True)

        # Space the two emits out: `_await_response` only resubscribes to
        # the bus AFTER the wrong-action message is received and discarded,
        # so firing both immediately can drop the second before anything
        # is listening for it again.
        def _emit_wrong():
            skill.bus.emit(_response(action="launch_clock_app",
                                    status="success"))

        def _emit_right():
            skill.bus.emit(_response(action="launch_camera_app",
                                    status="success"))

        threading.Timer(0.05, _emit_wrong).start()
        threading.Timer(0.2, _emit_right).start()
        result = invoke_native_action(skill, message,
                                      NodeNativeAction.LAUNCH_CAMERA_APP)

        self.assertEqual(result.outcome, NativeActionOutcome.SUCCESS)

    def test_mismatched_response_is_discarded_then_timeout(self):
        wrong_action = _response(action="launch_clock_app", status="success")
        skill = _make_skill()
        # First call returns a response for the wrong action, second
        # call (after filtering) returns None because timeout elapsed
        skill.bus.wait_for_message = Mock(
            side_effect=[wrong_action, None])
        message = _node_message(action_supported=True)

        result = invoke_native_action(skill, message,
                                      NodeNativeAction.LAUNCH_CAMERA_APP)

        self.assertEqual(result.outcome, NativeActionOutcome.TIMEOUT)
        self.assertEqual(skill.bus.wait_for_message.call_count, 2)

    def test_mismatched_session_is_discarded(self):
        wrong_session = _response(status="success", session_id="node-other")
        skill = Mock()
        skill.settings = {}
        skill.dialog_renderer = None
        skill.bus = Mock()
        skill.bus.wait_for_message = Mock(side_effect=[wrong_session, None])
        message = _node_message(action_supported=True)

        result = invoke_native_action(skill, message,
                                      NodeNativeAction.LAUNCH_CAMERA_APP)

        self.assertEqual(result.outcome, NativeActionOutcome.TIMEOUT)

    def test_per_action_timeout_override_used(self):
        skill = _make_skill(settings={"per_action_timeouts":
                                      {"launch_camera_app": 1}})
        message = _node_message(action_supported=True)

        invoke_native_action(skill, message, NodeNativeAction.LAUNCH_CAMERA_APP)

        _, kwargs = skill.bus.wait_for_message.call_args
        self.assertLessEqual(kwargs["timeout"], 1)

    def test_default_timeout_used_when_no_override(self):
        skill = _make_skill()
        message = _node_message(action_supported=True,
                                action="launch_sms_app")

        invoke_native_action(skill, message, NodeNativeAction.LAUNCH_SMS_APP)

        _, kwargs = skill.bus.wait_for_message.call_args
        self.assertLessEqual(kwargs["timeout"], 5)

    def test_speaks_dialog_key_when_dialog_renderer_has_it(self):
        response = _response(status="error",
                             error={"code": "unavailable", "message": "x"})
        skill = _make_skill(wait_for_message_return=response)
        skill.dialog_renderer = Mock()
        skill.dialog_renderer.templates = {"native_action_error": []}
        message = _node_message(action_supported=True)

        invoke_native_action(skill, message, NodeNativeAction.LAUNCH_CAMERA_APP)

        skill.speak_dialog.assert_called_once()
        skill.speak.assert_not_called()

    def test_falls_back_to_speak_when_no_dialog_file(self):
        response = _response(status="error",
                             error={"code": "unavailable", "message": "x"})
        skill = _make_skill(wait_for_message_return=response)
        skill.dialog_renderer = Mock()
        skill.dialog_renderer.templates = {}
        message = _node_message(action_supported=True)

        invoke_native_action(skill, message, NodeNativeAction.LAUNCH_CAMERA_APP)

        skill.speak.assert_called_once()
        skill.speak_dialog.assert_not_called()

    def test_no_dialog_file_does_not_log_error_or_warning(self):
        # A missing native_action_*.dialog file is the expected, common
        # case post-change, not an exceptional one worth logging.
        skill = _make_skill()
        message = _node_message(action_supported=False)

        with patch("neon_utils.native_actions.LOG") as mock_log:
            invoke_native_action(skill, message,
                                 NodeNativeAction.LAUNCH_CAMERA_APP)

        mock_log.error.assert_not_called()
        mock_log.warning.assert_not_called()

    def test_not_supported_fallback_text(self):
        skill = _make_skill()
        message = _node_message(action_supported=False)

        invoke_native_action(skill, message, NodeNativeAction.LAUNCH_CAMERA_APP)

        self.assertEqual(skill.speak.call_args[0][0],
                         "This device cannot open the camera app.")

    def test_timeout_fallback_text(self):
        skill = _make_skill(wait_for_message_return=None)
        message = _node_message(action_supported=True)

        invoke_native_action(skill, message, NodeNativeAction.LAUNCH_CAMERA_APP)

        self.assertEqual(skill.speak.call_args[0][0],
                         "I did not reach your device.")

    def test_success_fallback_text(self):
        response = _response(status="success")
        skill = _make_skill(settings={"confirm_on_success": True},
                            wait_for_message_return=response)
        message = _node_message(action_supported=True)

        invoke_native_action(skill, message, NodeNativeAction.LAUNCH_CAMERA_APP)

        self.assertEqual(skill.speak.call_args[0][0], "Done.")

    def test_error_fallback_uses_node_message_when_present(self):
        response = _response(status="error",
                             error={"code": "unavailable",
                                    "message": "No camera app is available."})
        skill = _make_skill(wait_for_message_return=response)
        message = _node_message(action_supported=True)

        invoke_native_action(skill, message, NodeNativeAction.LAUNCH_CAMERA_APP)

        self.assertEqual(skill.speak.call_args[0][0],
                         "No camera app is available.")

    def test_error_fallback_generic_when_node_gives_no_message(self):
        response = _response(action="launch_sms_app", status="error",
                             error={"code": "unavailable", "message": ""})
        skill = _make_skill(wait_for_message_return=response)
        message = _node_message(action_supported=True,
                                action="launch_sms_app")

        invoke_native_action(skill, message, NodeNativeAction.LAUNCH_SMS_APP)

        self.assertEqual(skill.speak.call_args[0][0],
                         "I could not open the sms app.")


if __name__ == '__main__':
    unittest.main()
