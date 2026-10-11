# pytrends-alternative

Google Trends data for Python, returned in the same pandas shapes as pytrends, without the HTTP 429
`TooManyRequestsError` on your machine.

The requests don't come from your IP. Each call runs the Apify Actor
[Google Trends API & Scraper](https://apify.com/insanedev/google-trends-scraper) through the official
[`apify-client`](https://pypi.org/project/apify-client/) package and converts its rows into pytrends'
DataFrames and dicts.

> Disclosure: I'm insanedev, the author of that Actor. It is a paid Actor (you pay per result, see
> [Cost](#cost)), and this library is a thin MIT-licensed wrapper around it.

## Why

pytrends is no longer maintained: its GitHub repository is archived (read-only), and the last commit is from August 2024.

It also keeps hitting HTTP 429. Google Trends allows very few requests per IP, and since 1 October 2026 Google has
been throttling the widget requests made with tokens from the `explore` endpoint, which is the flow pytrends uses.
In our tests this lasted for long stretches and hit every IP we tried. In one test on 9 October, from the same fresh
IPs at the same moment, 26 of 30 such requests got a 429, while Google's public embed widgets for the same terms
answered 30 of 30.

The Actor sends its requests from rotating residential IPs, retries on a new IP when Google refuses one, and falls
back to the embed widgets, which return the same data.

## Install

```bash
pip install git+https://github.com/insanedevhq/pytrends-alternative
export APIFY_TOKEN=...   # Apify Console -> Settings -> API & Integrations
```

It needs Python 3.9+ and depends only on `apify-client` and `pandas`. You also need an
[Apify account](https://console.apify.com/sign-up).

## Quickstart

```python
from pytrends_alternative import TrendsClient

trends = TrendsClient()  # reads APIFY_TOKEN

df = trends.interest_over_time(["python", "javascript"], geo="US", timeframe="today 12-m")
regions = trends.interest_by_region(["python"], geo="US", inc_geo_code=True)
queries = trends.related_queries(["python"], geo="US")

print(df.tail())
print(queries["python"]["rising"].head())
```

`df` has the layout pytrends returns: a `date` index, one 0-100 column per keyword and an `isPartial` column.

## Migrating from pytrends

Change the import. `build_payload()` and the methods keep their names, arguments and return shapes:

```python
# from pytrends.request import TrendReq
from pytrends_alternative import TrendReq

pytrends = TrendReq(hl="en-US", tz=360)
pytrends.build_payload(["coffee", "tea"], cat=0, timeframe="today 3-m", geo="US", gprop="")
df = pytrends.interest_over_time()
```

| pytrends | pytrends-alternative | Actor input → output field |
|---|---|---|
| `TrendReq(hl, tz, timeout, proxies, retries, backoff_factor)` | `TrendReq(...)` accepts and ignores these; or `TrendsClient(token)` | - |
| `build_payload(kw_list, cat, timeframe, geo, gprop)` | same, up to 50 keywords | `searchTerms` + `isMultiple`, `category`, `timeRange`, `geo`, `property` |
| `interest_over_time()` | same shape | `interestOverTime_timelineData` |
| `interest_by_region(resolution="COUNTRY")` / `"REGION"` | same: countries for worldwide, subregions for a country | `interestBy` / `interestBySubregion` |
| `interest_by_region(resolution="DMA")` | same (US metro areas) | `interestByMetro` |
| `interest_by_region(resolution="CITY")` | same | `interestByCity` |
| `interest_by_region(inc_low_vol=True, inc_geo_code=True)` | same | `includeLowSearchVolumeRegions` |
| `related_queries()` | same dict; frames also have `formattedValue`, `isBreakout`, `likelyNoise` | `relatedQueries_top`, `relatedQueries_rising` |
| `related_topics()` | same dict (`topic_title`, `topic_mid`, …) | `relatedTopics_top`, `relatedTopics_rising` |
| `dailydata.get_daily_data("bitcoin", 2021, 1, 2025, 12)` | `interest_over_time("bitcoin", timeframe="2021-01-01 2025-12-31", daily=True)` | `dailyData` |
| `trending_searches()`, `realtime_trending_searches()` | not covered; see [Google Trends Trending Now API](https://apify.com/insanedev/google-trends-trending-now) | - |
| `suggestions()`, `top_charts()`, `categories()`, `get_historical_interest()` | not covered | - |

Differences from pytrends:

- Each method call is one Actor run, and in our checks a small query took about 7 to 20 seconds. To get several
  tables from one run, use `fetch()` (below).
- Up to 50 keywords go on one 0-100 scale. With more than 5, values keep 3 significant digits, so the columns
  are floats.
- Every timeline is the per-point median of 3 independent samples. Google computes Trends from a random sample of
  searches, so two single requests can return different numbers. `TrendsClient(samples=1)` (up to 10) changes the
  count.
- Google mixes generic queries such as "pet care tips" into the related queries it shows anonymous visitors.
  The `likelyNoise` column marks them, and `rising[~rising["likelyNoise"]]` drops them.
- When you compare keywords, their metro-area and city maps come one per keyword, each on its own 0-100 scale,
  and the library warns you about it. For up to 5 compared keywords, the country and subregion maps are Google's
  combined map, as in pytrends.
- Some pytrends inputs fail before any run starts: hourly ranges such as `"2017-02-06T10 2017-02-12T07"`,
  subcategories (`cat` takes top-level ids only) and DMA codes as `geo`.
- Errors raise `TrendsError` with the reason instead of pytrends' `TooManyRequestsError`, so you don't need a
  retry loop around each call.

## Several tables from one run

```python
result = trends.fetch(
    ["coffee", "tea"], geo="US", timeframe="today 3-m",
    data=["interestOverTime", "interestBySubregion", "relatedQueries"],
)
over_time = result.interest_over_time()
by_state = result.interest_by_region()
related = result.related_queries()
print(result.billed_results)  # 6: 2 keywords x 3 data types
```

`data` takes the Actor's data types: `interestOverTime`, `interestBySubregion`, `interestByCity`,
`interestByMetro`, `relatedQueries`, `relatedTopics`. For other input fields, pass a raw input to
`trends.run({...})`. `result.rows` holds the Actor's rows as they came.

[`examples/`](examples) has a quickstart, a before-and-after pytrends migration, and a script with 12 keywords on
one scale and daily data.

## Cost

The Actor charges per result: one search term × one data type that Google returned data for. Errors and empty
results are free, and there's no subscription or minimum.

| Your Apify plan | Price per 1,000 results | Default data (3 types) per 1,000 terms |
|---|---|---|
| Free / Starter | $3.00 | $9.00 |
| Scale | $2.50 | $7.50 |
| Business, Enterprise | $2.00 | $6.00 |

Examples on the Free plan: 1 term, timeline only = $0.003; 20 terms with default data = $0.18; 12 terms on one
scale, timeline only = $0.036; 1 term, daily data for 5 years = $0.021. New Apify accounts get free monthly
platform credit you can use to try it.

`TrendsClient(max_total_charge_usd=0.10)` caps what a run can cost; the Actor stops when it reaches the cap.

## Limits and caveats

- Unlike pytrends, it costs money: it calls a paid Apify Actor and needs an Apify account and API token. Keep the
  token in `APIFY_TOKEN` or a secrets manager, out of your code.
- A call takes seconds, so put several keywords into one call where you can.
- Google still refuses some requests. The Actor retries and falls back, but a data type can still fail; it isn't
  billed, the row's `dataErrors` says why, and this library raises `TrendsError`. In two 1,000-query tests on
  28 September 2026 (200 terms × 5 countries), 1,000 and 999 queries completed. Google's throttling has changed
  since then, as described above.
- Related topics are best effort. Google serves them to anonymous visitors only while its embed quota lasts; when
  the quota is used up you get empty frames and a warning, and you aren't billed for them.
- Values are relative 0-100 indexes rather than search counts, and because Google samples its data they can differ
  slightly from what the Google Trends website shows.
- This project isn't affiliated with Google. The data is aggregated, anonymous public statistics; you're responsible
  for how you use it, so check Google's terms and the laws that apply to you.

## Other options

- Google announced an official [Google Trends API](https://developers.google.com/search/blog/2025/07/trends-api) in
  July 2025. It's in alpha, and access is by application.
- You can call the Actor directly from any language through the [Apify API](https://docs.apify.com/api/v2), or
  from Make, Zapier, n8n or an MCP client. The [Actor page](https://apify.com/insanedev/google-trends-scraper)
  lists every input and output field.

## Development

```bash
pip install -e ".[test]"
pytest
```

The tests mock `apify-client` and use rows recorded from real runs, so they need no network or token.

## License

MIT © insanedev
