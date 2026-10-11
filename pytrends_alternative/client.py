"""Google Trends data in pytrends-shaped pandas DataFrames, fetched through an Apify Actor.

Every call starts one run of the Apify Actor "Google Trends API & Scraper"
(https://apify.com/insanedev/google-trends-scraper) with the official ``apify-client`` package, waits for it
to finish and converts the dataset rows into the DataFrames and dicts pytrends returns.
"""

from __future__ import annotations

import os
import re
import warnings
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

import pandas as pd
from apify_client import ApifyClient

ACTOR_ID = "insanedev/google-trends-scraper"
DEFAULT_TIMEFRAME = "today 5-y"  # pytrends' build_payload() default
MAX_TERMS = 50  # terms the Actor puts on one 0-100 scale

# The Actor's input schema accepts these values only (checked client-side for a clear error message).
CATEGORIES = frozenset(
    {0, 3, 5, 7, 8, 11, 12, 13, 14, 16, 18, 19, 20, 22, 29, 44, 45, 47, 65, 66, 67, 71, 174, 299, 533, 958}
)
PROPERTIES = frozenset({"", "youtube", "news", "images", "froogle"})
_TIMEFRAME = re.compile(r"^(all|now \d+-[Hd]|today \d+-[my]|\d{4}-\d{2}-\d{2}( \d{4}-\d{2}-\d{2})?)$")
_GEO = re.compile(r"^(|[A-Za-z]{2}(-[A-Za-z0-9]{1,3})?|[Ww]orldwide)$")

# Data types of the Actor and the input flag that turns each on.
DATA_TYPES = {
    "interestOverTime": "includeInterestOverTime",
    "interestBySubregion": "includeInterestBySubregion",
    "interestByCity": "includeInterestByCity",
    "interestByMetro": "includeInterestByMetro",
    "relatedQueries": "includeRelatedQueries",
    "relatedTopics": "includeRelatedTopics",
}
DEFAULT_DATA = ("interestOverTime", "interestBySubregion", "relatedQueries")

# pytrends' `resolution` argument -> the Actor's data type.
RESOLUTIONS = {
    "COUNTRY": "interestBySubregion",  # countries for Worldwide, subregions for a country (as in pytrends)
    "REGION": "interestBySubregion",
    "DMA": "interestByMetro",
    "CITY": "interestByCity",
}

Keywords = Union[str, Sequence[str]]


class TrendsError(RuntimeError):
    """The Actor run failed, or Google Trends data for the query could not be fetched."""


def _field(obj: Any, attr: str, key: str) -> Any:
    """Reads a run field from a dict (apify-client 1.x/2.x) or a model object (apify-client 3.x)."""
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, attr, None)


def _keywords(keywords: Keywords) -> List[str]:
    terms = [keywords] if isinstance(keywords, str) else list(keywords)
    terms = [str(t).strip() for t in terms if str(t).strip()]
    if not terms:
        raise ValueError("Give at least one keyword.")
    if len(terms) > MAX_TERMS:
        raise ValueError(f"The Actor compares at most {MAX_TERMS} keywords on one scale, got {len(terms)}.")
    if len(terms) > 1 and any("," in t for t in terms):
        raise ValueError("Compared keywords can't contain commas (the Actor uses commas to separate them).")
    return terms


def build_run_input(
    keywords: Keywords,
    geo: str = "",
    timeframe: str = DEFAULT_TIMEFRAME,
    cat: Union[int, str] = 0,
    gprop: str = "",
    *,
    data: Iterable[str] = DEFAULT_DATA,
    inc_low_vol: bool = False,
    daily: bool = False,
    samples: Optional[int] = None,
) -> Dict[str, Any]:
    """The Actor input for one pytrends-style payload. Several keywords are compared on one scale."""
    terms = _keywords(keywords)
    timeframe = (timeframe or "").strip()
    if not _TIMEFRAME.match(timeframe):
        raise ValueError(
            f"Unsupported timeframe {timeframe!r}: use 'today 5-y', 'today 3-m', 'now 7-d', 'now 1-H', 'all' "
            "or 'YYYY-MM-DD YYYY-MM-DD'."
        )
    geo = (geo or "").strip()
    if not _GEO.match(geo):
        raise ValueError(f"Unsupported geo {geo!r}: use '' (worldwide), a country ('US') or a region ('US-CA').")
    try:
        category = int(cat or 0)
    except ValueError:
        category = -1
    if category not in CATEGORIES:
        raise ValueError(f"Unsupported category {cat!r}: the Actor accepts 0 or a top-level category id.")
    if gprop not in PROPERTIES:
        raise ValueError(f"Unsupported gprop {gprop!r}: use '', 'images', 'news', 'youtube' or 'froogle'.")
    wanted = set(data)
    unknown = wanted - set(DATA_TYPES)
    if unknown or not wanted:
        raise ValueError(f"Unknown data types {sorted(unknown)}; choose from {sorted(DATA_TYPES)}.")

    run_input: Dict[str, Any] = {
        "searchTerms": [", ".join(terms)] if len(terms) > 1 else terms,
        "isMultiple": len(terms) > 1,
        "timeRange": timeframe,
        "geo": geo or "worldwide",
        "category": str(category) if category else "",
        "property": gprop,
        "includeLowSearchVolumeRegions": bool(inc_low_vol),
        "dailyData": bool(daily),
        "outputFormat": "nested",
    }
    for data_type, flag in DATA_TYPES.items():
        run_input[flag] = data_type in wanted
    if samples is not None:
        run_input["samples"] = int(samples)
    return run_input


