"""The same script before and after the switch from pytrends.

Only the import and the token change; build_payload() and the methods keep their names, arguments and
return shapes.

Run:  APIFY_TOKEN=... python examples/migrate_from_pytrends.py
"""

# Before:
#   from pytrends.request import TrendReq
#   pytrends = TrendReq(hl="en-US", tz=360, timeout=(10, 25), retries=2, backoff_factor=0.1)
# After (hl, tz, timeout, proxies, retries and backoff_factor are accepted and ignored):
from pytrends_alternative import TrendReq

pytrends = TrendReq(hl="en-US", tz=360, timeout=(10, 25), retries=2, backoff_factor=0.1)

# Unchanged.
pytrends.build_payload(["coffee", "tea"], cat=0, timeframe="today 3-m", geo="US", gprop="")

# Unchanged: a DataFrame with a `date` index, one column per keyword and `isPartial`.
over_time = pytrends.interest_over_time()
print(over_time.tail())

# Unchanged: index `geoName`, one column per keyword (+ geoCode).
by_state = pytrends.interest_by_region(resolution="REGION", inc_low_vol=False, inc_geo_code=True)
print(by_state.head())

# Unchanged: {keyword: {"top": DataFrame | None, "rising": DataFrame | None}}.
related = pytrends.related_queries()
for keyword, lists in related.items():
    print(keyword, None if lists["top"] is None else lists["top"]["query"].tolist()[:5])

# What changes: each call above is one run of the Apify Actor (a few seconds, billed per result), and
# failures raise pytrends_alternative.TrendsError instead of pytrends' TooManyRequestsError.
