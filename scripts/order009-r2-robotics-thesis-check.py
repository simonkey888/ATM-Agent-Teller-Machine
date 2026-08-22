#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

REPORT = Path("deliverables/robotics_supply_chain_thesis/ROBOTICS_SUPPLY_CHAIN_THESIS.md")
TASK_ID = "0x4c887264d5ede369de6e98c6214e6c03ee8708af108305ecafa0341a675e6147"

REQUIRED_HEADINGS = [
    "## 1. Executive investment thesis",
    "## 2. Methodology, evidence hierarchy, and limitations",
    "## 3. Robotics market structure and commercialization stages",
    "## 4. Supply-chain map: where the robot actually comes from",
    "## 5. Bottlenecks, pricing power, and value capture",
    "## 6. Geographic and geopolitical structure",
    "## 7. Company landscape",
    "## 8. Investable public-company theses",
    "## 9. Scenario analysis: base, bull, and bear",
    "## 10. Risks and thesis breakers",
    "## 11. Ranked conclusions and 12–24 month monitor",
    "## 12. Full source list",
]

REQUIRED_SUPPLY_CHAIN_TERMS = [
    "Semiconductors and compute",
    "Sensors: vision, LiDAR, radar, encoders, and force/torque",
    "Motors, actuators, servos, reducers, bearings, gears, and motion control",
    "Batteries and power electronics",
    "Precision components, materials, and manufacturing equipment",
    "Operating systems, simulation, foundation models, perception, planning, and control",
    "Contract manufacturers, integrators, distributors, and maintenance",
]

REQUIRED_GEOGRAPHIES = ["China", "Japan", "South Korea", "Taiwan", "Europe", "United States"]
REQUIRED_COMPANIES = [
    "Nabtesco", "Harmonic Drive Systems", "Yaskawa", "FANUC", "THK", "SMC",
    "KEYENCE", "Cognex", "Teradyne", "NVIDIA", "TSMC", "ABB",
]
PRIMARY_DOMAINS = {
    "ifr.org", "investor.nvidia.com", "investor.tsmc.com", "fanuc.co.jp", "www.fanuc.co.jp",
    "yaskawa-global.com", "www.yaskawa-global.com", "investors.teradyne.com",
    "nabtesco.com", "www.nabtesco.com", "hds.co.jp", "www.hds.co.jp",
    "thk.com", "www.thk.com", "investor.cognex.com", "keyence.co.jp", "www.keyence.co.jp",
    "keyence.com", "www.keyence.com", "smcworld.com", "www.smcworld.com",
    "meti.go.jp", "www.meti.go.jp", "miit.gov.cn", "www.miit.gov.cn", "bis.gov", "www.bis.gov",
    "media.bis.gov", "digital-strategy.ec.europa.eu", "ai-act-service-desk.ec.europa.eu",
    "new.abb.com", "abb.com", "www.abb.com",
}


def check(condition: bool, label: str, detail: str = "") -> None:
    state = "PASS" if condition else "FAIL"
    print(f"{label}={state}" + (f" {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def main() -> int:
    global failures
    failures = []
    text = REPORT.read_text(encoding="utf-8")
    check(TASK_ID in text, "TASK_ID_BOUND")
    check("Publication / data cutoff" in text and "18 August 2026" in text, "DATA_CUTOFF")
    for heading in REQUIRED_HEADINGS:
        check(heading in text, "HEADING_" + re.sub(r"[^A-Z0-9]+", "_", heading.upper()).strip("_"))

    body = text.split(REQUIRED_HEADINGS[0], 1)[1].split(REQUIRED_HEADINGS[-1], 1)[0]
    words = re.findall(r"\b[\w’'-]+\b", body)
    count = len(words)
    check(9000 <= count <= 11000, "SUBSTANTIVE_WORD_COUNT", f"count={count}")

    for term in REQUIRED_SUPPLY_CHAIN_TERMS:
        check(term in text, "SUPPLY_CHAIN_" + re.sub(r"[^A-Z0-9]+", "_", term.upper()).strip("_"))
    for geo in REQUIRED_GEOGRAPHIES:
        check(geo in text, "GEOGRAPHY_" + geo.upper().replace(" ", "_"))
    for company in REQUIRED_COMPANIES:
        check(company in text, "COMPANY_" + re.sub(r"[^A-Z0-9]+", "_", company.upper()).strip("_"))

    company_profiles = re.findall(r"^### 8\.\d+ ", text, flags=re.M)
    check(len(company_profiles) >= 10, "PUBLIC_COMPANY_PROFILE_COUNT", f"count={len(company_profiles)}")
    for token in ("**Bull case.**", "**Bear case.**", "**Catalysts.**", "**Valuation considerations.**"):
        check(text.count(token) >= 10, "PROFILE_FIELD_" + re.sub(r"[^A-Z]+", "_", token.upper()).strip("_"), f"count={text.count(token)}")

    for scenario in ("### Base case", "### Bull case", "### Bear case"):
        check(scenario in text, "SCENARIO_" + scenario.split()[1].upper())
    check("Thesis breaker" in text or "thesis breaker" in text, "THESIS_BREAKERS")
    check("Methodology and limitations summary" in text, "METHODOLOGY_LIMITATIONS")
    check("does not infer undisclosed supplier relationships" in text, "SUPPLIER_RELATIONSHIP_GUARD")
    check("not a forecast" in text.lower() or "not market forecasts" in text.lower(), "FORECAST_GUARD")
    check("guaranteed return" not in text.lower(), "NO_GUARANTEED_RETURN_LANGUAGE")

    urls = sorted(set(re.findall(r"https://[^\s)>]+", text)))
    check(len(urls) >= 35, "UNIQUE_SOURCE_URL_COUNT", f"count={len(urls)}")
    bad_scheme = [u for u in urls if urlparse(u).scheme != "https"]
    check(not bad_scheme, "HTTPS_ONLY_SOURCES", f"bad={len(bad_scheme)}")
    bad_domains = sorted({urlparse(u).netloc.lower() for u in urls if urlparse(u).netloc.lower() not in PRIMARY_DOMAINS})
    check(not bad_domains, "PRIMARY_SOURCE_DOMAIN_ALLOWLIST", f"unexpected={bad_domains}")

    source_section = text.split(REQUIRED_HEADINGS[-1], 1)[1]
    numbered_sources = re.findall(r"^\d+\. ", source_section, flags=re.M)
    check(len(numbered_sources) >= 40, "FULL_SOURCE_LIST_COUNT", f"count={len(numbered_sources)}")

    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    print(f"ARTIFACT_SHA256={digest}")
    print(f"INDEPENDENT_CHECK={'PASS' if not failures else 'FAIL'}")
    if failures:
        print("FAILURES=" + ",".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
