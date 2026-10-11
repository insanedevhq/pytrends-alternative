"""Things pytrends can't do: more than 5 keywords on one scale, daily data for years, one run for many tables.

Run:  APIFY_TOKEN=... python examples/compare_many_terms.py
Cost on the Apify Free plan: 12 results ($0.036) + 7 results ($0.021) + 6 results ($0.018).
"""

from pytrends_alternative import TrendsClient

# max_total_charge_usd caps what one run can cost.
trends = TrendsClient(max_total_charge_usd=0.10)

# 12 keywords on one 0-100 scale (Google compares at most 5). Small terms keep 3 significant digits.
terms = ["google", "weather", "coffee", "yoga", "pickleball", "kubernetes", "sourdough starter",
         "matcha latte", "rust programming", "python", "netflix", "tiktok"]
scale = trends.interest_over_time(terms, geo="US", timeframe="today 5-y")
print(scale.drop(columns="isPartial").mean().sort_values(ascending=False))

# Daily points for 5 years (Google gives weekly ones), stitched from 269-day windows: 7 results.
daily = trends.interest_over_time("bitcoin", geo="US", timeframe="today 5-y", daily=True)
print(len(daily), "days")

# Several tables from one run.
result = trends.fetch(["coffee", "tea"], geo="US", timeframe="today 3-m",
                      data=["interestOverTime", "interestBySubregion", "relatedQueries"])
print(result.interest_over_time().tail(3))
print(result.interest_by_region().head(3))
print(result.related_queries()["tea"]["top"].head(3))
print("billed results:", result.billed_results)