def _region_type(resolution: str, geo: str) -> str:
    """The Actor data type behind a pytrends `resolution`."""
    resolution = (resolution or "").upper()
    if resolution not in RESOLUTIONS:
        raise ValueError(f"Unsupported resolution {resolution!r}: use one of {sorted(RESOLUTIONS)}.")
    if resolution == "DMA" and geo.upper().startswith("US-"):
        return "interestBySubregion"  # for a US state, the Actor's subregion map is its metro areas
    return RESOLUTIONS[resolution]


def _at(values: Optional[Sequence[Any]], index: int) -> Any:
    return values[index] if values and index < len(values) else None


def _terms(row: Mapping[str, Any]) -> List[str]:
    terms = row.get("terms")
    if terms:
        return list(terms)
    term = row.get("term") or row.get("searchTerm")
    return [term] if term else []


def _per_term(row: Mapping[str, Any]) -> List[Tuple[str, Mapping[str, Any]]]:
    """(term, source) pairs: the row itself for one term, each `byTerm` entry for a comparison."""
    by_term = row.get("byTerm")
    if by_term:
        return [(entry.get("term"), entry) for entry in by_term]
    terms = _terms(row)
    return [(terms[0] if terms else row.get("searchTerm"), row)]


def _geo_frame(geo_rows: Sequence[Mapping[str, Any]], terms: Sequence[str], inc_geo_code: bool) -> pd.DataFrame:
    """pytrends' interest_by_region() layout: index geoName, optional geoCode, one column per term."""
    if not geo_rows:
        return pd.DataFrame()
    records = []
    for geo_row in geo_rows:
        record: Dict[str, Any] = {"geoName": geo_row.get("geoName")}
        if inc_geo_code:
            if "geoCode" in geo_row:
                record["geoCode"] = geo_row.get("geoCode")
            else:  # cities come with coordinates instead of a code, as in pytrends
                record["coordinates"] = geo_row.get("coordinates")
        for i, term in enumerate(terms):
            record[term] = _at(geo_row.get("value"), i)
        records.append(record)
    return pd.DataFrame.from_records(records).set_index("geoName").sort_index()


def _ranked_queries(items: Optional[Sequence[Mapping[str, Any]]]) -> Optional[pd.DataFrame]:
    """pytrends' top/rising frame (query, value) plus the Actor's formattedValue, isBreakout, likelyNoise."""
    if not items:
        return None  # pytrends returns None when Google has no list
    df = pd.DataFrame([dict(item) for item in items])
    if "isBreakout" not in df:
        df["isBreakout"] = df["formattedValue"].eq("Breakout") if "formattedValue" in df else False
    columns = [c for c in ("query", "value", "formattedValue", "isBreakout", "likelyNoise") if c in df]
    return df[columns]


def _ranked_topics(items: Optional[Sequence[Mapping[str, Any]]]) -> pd.DataFrame:
    """pytrends' related_topics() frame: json_normalize(sep='_') of Google's list (topic_mid, topic_title, ...)."""
    if not items:
        return pd.DataFrame()  # pytrends returns an empty DataFrame here, not None
    df = pd.json_normalize([dict(item) for item in items], sep="_")
    return df.drop(columns=[c for c in ("topicTitle", "topicType", "topicMid") if c in df])


