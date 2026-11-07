"""Migration contract: Responses, accounting, routing, failure and existing state."""
import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from app.wiring import Config, build_service
from app.storage import Storage
from brain.budget import Budget
from brain.claude_brain import ClaudeBrain
from brain.client import OpenAITransport, TransportError, cost_usd
from brain.recorder import CallLog
from engine.model import Pet
from engine.rng import Rng


class OpenAITests(unittest.TestCase):
    @patch.dict(os.environ, {"OPENAI_API_KEY": "fake-test-secret"})
    def test_responses_contract_and_usage(self):
        response = {"output": [{"type": "reasoning"}, {"type": "message", "content": [
            {"type": "output_text", "text": "Ответ"}]}],
            "usage": {"input_tokens": 2000, "output_tokens": 100}}
        with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(response).encode())) as call:
            result = OpenAITransport().complete("system", "user")
        request = call.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, "https://api.openai.com/v1/responses")
        self.assertEqual(body["instructions"], "system")
        self.assertEqual(body["input"], "user")
        self.assertFalse(body["store"])
        self.assertEqual((result.text, result.in_tokens, result.out_tokens), ("Ответ", 2000, 100))
        self.assertAlmostEqual(cost_usd("gpt-5.4-mini", 2000, 100), .00195)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "fake-test-secret"})
    def test_http_failure_is_safe_and_returns_engine_fallback(self):
        error = urllib.error.HTTPError("url", 401, "fake-test-secret", {}, None)
        transport = OpenAITransport()
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaisesRegex(TransportError, "OpenAI HTTP 401") as caught:
                transport.complete("system", "user")
            self.assertNotIn("fake-test-secret", str(caught.exception))
            brain = ClaudeBrain(transport, CallLog(), Budget(), Rng(2), model=transport.model)
            brain.choose("owner_action:play", [("accept", 1)], {"pet": Pet.new(2)})
            self.assertEqual(brain.last.source, "fallback:api")

    def test_config_requires_key_and_rejects_unknown_provider(self):
        with self.assertRaises(SystemExit):
            Config.from_env({"NEUROCRITTER_DEV": "1", "NEUROCRITTER_BRAIN": "openai"})
        with self.assertRaises(SystemExit):
            Config.from_env({"NEUROCRITTER_DEV": "1", "NEUROCRITTER_BRAIN": "typo"})
        cfg = Config.from_env({"NEUROCRITTER_DEV": "1", "OPENAI_API_KEY": "fake"})
        self.assertEqual(cfg.brain, "openai")
        env = {"NEUROCRITTER_DEV": "1", "OPENAI_API_KEY": "fake",
               "NEUROCRITTER_PROVIDER": "openai", "NEUROCRITTER_BRAIN": "claude"}
        self.assertEqual(Config.from_env(env).brain, "openai")
        env["NEUROCRITTER_BRAIN"] = "stub"
        self.assertEqual(Config.from_env(env).brain, "stub")

    @patch.dict(os.environ, {"OPENAI_API_KEY": "fake-test-secret"})
    def test_wiring_preserves_pet_and_routes_both_models(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            storage = Storage(str(Path(tmp) / "pet.sqlite"))
            pet = Pet.new(12)
            pet.name = "Пиксель"
            pet.memory.self_narrative = "Мой лаборант хороший."
            storage.save_pet(pet)
            storage.close()
            cfg = Config(Path(tmp), "token-is-long-enough", False, "openai", 5)
            with patch("app.wiring._build_push", return_value=None):
                service = build_service(cfg)
            brain = service._brain(Rng(1))
            self.assertEqual(brain.model, "gpt-5.4-mini")
            self.assertEqual(brain.night.caller.model, "gpt-5.4")
            self.assertEqual(service.provider, "openai")
            saved = service.storage.load_pet()
            self.assertEqual(saved.name, "Пиксель")
            self.assertEqual(saved.memory.self_narrative, pet.memory.self_narrative)
            with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps({
                "output": [{"type": "message", "content": [{"type": "output_text", "text":
                    "ДЕЙСТВИЕ: играть\nРЕПЛИКА: ууу\nТОН: радостно"}]}],
                "usage": {"input_tokens": 100, "output_tokens": 20}}).encode())):
                brain.choose("owner_action:play", [("accept", 1)], {"pet": saved})
            self.assertEqual(brain.last.source, "openai")
            self.assertGreater(service.budget.spent_month(brain.clock()), 0)
            self.assertEqual(brain.calllog.count(), 1)
            service.storage.close()
            brain.calllog.db.close()
            service.budget.db.close()

    @patch.dict(os.environ, {"OPENAI_API_KEY": "fake-test-secret"})
    def test_budget_checked_before_every_openai_attempt(self):
        budget = Budget(monthly_limit=.001)
        transport = OpenAITransport()
        brain = ClaudeBrain(transport, CallLog(), budget, Rng(2), model=transport.model)
        with patch("urllib.request.urlopen") as call:
            brain.choose("owner_action:play", [("accept", 1)], {"pet": Pet.new(2)})
        call.assert_not_called()
        self.assertEqual(brain.last.source, "fallback:tired")
