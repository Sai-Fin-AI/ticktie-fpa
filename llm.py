"""One connection for every AI provider.

Ollama, Groq, Gemini and OpenRouter all accept the OpenAI-style chat format, so only the
address, key and model name change. Demo mode replays recorded answers and needs no AI at all.
"""
import json
from pathlib import Path

import jsonschema
import requests

DEMO = 'Demo (recorded responses)'
PROVIDERS = {
    DEMO: None,
    'Ollama (this computer)': {'base_url': 'http://localhost:11434/v1', 'model': 'qwen3:8b', 'key': False,
                               'extra': {'reasoning_effort': 'none'}},
    'Groq (free tier)': {'base_url': 'https://api.groq.com/openai/v1', 'model': 'llama-3.3-70b-versatile', 'key': True, 'extra': {}},
    'Google Gemini (free tier)': {'base_url': 'https://generativelanguage.googleapis.com/v1beta/openai', 'model': 'gemini-2.5-flash',
                                  'key': True, 'extra': {}},
    'OpenRouter': {'base_url': 'https://openrouter.ai/api/v1', 'model': 'meta-llama/llama-3.3-70b-instruct:free', 'key': True, 'extra': {}},
}
RECORDINGS = Path(__file__).resolve().parent / 'demo' / 'recordings.json'


class AIError(Exception):
    """A plain-language reason the AI step could not run. Callers fall back to rule-based output."""


def load_recordings(path=RECORDINGS):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


class Client:
    def __init__(self, provider, model=None, api_key=None, recordings=None, recorder=None, timeout=300):
        if provider not in PROVIDERS:
            raise ValueError('Unknown AI provider.')
        self.provider, self.config = provider, PROVIDERS[provider]
        self.model = model or (self.config['model'] if self.config else 'recorded')
        self.api_key, self.timeout = api_key, timeout
        self.recordings = load_recordings() if recordings is None else recordings
        self.recorder = recorder  # dict to collect live answers for demo mode

    @property
    def label(self):
        if self.config is None:
            source = self.recordings.get('model')
            return f'{self.provider} from {source}' if source else self.provider
        return f'{self.provider} · {self.model}'

    def complete(self, task, cache_key, system, user, schema):
        """Return a dict that matches `schema`, or raise AIError."""
        key = f'{task}:{cache_key}'
        if self.config is None:
            entry = self.recordings.get('responses', {}).get(key)
            if entry is None:
                raise AIError('Demo mode only has recorded answers for the sample data with the demo explanations loaded. '
                              'Load them, or switch to a live AI provider.')
            return entry
        if self.config['key'] and not self.api_key:
            raise AIError(f'{self.provider} needs an API key.')
        system = system + '\n\nReply with JSON only, matching this schema:\n' + json.dumps(schema)
        body = {'model': self.model, 'temperature': 0.2, **self.config['extra'],
                'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
                'response_format': {'type': 'json_schema', 'json_schema': {'name': task, 'schema': schema, 'strict': True}}}
        reply = self._post(body)
        if reply.status_code == 400:  # some hosted models only support plain JSON mode
            reply = self._post({**body, 'response_format': {'type': 'json_object'}})
        if reply.status_code == 429:
            raise AIError(f'{self.provider} usage limit reached. Try again later or use demo mode.')
        if reply.status_code in (401, 403):
            raise AIError(f'{self.provider} rejected the API key.')
        if reply.status_code != 200:
            raise AIError(f'{self.provider} returned an error (HTTP {reply.status_code}).')
        try:
            content = reply.json()['choices'][0]['message']['content']
            result = json.loads(content)
            jsonschema.validate(result, schema)
        except (KeyError, IndexError, TypeError, ValueError, jsonschema.ValidationError):
            raise AIError('The AI reply was not in the required format.') from None
        if self.recorder is not None:
            self.recorder[key] = result
        return result

    def _post(self, body):
        headers = {'Authorization': f'Bearer {self.api_key}'} if self.api_key else {}
        try:
            return requests.post(self.config['base_url'] + '/chat/completions', json=body, headers=headers, timeout=self.timeout)
        except requests.ConnectionError:
            hint = ' Is Ollama running?' if 'localhost' in self.config['base_url'] else ''
            raise AIError(f'Could not reach {self.provider}.{hint}') from None
        except requests.Timeout:
            raise AIError(f'{self.provider} took too long to answer.') from None
