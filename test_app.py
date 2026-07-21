import json
import threading
import unittest
import urllib.error
import urllib.request

from app import AppHandler, FARMS, FarmRecord, ThreadingHTTPServer, build_dashboard_payload, predict_farm_status


class AquacultureAppTests(unittest.TestCase):
    def test_predict_farm_status_returns_expected_metrics(self) -> None:
        farm = FarmRecord(
            farm_name="Test Farm",
            department="Ouest",
            species="Tilapia",
            production_type="Fish",
            population=10000,
            average_weight_grams=200,
            water_temperature_c=28.0,
            dissolved_oxygen_mg_l=6.5,
            ph=7.6,
            salinity_ppt=1.0,
            turbidity_ntu=12.0,
            feed_kg_day=180,
            mortality_rate_pct=1.0,
            disease_signals="None observed",
        )

        prediction = predict_farm_status(farm)

        self.assertEqual(prediction["temperature_status"], "Optimal")
        self.assertGreaterEqual(prediction["health_score"], 80)
        self.assertGreater(prediction["estimated_growth_g_week"], 0)

    def test_predict_farm_status_flags_poor_conditions(self) -> None:
        farm = FarmRecord(
            farm_name="Risk Farm",
            department="Centre",
            species="Shrimp",
            production_type="Shellfish",
            population=15000,
            average_weight_grams=24,
            water_temperature_c=33.0,
            dissolved_oxygen_mg_l=3.8,
            ph=6.5,
            salinity_ppt=10.0,
            turbidity_ntu=38.0,
            feed_kg_day=90,
            mortality_rate_pct=6.5,
            disease_signals="Heat stress and lesions observed",
        )

        prediction = predict_farm_status(farm)

        self.assertEqual(prediction["temperature_status"], "Monitor")
        self.assertLess(prediction["health_score"], 55)
        self.assertEqual(
            prediction["recommended_action"],
            "Increase aeration and reduce afternoon feeding.",
        )

    def test_build_dashboard_payload_includes_haiti_departments_and_farms(self) -> None:
        payload = build_dashboard_payload()

        self.assertEqual(payload["country"], "Haiti")
        self.assertEqual(len(payload["departments"]), 10)
        self.assertEqual(payload["overview"]["farm_count"], len(FARMS))
        self.assertTrue(any(farm["production_type"] == "Shellfish" for farm in payload["farms"]))
        self.assertTrue(
            {"name", "x", "y", "farm_count", "average_health", "status"}.issubset(payload["departments"][0])
        )
        self.assertTrue(
            {
                "water_quality_score",
                "water_quality_status",
                "health_score",
                "health_status",
                "temperature_status",
                "estimated_growth_g_week",
                "feeding_adjustment_pct",
                "recommended_action",
            }.issubset(payload["farms"][0]["prediction"])
        )

    def test_from_payload_rejects_unsupported_department(self) -> None:
        with self.assertRaises(ValueError):
            FarmRecord.from_payload(
                {
                    "farm_name": "Invalid Department Farm",
                    "department": "Unknown",
                    "species": "Tilapia",
                    "production_type": "Fish",
                    "population": 1000,
                    "average_weight_grams": 120,
                    "water_temperature_c": 28,
                    "dissolved_oxygen_mg_l": 6.0,
                    "ph": 7.5,
                    "salinity_ppt": 1.0,
                    "turbidity_ntu": 10.0,
                    "feed_kg_day": 24,
                    "mortality_rate_pct": 0.5,
                    "disease_signals": "None observed",
                }
            )

    def test_from_payload_rejects_negative_population(self) -> None:
        with self.assertRaises(ValueError):
            FarmRecord.from_payload(
                {
                    "farm_name": "Negative Population Farm",
                    "department": "Ouest",
                    "species": "Tilapia",
                    "production_type": "Fish",
                    "population": -1,
                    "average_weight_grams": 120,
                    "water_temperature_c": 28,
                    "dissolved_oxygen_mg_l": 6.0,
                    "ph": 7.5,
                    "salinity_ppt": 1.0,
                    "turbidity_ntu": 10.0,
                    "feed_kg_day": 24,
                    "mortality_rate_pct": 0.5,
                    "disease_signals": "None observed",
                }
            )

    def test_post_invalid_json_returns_bad_request(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), AppHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/farms",
                data=b"{bad json",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as error_context:
                urllib.request.urlopen(request)

            self.assertEqual(error_context.exception.code, 400)
            response_body = json.loads(error_context.exception.read().decode("utf-8"))
            self.assertEqual(response_body["error"], "Invalid JSON payload")
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()


if __name__ == "__main__":
    unittest.main()
