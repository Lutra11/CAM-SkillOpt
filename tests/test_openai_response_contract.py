"""Offline response-contract tests for the Azure/OpenAI wrapper."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import traceback
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import openai
from openai._base_client import httpx2
from skillopt.model import azure_openai as backend
from skillopt.model.infra_errors import InfraError


def _client(*, chat=None, responses=None):
    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=chat or Mock())),
        responses=SimpleNamespace(create=responses or Mock()),
    )


class ResponseContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"SKILLOPT_INFRA_ARTIFACT_DIR": self.temp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.temp.cleanup)
        self.sleep = patch.object(backend.time, "sleep")
        self.sleep.start()
        self.addCleanup(self.sleep.stop)

    def _chat(self, response, *, attempts=1, message_mode=False, return_message=False):
        client = _client(chat=Mock(return_value=response))
        if message_mode:
            return backend._chat_messages_impl(client, "gpt-4o", [{"role": "user", "content": "x"}],
                                              32, attempts, "test", return_message=return_message)
        return backend._chat_impl(client, "gpt-4o", "s", "u", 32, attempts, "test")

    def test_null_and_empty_choices_become_bounded_infra_failures(self):
        for choices in (None, []):
            with self.subTest(choices=choices), self.assertRaises(InfraError) as caught:
                self._chat(SimpleNamespace(choices=choices, usage=None), attempts=2)
            self.assertEqual(caught.exception.failure_type, "provider_error")
            self.assertEqual(caught.exception.details["response_contract"], "chat_choices_missing_or_empty")
        response = {"choices": None, "status_code": 503, "_request_id": "shape-req"}
        with self.assertRaises(InfraError) as caught:
            self._chat(response)
        self.assertEqual(caught.exception.details["response_shape"]["choices"], "missing")
        self.assertEqual(caught.exception.details["status_code"], 503)
        self.assertEqual(caught.exception.details["request_id"], "shape-req")
        self.assertEqual(caught.exception.details["attempt"], 1)
        self.assertEqual(caught.exception.details["max_attempts"], 1)

    def test_empty_text_fails_but_missing_usage_is_valid(self):
        with self.assertRaises(InfraError):
            self._chat(SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=" "))], usage=None))
        text, usage = self._chat(SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))], usage=None))
        self.assertEqual(text, "ok")
        self.assertEqual(usage, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})

    def test_tool_only_message_is_valid_only_for_message_return(self):
        msg = SimpleNamespace(content=None, tool_calls=[{"id": "call_1", "function": {
            "name": "lookup", "arguments": "{\"q\": 1}"}}])
        response = SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None)
        message, _ = self._chat(response, message_mode=True, return_message=True)
        self.assertIs(message, msg)
        with self.assertRaises(InfraError):
            self._chat(response, message_mode=True, return_message=False)

    def test_responses_api_text_and_message_contracts(self):
        resp = SimpleNamespace(output_text="answer", output=[], usage=None, error=None, status="completed")
        client = _client(responses=Mock(return_value=resp))
        text, usage = backend._chat_impl(client, "gpt-5.3-codex", "s", "u", 32, 1, "test")
        self.assertEqual(text, "answer")
        self.assertEqual(usage["total_tokens"], 0)
        tool_resp = SimpleNamespace(output_text="", output=[SimpleNamespace(
            type="function_call", call_id="c1", name="f", arguments="{}")], usage=None, error=None, status="completed")
        client = _client(responses=Mock(return_value=tool_resp))
        message, _ = backend._chat_messages_impl(client, "gpt-5.3-codex", [], 32, 1, "test", return_message=True)
        self.assertEqual(len(message.tool_calls), 1)
        client = _client(responses=Mock(return_value=tool_resp))
        with self.assertRaises(InfraError):
            backend._chat_impl(client, "gpt-5.3-codex", "s", "u", 32, 1, "test")

    def test_provider_error_payload_and_transient_contract_retry_are_bounded(self):
        bad = SimpleNamespace(choices=None, usage=None)
        call = Mock(side_effect=[bad, bad, bad, bad, bad, bad])
        with self.assertRaises(InfraError):
            backend._chat_impl(_client(chat=call), "gpt-4o", "s", "u", 32, 99, "test")
        self.assertEqual(call.call_count, backend._MAX_CHAT_ATTEMPTS)
        response_error = SimpleNamespace(choices=[], error=SimpleNamespace(code="invalid_response"), usage=None)
        with self.assertRaises(InfraError):
            self._chat(response_error)
        auth_payload = SimpleNamespace(choices=None, error=SimpleNamespace(code="unauthorized", status=401), usage=None)
        client = _client(chat=Mock(return_value=auth_payload))
        with self.assertRaises(InfraError) as caught:
            backend._chat_impl(client, "gpt-4o", "s", "u", 32, 5, "test")
        self.assertEqual(caught.exception.failure_type, "auth_error")
        self.assertEqual(client.chat.completions.create.call_count, 1)

    def test_mapping_error_payloads_fail_fast_and_include_shape_metadata(self):
        for payload, failure_type in (
            ({"choices": [], "error": {"code": "unauthorized", "status": 401}}, "auth_error"),
            ({"choices": [], "error": {"code": "model_not_found", "status": 404}}, "model_unavailable"),
            ({"choices": [], "error": {"code": "invalid_request_error", "status": 400}}, "provider_error"),
        ):
            call = Mock(return_value=payload)
            with self.subTest(payload=payload), self.assertRaises(InfraError) as caught:
                backend._chat_impl(_client(chat=call), "gpt-4o", "s", "u", 32, 5, "test")
            self.assertEqual(call.call_count, 1)
            self.assertEqual(caught.exception.failure_type, failure_type)
            self.assertEqual(caught.exception.details["attempt"], 1)
            self.assertEqual(caught.exception.details["max_attempts"], 5)
            self.assertEqual(caught.exception.details["response_shape"]["choices"], 0)
            self.assertEqual(caught.exception.details["status_code"], int(payload["error"]["status"]))

    def test_real_sdk_timeout_and_connection_exceptions_retry_once(self):
        ok = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))], usage=None)
        cases = (
            (openai.APITimeoutError(request=object()), "llm_timeout"),
            (openai.APIConnectionError(message="offline", request=object()), "network_error"),
        )
        for exc, failure_type in cases:
            call = Mock(side_effect=[exc, ok])
            client = _client(chat=call)
            client.with_options = Mock(return_value=client)
            with self.subTest(exception=type(exc).__name__):
                text, _ = backend._chat_impl(client, "gpt-4o", "s", "u", 32, 2, "test")
            self.assertEqual(text, "ok")
            self.assertEqual(call.call_count, 2)
            client.with_options.assert_called_once_with(max_retries=0)
        for exc, failure_type in cases:
            error = backend._raise_provider_failure(exc, "test", "gpt-4o")
            self.assertEqual(error.failure_type, failure_type)

    def test_retries_must_be_positive_integer(self):
        for retries in (0, -1, 1.5, True, "2"):
            with self.subTest(retries=retries), self.assertRaises(ValueError):
                backend._chat_impl(_client(), "gpt-4o", "s", "u", 32, retries, "test")

    def test_invalid_tool_calls_and_incomplete_responses_fail(self):
        bad_message = SimpleNamespace(content="text", tool_calls=[{"id": "", "function": {
            "name": "lookup", "arguments": "{}"}}])
        with self.assertRaises(InfraError) as caught:
            self._chat(SimpleNamespace(choices=[SimpleNamespace(message=bad_message)], usage=None),
                       message_mode=True, return_message=True)
        self.assertEqual(caught.exception.details["response_contract"], "chat_tool_calls_invalid")
        for status in ("failed", "incomplete"):
            response = SimpleNamespace(output_text="answer", output=[], error=None, status=status)
            with self.subTest(status=status), self.assertRaises(InfraError):
                backend._chat_impl(_client(responses=Mock(return_value=response)),
                                   "gpt-5.3-codex", "s", "u", 32, 1, "test")

    def test_truncated_and_filtered_chat_responses_are_not_success_or_retried(self):
        for finish_reason in ("length", "content_filter"):
            response = {"choices": [{"finish_reason": finish_reason,
                                      "message": {"content": "partial private provider text"}}], "usage": None}
            call = Mock(return_value=response)
            with self.subTest(finish_reason=finish_reason), self.assertRaises(InfraError) as caught:
                backend._chat_impl(_client(chat=call), "gpt-4o", "s", "u", 32, 5, "test")
            self.assertEqual(call.call_count, 1)
            self.assertEqual(caught.exception.details["finish_reason"], finish_reason)
            self.assertEqual(caught.exception.details["response_contract"], f"chat_finish_reason_{finish_reason}")
            artifact_text = "\n".join(path.read_text(encoding="utf-8") for path in Path(self.temp.name).rglob("*") if path.is_file())
            self.assertNotIn("partial private provider text", artifact_text)

    def test_incomplete_responses_api_is_a_fixed_nonretryable_contract_failure(self):
        response = {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
                    "output_text": "truncated", "output": [], "_request_id": "resp-safe-id"}
        call = Mock(return_value=response)
        with self.assertRaises(InfraError) as caught:
            backend._chat_impl(_client(responses=call), "gpt-5.3-codex", "s", "u", 32, 5, "test")
        self.assertEqual(call.call_count, 1)
        self.assertEqual(caught.exception.details["response_contract"], "responses_incomplete")
        self.assertEqual(caught.exception.details["incomplete_reason"], "max_output_tokens")
        self.assertEqual(caught.exception.details["request_id"], "resp-safe-id")

    def test_auth_error_fails_fast_and_diagnostics_omit_exception_text(self):
        class Unauthorized(Exception):
            status_code = 401
            request_id = "req-safe"
            code = "unauthorized"

        call = Mock(side_effect=Unauthorized("CREDENTIAL_SENTINEL"))
        with self.assertRaises(InfraError) as caught:
            backend._chat_impl(_client(chat=call), "gpt-4o", "s", "u", 32, 5, "test")
        self.assertEqual(call.call_count, 1)
        self.assertEqual(caught.exception.failure_type, "auth_error")
        artifacts = list(Path(self.temp.name).rglob("*"))
        content = "\n".join(p.read_text(encoding="utf-8") for p in artifacts if p.is_file())
        self.assertNotIn("CREDENTIAL_SENTINEL", content)
        self.assertIn("req-safe", content)
        self.assertNotIn("CREDENTIAL_SENTINEL", "".join(traceback.format_exception(caught.exception)))

    def test_sdk_constructor_disables_internal_retries(self):
        with patch.object(backend, "_role_config", return_value={
            "endpoint": "https://example.invalid", "auth_mode": "openai_compatible", "api_key": "dummy",
            "api_version": "v1", "ad_scope": "scope", "managed_identity_client_id": "",
        }), patch.object(backend, "OpenAI") as ctor:
            backend._make_client("optimizer")
        self.assertEqual(ctor.call_args.kwargs["max_retries"], 0)

    def test_externally_created_openai_client_is_cloned_with_zero_retries(self):
        client = openai.OpenAI(api_key="offline-placeholder", base_url="https://example.invalid", max_retries=4)
        safe_client = backend._client_with_zero_retries(client)
        self.assertEqual(client.max_retries, 4)
        self.assertEqual(safe_client.max_retries, 0)

    def test_sdk_mock_transport_retries_http_503_once_then_succeeds(self):
        requests = []

        def handler(request):
            requests.append(request)
            if len(requests) == 1:
                return httpx2.Response(503, json={"error": {"code": "server_busy"}})
            return httpx2.Response(200, json={
                "id": "chatcmpl-offline", "object": "chat.completion", "created": 1,
                "model": "gpt-4o", "choices": [{"index": 0,
                    "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            })

        transport = httpx2.MockTransport(handler)
        http_client = httpx2.Client(transport=transport)
        client = openai.OpenAI(api_key="offline-placeholder", base_url="https://example.invalid/v1",
                               max_retries=4, http_client=http_client)
        try:
            text, _ = backend._chat_impl(client, "gpt-4o", "s", "u", 32, 3, "test")
        finally:
            client.close()
        self.assertEqual(text, "ok")
        self.assertEqual(len(requests), 2)


if __name__ == "__main__":
    unittest.main()
