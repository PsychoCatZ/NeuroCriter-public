# Настройка провайдера

В закрытом файле окружения вашей установки задайте:

```text
NEUROCRITTER_BRAIN=openai
NEUROCRITTER_PROVIDER=openai
OPENAI_API_KEY=
NEUROCRITTER_MODEL_VOICE=gpt-5.4-mini
NEUROCRITTER_MODEL_NIGHT=gpt-5.4
NEUROCRITTER_MONTHLY_LIMIT=5
```

Значение ключа вводится только локально в закрытые настройки; в примере оно пустое.
Убедитесь, что модели доступны вашему аккаунту и цены в `server/brain/client.py`
соответствуют выбранным моделям. При включении мозг получает игровые события,
память и текст обращений хозяина. Ответы и бюджет записываются локально.

Для Anthropic задайте `NEUROCRITTER_BRAIN=claude`, `NEUROCRITTER_PROVIDER=claude`
и `ANTHROPIC_API_KEY`; уберите `NEUROCRITTER_MODEL_*`, чтобы использовать
предустановленные модели этого транспорта, или укажите совместимые модели.
Для работы без API установите `NEUROCRITTER_BRAIN=stub`.
Смена провайдера не требует заново создавать питомца.
