"""Google Trends data for Python in pytrends' shapes, without pytrends' HTTP 429 errors.

Data comes from the Apify Actor "Google Trends API & Scraper" (insanedev/google-trends-scraper),
called through the official apify-client package. Set APIFY_TOKEN before use.
"""

from .client import (
    ACTOR_ID,
    DATA_TYPES,
    TrendReq,
    TrendsClient,
    TrendsError,
    TrendsResult,
    build_run_input,
)

__version__ = "0.1.0"

__all__ = [
    "ACTOR_ID",
    "DATA_TYPES",
    "TrendReq",
    "TrendsClient",
    "TrendsError",
    "TrendsResult",
    "build_run_input",
    "__version__",
]
