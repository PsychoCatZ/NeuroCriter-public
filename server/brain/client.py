"""Транспорт к API. Настоящий вызов только в AnthropicTransport."""
from dataclasses import dataclass
import json
import os
import urllib.error
import urllib.request

MODEL_VOICE = "claude-haiku-5-5"
MODEL_NIGHT = "claude-sonnet-5-5"
OPENAI_VOICE = "gpt-5.4-mini"
OPENAI_NIGHT = "gpt-5.4"
PRICES = {  # $ за 1M токенов: (вход, выход)
    "claude-haiku-5-5": (0.10, 0.50),     # промпт до 100K токенов
    "claude-sonnet-5-5": (2.00, 10.00),
    "gpt-5.4-mini": (0.75, 4.50),
    "gpt-5.4": (2.50, 15.00),
}


def cost_usd(model, in_tokens, out_tokens):
    p_in, p_out = PRICES[model]
    return in_tokens / 1e6 * p_in + out_tokens / 1e6 * p_out


@dataclass
class Completion:
    text: str
    in_tokens: int
    out_tokens: int


class TransportError(RuntimeError):
    """Ошибка API без ключа, тела запроса или ответа в сообщении."""


class OpenAITransport:
    """Responses API через стандартную библиотеку; хранение запросов отключено."""
    provider = "openai"

    def __init__(self, model=OPENAI_VOICE, max_tokens=1000, effort="none"):
        if model not in (OPENAI_VOICE, OPENAI_NIGHT):
            raise ValueError("Неизвестный тариф модели OpenAI")
        self.api_key = os.environ.get("OPENAI_API_KEY", "")
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY не задан")
        self.model, self.max_tokens, self.effort = model, max_tokens, effort

    def estimate_cost(self, system, user):
        # UTF-8 bytes дают консервативную верхнюю оценку входных токенов.
        return cost_usd(self.model, len((system + user).encode("utf-8")) + 128,
                        self.max_tokens)

    def complete(self, system, user):
        payload = {"model": self.model, "instructions": system, "input": user,
                   "reasoning": {"effort": self.effort},
                   "max_output_tokens": self.max_tokens, "store": False}
        request = urllib.request.Request(
            "https://api.openai.com/v1/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": "Bearer " + self.api_key,
                     "Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            raise TransportError(f"OpenAI HTTP {exc.code}") from None
        except (OSError, ValueError):
            raise TransportError("OpenAI: ошибка соединения или ответа") from None
        try:
            usage = result["usage"]
            text = "".join(part["text"] for item in result.get("output", [])
                           if item.get("type") == "message"
                           for part in item.get("content", [])
                           if part.get("type") == "output_text")
            # Считаем весь вход без скидки кэша и весь выход, включая reasoning.
            return Completion(text, int(usage["input_tokens"]), int(usage["output_tokens"]))
        except (KeyError, TypeError, ValueError):
            raise TransportError("OpenAI: ответ без корректного учёта токенов") from None


class AnthropicTransport:
    """Настоящий вызов Claude. Ключ берётся из ANTHROPIC_API_KEY."""

    def __init__(self, model=MODEL_VOICE, max_tokens=1000, effort="low"):
        # запас под размышления: при 400 иногда приходил пустой текст
        import anthropic  # импорт здесь, чтобы тесты шли без сети и ключа
        self.client = anthropic.Anthropic()
        self.model = model
        self.max_tokens = max_tokens
        self.effort = effort

    def complete(self, system, user):
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            output_config={"effort": self.effort},
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        return Completion(text, resp.usage.input_tokens, resp.usage.output_tokens)


class FakeTransport:
    """Скриптованный ответчик для тестов: отдаёт тексты по очереди."""

    def __init__(self, texts):
        self.texts = list(texts)
        self.calls = 0
        self.last_user = None

    def complete(self, system, user):
        self.calls += 1
        self.last_user = user
        text = self.texts[min(self.calls - 1, len(self.texts) - 1)]
        return Completion(text, max(1, len(system + user) // 4), max(1, len(text) // 4))
