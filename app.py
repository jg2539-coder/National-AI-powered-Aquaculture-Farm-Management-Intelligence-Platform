from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


HAITI_DEPARTMENTS = [
    {"name": "Nord-Ouest", "x": 8, "y": 32},
    {"name": "Nord", "x": 24, "y": 26},
    {"name": "Nord-Est", "x": 40, "y": 26},
    {"name": "Artibonite", "x": 22, "y": 44},
    {"name": "Centre", "x": 42, "y": 42},
    {"name": "Ouest", "x": 32, "y": 58},
    {"name": "Sud-Est", "x": 52, "y": 62},
    {"name": "Nippes", "x": 20, "y": 74},
    {"name": "Sud", "x": 10, "y": 82},
    {"name": "Grand'Anse", "x": 2, "y": 66},
]
SUPPORTED_DEPARTMENTS = {item["name"] for item in HAITI_DEPARTMENTS}

FISH_TARGET_TEMPERATURE = 28.0
SHELLFISH_TARGET_TEMPERATURE = 26.0
TEMPERATURE_PENALTY_RATE = 4.5
BASELINE_DISSOLVED_OXYGEN = 4.5
OXYGEN_BONUS_RATE = 8.0
MAX_OXYGEN_BONUS = 20.0
IDEAL_PH = 7.6
PH_PENALTY_RATE = 10.0
MORTALITY_PENALTY_RATE = 7.0
MAX_MORTALITY_PENALTY = 35.0
TURBIDITY_THRESHOLD = 20.0
TURBIDITY_PENALTY_RATE = 1.2
MAX_TURBIDITY_PENALTY = 18.0
DISEASE_SIGNAL_PENALTY = 18.0
FISH_GROWTH_RATE = 0.045
SHELLFISH_GROWTH_RATE = 0.03
FEED_CONVERSION_MULTIPLIER = 1000.0
OXYGEN_GROWTH_MULTIPLIER = 0.08
TEMPERATURE_GROWTH_PENALTY = 0.1
MIN_GROWTH_ESTIMATE = 0.5
MAX_GROWTH_ESTIMATE = 40.0
TARGET_FEEDING_OXYGEN = 7.0
TEMPERATURE_FEED_ADJUSTMENT_RATE = -2.2
OXYGEN_FEED_ADJUSTMENT_RATE = 3.5
MIN_FEED_ADJUSTMENT = -15.0
MAX_FEED_ADJUSTMENT = 20.0
OPTIMAL_TEMPERATURE_PENALTY_THRESHOLD = 6.0
HEALTH_ALERT_THRESHOLD = 55.0


def require_non_negative_float(value: Any, field_name: str) -> float:
    numeric_value = float(value)
    if numeric_value < 0:
        raise ValueError(f"{field_name} must be non-negative.")
    return numeric_value


@dataclass
class FarmRecord:
    farm_name: str
    department: str
    species: str
    production_type: str
    population: int
    average_weight_grams: float
    water_temperature_c: float
    dissolved_oxygen_mg_l: float
    ph: float
    salinity_ppt: float
    turbidity_ntu: float
    feed_kg_day: float
    mortality_rate_pct: float
    disease_signals: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "FarmRecord":
        department = str(payload["department"]).strip()
        if department not in SUPPORTED_DEPARTMENTS:
            raise ValueError("Department must be one of the Haiti department names in the dashboard.")
        population = int(payload["population"])
        if population < 0:
            raise ValueError("Population must be non-negative.")

        return cls(
            farm_name=str(payload["farm_name"]).strip(),
            department=department,
            species=str(payload["species"]).strip(),
            production_type=str(payload["production_type"]).strip(),
            population=population,
            average_weight_grams=require_non_negative_float(payload["average_weight_grams"], "Average weight"),
            water_temperature_c=float(payload["water_temperature_c"]),
            dissolved_oxygen_mg_l=float(payload["dissolved_oxygen_mg_l"]),
            ph=float(payload["ph"]),
            salinity_ppt=require_non_negative_float(payload["salinity_ppt"], "Salinity"),
            turbidity_ntu=require_non_negative_float(payload["turbidity_ntu"], "Turbidity"),
            feed_kg_day=require_non_negative_float(payload["feed_kg_day"], "Feed"),
            mortality_rate_pct=require_non_negative_float(payload["mortality_rate_pct"], "Mortality rate"),
            disease_signals=str(payload.get("disease_signals", "None observed")).strip() or "None observed",
        )


