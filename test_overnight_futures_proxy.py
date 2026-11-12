# -*- coding: utf-8 -*-
import unittest
from datetime import datetime
try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None
import ml_predictor_5y as ml


class TestOvernightFuturesProxy(unittest.TestCase):
    def _spot_lookup(self):
        d = {s: {"effective_pct": 1.0, "pct_change": 9.9, "ext_price": 1.0, "ext_pct": 9.9} for s in
             ["KO", "PEP", "MCD", "LLY", "MRNA", "TLT", "GLD", "VIXY"]}
        d.update({s: {"effective_pct": -3.71, "pct_change": 9.9, "ext_price": 1.0, "ext_pct": -3.71} for s in
                  ["SMH", "SOXL", "NVDA", "QQQ"]})
        return d

    def _now(self):
        tz = ZoneInfo("America/New_York") if ZoneInfo else None
        return datetime(2026, 11, 12, 21, 30, tzinfo=tz)

    def test_regular_session_keeps_spot_formula(self):
        def _should_not_call(_now):
            raise AssertionError("futures fetcher must not be called in REGULAR")
        res = ml.compute_defensive_flight_alert(live_lookup=self._spot_lookup(), session_code="REGULAR",
                                                now_et=self._now(), futures_fetcher=_should_not_call)
        self.assertAlmostEqual(res["flight_score"], 4.71, places=2)
        self.assertEqual(res["score_source"], "spot_basket")
        self.assertEqual(res["session"], "REGULAR")

    def test_overnight_uses_futures_not_frozen_spot(self):
        fut = {"components": {
            "NQ=F": {"pct": -2.0, "quote_time": "2026-11-12 21:30 ET"},
            "ZB=F": {"pct": 1.5, "quote_time": "2026-11-12 21:30 ET"},
            "GC=F": {"pct": 2.5, "quote_time": "2026-11-12 21:30 ET"},
        }, "missing": []}
        res = ml.compute_defensive_flight_alert(live_lookup=self._spot_lookup(), session_code="OVERNIGHT",
                                                now_et=self._now(), futures_fetcher=lambda _n: fut)
        self.assertAlmostEqual(res["flight_score"], 4.00, places=2)
        self.assertEqual(res["signature"] if "signature" in res else res["score_source"], "futures_proxy")
        self.assertEqual(res["session"], "OVERNIGHT")
        self.assertIn("夜盘·期货代理", res["alert_text"])
        self.assertIn("NQ=F", res["components"])
        self.assertNotAlmostEqual(res["flight_score"], 4.71, places=2)

    def test_overnight_missing_futures_skips(self):
        fut = {"components": {"ZB=F": {"pct": 2.0}}, "missing": ["NQ=F", "GC=F"]}
        res = ml.compute_defensive_flight_alert(live_lookup=self._spot_lookup(), session_code="OVERNIGHT",
                                                now_et=self._now(), futures_fetcher=lambda _n: fut)
        self.assertFalse(res["is_alert"])
        self.assertTrue(res["skip_reason"])
        self.assertIn("NQ=F", res["skip_reason"])


if __name__ == "__main__":
    unittest.main()
