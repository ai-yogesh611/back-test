"""The data-source refusal at the request boundary.

The policy deciding is one thing; the user finding out is another. These pin
the second: a disabled source returns 409 with an explanation, and the routes
that do not consume candles keep working.
"""

from __future__ import annotations

import pytest

from backtest.data.sources_policy import SourcePolicy, SourceSpec, build_policy, reset_source_policy
from backtest.web.app import create_app

#: synthetic off, everything else on — the posture a real deployment has.
STRICT = SourcePolicy(
    {
        "synthetic": SourceSpec("synthetic", enabled=False, certifiable=False, note="Generated."),
        "db": SourceSpec("db", enabled=True, certifiable=True),
    },
    profile="unit",
)


@pytest.fixture()
def strict_client():
    """An app whose configured source is disabled."""
    app = create_app(source="synthetic")
    app.config["DATA_SOURCE_POLICY"] = STRICT
    reset_source_policy()
    return app.test_client()


class TestTheRefusal:
    def test_a_backtest_is_refused(self, strict_client):
        r = strict_client.post(
            "/api/backtest/run",
            json={
                "strategy": "sma_crossover",
                "symbol": "RELIANCE",
                "from_date": "2022-01-01",
                "to_date": "2023-01-01",
            },
        )
        assert r.status_code == 409
        body = r.get_json()
        assert body["code"] == "data_source_disabled"
        assert "disabled" in body["error"].lower()

    def test_a_parallel_backtest_is_refused(self, strict_client):
        r = strict_client.post(
            "/api/backtest/run-many", json={"shared": {}, "slots": [{"strategy": "sma_crossover"}]}
        )
        assert r.status_code == 409

    def test_starting_an_optimization_is_refused(self, strict_client):
        r = strict_client.post("/api/optimize/runs", json={"strategyId": "sma_crossover"})
        assert r.status_code == 409
        assert r.get_json()["code"] == "data_source_disabled"

    def test_estimating_a_run_is_refused(self, strict_client):
        """Estimate loads candles too. Letting it through would let the user
        plan a run that then refuses to start."""
        r = strict_client.post("/api/optimize/estimate", json={"strategyId": "sma_crossover"})
        assert r.status_code == 409

    def test_the_refusal_names_what_to_do_instead(self, strict_client):
        r = strict_client.get("/api/data-sources")
        assert r.status_code == 200
        body = r.get_json()
        assert body["allowed"] is False
        assert body["refusal"] and "db" in body["refusal"]
        assert body["active"] == "synthetic"
        assert body["certifiable"] is False

    def test_the_refusal_survives_a_second_request(self, strict_client):
        """No hidden one-shot state: the app must keep saying no."""
        for _ in range(3):
            assert strict_client.post("/api/backtest/run", json={}).status_code == 409


class TestWhatKeepsWorking:
    """A refusal that breaks the whole app is not a control, it's an outage."""

    @pytest.mark.parametrize("path", ["/backtest", "/compare", "/optimize", "/"])
    def test_pages_still_render(self, strict_client, path):
        assert strict_client.get(path).status_code == 200

    def test_run_history_is_still_readable(self, strict_client):
        """You must be able to look at the runs you already did."""
        assert strict_client.get("/api/optimize/runs").status_code == 200

    def test_the_strategy_catalogue_is_still_readable(self, strict_client):
        assert strict_client.get("/api/strategies").status_code == 200

    def test_the_data_source_endpoint_always_answers(self, strict_client):
        r = strict_client.get("/api/data-sources")
        assert r.status_code == 200
        assert {row["name"] for row in r.get_json()["sources"]} >= {"synthetic", "db"}


class TestTheOptIn:
    def test_an_enabled_source_runs_normally(self):
        app = create_app(source="db")
        app.config["DATA_SOURCE_POLICY"] = SourcePolicy(
            {"db": SourceSpec("db", enabled=True)}, profile="unit"
        )
        reset_source_policy()
        # No 409 — whether the run itself succeeds depends on there being a
        # database, which is a different question from whether it is allowed.
        r = app.test_client().post(
            "/api/backtest/run", json={"strategy": "sma_crossover", "symbol": "X"}
        )
        assert r.status_code != 409
        reset_source_policy()

    def test_a_missing_config_file_still_refuses_synthetic(self, tmp_path):
        policy = build_policy(path=tmp_path / "absent.yaml", profile="default", env={})
        assert policy.is_enabled("synthetic") is False
        assert policy.refusal_for("synthetic")