class TrendsResult:
    """Rows of one Actor run, with converters to pytrends' return shapes.

    Use :meth:`TrendsClient.fetch` to get several data types from one run, then call the converters you need.
    """

    def __init__(
        self,
        rows: Iterable[Mapping[str, Any]],
        *,
        run_id: Optional[str] = None,
        dataset_id: Optional[str] = None,
        status_message: Optional[str] = None,
    ) -> None:
        self.rows: List[Dict[str, Any]] = [dict(r) for r in rows]
        self.run_id = run_id
        self.dataset_id = dataset_id
        self.status_message = status_message

    def __repr__(self) -> str:
        return f"TrendsResult(run_id={self.run_id!r}, rows={len(self.rows)}, billed_results={self.billed_results})"

    @property
    def billed_results(self) -> int:
        """Results this run was billed for (one per term and data type that Google returned data for)."""
        return sum(int(row.get("billedResults") or 0) for row in self.rows)

    def _row(self, data_type: str) -> Dict[str, Any]:
        if not self.rows:
            raise TrendsError(f"The Actor run returned no rows ({self.status_message or 'no status message'}).")
        row = self.rows[0]
        if row.get("status") == "error":
            raise TrendsError(row.get("errorMessage") or "Google Trends did not answer; try again later.")
        error = (row.get("dataErrors") or {}).get(data_type)
        if error:
            raise TrendsError(f"{data_type} could not be fetched: {error}")
        return row

    def interest_over_time(self) -> pd.DataFrame:
        """Like pytrends: a ``date`` index, one column per keyword (0-100) and a boolean ``isPartial`` column."""
        row = self._row("interestOverTime")
        points = row.get("interestOverTime_timelineData") or []
        if not points:
            return pd.DataFrame()
        index = pd.DatetimeIndex(pd.to_datetime([float(p["time"]) for p in points], unit="s"), name="date")
        columns = {term: [_at(p.get("value"), i) for p in points] for i, term in enumerate(_terms(row))}
        df = pd.DataFrame(columns, index=index)
        df["isPartial"] = [bool(p.get("isPartial")) for p in points]
        return df.sort_index()

    def interest_by_region(self, resolution: str = "COUNTRY", inc_geo_code: bool = False) -> pd.DataFrame:
        """Like pytrends: a ``geoName`` index and one column per keyword.

        ``COUNTRY``/``REGION`` give countries for a worldwide query and subregions (states) for a country,
        ``DMA`` gives US metro areas and ``CITY`` gives cities.
        """
        geo = str((self.rows[0].get("geo") if self.rows else "") or "")
        data_type = _region_type(resolution, geo)
        row = self._row(data_type)
        if data_type == "interestBySubregion":
            # One term: its map. Up to 5 compared terms: Google's combined map, one value per term.
            # Worldwide queries keep the country map in `interestBy`.
            combined = row.get("interestBySubregion") or row.get("interestBy") or []
            if combined or not row.get("byTerm"):
                return _geo_frame(combined, _terms(row), inc_geo_code)
        elif not row.get("byTerm"):
            return _geo_frame(row.get(data_type) or [], _terms(row), inc_geo_code)
        return self._merge_term_maps(row, data_type, inc_geo_code)

    @staticmethod
    def _merge_term_maps(row: Mapping[str, Any], key: str, inc_geo_code: bool) -> pd.DataFrame:
        """Joins the per-term maps of a comparison (each term on its own 0-100 scale) on geoName."""
        per_term = _per_term(row)
        records: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
        for term, entry in per_term:
            for geo_row in entry.get(key) or []:
                coordinates = geo_row.get("coordinates") or {}
                # Cities have no code and can share a name (Portland, ME and Portland, OR): key them by position.
                ident = (geo_row.get("geoName"), geo_row.get("geoCode"), coordinates.get("lat"), coordinates.get("lng"))
                record = records.setdefault(ident, {"geoName": geo_row.get("geoName")})
                if inc_geo_code:
                    if "geoCode" in geo_row:
                        record["geoCode"] = geo_row.get("geoCode")
                    else:
                        record["coordinates"] = geo_row.get("coordinates")
                record[term] = _at(geo_row.get("value"), 0)
        if not records:
            return pd.DataFrame()
        warnings.warn(
            "No combined map for this comparison: each keyword's column is its own map on its own 0-100 scale.",
            stacklevel=3,
        )
        df = pd.DataFrame.from_records(list(records.values()))
        columns = [c for c in ("geoName", "geoCode", "coordinates") if c in df] + [t for t, _ in per_term if t in df]
        return df[columns].set_index("geoName").sort_index()

    def related_queries(self) -> Dict[str, Dict[str, Optional[pd.DataFrame]]]:
        """Like pytrends: ``{keyword: {"top": DataFrame | None, "rising": DataFrame | None}}``.

        Besides ``query`` and ``value``, each frame has ``formattedValue``, ``isBreakout`` and ``likelyNoise``
        (the Actor's flag for generic filler queries Google mixes into anonymous results).
        """
        row = self._row("relatedQueries")
        return {
            term: {
                "top": _ranked_queries(source.get("relatedQueries_top")),
                "rising": _ranked_queries(source.get("relatedQueries_rising")),
            }
            for term, source in _per_term(row)
        }

    def related_topics(self) -> Dict[str, Dict[str, pd.DataFrame]]:
        """Like pytrends: ``{keyword: {"top": DataFrame, "rising": DataFrame}}`` (empty frames when none)."""
        row = self._row("relatedTopics")
        result = {}
        for term, source in _per_term(row):
            if source.get("relatedTopicsStatus") == "unavailable":
                warnings.warn(
                    f"Google did not serve related topics for {term!r} (its embed quota was used up); "
                    "these are not billed. Try again later.",
                    stacklevel=2,
                )
            result[term] = {
                "top": _ranked_topics(source.get("relatedTopics_top")),
                "rising": _ranked_topics(source.get("relatedTopics_rising")),
            }
        return result