FARMS: list[FarmRecord] = [
    FarmRecord(
        farm_name="Cap Aqua Tilapia",
        department="Nord",
        species="Tilapia",
        production_type="Fish",
        population=18000,
        average_weight_grams=320,
        water_temperature_c=28.4,
        dissolved_oxygen_mg_l=6.8,
        ph=7.4,
        salinity_ppt=1.2,
        turbidity_ntu=18,
        feed_kg_day=440,
        mortality_rate_pct=1.8,
        disease_signals="None observed",
    ),
    FarmRecord(
        farm_name="Artibonite Shrimp Hub",
        department="Artibonite",
        species="Shrimp",
        production_type="Shellfish",
        population=92000,
        average_weight_grams=21,
        water_temperature_c=29.7,
        dissolved_oxygen_mg_l=5.6,
        ph=7.8,
        salinity_ppt=13.0,
        turbidity_ntu=24,
        feed_kg_day=310,
        mortality_rate_pct=3.5,
        disease_signals="Mild stress during afternoon heat",
    ),
    FarmRecord(
        farm_name="Sud Oyster Reserve",
        department="Sud",
        species="Oyster",
        production_type="Shellfish",
        population=64000,
        average_weight_grams=85,
        water_temperature_c=27.3,
        dissolved_oxygen_mg_l=6.4,
        ph=8.0,
        salinity_ppt=24.0,
        turbidity_ntu=11,
        feed_kg_day=0,
        mortality_rate_pct=0.9,
        disease_signals="None observed",
    ),
]


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(value, maximum))


def score_band(score: float) -> str:
    if score >= 80:
        return "Excellent"
    if score >= 60:
        return "Stable"
    if score >= 40:
        return "Watch"
    return "Critical"


def predict_farm_status(farm: FarmRecord) -> dict[str, Any]:
    target_temp = FISH_TARGET_TEMPERATURE if farm.production_type == "Fish" else SHELLFISH_TARGET_TEMPERATURE
    temp_penalty = abs(farm.water_temperature_c - target_temp) * TEMPERATURE_PENALTY_RATE
    oxygen_bonus = clamp(
        (farm.dissolved_oxygen_mg_l - BASELINE_DISSOLVED_OXYGEN) * OXYGEN_BONUS_RATE,
        0,
        MAX_OXYGEN_BONUS,
    )
    ph_penalty = abs(farm.ph - IDEAL_PH) * PH_PENALTY_RATE
    mortality_penalty = clamp(farm.mortality_rate_pct * MORTALITY_PENALTY_RATE, 0, MAX_MORTALITY_PENALTY)
    turbidity_penalty = clamp(
        max(0.0, farm.turbidity_ntu - TURBIDITY_THRESHOLD) * TURBIDITY_PENALTY_RATE,
        0,
        MAX_TURBIDITY_PENALTY,
    )
    disease_penalty = (
        DISEASE_SIGNAL_PENALTY
        if "stress" in farm.disease_signals.lower() or "lesion" in farm.disease_signals.lower()
        else 0
    )

    water_quality_score = clamp(100 - temp_penalty - ph_penalty - turbidity_penalty + oxygen_bonus, 0, 100)
    health_score = clamp(water_quality_score - mortality_penalty - disease_penalty, 0, 100)
    estimated_growth_g_week = round(
        clamp(
            (
                farm.average_weight_grams * FISH_GROWTH_RATE
                if farm.production_type == "Fish"
                else farm.average_weight_grams * SHELLFISH_GROWTH_RATE
            )
            + (farm.feed_kg_day / max(farm.population, 1)) * FEED_CONVERSION_MULTIPLIER
            + oxygen_bonus * OXYGEN_GROWTH_MULTIPLIER
            - temp_penalty * TEMPERATURE_GROWTH_PENALTY,
            MIN_GROWTH_ESTIMATE,
            MAX_GROWTH_ESTIMATE,
        ),
        2,
    )
    feeding_adjustment_pct = round(
        clamp(
            (target_temp - farm.water_temperature_c) * TEMPERATURE_FEED_ADJUSTMENT_RATE
            + (TARGET_FEEDING_OXYGEN - farm.dissolved_oxygen_mg_l) * OXYGEN_FEED_ADJUSTMENT_RATE,
            MIN_FEED_ADJUSTMENT,
            MAX_FEED_ADJUSTMENT,
        ),
        1,
    )

    return {
        "water_quality_score": round(water_quality_score, 1),
        "water_quality_status": score_band(water_quality_score),
        "health_score": round(health_score, 1),
        "health_status": score_band(health_score),
        "temperature_status": "Optimal" if temp_penalty < OPTIMAL_TEMPERATURE_PENALTY_THRESHOLD else "Monitor",
        "estimated_growth_g_week": estimated_growth_g_week,
        "feeding_adjustment_pct": feeding_adjustment_pct,
        "recommended_action": (
            "Increase aeration and reduce afternoon feeding."
            if health_score < HEALTH_ALERT_THRESHOLD
            else "Maintain current operations and continue monitoring."
        ),
    }


