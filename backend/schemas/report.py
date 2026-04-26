from pydantic import BaseModel
from typing import Optional, List

class ReportQuery(BaseModel):
    start_date: Optional[str] = None  # YYYY-MM-DD
    end_date: Optional[str] = None
    project_id: Optional[int] = None

class ReportSummary(BaseModel):
    total_count: int = 0
    good_count: int = 0
    bad_count: int = 0
    yield_rate: float = 0.0
    avg_duration: float = 0.0

class DailyStatResponse(BaseModel):
    date: str
    project_id: Optional[int] = None
    good_count: int = 0
    bad_count: int = 0
    total_count: int = 0
    yield_rate: float = 0.0

class TrendData(BaseModel):
    dates: List[str]
    good_counts: List[int]
    bad_counts: List[int]
    yield_rates: List[float]
