from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
        return cls(
            farm_name=str(payload["farm_name"]).strip(),
            department=str(payload["department"]).strip(),
            species=str(payload["species"]).strip(),
            production_type=str(payload["production_type"]).strip(),
            population=max(0, int(payload["population"])),
            average_weight_grams=max(0.0, float(payload["average_weight_grams"])),
            water_temperature_c=float(payload["water_temperature_c"]),
            dissolved_oxygen_mg_l=float(payload["dissolved_oxygen_mg_l"]),
            ph=float(payload["ph"]),
            salinity_ppt=max(0.0, float(payload["salinity_ppt"])),
            turbidity_ntu=max(0.0, float(payload["turbidity_ntu"])),
            feed_kg_day=max(0.0, float(payload["feed_kg_day"])),
            mortality_rate_pct=max(0.0, float(payload["mortality_rate_pct"])),
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
    target_temp = 28.0 if farm.production_type == "Fish" else 26.0
    temp_penalty = abs(farm.water_temperature_c - target_temp) * 4.5
    oxygen_bonus = clamp((farm.dissolved_oxygen_mg_l - 4.5) * 8, 0, 20)
    ph_penalty = abs(farm.ph - 7.6) * 10
    mortality_penalty = clamp(farm.mortality_rate_pct * 7, 0, 35)
    turbidity_penalty = clamp((farm.turbidity_ntu - 20) * 1.2, 0, 18)
    disease_penalty = 18 if "stress" in farm.disease_signals.lower() or "lesion" in farm.disease_signals.lower() else 0

    water_quality_score = clamp(100 - temp_penalty - ph_penalty - turbidity_penalty + oxygen_bonus, 0, 100)
    health_score = clamp(water_quality_score - mortality_penalty - disease_penalty, 0, 100)
    estimated_growth_g_week = round(
        clamp(
            (farm.average_weight_grams * 0.045 if farm.production_type == "Fish" else farm.average_weight_grams * 0.03)
            + (farm.feed_kg_day / max(farm.population, 1)) * 1000
            + oxygen_bonus * 0.08
            - temp_penalty * 0.1,
            0.5,
            40.0,
        ),
        2,
    )
    feeding_adjustment_pct = round(
        clamp((target_temp - farm.water_temperature_c) * -2.2 + (7 - farm.dissolved_oxygen_mg_l) * 3.5, -15, 20),
        1,
    )

    return {
        "water_quality_score": round(water_quality_score, 1),
        "water_quality_status": score_band(water_quality_score),
        "health_score": round(health_score, 1),
        "health_status": score_band(health_score),
        "temperature_status": "Optimal" if temp_penalty < 6 else "Monitor",
        "estimated_growth_g_week": estimated_growth_g_week,
        "feeding_adjustment_pct": feeding_adjustment_pct,
        "recommended_action": (
            "Increase aeration and reduce afternoon feeding."
            if health_score < 55
            else "Maintain current operations and continue monitoring."
        ),
    }


def build_dashboard_payload() -> dict[str, Any]:
    farm_entries = []
    department_rollup: dict[str, list[float]] = {item["name"]: [] for item in HAITI_DEPARTMENTS}

    for farm in FARMS:
        prediction = predict_farm_status(farm)
        department_rollup.setdefault(farm.department, []).append(prediction["health_score"])
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


HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>National AI Aquaculture Intelligence Platform</title>
  <style>
    :root {
      --bg: #071826;
      --panel: #0e2233;
      --panel-alt: #12304a;
      --line: rgba(255,255,255,0.08);
      --text: #eef7ff;
      --muted: #9bb7cb;
      --accent: #4dd0e1;
      --good: #2ecc71;
      --warn: #f1c40f;
      --risk: #e74c3c;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Arial, Helvetica, sans-serif;
      background: linear-gradient(135deg, #04111d, #0a2740 60%, #113f59);
      color: var(--text);
    }
    .page {
      max-width: 1280px;
      margin: 0 auto;
      padding: 24px;
    }
    .hero, .panel {
      background: rgba(14, 34, 51, 0.92);
      border: 1px solid var(--line);
      border-radius: 18px;
      box-shadow: 0 18px 50px rgba(0, 0, 0, 0.25);
    }
    .hero {
      padding: 28px;
      display: grid;
      gap: 10px;
      margin-bottom: 20px;
    }
    .hero h1 { margin: 0; font-size: 2rem; }
    .hero p { margin: 0; color: var(--muted); max-width: 820px; }
    .metrics, .layout {
      display: grid;
      gap: 16px;
    }
    .metrics {
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      margin-bottom: 20px;
    }
    .metric, .panel { padding: 18px; }
    .metric {
      background: rgba(7, 24, 38, 0.9);
      border: 1px solid var(--line);
      border-radius: 16px;
    }
    .metric span { color: var(--muted); display: block; margin-bottom: 8px; }
    .metric strong { font-size: 1.7rem; }
    .layout {
      grid-template-columns: 1.4fr 1fr;
      align-items: start;
    }
    .grid-2 {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 14px;
    }
    .map {
      position: relative;
      min-height: 380px;
      border-radius: 16px;
      background:
        radial-gradient(circle at 10% 20%, rgba(77, 208, 225, 0.18), transparent 25%),
        linear-gradient(180deg, rgba(18,48,74,0.95), rgba(7,24,38,0.95));
      overflow: hidden;
      border: 1px solid var(--line);
    }
    .map::before {
      content: "";
      position: absolute;
      inset: 10% 8%;
      border-radius: 32% 48% 35% 42%;
      border: 1px dashed rgba(255,255,255,0.08);
    }
    .dept {
      position: absolute;
      width: 122px;
      transform: translate(-50%, -50%);
      padding: 10px;
      border-radius: 14px;
      background: rgba(4, 17, 29, 0.88);
      border: 1px solid var(--line);
    }
    .dept strong { display: block; margin-bottom: 6px; font-size: 0.92rem; }
    .dept small { color: var(--muted); display: block; }
    .good { color: var(--good); }
    .warn { color: var(--warn); }
    .risk { color: var(--risk); }
    table {
      width: 100%;
      border-collapse: collapse;
      margin-top: 8px;
    }
    th, td {
      text-align: left;
      padding: 10px 8px;
      border-bottom: 1px solid var(--line);
      vertical-align: top;
    }
    th { color: var(--muted); font-size: 0.84rem; }
    form {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 12px;
    }
    label {
      display: grid;
      gap: 6px;
      font-size: 0.88rem;
      color: var(--muted);
    }
    input, select {
      width: 100%;
      border: 1px solid var(--line);
      background: var(--bg);
      color: var(--text);
      border-radius: 12px;
      padding: 10px 12px;
    }
    button {
      margin-top: 8px;
      border: 0;
      border-radius: 12px;
      padding: 12px 16px;
      background: linear-gradient(135deg, #26c6da, #00acc1);
      color: #042131;
      font-weight: 700;
      cursor: pointer;
    }
    .section-title {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
      margin-bottom: 14px;
    }
    .section-title h2 {
      margin: 0;
      font-size: 1.1rem;
    }
    .footnote { color: var(--muted); font-size: 0.85rem; }
    @media (max-width: 920px) {
      .layout { grid-template-columns: 1fr; }
      .map { min-height: 520px; }
    }
  </style>
</head>
<body>
  <div class="page">
    <section class="hero">
      <h1>National AI Aquaculture Intelligence Platform</h1>
      <p>
        A simple, beautiful dashboard for Haiti-wide fish and shellfish farm monitoring,
        combining operational data collection with AI-assisted predictions for feeding,
        growth, water quality, temperature, and health.
      </p>
    </section>
    <section id="metrics" class="metrics"></section>
    <section class="layout">
      <div class="panel">
        <div class="section-title">
          <h2>Haiti Department Map View</h2>
          <span class="footnote">Territorial coverage across all 10 departments</span>
        </div>
        <div id="map" class="map"></div>
      </div>
      <div class="panel">
        <div class="section-title">
          <h2>Register a Farm Observation</h2>
          <span class="footnote">Data collection for company teams</span>
        </div>
        <form id="farm-form">
          <label>Farm name<input name="farm_name" required value="Ouest Lagoon Farm"></label>
          <label>Department<select name="department"></select></label>
          <label>Species<input name="species" required value="Tilapia"></label>
          <label>Production type<select name="production_type"><option>Fish</option><option>Shellfish</option></select></label>
          <label>Population<input name="population" type="number" min="0" value="12000"></label>
          <label>Avg weight (g)<input name="average_weight_grams" type="number" step="0.1" min="0" value="180"></label>
          <label>Temperature °C<input name="water_temperature_c" type="number" step="0.1" value="29"></label>
          <label>Dissolved oxygen<input name="dissolved_oxygen_mg_l" type="number" step="0.1" value="5.8"></label>
          <label>pH<input name="ph" type="number" step="0.1" value="7.5"></label>
          <label>Salinity ppt<input name="salinity_ppt" type="number" step="0.1" value="2"></label>
          <label>Turbidity NTU<input name="turbidity_ntu" type="number" step="0.1" value="20"></label>
          <label>Feed kg/day<input name="feed_kg_day" type="number" step="0.1" value="240"></label>
          <label>Mortality %<input name="mortality_rate_pct" type="number" step="0.1" value="1.2"></label>
          <label>Disease signals<input name="disease_signals" value="None observed"></label>
          <div><button type="submit">Add observation</button></div>
        </form>
      </div>
    </section>
    <section class="panel" style="margin-top: 20px;">
      <div class="section-title">
        <h2>Farm Intelligence Table</h2>
        <span class="footnote">AI-assisted operations recommendations</span>
      </div>
      <table>
        <thead>
          <tr>
            <th>Farm</th>
            <th>Department</th>
            <th>Species</th>
            <th>Water quality</th>
            <th>Health</th>
            <th>Growth / week</th>
            <th>Feeding action</th>
          </tr>
        </thead>
        <tbody id="farm-table"></tbody>
      </table>
    </section>
  </div>
  <script>
    const toneClass = (status) => {
      if (["Excellent", "Optimal"].includes(status)) return "good";
      if (["Stable", "Watch", "Monitor"].includes(status)) return "warn";
      return "risk";
    };

    const render = async () => {
      const response = await fetch("/api/overview");
      const data = await response.json();

      document.querySelector("#metrics").innerHTML = [
        ["Active farms", data.overview.farm_count],
        ["Fish farms", data.overview.fish_farms],
        ["Shellfish farms", data.overview.shellfish_farms],
        ["Tracked stock", data.overview.total_population.toLocaleString()],
        ["Average health", data.overview.average_health_score]
      ].map(([label, value]) => `
        <div class="metric">
          <span>${label}</span>
          <strong>${value}</strong>
        </div>
      `).join("");

      document.querySelector("#map").innerHTML = data.departments.map((dept) => `
        <div class="dept" style="left:${dept.x}%; top:${dept.y}%;">
          <strong>${dept.name}</strong>
          <small>${dept.farm_count} farm(s)</small>
          <small class="${toneClass(dept.status)}">${dept.status}${dept.farm_count ? ` · ${dept.average_health}` : ""}</small>
        </div>
      `).join("");

      document.querySelector("#farm-table").innerHTML = data.farms.map((farm) => `
        <tr>
          <td><strong>${farm.farm_name}</strong><br><span class="footnote">${farm.production_type}</span></td>
          <td>${farm.department}</td>
          <td>${farm.species}</td>
          <td><span class="${toneClass(farm.prediction.water_quality_status)}">${farm.prediction.water_quality_score} · ${farm.prediction.water_quality_status}</span></td>
          <td><span class="${toneClass(farm.prediction.health_status)}">${farm.prediction.health_score} · ${farm.prediction.health_status}</span></td>
          <td>${farm.prediction.estimated_growth_g_week} g</td>
          <td>${farm.prediction.feeding_adjustment_pct > 0 ? "+" : ""}${farm.prediction.feeding_adjustment_pct}%<br><span class="footnote">${farm.prediction.recommended_action}</span></td>
        </tr>
      `).join("");

      const departmentSelect = document.querySelector("select[name='department']");
      departmentSelect.innerHTML = data.departments.map((dept) => `<option>${dept.name}</option>`).join("");
    };

    document.querySelector("#farm-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const formData = new FormData(event.target);
      const payload = Object.fromEntries(formData.entries());
      [
        "population", "average_weight_grams", "water_temperature_c", "dissolved_oxygen_mg_l",
        "ph", "salinity_ppt", "turbidity_ntu", "feed_kg_day", "mortality_rate_pct"
      ].forEach((key) => payload[key] = Number(payload[key]));

      await fetch("/api/farms", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload)
      });

      event.target.reset();
      render();
    });

    render();
  </script>
</body>
</html>
"""


class AppHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        route = urlparse(self.path).path
        if route == "/":
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML.encode("utf-8"))
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
        payload = json.loads(self.rfile.read(length) or b"{}")
        farm = FarmRecord.from_payload(payload)
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