def build_dashboard_payload() -> dict[str, Any]:
    farm_entries = []
    department_rollup: dict[str, list[float]] = {item["name"]: [] for item in HAITI_DEPARTMENTS}

    for farm in FARMS:
        prediction = predict_farm_status(farm)
        department_rollup[farm.department].append(prediction["health_score"])
        farm_entries.append({**asdict(farm), "prediction": prediction})

    departments = []
    for item in HAITI_DEPARTMENTS:
        scores = department_rollup.get(item["name"], [])
        average_health = round(sum(scores) / len(scores), 1) if scores else 0.0
        departments.append(
            {
                **item,
                "farm_count": len(scores),
                "average_health": average_health,
                "status": score_band(average_health) if scores else "No data",
            }
        )

    total_population = sum(farm.population for farm in FARMS)
    fish_farms = sum(1 for farm in FARMS if farm.production_type == "Fish")
    shellfish_farms = sum(1 for farm in FARMS if farm.production_type == "Shellfish")
    average_health = round(
        sum(predict_farm_status(farm)["health_score"] for farm in FARMS) / max(len(FARMS), 1),
        1,
    )

    return {
        "company": "National AI Aquaculture Intelligence Platform",
        "country": "Haiti",
        "overview": {
            "farm_count": len(FARMS),
            "total_population": total_population,
            "fish_farms": fish_farms,
            "shellfish_farms": shellfish_farms,
            "average_health_score": average_health,
        },
        "departments": departments,
        "farms": farm_entries,
    }


TEMPLATE_PATH = Path(__file__).with_name("dashboard.html")


def load_dashboard_html() -> str:
    return TEMPLATE_PATH.read_text(encoding="utf-8")


class AppHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        route = urlparse(self.path).path
        if route == "/":
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(load_dashboard_html().encode("utf-8"))
            return

        if route == "/api/overview":
            self.send_json(build_dashboard_payload())
            return

        if route == "/api/farms":
            self.send_json([asdict(farm) for farm in FARMS])
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Page not found")

    def do_POST(self) -> None:
        route = urlparse(self.path).path
        if route != "/api/farms":
            self.send_error(HTTPStatus.NOT_FOUND, "Page not found")
            return

        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            farm = FarmRecord.from_payload(payload)
        except json.JSONDecodeError:
            self.send_json({"error": "Invalid JSON payload"}, HTTPStatus.BAD_REQUEST)
            return
        except (KeyError, TypeError, ValueError):
            self.send_json({"error": "Invalid farm observation payload"}, HTTPStatus.BAD_REQUEST)
            return

        FARMS.append(farm)
        self.send_json({"status": "created", "farm": asdict(farm)}, HTTPStatus.CREATED)

    def log_message(self, msg_format: str, *args: Any) -> None:
        return

    def send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_server(host: str = "127.0.0.1", port: int = 8000) -> None:
    server = ThreadingHTTPServer((host, port), AppHandler)
    print(f"Aquaculture dashboard running on http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    run_server()
