import dataclasses
from types import SimpleNamespace

from config import settings


class FakeAPIError(Exception):
    def __init__(self, code, message="boom"):
        super().__init__(f"{code} {message}")
        self.code = code


def resp(text, finish="STOP"):
    return SimpleNamespace(text=text, candidates=[SimpleNamespace(finish_reason=finish)])


class FakeModels:
    def __init__(self, script):
        self.script, self.calls = list(script), []

    async def generate_content(self, model, contents, config):
        self.calls.append((model, contents, config))
        item = self.script.pop(0) if self.script else resp("default")
        if isinstance(item, Exception):
            raise item
        return item


def make_factory(*scripts):
    clients = [SimpleNamespace(aio=SimpleNamespace(models=FakeModels(s))) for s in scripts]
    it = iter(clients)
    return (lambda key, opts: next(it)), clients


def cfg(**kw):
    base = dict(gemini_keys=("k1", "k2"), max_wait_seconds=3, cooldown_429=1, cooldown_5xx=1,
                cooldown_network=1, cooldown_auth=30, cooldown_daily=30, max_continuations=2,
                queue_poll_seconds=1)
    base.update(kw)
    return dataclasses.replace(settings, **base)