class TrendsClient:
    """Google Trends through the Apify Actor ``insanedev/google-trends-scraper``.

    Each method call is one Actor run, billed per result (one keyword x one data type with data).
    The token comes from ``token`` or the ``APIFY_TOKEN`` environment variable.
    """

    def __init__(
        self,
        token: Optional[str] = None,
        *,
        actor_id: str = ACTOR_ID,
        samples: Optional[int] = None,
        max_total_charge_usd: Optional[float] = None,
        apify_client: Any = None,
    ) -> None:
        if apify_client is None:
            token = token or os.environ.get("APIFY_TOKEN")
            if not token:
                raise TrendsError(
                    "No Apify API token: set the APIFY_TOKEN environment variable or pass token=... "
                    "(Apify Console -> Settings -> API & Integrations)."
                )
            apify_client = ApifyClient(token)
        self._client = apify_client
        self.actor_id = actor_id
        self.samples = samples
        self.max_total_charge_usd = max_total_charge_usd

    def __repr__(self) -> str:  # never shows the token
        return f"TrendsClient(actor_id={self.actor_id!r})"

    def run(self, run_input: Mapping[str, Any]) -> TrendsResult:
        """Runs the Actor with a raw input (any field of its input schema) and returns its rows."""
        kwargs: Dict[str, Any] = {"run_input": dict(run_input)}
        if self.max_total_charge_usd is not None:
            kwargs["max_total_charge_usd"] = Decimal(str(self.max_total_charge_usd))
        run = self._client.actor(self.actor_id).call(**kwargs)
        if run is None:
            raise TrendsError("The Actor run did not finish.")
        run_id = _field(run, "id", "id")
        status = _field(run, "status", "status")
        status = getattr(status, "value", status)
        message = _field(run, "status_message", "statusMessage")
        if status != "SUCCEEDED":
            raise TrendsError(f"Actor run {run_id} ended with status {status}: {message}")
        dataset_id = _field(run, "default_dataset_id", "defaultDatasetId")
        rows = self._client.dataset(dataset_id).iterate_items()
        return TrendsResult(rows, run_id=run_id, dataset_id=dataset_id, status_message=message)

    def fetch(
        self,
        keywords: Keywords,
        geo: str = "",
        timeframe: str = DEFAULT_TIMEFRAME,
        cat: Union[int, str] = 0,
        gprop: str = "",
        *,
        data: Iterable[str] = DEFAULT_DATA,
        inc_low_vol: bool = False,
        daily: bool = False,
    ) -> TrendsResult:
        """Several data types in one run, e.g. ``data=["interestOverTime", "relatedQueries"]``."""
        run_input = build_run_input(
            keywords, geo, timeframe, cat, gprop, data=data, inc_low_vol=inc_low_vol, daily=daily,
            samples=self.samples,
        )
        return self.run(run_input)

    def interest_over_time(
        self,
        keywords: Keywords,
        geo: str = "",
        timeframe: str = DEFAULT_TIMEFRAME,
        cat: Union[int, str] = 0,
        gprop: str = "",
        *,
        daily: bool = False,
    ) -> pd.DataFrame:
        """Interest over time on one 0-100 scale for up to 50 keywords.

        ``daily=True`` returns daily points for periods longer than about 9 months (billed per 269-day window).
        """
        result = self.fetch(keywords, geo, timeframe, cat, gprop, data=["interestOverTime"], daily=daily)
        return result.interest_over_time()

    def interest_by_region(
        self,
        keywords: Keywords,
        geo: str = "",
        timeframe: str = DEFAULT_TIMEFRAME,
        cat: Union[int, str] = 0,
        gprop: str = "",
        *,
        resolution: str = "COUNTRY",
        inc_low_vol: bool = False,
        inc_geo_code: bool = False,
    ) -> pd.DataFrame:
        """Interest by country, subregion, US metro area (``DMA``) or city (``CITY``)."""
        data_type = _region_type(resolution, geo or "")
        result = self.fetch(keywords, geo, timeframe, cat, gprop, data=[data_type], inc_low_vol=inc_low_vol)
        return result.interest_by_region(resolution, inc_geo_code)

    def related_queries(
        self,
        keywords: Keywords,
        geo: str = "",
        timeframe: str = DEFAULT_TIMEFRAME,
        cat: Union[int, str] = 0,
        gprop: str = "",
    ) -> Dict[str, Dict[str, Optional[pd.DataFrame]]]:
        """Top and rising related queries per keyword."""
        return self.fetch(keywords, geo, timeframe, cat, gprop, data=["relatedQueries"]).related_queries()

    def related_topics(
        self,
        keywords: Keywords,
        geo: str = "",
        timeframe: str = DEFAULT_TIMEFRAME,
        cat: Union[int, str] = 0,
        gprop: str = "",
    ) -> Dict[str, Dict[str, pd.DataFrame]]:
        """Top and rising related topics per keyword (best effort: Google often withholds them)."""
        return self.fetch(keywords, geo, timeframe, cat, gprop, data=["relatedTopics"]).related_topics()


