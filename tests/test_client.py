import copy
import enum
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

from pytrends_alternative import TrendReq, TrendsClient, TrendsError, TrendsResult, build_run_input

from .conftest import fake_apify, run_input_of

FLAGS = [
    "includeInterestOverTime",
    "includeInterestBySubregion",
    "includeInterestByCity",
    "includeInterestByMetro",
    "includeRelatedQueries",
    "includeRelatedTopics",
]


def only(flag):
    return {f: f == flag for f in FLAGS}


# --- input mapping -------------------------------------------------------------------------------------------------


def test_single_keyword_input():
    run_input = build_run_input("python")
    assert run_input["searchTerms"] == ["python"]
    assert run_input["isMultiple"] is False
    assert run_input["geo"] == "worldwide"
    assert run_input["timeRange"] == "today 5-y"
    assert run_input["category"] == ""
    assert run_input["property"] == ""
    assert run_input["outputFormat"] == "nested"
    assert "samples" not in run_input


def test_comparison_input_maps_pytrends_arguments():
    run_input = build_run_input(
        ["python", "javascript"], geo="US-CA", timeframe="2024-01-01 2024-06-30", cat=5, gprop="youtube",
        data=["interestOverTime", "relatedQueries"], inc_low_vol=True, daily=True, samples=1,
    )
    assert run_input["searchTerms"] == ["python, javascript"]
    assert run_input["isMultiple"] is True
    assert run_input["geo"] == "US-CA"
    assert run_input["timeRange"] == "2024-01-01 2024-06-30"
    assert run_input["category"] == "5"
    assert run_input["property"] == "youtube"
    assert run_input["includeLowSearchVolumeRegions"] is True
    assert run_input["dailyData"] is True
    assert run_input["samples"] == 1
    assert run_input["includeInterestOverTime"] and run_input["includeRelatedQueries"]
    assert not run_input["includeInterestBySubregion"] and not run_input["includeRelatedTopics"]


def test_fifty_keywords_go_on_one_scale():
    run_input = build_run_input([f"term {i}" for i in range(50)])
    assert run_input["searchTerms"][0].count(", ") == 49


@pytest.mark.parametrize(
    "kwargs",
    [
        {"keywords": []},
        {"keywords": [f"t{i}" for i in range(51)]},
        {"keywords": ["a, b", "c"]},
        {"keywords": "x", "timeframe": "2017-02-06T10 2017-02-12T07"},
        {"keywords": "x", "geo": "US-NY-501"},
        {"keywords": "x", "cat": 1227},
        {"keywords": "x", "gprop": "maps"},
        {"keywords": "x", "data": ["trendingSearches"]},
    ],
)
def test_invalid_payloads_fail_before_any_run(kwargs):
    with pytest.raises(ValueError):
        build_run_input(**kwargs)


# --- token handling --------------------------------------------------------------------------------------------------


def test_token_from_environment_and_never_in_repr(monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "apify_api_SECRET")
    with patch("pytrends_alternative.client.ApifyClient") as apify_cls:
        client = TrendsClient()
    apify_cls.assert_called_once_with("apify_api_SECRET")
    assert "SECRET" not in repr(client)


def test_explicit_token_wins(monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "from-env")
    with patch("pytrends_alternative.client.ApifyClient") as apify_cls:
        TrendsClient("explicit")
    apify_cls.assert_called_once_with("explicit")


def test_missing_token_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    with pytest.raises(TrendsError, match="APIFY_TOKEN"):
        TrendsClient()


# --- interest over time ----------------------------------------------------------------------------------------------


def test_interest_over_time_has_pytrends_shape(make_client, comparison_rows):
    client, apify = make_client(comparison_rows)
    df = client.interest_over_time(["coffee", "tea"], geo="US", timeframe="today 1-m")

    apify.actor.assert_called_once_with("insanedev/google-trends-scraper")
    apify.dataset.assert_called_once_with("DATASET123")
    run_input = run_input_of(apify)
    assert run_input["searchTerms"] == ["coffee, tea"]
    assert {f: run_input[f] for f in FLAGS} == only("includeInterestOverTime")

    assert list(df.columns) == ["coffee", "tea", "isPartial"]
    assert df.index.name == "date"
    assert isinstance(df.index, pd.DatetimeIndex)
    assert df.index[0] == pd.Timestamp("2026-09-10")
    assert str(df["coffee"].dtype) == "int64"
    assert df["isPartial"].dtype == bool
    assert df["isPartial"].tolist() == [False, False, False, True]
    assert df.loc["2026-09-10", "coffee"] == 67 and df.loc["2026-09-10", "tea"] == 31


