# storage.py
import json
import time
from pathlib import Path
from typing import Any, List, Dict
from datetime import datetime
import os

# Use top-level import (files must be in the same directory)
from config import Config

def make_json_serializable(obj: Any) -> Any:
    """Convert common non-serializable types to JSON-friendly types."""
    try:
        import numpy as np
        import pandas as pd
    except Exception:
        np = None
        pd = None

    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if np is not None:
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        if isinstance(obj, (np.str_,)):
            return str(obj)
    if isinstance(obj, (datetime,)):
        return obj.isoformat()
    if pd is not None and isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): make_json_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [make_json_serializable(v) for v in obj]
    try:
        return str(obj)
    except Exception:
        return None

def atomic_write(path: Path, data: Any) -> None:
    """
    Atomically write JSON-serializable data to path using a temp file and rename.
    """
    tmp = path.with_suffix(f".tmp.{int(time.time()*1000)}")
    text = json.dumps(make_json_serializable(data), indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(text)
    tmp.replace(path)

def safe_read_json(path: Path) -> List[Dict]:
    """
    Read JSON array from path. If file is missing, return [].
    If file is corrupted, back it up and return [].
    """
    if not path.exists():
        return []
    try:
        raw = path.read_text()
        data = json.loads(raw)
        if isinstance(data, list):
            return data
        return list(data)
    except json.JSONDecodeError:
        ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        corrupt_path = path.with_suffix(f".corrupt.{ts}")
        try:
            path.replace(corrupt_path)
        except Exception:
            try:
                os.rename(path, corrupt_path)
            except Exception:
                pass
        return []
    except Exception:
        return []

def read_alerts(cfg: Config) -> List[Dict]:
    return safe_read_json(cfg.alert_log_file)

def write_alerts(cfg: Config, alerts: List[Dict]) -> None:
    atomic_write(cfg.alert_log_file, alerts)

def append_alert(cfg: Config, alert: Dict) -> None:
    alerts = read_alerts(cfg)
    alerts.append(alert)
    write_alerts(cfg, alerts)

def write_archive(cfg: Config, removed: List[Dict]) -> None:
    if not removed:
        return
    archive = safe_read_json(cfg.alert_archive_file)
    archive.extend(removed)
    atomic_write(cfg.alert_archive_file, archive)
