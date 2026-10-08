"""Tiny Ollama client: auto-starts the bundled server, auto-pulls models, JSON-schema chat."""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

import requests

from .paths import IS_WIN, MODELS, ollama_bin

_server_proc: subprocess.Popen | None = None


class LLMError(RuntimeError):
    pass


class Ollama:
    def __init__(self, url: str = "http://127.0.0.1:11434", log: Callable[[str], None] | None = None):
        self.url = url.rstrip("/")
        self.log = log or (lambda m: None)

    # ---------------------------------------------------------------- server
    def alive(self) -> bool:
        try:
            return requests.get(self.url + "/api/version", timeout=2).ok
        except requests.RequestException:
            return False

    def ensure_server(self) -> None:
        global _server_proc
        if self.alive():
            return
        exe = ollama_bin()
        if not exe:
            raise LLMError("Ollama not found. Re-run install.bat.")
        env = dict(os.environ)
        env.setdefault("OLLAMA_MODELS", str(MODELS / "ollama"))
        host = self.url.split("://", 1)[-1]
        env["OLLAMA_HOST"] = host
        env.setdefault("OLLAMA_FLASH_ATTENTION", "1")
        env.setdefault("OLLAMA_KV_CACHE_TYPE", "q8_0")
        self.log(f"Starting Ollama server ({exe})")
        _server_proc = subprocess.Popen([exe, "serve"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                        creationflags=0x08000000 if IS_WIN else 0)
        for _ in range(60):
            if self.alive():
                return
            time.sleep(0.5)
        raise LLMError("Ollama server did not start.")

    def has_model(self, name: str) -> bool:
        r = requests.get(self.url + "/api/tags", timeout=10)
        r.raise_for_status()
        names = {m["name"] for m in r.json().get("models", [])}
        return name in names or (":" not in name and f"{name}:latest" in names)

    def ensure_model(self, name: str, on_progress: Callable[[float, str], None] | None = None) -> None:
        self.ensure_server()
        if self.has_model(name):
            return
        self.log(f"Downloading LLM {name} (first run only)")
        with requests.post(self.url + "/api/pull", json={"model": name, "stream": True}, stream=True, timeout=None) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                d = json.loads(line)
                if "error" in d:
                    raise LLMError(d["error"])
                if on_progress and d.get("total"):
                    on_progress(d.get("completed", 0) / d["total"], d.get("status", ""))

    def unload(self, name: str) -> None:
        try:
            requests.post(self.url + "/api/generate", json={"model": name, "keep_alive": 0}, timeout=30)
        except requests.RequestException:
            pass

    # ------------------------------------------------------------------ chat
    def chat_json(self, model: str, system: str, user: str, schema: dict | None = None,
                  images: list[str | Path] | None = None, ctx: int = 16384, temperature: float = 0.2,
                  keep_alive: str | int = "10m", retries: int = 2) -> Any:
        self.ensure_server()
        msg: dict[str, Any] = {"role": "user", "content": user}
        if images:
            msg["images"] = [base64.b64encode(Path(p).read_bytes()).decode() for p in images]
        body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "system", "content": system}, msg],
            "stream": False,
            "think": False,
            "keep_alive": keep_alive,
            "options": {"temperature": temperature, "num_ctx": ctx},
            "format": schema if schema else "json",
        }
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            try:
                r = requests.post(self.url + "/api/chat", json=body, timeout=900)
                if r.status_code == 400 and "think" in r.text.lower():
                    body.pop("think", None)
                    r = requests.post(self.url + "/api/chat", json=body, timeout=900)
                r.raise_for_status()
                content = r.json()["message"]["content"]
                return parse_json(content)
            except Exception as e:  # noqa: BLE001
                last_err = e
                self.log(f"LLM attempt {attempt + 1} failed: {e}")
                body["options"]["temperature"] = min(0.7, temperature + 0.2 * (attempt + 1))
        raise LLMError(f"LLM failed: {last_err}")


def parse_json(text: str) -> Any:
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.S)
        if m:
            return json.loads(m.group(0))
        raise