class TrendReq:
    """Drop-in for ``pytrends.request.TrendReq``: call ``build_payload()``, then the same methods.

    pytrends' connection arguments (``hl``, ``tz``, ``timeout``, ``proxies``, ``retries``, ``backoff_factor``,
    ``requests_args``) are accepted and ignored: the Actor handles proxies and retries.
    """

    def __init__(
        self,
        hl: str = "en-US",
        tz: int = 360,
        geo: str = "",
        timeout: Any = None,
        proxies: Any = "",
        retries: int = 0,
        backoff_factor: float = 0,
        requests_args: Any = None,
        *,
        token: Optional[str] = None,
        client: Optional[TrendsClient] = None,
        **client_options: Any,
    ) -> None:
        self.client = client or TrendsClient(token, **client_options)
        self.hl = hl
        self.tz = tz
        self.geo = geo
        self.kw_list: List[str] = []
        self._payload: Dict[str, Any] = {}

    def build_payload(
        self,
        kw_list: Sequence[str],
        cat: Union[int, str] = 0,
        timeframe: str = DEFAULT_TIMEFRAME,
        geo: str = "",
        gprop: str = "",
    ) -> None:
        """Stores the query. Unlike pytrends, up to 50 keywords can go on one scale."""
        build_run_input(kw_list, geo, timeframe, cat, gprop)  # validate now, as pytrends does
        self.kw_list = list(kw_list)
        self.geo = geo
        self._payload = {"keywords": self.kw_list, "geo": geo, "timeframe": timeframe, "cat": cat, "gprop": gprop}

    def _need_payload(self) -> Dict[str, Any]:
        if not self._payload:
            raise ValueError("Call build_payload() first.")
        return self._payload

    def interest_over_time(self) -> pd.DataFrame:
        return self.client.interest_over_time(**self._need_payload())

    def interest_by_region(
        self, resolution: str = "COUNTRY", inc_low_vol: bool = False, inc_geo_code: bool = False
    ) -> pd.DataFrame:
        return self.client.interest_by_region(
            **self._need_payload(), resolution=resolution, inc_low_vol=inc_low_vol, inc_geo_code=inc_geo_code
        )

    def related_queries(self) -> Dict[str, Dict[str, Optional[pd.DataFrame]]]:
        return self.client.related_queries(**self._need_payload())

    def related_topics(self) -> Dict[str, Dict[str, pd.DataFrame]]:
        return self.client.related_topics(**self._need_payload())
