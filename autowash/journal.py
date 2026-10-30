"""Historique des actions d'Autowash (%LOCALAPPDATA%\\Autowash\\history.jsonl) et annulation."""
from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Callable

from .system import data_dir, restore_recycled

_LOCK = threading.Lock()
_UNDOERS: dict[str, Callable[[dict], str]] = {}


def register_undo(kind: str, func: Callable[[dict], str]) -> None:
    """Associe un type d'annulation (champ « kind » de Action.undo) à la fonction qui l'exécute."""
    _UNDOERS[kind] = func


def _path():
    return data_dir() / "history.jsonl"


def _append(line: dict) -> None:
    with _LOCK, open(_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def record(category: str, label: str, action: str, ok: bool, message: str, undo: dict | None = None) -> None:
    _append({"id": uuid.uuid4().hex[:12], "time": time.strftime("%Y-%m-%d %H:%M:%S"), "category": category,
             "label": label, "action": action, "ok": ok, "message": message, "undo": undo if ok else None})


def entries(limit: int = 300) -> list[dict]:
    """Actions enregistrées, de la plus récente à la plus ancienne, avec leur état « annulée »."""
    if not _path().exists():
        return []
    items: dict[str, dict] = {}
    with _LOCK, open(_path(), encoding="utf-8") as f:
        for line in f:
            try:
                data = json.loads(line)
            except ValueError:
                continue
            if "undone_id" in data:
                if data["undone_id"] in items:
                    items[data["undone_id"]]["undone"] = data["time"]
            else:
                items[data["id"]] = data
    return list(reversed(items.values()))[:limit]


def undo(entry: dict) -> str:
    spec = entry.get("undo")
    if not spec or entry.get("undone"):
        raise RuntimeError("Cette action ne peut pas (ou plus) être annulée.")
    func = _UNDOERS.get(spec["kind"])
    if not func:
        raise RuntimeError(f"Annulation « {spec['kind']} » inconnue.")
    message = func(spec)
    _append({"undone_id": entry["id"], "time": time.strftime("%Y-%m-%d %H:%M:%S")})
    record(entry["category"], entry["label"], f"Annuler : {entry['action']}", True, message)
    return message


def record_score(score: int, critical: int, warning: int) -> None:
    path = data_dir() / "scores.json"
    with _LOCK:
        history = json.loads(path.read_text("utf-8")) if path.exists() else []
        history.append({"time": time.strftime("%Y-%m-%d %H:%M"), "score": score,
                        "critical": critical, "warning": warning})
        path.write_text(json.dumps(history[-365:], ensure_ascii=False), "utf-8")


def scores() -> list[dict]:
    path = data_dir() / "scores.json"
    with _LOCK:
        return json.loads(path.read_text("utf-8")) if path.exists() else []


register_undo("recycle", lambda spec: "\n".join(restore_recycled(p) for p in spec["paths"]))
