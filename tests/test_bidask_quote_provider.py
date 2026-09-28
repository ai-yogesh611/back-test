"""Gap-PRD P4 — BidAskQuoteProvider: buy at ask, sell at bid (opt-in).

Two contracts under test:

1. The wrapper itself — protocol-compatible passthrough for marks,
   side-aware ``execution_quote`` for fills, full delegation so it can sit
   anywhere in the provider chain.
2. The paper broker's opt-in seam — a wrapped provider makes fills cross
   the spread; an unwrapped provider keeps V1 LTP fills byte-identical.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from backtest.options.paper_trading import FakeQuoteProvider, OptionPaperBroker
from backtest.options.quote_providers import (
    BidAskQuoteProvider,
    SyntheticChainGenerator,
    SyntheticQuoteProvider,
)
from backtest.strategy.intent import Direction, MarketView, OptionLeg, TradeIntent

EXPIRY = SyntheticChainGenerator().next_monthly_expiry()  # always in the future


def _long_call_intent() -> TradeIntent:
    leg = OptionLeg(
        instrument_token="T24800",
        trading_symbol="NIFTYTEST24800CE",
        side="BUY",
        quantity=1,
        lot_size=25,
    )
    view = MarketView(
        direction=Direction.BULLISH,
        confidence=0.8,
        underlying="NIFTY",
        spot_price=Decimal("24800"),
    )
    return TradeIntent(
        view=view,
        structure_type="long_call",
        legs=(leg,),
        expiry=EXPIRY,
        strategy_name="test_strategy",
        metadata={"option_type": "CE", "strike": "24800"},
    )


class TestWrapper:
    def test_get_quote_is_an_unchanged_passthrough(self):
        provider = BidAskQuoteProvider(FakeQuoteProvider(default_price=100.0))
        assert provider.get_quote("T1") == {"ltp": 100.0, "bid": 99.5, "ask": 100.5}

    def test_buy_executes_at_ask(self):
        provider = BidAskQuoteProvider(FakeQuoteProvider(default_price=100.0))
        quote = provider.execution_quote("T1", "BUY")
        assert quote["ltp"] == 100.5
        assert quote["execution_side"] == "BUY"

    def test_sell_executes_at_bid(self):
        provider = BidAskQuoteProvider(FakeQuoteProvider(default_price=100.0))
        quote = provider.execution_quote("T1", "SELL")
        assert quote["ltp"] == 99.5
        assert quote["execution_side"] == "SELL"

    def test_missing_bid_ask_falls_back_to_ltp(self):
        class LtpOnly:
            def get_quote(self, token):
                return {"ltp": 42.0}

        provider = BidAskQuoteProvider(LtpOnly())
        quote = provider.execution_quote("T1", "BUY")
        assert quote["ltp"] == 42.0  # never 0 — same spirit as the ltp=0 guard
        assert "execution_side" not in quote

    def test_delegates_to_the_inner_provider(self):
        inner = SyntheticQuoteProvider()
        provider = BidAskQuoteProvider(inner)
        provider.set_spot("NIFTY", 25_000.0)  # __getattr__ delegation
        assert inner.get_spot("NIFTY") == 25_000.0
        assert provider.generator is inner.generator
        assert provider.source_name == "synthetic:bs+bidask"


class TestPaperBrokerSeam:
    def test_wrapped_provider_fills_a_buy_at_the_ask(self):
        broker = OptionPaperBroker(capital=1_000_000, slippage_pct=0)
        provider = BidAskQuoteProvider(FakeQuoteProvider(default_price=100.0))
        positions = broker.execute_structure(_long_call_intent(), provider)
        assert positions[0].entry_price == Decimal("100.50")  # ask, not LTP

    def test_wrapped_provider_closes_a_long_at_the_bid(self):
        broker = OptionPaperBroker(capital=1_000_000, slippage_pct=0)
        provider = BidAskQuoteProvider(FakeQuoteProvider(default_price=100.0))
        positions = broker.execute_structure(_long_call_intent(), provider)
        broker.close_structure(positions[0].structure_id, provider)
        # Entered at the ask (100.50), exited at the bid (99.50): the round
        # trip pays the full ₹1 spread × 25 lot — that IS the feature.
        assert positions[0].current_price == Decimal("99.50")  # bid, not LTP
        assert positions[0].realized_pnl == Decimal("-25.00")

    def test_unwrapped_provider_keeps_v1_ltp_fills(self):
        broker = OptionPaperBroker(capital=1_000_000, slippage_pct=0)
        provider = FakeQuoteProvider(default_price=100.0)
        positions = broker.execute_structure(_long_call_intent(), provider)
        assert positions[0].entry_price == Decimal("100.00")  # byte-identical V1
