"""System faults the operator can switch on from the control panel to watch Datadog catch them. Pure: no DB, no HTTP.

Pond conditions break the water; these break ShrimpWatch itself (a slow worker, a failing API, a silent feed), so the
"Sức khoẻ hệ thống" dashboard group and its monitors can be shown reacting. Each switches itself off after
`duration_min`, so a forgotten demo fault does not keep alerting.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class FaultType(str, Enum):
    SLOW_WORKER = "slow_worker"
    API_ERRORS = "api_errors"
    FEED_STOPPED = "feed_stopped"


@dataclass(frozen=True)
class Fault:
    label: str
    # What breaks
    description: str
    # Where Datadog shows it
    watch: str
    duration_min: int = 10


# Added to every worker cycle: the "worker slow" monitor fires above 25 s on its 5-minute average
SLOW_WORKER_DELAY_SEC = 30
# Share of API requests answered 503: the "API error rate" monitor fires above 5 % over 10 minutes
API_ERROR_SHARE = 0.5

FAULTS: dict[FaultType, Fault] = {
    FaultType.SLOW_WORKER: Fault(
        "Worker chạy chậm",
        f"Mỗi vòng kiểm tra ao chậm thêm {SLOW_WORKER_DELAY_SEC} giây: sự cố mở và đóng trễ hơn",
        "Biểu đồ thời gian một vòng worker vượt 25 giây; monitor Worker chạy chậm báo sau khoảng 5 phút",
    ),
    FaultType.API_ERRORS: Fault(
        f"API lỗi {API_ERROR_SHARE:.0%}",
        f"{API_ERROR_SHARE:.0%} yêu cầu tới API trả lỗi 503, kể cả số đo gửi lên (phần điều khiển này vẫn chạy)",
        "Biểu đồ tỉ lệ lỗi API vượt 5%; monitor Tỉ lệ lỗi API báo; trace lỗi trong APM",
    ),
    FaultType.FEED_STOPPED: Fault(
        "Mất dữ liệu cảm biến",
        "Simulator ngừng gửi số đo của mọi ao, như khi bộ thu hoặc mạng ở trại hỏng",
        "Biểu đồ đầu dò của mọi ao về 0%; monitor Không nhận được dữ liệu cảm biến báo sau 5 phút",
    ),
}


def is_active(doc: dict[str, Any] | None, now: datetime) -> bool:
    """A stored fault is on until its `until` time."""
    until = (doc or {}).get("until")
    return isinstance(until, datetime) and until > now
