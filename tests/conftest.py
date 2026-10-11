import copy
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pytrends_alternative import TrendsClient

FIXTURES = Path(__file__).parent / "fixtures"


def load_rows(name):
    """Dataset rows of two real runs of insanedev/google-trends-scraper (2026-10-10), trimmed to a few items per list.

    comparison_coffee_tea_us.json: "coffee, tea" compared, US, today 1-m, all six data types.
    single_pickleball_worldwide.json: "pickleball", worldwide, today 3-m, timeline, regions, queries and topics.
    """
    with open(FIXTURES / name, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def comparison_rows():
    return load_rows("comparison_coffee_tea_us.json")


@pytest.fixture
def single_rows():
    return load_rows("single_pickleball_worldwide.json")


def fake_apify(rows, run=None):
    """A MagicMock standing in for apify_client.ApifyClient: call() returns `run`, the dataset yields `rows`."""
    apify = MagicMock(name="ApifyClient")
    apify.actor.return_value.call.return_value = run or {
        "id": "RUN123",
        "status": "SUCCEEDED",
        "statusMessage": "Done: 1 of 1 queries complete; 4 results billed.",
        "defaultDatasetId": "DATASET123",
    }
    apify.dataset.return_value.iterate_items.side_effect = lambda *a, **k: iter(copy.deepcopy(rows))
    return apify


def run_input_of(apify):
    """The run_input of the last Actor call."""
    return apify.actor.return_value.call.call_args.kwargs["run_input"]


@pytest.fixture
def make_client():
    def make(rows, run=None, **options):
        apify = fake_apify(rows, run)
        return TrendsClient(apify_client=apify, **options), apify

    return make
