"""Interest over time, regions and related queries for two keywords.

Run:  APIFY_TOKEN=... python examples/quickstart.py
Cost on the Apify Free plan: 2 keywords x 3 data types = 6 results = $0.018.
"""

from pytrends_alternative import TrendsClient

trends = TrendsClient()  # reads APIFY_TOKEN from the environment

# One run per call, like one request per call in pytrends.
df = trends.interest_over_time(["python", "javascript"], geo="US", timeframe="today 12-m")
print(df.tail())

regions = trends.interest_by_region(["python", "javascript"], geo="US", inc_geo_code=True)
print(regions.sort_values("python", ascending=False).head())

queries = trends.related_queries(["python", "javascript"], geo="US", timeframe="today 12-m")
rising = queries["python"]["rising"]
if rising is not None:
    # likelyNoise marks generic filler queries Google mixes into anonymous results.
    print(rising[~rising["likelyNoise"]].head(10))