def test_columns_follow_the_terms_the_actor_returned(single_rows):
    row = copy.deepcopy(single_rows[0])
    df = TrendsResult([row]).interest_over_time()
    assert list(df.columns) == ["pickleball", "isPartial"]


def test_empty_timeline_gives_empty_frame(single_rows):
    row = dict(single_rows[0], interestOverTime_timelineData=[], status="noData")
    assert TrendsResult([row]).interest_over_time().empty


def test_works_with_apify_client_3_run_models(single_rows):
    class Status(enum.Enum):
        SUCCEEDED = "SUCCEEDED"

    run = SimpleNamespace(id="RUN3", status=Status.SUCCEEDED, status_message="Done", default_dataset_id="DS3")
    client = TrendsClient(apify_client=fake_apify(single_rows, run))
    result = client.fetch("pickleball", data=["interestOverTime"])
    assert result.run_id == "RUN3" and result.dataset_id == "DS3"
    assert len(result.interest_over_time()) == 4


def test_daily_samples_and_cost_cap_are_passed(make_client, single_rows):
    client, apify = make_client(single_rows, samples=1, max_total_charge_usd=0.05)
    client.interest_over_time("pickleball", timeframe="today 5-y", daily=True)
    call = apify.actor.return_value.call.call_args.kwargs
    assert call["run_input"]["dailyData"] is True
    assert call["run_input"]["samples"] == 1
    assert call["max_total_charge_usd"] == Decimal("0.05")


# --- interest by region ----------------------------------------------------------------------------------------------


def test_worldwide_region_map_is_countries(make_client, single_rows):
    client, apify = make_client(single_rows)
    df = client.interest_by_region("pickleball", inc_geo_code=True)
    assert {f: run_input_of(apify)[f] for f in FLAGS} == only("includeInterestBySubregion")
    assert df.index.name == "geoName"
    assert list(df.columns) == ["geoCode", "pickleball"]
    assert df.loc["Philippines", "pickleball"] == 100
    assert df.loc["Philippines", "geoCode"] == "PH"
    assert list(df.index) == sorted(df.index)


def test_compared_region_map_is_googles_combined_map(comparison_rows, recwarn):
    df = TrendsResult(comparison_rows).interest_by_region("REGION")
    assert list(df.columns) == ["coffee", "tea"]
    assert df.loc["Kansas"].tolist() == [74, 26]
    assert not [w for w in recwarn if "scale" in str(w.message)]


def test_metro_areas_of_a_comparison_are_joined_per_term(comparison_rows):
    with pytest.warns(UserWarning, match="own 0-100 scale"):
        df = TrendsResult(comparison_rows).interest_by_region("DMA", inc_geo_code=True)
    assert list(df.columns) == ["geoCode", "coffee", "tea"]
    assert df.loc["Wichita-Hutchinson KS", "coffee"] == 100
    assert df.loc["Wichita-Hutchinson KS", "tea"] == 100
    assert pd.isna(df.loc["Missoula MT", "tea"])


def test_cities_keep_coordinates(comparison_rows):
    with pytest.warns(UserWarning):
        df = TrendsResult(comparison_rows).interest_by_region("CITY", inc_geo_code=True)
    assert "coordinates" in df.columns
    assert df.loc["Portland", "coordinates"]["lat"] == pytest.approx(43.661471)
    assert len(df) == 6  # 3 cities per term, none shared


def test_dma_for_a_us_state_uses_its_subregion_map(make_client, single_rows):
    client, apify = make_client(single_rows)
    client.interest_by_region("pickleball", geo="US-CA", resolution="DMA")
    assert {f: run_input_of(apify)[f] for f in FLAGS} == only("includeInterestBySubregion")


def test_unknown_resolution(single_rows):
    with pytest.raises(ValueError):
        TrendsResult(single_rows).interest_by_region("ZIP")


# --- related queries and topics -------------------------------------------------------------------------------------


