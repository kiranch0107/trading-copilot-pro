from dataclasses import dataclass
from pathlib import Path
from typing import Set

@dataclass
class Config:
    # Files
    alert_log_file: Path = Path("alert_history.json")
    alert_archive_file: Path = Path("alert_archive.json")

    # Dedupe policy
    dedupe_by: str = "id"  # "id" or "entry_target"
    dedupe_window_minutes: int = 60  # 1 hour

    # Recent alerts window
    recent_alert_window_minutes: int = 7 * 24 * 60  # 7 days

    # Option selection thresholds
    min_dte: int = 1
    max_dte: int = 30
    min_adx: int = 25
    min_option_volume: int = 50
    min_option_oi: int = 20

    # Force allow same-day expiries for specific tickers
    force_allow_same_day: Set[str] = frozenset()

    # Debug / behavior
    debug_level: str = "normal"  # "quiet", "normal", "verbose"

    def validate(self):
        if self.dedupe_by not in ("id", "entry_target"):
            raise ValueError("dedupe_by must be 'id' or 'entry_target'")
        if self.dedupe_window_minutes < 0:
            raise ValueError("dedupe_window_minutes must be >= 0")
        if self.recent_alert_window_minutes < 0:
            raise ValueError("recent_alert_window_minutes must be >= 0")
        if self.min_dte < 0 or self.max_dte < 0:
            raise ValueError("DTE bounds must be non-negative")
        if self.min_option_volume < 0 or self.min_option_oi < 0:
            raise ValueError("Option liquidity thresholds must be non-negative")