def test_related_queries_per_term(make_client, comparison_rows):
    client, apify = make_client(comparison_rows)
    result = client.related_queries(["coffee", "tea"], geo="US", timeframe="today 1-m")
    assert {f: run_input_of(apify)[f] for f in FLAGS} == only("includeRelatedQueries")
    assert list(result) == ["coffee", "tea"]
    top, rising = result["coffee"]["top"], result["coffee"]["rising"]
    assert list(top.columns) == ["query", "value", "formattedValue", "isBreakout", "likelyNoise"]
    assert top.loc[0, "query"] == "coffee near me" and top.loc[0, "value"] == 100
    assert rising.loc[0, "isBreakout"] and rising.loc[0, "formattedValue"] == "Breakout"


def test_related_queries_single_term_and_missing_lists(single_rows):
    row = dict(single_rows[0], relatedQueries_rising=[])
    result = TrendsResult([row]).related_queries()
    assert list(result) == ["pickleball"]
    assert result["pickleball"]["top"].loc[0, "query"] == "paddle"
    assert result["pickleball"]["rising"] is None  # as in pytrends


def test_related_topics_like_pytrends(single_rows):
    result = TrendsResult(single_rows).related_topics()
    top = result["pickleball"]["top"]
    assert {"value", "formattedValue", "link", "topic_mid", "topic_title", "topic_type"} <= set(top.columns)
    assert "topicTitle" not in top.columns
    assert top.loc[0, "topic_title"] == "Pickleball"


def test_withheld_related_topics_warn_and_return_empty_frames(comparison_rows):
    with pytest.warns(UserWarning, match="embed quota"):
        result = TrendsResult(comparison_rows).related_topics()
    assert result["coffee"]["top"].empty and result["tea"]["rising"].empty


# --- errors -------------------------------------------------------------------------------------------------------


def test_failed_run_raises_with_status_message(single_rows):
    run = {"id": "R", "status": "FAILED", "statusMessage": "Google Trends did not answer", "defaultDatasetId": "D"}
    client = TrendsClient(apify_client=fake_apify(single_rows, run))
    with pytest.raises(TrendsError, match="FAILED: Google Trends did not answer"):
        client.interest_over_time("pickleball")


def test_error_row_raises():
    row = {"status": "error", "errorMessage": "HTTP 429 on all 3 attempts", "terms": ["x"], "billedResults": 0}
    with pytest.raises(TrendsError, match="429"):
        TrendsResult([row]).interest_over_time()


def test_failed_data_type_raises_but_others_work(single_rows):
    row = dict(single_rows[0], status="partial", dataErrors={"relatedQueries": "HTTP 429"})
    result = TrendsResult([row])
    assert not result.interest_over_time().empty
    with pytest.raises(TrendsError, match="relatedQueries"):
        result.related_queries()


def test_no_rows_raises():
    with pytest.raises(TrendsError, match="cost"):
        TrendsResult([], status_message="Stopped at the maximum cost you set for this run").interest_over_time()


def test_billed_results(comparison_rows, single_rows):
    assert TrendsResult(comparison_rows).billed_results == 10
    assert TrendsResult(single_rows).billed_results == 4


# --- pytrends drop-in ---------------------------------------------------------------------------------------------


def test_trendreq_drop_in(comparison_rows):
    apify = fake_apify(comparison_rows)
    pytrends = TrendReq(hl="en-US", tz=360, timeout=(10, 25), retries=2, backoff_factor=0.1,
                        client=TrendsClient(apify_client=apify))
    pytrends.build_payload(["coffee", "tea"], cat=0, timeframe="today 1-m", geo="US", gprop="")
    df = pytrends.interest_over_time()
    assert list(df.columns) == ["coffee", "tea", "isPartial"]
    assert run_input_of(apify)["timeRange"] == "today 1-m"

    regions = pytrends.interest_by_region(resolution="REGION", inc_low_vol=True, inc_geo_code=True)
    assert run_input_of(apify)["includeLowSearchVolumeRegions"] is True
    assert regions.loc["Kansas", "geoCode"] == "US-KS"

    assert list(pytrends.related_queries()) == ["coffee", "tea"]


def test_trendreq_needs_a_payload():
    pytrends = TrendReq(client=TrendsClient(apify_client=fake_apify([])))
    with pytest.raises(ValueError, match="build_payload"):
        pytrends.interest_over_time()


def test_trendreq_validates_like_pytrends():
    pytrends = TrendReq(client=TrendsClient(apify_client=fake_apify([])))
    with pytest.raises(ValueError):
        pytrends.build_payload(["a"], timeframe="next week")
