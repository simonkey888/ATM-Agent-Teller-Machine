# The Global Robotics Supply Chain: An Investment Thesis on Scarcity, Qualification, and the Economics of Physical AI

**TaskMarket task:** `0x4c887264d5ede369de6e98c6214e6c03ee8708af108305ecafa0341a675e6147`  
**Publication / data cutoff:** 18 August 2026  
**Scope:** Global robotics supply chain across industrial robots, collaborative robots, autonomous mobile robots (AMRs), logistics automation, commercially significant service robots, and humanoid / general-purpose embodied systems.  
**Currency convention:** Company-reported figures retain their reported currency and period. USD conversions are avoided unless the primary source itself reports USD.  
**Important:** This report distinguishes facts from investment judgments. It is research, not individualized investment advice.

## Table of Contents

1. Executive investment thesis  
2. Methodology, evidence hierarchy, and limitations  
3. Robotics market structure and commercialization stages  
4. Supply-chain map: where the robot actually comes from  
5. Bottlenecks, pricing power, and value capture  
6. Geographic and geopolitical structure  
7. Company landscape  
8. Investable public-company theses  
9. Scenario analysis: base, bull, and bear  
10. Risks and thesis breakers  
11. Ranked conclusions and 12–24 month monitor  
12. Full source list

---

## 1. Executive investment thesis

### Central argument

The strongest investable proposition in robotics is not that a particular humanoid platform will dominate. It is that a broad expansion of machine autonomy increases the economic value of a smaller set of enabling capabilities that are difficult to qualify, difficult to manufacture consistently, or difficult to replace once designed into an automation system.

That distinction matters. Robot headlines tend to concentrate attention on complete machines: a humanoid walking on stage, a warehouse AMR moving a tote, a collaborative arm performing a demo, or a factory robot welding a body panel. Yet the complete machine is only the visible endpoint of a stack that includes semiconductors, compute, precision motion, reducers, bearings, encoders, machine vision, force sensing, power electronics, safety systems, simulation, deployment software, integrators, and after-sales service. In many categories, the platform layer can face intense competition and falling hardware prices while qualified component suppliers, software infrastructure, and integration channels retain switching costs.

The empirical base for this thesis is already larger than the humanoid narrative. The International Federation of Robotics (IFR) reports 542,000 industrial robots installed globally in 2024, more than twice the level ten years earlier and above 500,000 for the fourth consecutive year. Asia accounted for 74% of new installations, Europe 16%, and the Americas 9%. China alone installed 295,000 industrial robots, or 54% of the global total, while Chinese domestic robot suppliers reached a 57% share of their home market. Those are installed, revenue-bearing systems rather than forecasts. [IFR, World Robotics 2025](https://ifr.org/worldrobotics/report-2025)

Professional service robotics is smaller and less comprehensively measured, but also commercially real. IFR's supplier sample recorded almost 200,000 professional service robots sold in 2024, up 9%; transportation and logistics was the largest application group at 102,900 units, up 14%. Robot-as-a-service fleets in the sample rose 31%, and logistics RaaS 42%. IFR explicitly warns that its service-robot data is sample-based rather than a full-industry census, which is why this report uses it as adoption evidence rather than a precise market-size denominator. [IFR, Service Robots See Global Growth Boom](https://ifr.org/news/service-robots-see-global-growth-boom/1st-) IFR also classifies AMRs used for professional purposes as service robots, which helps separate them analytically from fixed industrial manipulators. [IFR, World Robotics Service Robots](https://ifr.org/wr-service-robots)

The investment implication is a **barbell of value capture**. On one side are scarce or qualification-heavy physical components: precision reducers, linear motion, high-quality bearings, servo systems, encoders, force/torque sensing, machine vision, and specialized automation components. On the other side are compute, foundry capacity, simulation, safety, data infrastructure, and software that make physical AI trainable, verifiable, deployable, and supportable. Between those poles sit robot OEMs, where the economics differ by category. Mature industrial robot OEMs can defend ecosystems, service relationships, safety know-how, and application libraries, but face cyclical capital-spending exposure and intensifying Chinese competition. Humanoid OEMs may capture exceptional value if a dominant platform emerges, but their current economics are too unproven to be the base case.

### Five highest-conviction conclusions

**1. The near-term robotics earnings pool is industrial automation and logistics, not humanoids.** Industrial robots have an installed base, replacement cycle, service ecosystem, integrators, and measurable annual installations. Logistics robots have observable unit sales and RaaS adoption. Humanoids should be treated as a long-duration call option on top of these existing automation markets, not as the denominator used to justify every supplier valuation.

**2. Qualification is a better moat than novelty.** A component that sits inside a safety-critical joint, motion-control loop, inspection cell, or high-uptime production line earns value not merely from its bill-of-materials share but from the cost of failure and requalification. Precision reducers illustrate the point. Nabtesco states that its precision reduction gears, used in joints of medium and large industrial robots, hold about 60% global share by its own estimate. [Nabtesco precision reduction gears](https://www.nabtesco.com/en/products/robot/) Harmonic Drive Systems similarly reports that much of its product demand is tied to industrial robots and industrial machinery, making its economics sensitive to automation capex but also directly exposed to high-precision motion. [Harmonic Drive Systems IR FAQ](https://www.hds.co.jp/english/ir/faq/)

**3. China changes the shape of the profit pool.** China's 54% share of 2024 global industrial robot installations and rising domestic supplier share mean investors should assume continued cost compression at the complete-machine level. [IFR, World Robotics 2025](https://ifr.org/worldrobotics/report-2025) That pressure can hurt undifferentiated hardware, but it can also expand unit volumes and accelerate automation adoption. The best suppliers are therefore those that can either remain technologically difficult to substitute or benefit from unit growth even as average selling prices fall.

**4. Physical AI increases demand for compute and data infrastructure, but robotics is not yet large enough to underwrite semiconductor leaders by itself.** NVIDIA is building an explicit physical-AI stack spanning synthetic-data generation, simulation, reinforcement learning, evaluation, and safety. In 2026 it announced an open physical-AI data-factory blueprint and a full-stack safety system for robotics. [NVIDIA Physical AI Data Factory](https://investor.nvidia.com/news/press-release-details/2026/NVIDIA-Announces-Open-Physical-AI-Data-Factory-Blueprint-to-Accelerate-Robotics-Vision-AI-Agents-and-Autonomous-Vehicle-Development/default.aspx) [NVIDIA Halos for Robotics](https://investor.nvidia.com/news/press-release-details/2026/NVIDIA-Announces-Halos-for-Robotics-the-Industrys-First-Full-Stack-Safety-System-for-Physical-AI/default.aspx) The robotics thesis strengthens NVIDIA's platform optionality but should not be mistaken for the principal driver of its current revenue. The same discipline applies to TSMC: robotics and edge AI are incremental beneficiaries of advanced logic demand, while TSMC's investment case remains much broader. TSMC reported Q2 2026 revenue of US$40.20 billion, a 67.7% gross margin, and a 60.3% operating margin. [TSMC Q2 2026](https://investor.tsmc.com/english/quarterly-results/2026/q2)

**5. Integration and safety are underestimated complements, not friction to be wished away.** Robotics adoption is constrained by workflow redesign, uptime requirements, risk assessment, certification, data availability, operator training, maintenance, and change management. Japan's 2026 AI Robotics Strategy explicitly emphasizes changing facilities and workflows into “robot-friendly” environments rather than assuming robots can simply be dropped into existing processes. [Japan METI robotics policy](https://www.meti.go.jp/policy/mono_info_service/mono/robot/index.html) In Europe, AI systems that operate safety functions in machinery can fall into high-risk regulatory treatment. [European Commission AI Act guidance](https://digital-strategy.ec.europa.eu/en/faqs/navigating-ai-act) These constraints create revenue pools for integrators, safety tooling, simulation, machine vision, and monitoring.

### Time horizon

The central horizon is **five to ten years**, with a nearer **12–24 month monitoring window** for order trends, capex cycles, product qualification, Chinese pricing, embodied-AI software adoption, and policy implementation. The thesis is deliberately structured so that it can work without a rapid humanoid takeoff. If humanoids scale, precision motion, sensing, compute, simulation, and manufacturing-equipment demand receives an additional leg. If humanoids disappoint, industrial automation and logistics can still support the base case.

### Key assumptions that must hold

1. Global manufacturing and logistics continue to automate because of labor cost, labor availability, quality, flexibility, reshoring, and throughput pressures.
2. Robot unit growth continues even if average selling prices decline.
3. High-reliability motion, sensing, and control components retain meaningful qualification and process barriers.
4. AI improves deployment economics enough to broaden applications, but does not eliminate the need for safety, integration, precision hardware, and domain engineering.
5. Export controls and geopolitical fragmentation do not cause a collapse in global automation capex; they instead re-route some investment and increase local-for-local supply-chain spending.
6. A severe global recession or prolonged manufacturing capex contraction does not persist through most of the horizon.

### What the market may misunderstand

The first misunderstanding is to equate robotics with humanoids. Humanoids are visually compelling but economically unproven at scale. The second is to assume the robot OEM necessarily captures most value. In a competitive hardware category, value can migrate toward critical components, application software, and service. The third is to assume every “AI supplier” gains equally. Physical AI creates specific constraints—latency, power, safety, data collection, simulation fidelity, deterministic control, and edge deployment—that favor certain infrastructure and tooling rather than generic software. The fourth is to treat geographic diversification as a simple relocation away from China. China is simultaneously the largest industrial robot market, a rapidly improving source of robot OEMs and components, and a geopolitical risk. Investors should model parallel ecosystems, not a clean decoupling.

---

## 2. Methodology, evidence hierarchy, and limitations

This report uses an evidence hierarchy designed to reduce one of the most common problems in robotics research: circular claims in which a vendor forecast is repeated by an article, then cited by a market report, then reappears as apparent consensus.

**Tier 1 evidence** is company filings, earnings releases, investor-relations materials, government publications, regulator material, and official industry statistics. These sources anchor factual claims about shipments, revenue, strategy, product scope, market share where a company discloses an estimate, and regulatory rules.

**Tier 2 evidence** would include peer-reviewed academic work and standards bodies. It is used conceptually where necessary, but this report avoids pretending academic benchmark performance directly proves commercial economics.

**Tier 3 evidence**—press coverage, market research aggregators, promotional forecasts, and unsourced supplier maps—is not used to establish material factual claims. That is particularly important for alleged humanoid supplier relationships. Unless a company, customer, filing, or similarly credible primary source confirms a relationship, this report does not state it as fact.

Market size is handled conservatively. IFR's industrial-robot installation data is treated as a high-quality adoption series. IFR's service-robot data is explicitly sample-based, so it is used directionally. This report does not select the largest third-party humanoid TAM forecast because doing so would create false precision. Instead, scenarios are tied to observable indicators: unit shipments, purchase orders, factory deployments, safety approvals, uptime, payback periods, and supplier capacity.

Company analysis is exposure-based, not “secret supplier” based. For example, a precision-motion company can be attractive because its technology is relevant to robotic joints and automation broadly even if no named humanoid OEM relationship is confirmed. Semiconductor companies are classified as indirect exposure when robotics represents only a small portion of their end demand.

Valuation discussion is framework-based rather than built around a single day's share price. Robotics beneficiaries span Japanese, U.S., European, and Taiwanese listings with different accounting, cyclicality, capital intensity, and conglomerate structures. A useful valuation process must therefore ask: what portion of revenue and profit is actually exposed to robotics; what cycle is embedded in current earnings; how much growth is already capitalized; how much of the business is unrelated; and what incremental return on invested capital is required to justify new capacity. Live multiples should be refreshed immediately before any investment decision.

The cutoff date is 18 August 2026. Events after that date are outside the report. Forward-looking statements made by companies are identified as management expectations rather than facts.

---

## 3. Robotics market structure and commercialization stages

Robotics is not one market. The economics of a six-axis welding robot in an automotive plant differ from a cobot in a machine-tending cell, an AMR in a warehouse, a surgical system, or a humanoid attempting general manipulation. Investment analysis improves when categories are separated by commercialization stage and customer problem.

### 3.1 Traditional industrial robots: mature technology, still growing units

Traditional industrial robots are the most commercially established category. They perform repeatable tasks such as welding, painting, material handling, assembly, dispensing, packaging, and machine tending. Their strengths are speed, repeatability, payload, durability, and integration into engineered production cells.

IFR's 542,000 global installations in 2024 demonstrate scale. More importantly, installations stayed above 500,000 for four consecutive years, showing that industrial automation is not a one-cycle phenomenon. [IFR, World Robotics 2025](https://ifr.org/worldrobotics/report-2025) The category remains cyclical because automotive, electronics, machinery, and other manufacturing customers adjust capex, but the secular drivers—labor economics, quality, throughput, and production localization—remain.

The economic moat of established industrial robot vendors comes from more than the arm. It includes controls, programming tools, safety certifications, installed-base service, application engineering, integrator relationships, spare parts, and customer familiarity. The risk is that these advantages are offset by lower-cost Chinese competitors, especially in China, where domestic vendors have already gained substantial share.

### 3.2 Collaborative robots: commercial, expanding applications, intense competition

Collaborative robots, or cobots, are designed to lower deployment friction for applications where flexibility and easier programming matter more than the maximum speed and payload of traditional industrial systems. Teradyne's 2025 Form 10-K describes Universal Robots as the provider of the first commercially viable cobot in 2008 and reports more than 110,000 cobots sold worldwide by the time of its 2026 filing. Teradyne's Robotics segment comprises Universal Robots cobot arms and Mobile Industrial Robots AMRs. [Teradyne 2025 Form 10-K filed 2026](https://investors.teradyne.com/sec-filings/all-sec-filings/content/0001193125-26-059002/ter-20251231.htm)

Cobots broaden the addressable customer base because they can be deployed in smaller manufacturers and more variable tasks. However, “collaborative” does not eliminate application-specific risk assessment, guarding, end-effector hazards, or integration. The economic contest is shifting from merely selling an arm to delivering complete application outcomes: palletizing, welding, machine tending, inspection, and material handling.

### 3.3 AMRs and logistics robots: commercial and supported by measurable workflow ROI

AMRs solve a different problem: moving materials through dynamic environments without fixed tracks. IFR reports transportation and logistics as the largest professional service-robot application in its 2024 sample, with 102,900 units sold and 14% growth. [IFR service robotics](https://ifr.org/news/service-robots-see-global-growth-boom/1st-) That matters because warehouse and factory intralogistics often has a clearer utilization metric than general-purpose manipulation: travel time, labor hours, pick-path efficiency, work-in-process movement, and safety incidents can be measured.

The AMR investment opportunity extends beyond vehicle OEMs to fleet management, mapping and perception, batteries, safety sensors, charging systems, warehouse-management integration, and maintenance. Hardware may commoditize faster than orchestration and enterprise integration.

### 3.4 Machine vision and inspection: mature enabling layer, entering an AI upgrade cycle

Machine vision is both a stand-alone automation category and a sensory layer for robotics. Cognex describes itself as an industrial machine-vision provider serving more than 30,000 customers across manufacturing and distribution. [Cognex investor relations](https://investor.cognex.com/home/default.aspx) In 2026 it made OneVision generally available after a beta in which more than 100 customers used the environment to develop and deploy AI-powered inspection. [Cognex OneVision](https://investor.cognex.com/news/news-details/2026/Cognex-OneVision-Adoption-Ramps-as-Manufacturers-Scale-AI-Vision-Globally/default.aspx)

The transition from deterministic rules to AI-assisted inspection can expand addressable use cases, but vision retains a hardware-and-process component: optics, lighting, sensor quality, edge compute, calibration, data management, and integration into line control. That makes machine vision a useful picks-and-shovels exposure to both conventional automation and AI-enabled robotics.

KEYENCE occupies a related position across sensors, code readers, measurement, and machine vision. Its corporate materials describe products spanning code readers, laser markers, machine-vision systems, measuring systems, microscopes, sensors, and static eliminators, with a direct-sales engineering model. [KEYENCE corporate overview](https://www.keyence.com/about-us/corporate/) This is diversified factory-automation exposure rather than a pure robotics bet.

### 3.5 Humanoids and general-purpose embodied robots: pre-scale, strategically important, economically uncertain

Humanoids could become a very large category because a human-shaped machine can theoretically fit environments designed for people. That architectural compatibility is real, but the economic hurdle is higher than a demo suggests. A useful humanoid must combine safe locomotion, dexterous manipulation, perception, planning, reliable actuators, energy density, thermal control, edge compute, robust software, maintainability, and a task-level ROI that beats alternatives.

The most useful way to model humanoids today is as a set of **optionality vectors** across the supply chain:
- more joints increase actuator, reducer, bearing, encoder, and motor content;
- dexterity increases sensing and control complexity;
- autonomy increases compute, training, simulation, and data needs;
- operation around people raises safety requirements;
- scaling creates demand for precision manufacturing and test equipment.

Policy confirms strategic interest but not commercial inevitability. Japan's revised 2026 AI Robotics Strategy targets broad social implementation across 18 fields and approximately 10 million robots by 2040; this is a government objective, not a forecast that investors should capitalize mechanically. [METI press conference, June 2026](https://www.meti.go.jp/english/speeches/press_conferences/2026/0630001.html) China's MIIT and SASAC launched a 2026 initiative to deploy humanoid robots and embodied intelligence in real operating environments to accelerate scale development. [MIIT 2026 action](https://www.miit.gov.cn/zwgk/zcwj/wjfb/tz/art/2026/art_f291ccd3da4c47ce95741de63cc088e6.html)

### 3.6 Other service robots

Professional cleaning, hospitality, agriculture, inspection, medical, and field robots have very different economics. They should not be rolled into one TAM. The common investment feature is that successful products are often application-specific and constrained by environment, regulation, safety, or workflow. This favors domain expertise and integration rather than a single universal hardware architecture.

### Demand drivers over five to ten years

The strongest common demand drivers are:
1. labor scarcity and wage pressure;
2. manufacturing localization and supply-chain resilience;
3. quality and traceability requirements;
4. shorter product cycles and the need for flexible automation;
5. falling compute and perception costs;
6. better AI tooling for unstructured tasks;
7. safety improvements and better simulation;
8. warehouse and logistics throughput;
9. aging workforces in developed economies;
10. industrial policy supporting domestic manufacturing.

The principal offsetting forces are recession, high capital costs, Chinese price competition, poor integration ROI, safety failures, regulation, and the possibility that AI advances faster in software than in robust physical manipulation.

---

## 4. Supply-chain map: where the robot actually comes from

A robot is best understood as a hierarchy of coupled systems. Failure in a low-cost component can disable a high-value machine, which means bill-of-materials share is not the same as economic importance.

### 4.1 Semiconductors and compute

Robotics compute spans microcontrollers, motor-control ICs, FPGAs, image signal processors, CPUs, GPUs, AI accelerators, memory, connectivity, and safety-related devices. Training advanced embodied models can require data-center compute; deployment often needs low-latency edge inference under tight power and thermal constraints.

NVIDIA is attempting to own a large part of the physical-AI developer stack, not just sell GPUs. Its 2026 physical-AI data-factory blueprint covers data curation, synthetic-data generation, reinforcement learning, and evaluation, while Halos for Robotics targets safety across development and deployment. [NVIDIA data factory](https://investor.nvidia.com/news/press-release-details/2026/NVIDIA-Announces-Open-Physical-AI-Data-Factory-Blueprint-to-Accelerate-Robotics-Vision-AI-Agents-and-Autonomous-Vehicle-Development/default.aspx) [NVIDIA Halos](https://investor.nvidia.com/news/press-release-details/2026/NVIDIA-Announces-Halos-for-Robotics-the-Industrys-First-Full-Stack-Safety-System-for-Physical-AI/default.aspx)

TSMC is the manufacturing leverage point behind a broad set of advanced compute customers. Robotics alone is not a meaningful way to forecast TSMC, but the industry's increasing edge-AI intensity adds to the same advanced-node and packaging demand generated by larger AI markets. TSMC's Q2 2026 profitability underscores the value currently captured by leading-edge manufacturing. [TSMC Q2 2026](https://investor.tsmc.com/english/quarterly-results/2026/q2)

**Investment judgment:** compute is strategically scarce at the frontier but economically diversified. The correct robotics exposure is therefore “platform optionality,” not a pure-play robotics multiple.

### 4.2 Sensors: vision, LiDAR, radar, encoders, and force/torque

Robots must estimate both the external world and their own internal state. External perception uses cameras, depth sensors, LiDAR, radar, ultrasonics, and proximity sensors. Internal state relies on encoders, current sensing, temperature sensing, and joint position/velocity feedback. Manipulation adds force/torque and tactile sensing.

Vision suppliers can capture value because industrial perception is a system, not a commodity camera module. Lighting, optics, calibration, software, edge compute, deployment tools, and application support matter. Cognex's push into embedded AI and centralized vision development shows how vendors can defend value as raw image sensors commoditize. Its In-Sight 3900, launched in May 2026, integrates embedded AI vision using Qualcomm platforms. [Cognex In-Sight 3900](https://investor.cognex.com/news/news-details/2026/Cognex-Launches-Highest-Performance-Embedded-AI-Vision-System-Powered-by-Qualcomm/default.aspx)

KEYENCE's direct-sales model is another moat mechanism: technical sales engineers help specify and deploy sensors and inspection systems. That creates an application-knowledge layer around hardware. [KEYENCE corporate overview](https://www.keyence.com/about-us/corporate/)

**Commoditization risk:** consumer-derived cameras and generic depth sensors can see severe price compression. The attractive layer is where hardware, software, calibration, and application know-how combine.

### 4.3 Motors, actuators, servos, reducers, bearings, gears, and motion control

This is one of the most important layers in the thesis. A robotic joint converts electrical power into controlled mechanical motion. Performance depends on torque density, efficiency, backlash, stiffness, repeatability, thermal behavior, noise, durability, and manufacturability.

Precision reducers are especially important in industrial robot joints. Nabtesco says its precision reduction gears are used in medium and large industrial robots and estimates roughly 60% global share. [Nabtesco](https://www.nabtesco.com/en/products/robot/) Harmonic Drive Systems sells strain-wave gearing and related products into industrial robots and machinery; its fiscal 2025 fourth-quarter orders reached ¥10,605 million, up 32.1% year over year, while sales were ¥9,634 million, up 14.6%. [Harmonic Drive orders and sales](https://www.hds.co.jp/english/ir/achievements/accounts/) For the year ending March 2027, it forecasts ¥68.0 billion in sales and ¥6.2 billion in operating profit, while citing firm automation and advanced-semiconductor demand. [Harmonic Drive forecast](https://www.hds.co.jp/english/ir/achievements/forecast/)

THK is exposed through linear-motion systems, ball screws, actuators, cross-roller rings, splines, joints, and robot solutions. [THK investor / product links](https://www.thk.com/jp/en/ir/) Its relevance is broader than robotics: machine tools, semiconductor equipment, and automation also consume motion components.

Yaskawa and FANUC sit higher in the stack, combining servos, drives, motion control, and robot systems. Their vertical integration gives them design knowledge and an installed base but also greater exposure to complete-system competition.

**Investment judgment:** precision motion is one of the most durable robotics bottlenecks because small tolerances compound across multi-axis systems, qualification is costly, and manufacturing know-how matters. The bear case is that Chinese competitors close the quality gap rapidly and pricing collapses.

### 4.4 Pneumatics, grippers, end effectors, connectors, and factory automation components

Not every robotic degree of freedom is electromechanical. Pneumatics remain deeply embedded in factories because they are simple, fast, clean, and well understood. SMC describes itself as a comprehensive automatic-control-equipment manufacturer supporting automation and labor saving. Its investor materials report FY ended March 2026 consolidated sales of ¥842.5 billion and net income attributable to owners of ¥167.3 billion, while its own estimate cites 36% global sales share and 62% Japanese share. [SMC investor overview](https://www.smcworld.com/ir/ja-jp/investor.html)

End effectors—the gripper, welder, screwdriver, vacuum cup, tool changer, or custom fixture—often determine whether a robot can perform a useful task. This layer is fragmented and application-specific. Fragmentation lowers supplier concentration but creates a large integration ecosystem. Standardization may commoditize simple grippers while adaptive hands, force-controlled tooling, and specialized end effectors retain engineering value.

Connectors, cable management, thermal parts, and power distribution rarely receive robotics headlines. Yet mobile and articulated systems impose repeated flexing, vibration, current-density, weight, and thermal constraints. The opportunity is strongest in high-reliability, high-cycle applications rather than generic commodity components.

### 4.5 Batteries and power electronics

Mobile robots and humanoids require batteries, battery-management systems, DC/DC conversion, inverters, charging, and thermal management. Battery cells themselves are likely to be a less attractive robotics-specific moat because robotics volume is small relative to electric vehicles and consumer electronics. Pack design, high-power delivery, safety, thermal control, and autonomous charging can be more differentiated.

A humanoid's battery challenge is particularly severe: energy consumption must be balanced against weight, runtime, peak actuator power, and heat. If humanoid deployments become multi-shift industrial assets, charging strategy and battery life become direct contributors to ROI.

### 4.6 Precision components, materials, and manufacturing equipment

Scaling robots requires more than sourcing parts. It requires machining, casting, gears, bearings, calibration rigs, metrology, test systems, tooling, and process control. This is where the “robot factory” itself becomes an automation customer.

The investment attraction of manufacturing equipment is second-order exposure: even if end-robot brands change, capacity expansion can benefit suppliers of precision motion, metrology, machine vision, semiconductor equipment, and automation. The risk is cyclicality—equipment demand can overshoot and then correct sharply.

### 4.7 Operating systems, simulation, foundation models, perception, planning, and control

The software stack has several layers:
- low-level real-time control;
- robot middleware and communications;
- mapping and localization;
- perception;
- task planning;
- simulation and digital twins;
- synthetic data;
- reinforcement learning;
- foundation models;
- fleet orchestration;
- safety monitoring;
- application software.

The crucial distinction is between **intelligence** and **control authority**. A foundation model can propose actions, but safe industrial deployment often needs deterministic constraints, validated control loops, and independent safety systems. This reduces the likelihood that one end-to-end model simply replaces the whole stack in safety-critical use cases.

Japan's METI explicitly selected R&D themes for robotics foundation models and for making manufacturing data AI-ready in 2026, highlighting real-data access as a strategic resource. [METI GENIAC robotics foundation models](https://www.meti.go.jp/english/press/2026/0514_001.html)

Simulation is attractive because physical data is expensive. If a robot must learn from millions of edge cases, generating and testing scenarios virtually can reduce time and cost. But simulation fidelity is itself a bottleneck: contact-rich manipulation, deformable objects, friction, sensor noise, and hardware wear are difficult to model perfectly.

### 4.8 Contract manufacturers, integrators, distributors, and maintenance

Robot deployment remains a project business in many settings. Systems integrators select robots, build cells, design guarding, write PLC logic, configure vision, install end effectors, connect manufacturing systems, validate safety, and train operators. Distributors and application partners extend OEM reach.

This layer can capture value because customers buy outcomes, not robots. The downside is lower scalability and labor intensity. The best integrators can build reusable application templates and service revenue; weak integrators remain project-by-project engineering shops.

Maintenance becomes increasingly important as robot fleets grow. Downtime economics are nonlinear: one failed joint or sensor can stop a line. Predictive maintenance, spare-parts logistics, remote diagnostics, and lifecycle service can create recurring revenue that is less volatile than new-equipment sales.

---

## 5. Bottlenecks, pricing power, and value capture

### 5.1 Where qualified supply is genuinely limited

**Precision reduction gears.** High torque density, low backlash, stiffness, durability, and repeatable manufacturing create a demanding process. Nabtesco's self-reported ~60% share in precision gears for medium/large industrial robot joints indicates historical concentration. [Nabtesco](https://www.nabtesco.com/en/products/robot/) Harmonic Drive's relevance in compact precision motion creates exposure to smaller joints and other high-precision applications.

**Advanced compute and manufacturing.** Leading-edge inference and training depend on a concentrated semiconductor ecosystem. Foundry, advanced packaging, high-bandwidth memory, EDA, and semiconductor equipment are all subject to capacity, technology, and geopolitical constraints. U.S. export controls explicitly target advanced computing chips, HBM, and semiconductor manufacturing equipment, demonstrating that these layers are strategic chokepoints beyond robotics. [BIS December 2024 controls](https://www.bis.gov/press-release/commerce-strengthens-export-controls-restrict-chinas-capability-produce-advanced-semiconductors-military) In January 2026 BIS revised licensing policy for certain H200/MI325X-class exports to China to case-by-case review subject to conditions, showing the policy remains dynamic. [BIS January 2026](https://media.bis.gov/press-release/department-commerce-revises-license-review-policy-semiconductors-exported-china)

**High-performance machine vision and sensing.** Basic sensors are abundant. The bottleneck is reliable perception under real production variation, with calibration, software, lighting, edge inference, and support. Cognex and KEYENCE monetize this integration of components and application knowledge.

**System integration and safety engineering.** There is no global shortage statistic as clean as robot installations, but field deployment repeatedly depends on integrators who understand the customer's process. Japan's robot-policy emphasis on “robot-friendly” environments is evidence that workflow and infrastructure are part of the deployment constraint. [METI robotics policy](https://www.meti.go.jp/policy/mono_info_service/mono/robot/index.html)

**Data for physical tasks.** Internet-scale text data does not directly solve manipulation. Real-world trajectories, demonstrations, failure cases, tactile feedback, and environment variation are expensive to collect. That is why both government and industry are investing in synthetic data, simulation, and physical-AI data factories.

### 5.2 Switching costs and qualification cycles

Qualification protects suppliers when replacing a component changes robot dynamics or safety behavior. Consider a reducer. A new supplier may fit mechanically, yet a robot OEM must verify backlash, torque capacity, fatigue, vibration, noise, lubrication, thermal behavior, control tuning, lifetime, and manufacturing consistency. Requalification can consume engineering time and create warranty risk. Similar logic applies to encoders, safety systems, vision components, and servo drives.

Switching costs are lower for non-critical, standardized parts. Investors should therefore avoid assuming “robot content” automatically means pricing power. A commodity fastener benefits from volume but not necessarily margin. A proprietary high-cycle bearing, calibrated force sensor, or validated safety controller can have better economics despite a smaller unit cost.

### 5.3 What commoditizes first

The most vulnerable areas are:
- basic camera modules;
- low-end generic motors;
- standardized metal fabrication;
- commodity battery cells;
- simple grippers;
- basic AMR chassis hardware;
- undifferentiated robot arms at common payloads;
- software features that become embedded in broader platforms.

Chinese manufacturing scale accelerates this process. IFR's evidence that Chinese robot suppliers held 57% of their domestic market in 2024 suggests investors should assume competition expands outward over time. [IFR](https://ifr.org/worldrobotics/report-2025)

Commoditization is not uniformly bearish. Lower hardware prices can unlock demand, increasing total units of sensors, reducers, controllers, and service. A supplier can therefore gain volume even as the robot OEM's gross margin falls.

### 5.4 Who is likely to win as production scales

**Component suppliers win** when the component is hard to qualify, has process IP, and grows with robot degrees of freedom. Precision motion is the clearest example.

**Software and compute platforms win** if they become standards across multiple OEMs. NVIDIA's strategy is explicitly cross-platform: simulation, data generation, foundation-model tooling, and safety are designed to serve many robot developers. The economic risk is that open-source alternatives or customer-specific stacks reduce platform take rates.

**Robot OEMs win** when they combine reliable hardware with a large integrator ecosystem, application software, service, and installed-base lock-in. FANUC, Yaskawa, and Universal Robots illustrate different variants of that model. OEMs lose when the arm becomes interchangeable and customers purchase primarily on price.

**Integrators win** when deployment complexity remains high. They lose if applications become sufficiently standardized that OEMs or software platforms can sell turnkey packages directly.

### 5.5 Value capture by category

For traditional industrial robots, value is likely to be shared among OEMs, servo/motion suppliers, integrators, and service. For cobots, ecosystem and ease of deployment matter more, but price competition is rising. For AMRs, fleet software and workflow integration may be more durable than chassis hardware. For humanoids, the value pool is unknown; initially, scarce actuators, precision components, compute, and engineering could capture outsized value, while later mass production could shift value toward software, service, and distribution.

### 5.6 Capacity constraints versus temporary tightness

Investors should distinguish structural bottlenecks from cyclical shortages. A component is structurally attractive when expanding capacity requires specialized know-how, long qualification, proprietary process control, or customer validation. It is only temporarily tight when standard machinery and capital can add supply quickly.

The practical monitor is lead time plus gross margin plus competitor entry. If lead times rise and margins expand while qualified competitors remain limited, pricing power is real. If dozens of suppliers add capacity and ASPs collapse, the bottleneck was temporary.

---

## 6. Geographic and geopolitical structure

### 6.1 China: demand center, manufacturing challenger, policy accelerator

China is simultaneously the world's largest industrial robot market and the most important competitive threat to incumbent robot OEM economics. IFR's 295,000 installations in 2024—54% of the global total—and 57% domestic-supplier share in China show both sides of the equation. [IFR](https://ifr.org/worldrobotics/report-2025)

China's policy is moving from prototype promotion toward standards, field deployment, and scale. In June 2026 MIIT and SASAC announced a real-world training initiative for humanoid robots and embodied intelligence, calling for normalized deployment in real production and life environments. [MIIT](https://www.miit.gov.cn/zwgk/zcwj/wjfb/tz/art/2026/art_f291ccd3da4c47ce95741de63cc088e6.html) MIIT has also emphasized integrated joints, computing chips, testing, data security, and standards as development priorities. [MIIT 2025 industrial development review](https://www.miit.gov.cn/xwfb/bldhd/art/2026/art_4ba01aa6d2f54ced8ba3490ea4fb52c4.html)

**Investment implication:** do not assume Western and Japanese suppliers permanently retain China share. Model local substitution. At the same time, do not ignore China's unit growth: suppliers that remain qualified or participate through local production can benefit from the largest installation base.

### 6.2 Japan: precision motion, industrial robotics, and a policy push toward AI robotics

Japan is unusually important because FANUC, Yaskawa, Harmonic Drive Systems, Nabtesco, THK, KEYENCE, and SMC collectively span robot OEMs, servos, reducers, motion components, sensors, and factory automation.

Japan's 2026 AI Robotics Strategy targets deployment across 18 fields and about 10 million robots by 2040. [METI](https://www.meti.go.jp/english/speeches/press_conferences/2026/0630001.html) More important than the headline target is the policy design: support user-company adoption, create a core AI robotics hub, make real industrial data usable for AI, and develop robotics foundation models. [METI GENIAC](https://www.meti.go.jp/english/press/2026/0514_001.html)

**Investment implication:** Japan offers a dense set of listed picks-and-shovels companies. The risk is that their legacy exposure to machine tools, semiconductors, and automotive capex makes earnings cyclical and can obscure the robotics structural trend.

### 6.3 South Korea

South Korea's strategic relevance comes from semiconductors, memory, batteries, electronics manufacturing, and industrial automation. Even without assuming a specific humanoid supply relationship, Korean HBM, battery, and electronics capabilities sit in enabling layers of physical AI. The key risk is geopolitical exposure to both U.S.-aligned export regimes and China-linked demand.

This report does not assign a specific Korean company a confirmed robotics customer relationship without primary evidence. The geographic thesis is capability-based.

### 6.4 Taiwan

Taiwan's central role is semiconductor manufacturing and electronics supply chains. TSMC is the clearest listed exposure. The economic advantage is unmatched leading-edge manufacturing scale and customer trust; the risk is geopolitical concentration. TSMC's own investor materials remain the appropriate source for current financial performance rather than robotics-specific market forecasts. [TSMC investor relations](https://investor.tsmc.com/english)

### 6.5 Europe

Europe combines industrial automation leaders, machinery OEMs, and strict product-safety regulation. ABB's robotics business illustrates both strategic value and portfolio restructuring: ABB agreed in October 2025 to sell its Robotics division to SoftBank at an enterprise value of US$5.375 billion, with closing expected in mid-to-late 2026, replacing its earlier spin-off plan. ABB reported that the division had 2024 revenue of US$2.3 billion and a 12.1% operational EBITA margin. [ABB sale to SoftBank](https://new.abb.com/news/detail/129776/abb-to-divest-robotics-division-to-softbank-group)

The EU AI Act creates an additional layer for AI systems used as safety components of regulated products. European Commission guidance notes that AI systems operating robots can be high-risk when they meet the relevant product and conformity-assessment conditions. [EU AI Act FAQ](https://digital-strategy.ec.europa.eu/en/faqs/navigating-ai-act) Draft guidance published in 2026 provides examples such as AI computer vision detecting human presence in a robot cell and triggering safe stop or speed reduction. [EU AI Act Service Desk](https://ai-act-service-desk.ec.europa.eu/en/high-risk-ai-regulated-products)

**Investment implication:** compliance raises costs, but it can favor established safety, control, testing, and integration vendors. Regulation can therefore become a moat for qualified systems rather than only a burden.

### 6.6 United States

The U.S. has strengths in AI compute, software, semiconductor design, machine vision, and robotics startups, while much precision motion manufacturing remains globally distributed. NVIDIA, Cognex, and Teradyne provide public-market exposure to different parts of the stack.

U.S. export controls are a major variable for advanced computing and semiconductor equipment. BIS has progressively restricted advanced computing, HBM, and semiconductor manufacturing equipment to China, while revising license review policies as technology and policy change. [BIS controls](https://www.bis.gov/press-release/commerce-strengthens-export-controls-restrict-chinas-capability-produce-advanced-semiconductors-military) [BIS January 2026](https://media.bis.gov/press-release/department-commerce-revises-license-review-policy-semiconductors-exported-china)

**Investment implication:** export controls can reduce addressable markets for certain products, accelerate Chinese substitution, and redirect semiconductor capex geographically. They also increase the strategic value of non-China manufacturing capacity and compliant supply chains.

### 6.7 Reshoring and local-for-local manufacturing

Robotics both enables and benefits from reshoring. Higher local labor costs can improve automation ROI, while new factories create greenfield opportunities to design robot-friendly workflows from the start. ABB's 2025 reporting emphasized a “local-for-local” strategy as a resilience mechanism. [ABB annual-report chairman letter](https://www.abb.com/global/en/company/annual-reporting-suite/chairmans-letter)

The investment trap is to count announced factories as guaranteed robot orders. Investors should wait for capex, purchase orders, automation contracts, and supplier commentary.

---

## 7. Company landscape

The table below separates confirmed business roles from investment interpretation. “Exposure type” describes how directly a company's economics are tied to robotics; it does **not** assert supplier relationships to any unnamed robot OEM.

| Company | Country | Public? | Supply-chain role | Confirmed evidence | Exposure type | Principal risk |
|---|---|---:|---|---|---|---|
| FANUC | Japan | Yes | Industrial robots, CNC, factory automation | Current financial materials available through official IR; major robot OEM | Direct | China competition, capex cyclicality |
| Yaskawa Electric | Japan | Yes | Industrial robots, servos, drives, motion control | Official IR and 2026 robotics/AI announcements | Direct | Cyclicality, price competition |
| Teradyne | U.S. | Yes | Universal Robots cobots, MiR AMRs; semiconductor test | 2026 10-K identifies Robotics segment and >110k cobots sold | Direct but diversified | Robotics profitability, semiconductor-cycle mix |
| Nabtesco | Japan | Yes | Precision reduction gears | Company estimates ~60% global share for medium/large industrial robot joint reducers | Direct component | Customer concentration, Chinese substitution |
| Harmonic Drive Systems | Japan | Yes | Precision strain-wave gears, mechatronics | IR ties products to industrial robots/machinery | Direct component | High cyclicality, capacity/price |
| THK | Japan | Yes | Linear guides, ball screws, actuators, cross-roller rings, joints | Official product and IR materials | Direct component / diversified automation | Machine-tool cycle |
| SMC | Japan | Yes | Pneumatics and automatic-control components | Official materials describe automation/labor-saving role and global share estimate | Automation picks-and-shovels | Factory capex cycle, pricing |
| KEYENCE | Japan | Yes | Sensors, machine vision, measurement, code readers | Official corporate and 2026 IR materials | Automation picks-and-shovels | Premium valuation, cyclicality |
| Cognex | U.S. | Yes | Industrial machine vision, edge AI inspection | 2026 10-K and product releases | Direct enabling layer | Electronics/auto capex, competition |
| NVIDIA | U.S. | Yes | AI compute, simulation/data/safety stack for physical AI | Official 2026 physical-AI and robotics launches | Indirect / platform option | Valuation, AI-cycle concentration |
| TSMC | Taiwan | Yes | Foundry manufacturing for advanced logic | Official 2026 financials | Indirect | Geopolitics, capex intensity |
| ABB | Switzerland | Yes | Automation; Robotics division pending sale to SoftBank | US$5.375bn sale agreement; Robotics discontinued operations | Transitioning / indirect | Transaction timing; reduced direct robotics exposure |
| SoftBank Group | Japan | Yes | Pending acquirer of ABB Robotics; AI/robotics capital allocation | Signed ABB Robotics acquisition | Strategic option | Portfolio complexity, execution |
| Agility Robotics | U.S. | Private | Humanoid / bipedal robots | NVIDIA says Agility is first incorporating elements of Halos | Private watch | Pre-scale unit economics |
| Skild AI | U.S. | Private | Robotics foundation models | Named by NVIDIA among physical-AI data-factory users | Private watch | Model differentiation, commercialization |
| FieldAI | U.S. | Private | General-purpose autonomy / field robotics | Named by NVIDIA among physical-AI data-factory users | Private watch | Deployment economics |
| Intrinsic | U.S. | Private (Alphabet) | Industrial robotics software | Participates in industrial automation ecosystem | Private/subsidiary watch | Monetization and integration |

Primary company sources: [FANUC IR](https://www.fanuc.co.jp/en/ir/announce/), [Yaskawa IR](https://www.yaskawa-global.com/ir), [Teradyne 10-K](https://investors.teradyne.com/sec-filings/all-sec-filings/content/0001193125-26-059002/ter-20251231.htm), [Nabtesco precision](https://www.nabtesco.com/en/products/robot/), [Harmonic Drive IR](https://www.hds.co.jp/english/ir/), [THK IR](https://www.thk.com/jp/en/ir/), [SMC IR](https://www.smcworld.com/ir/en-jp/), [KEYENCE IR](https://www.keyence.co.jp/investor/en/), [Cognex 10-K](https://investor.cognex.com/financial-reports/sec-filings/sec-filings-details/default.aspx?FilingId=19139108), [NVIDIA IR](https://investor.nvidia.com/financial-info/annual-reports-and-proxies/default.aspx), [TSMC IR](https://investor.tsmc.com/english), [ABB Robotics transaction](https://new.abb.com/news/detail/129776/abb-to-divest-robotics-division-to-softbank-group).

---

## 8. Investable public-company theses

This section provides more than the required ten investable public companies. “Bull,” “bear,” and “valuation” are analytical frameworks, not price targets.

### 8.1 Nabtesco — concentrated precision-reducer leverage

**Role and exposure.** Nabtesco's precision reduction gears are used in joints of medium and large industrial robots. The company estimates roughly 60% global share in this category. [Nabtesco precision reduction gears](https://www.nabtesco.com/en/products/robot/) That is unusually direct picks-and-shovels exposure.

**Bull case.** Robot unit growth increases reducer demand even if robot ASPs fall. A humanoid wave is not required because traditional industrial robots already consume the product. Manufacturing know-how, reliability, and customer qualification can defend share longer than a generic mechanical component.

**Bear case.** Chinese reducer suppliers improve faster than expected, qualifying with major OEMs and compressing both share and pricing. Industrial robot capex can also fall sharply in recessions. Concentrated exposure is a benefit in the thesis and a risk in the cycle.

**Catalysts.** Industrial robot order recovery; capacity utilization; new product wins; evidence that precision demand expands into adjacent automation; stabilization of China pricing.

**Valuation considerations.** Normalize earnings across the factory-automation cycle. A peak-cycle multiple can look deceptively cheap. The key variables are sustainable reducer margin, share durability, and incremental returns on capacity.

**Exposure classification:** direct component, high conviction.

### 8.2 Harmonic Drive Systems — high-beta precision-motion option

**Role and exposure.** Harmonic Drive supplies precision gearing used in industrial robots and other machinery. Its Q4 fiscal 2025 order and sales growth show a recovery signal, while management's fiscal 2026 forecast assumes firm automation and advanced-semiconductor demand. [Orders/sales](https://www.hds.co.jp/english/ir/achievements/accounts/) [Forecast](https://www.hds.co.jp/english/ir/achievements/forecast/)

**Bull case.** Smaller and multi-axis robots require compact, low-backlash reducers. Humanoids, dexterous arms, semiconductor equipment, and automation can all add demand. Operating leverage can be substantial when utilization rises.

**Bear case.** The same operating leverage works in reverse. Precision-reducer demand is cyclical, competitive, and capacity sensitive. A humanoid-driven order surge could lead to overexpansion before end demand is proven.

**Catalysts.** Sustained bookings, margin recovery, customer diversification, better utilization, capacity discipline.

**Valuation considerations.** Use normalized free cash flow and mid-cycle margins rather than extrapolating a single rebound quarter. Treat humanoid demand as an option, not base earnings.

**Exposure classification:** direct component, higher risk.

### 8.3 Yaskawa Electric — integrated motion-control and robot OEM

**Role and exposure.** Yaskawa combines industrial robots, servo motors, drives, and motion control. Its official 2026 news includes AI-robot initiatives and partnerships, including MOTOMAN NEXT work positioned around AI-enabled robotics. [Yaskawa IR news](https://www.yaskawa-global.com/ir/news)

**Bull case.** Vertical integration allows Yaskawa to capture economics in both robot systems and motion components. If AI makes automation easier to program, the installed base and motion expertise can translate into more applications rather than requiring a new hardware architecture.

**Bear case.** Industrial robot pricing faces Chinese pressure. The company is exposed to manufacturing capex cycles, and AI-enabled product announcements may not translate into differentiated margins.

**Catalysts.** Robot order growth, servo demand, China stabilization, evidence of monetized AI applications, service/software growth.

**Valuation considerations.** Separate cyclical recovery from structural growth. Compare robot/motion profitability against historical margins and peers, and avoid paying a humanoid premium without disclosed revenue.

**Exposure classification:** direct, diversified within automation.

### 8.4 FANUC — installed-base quality with China-cycle sensitivity

**Role and exposure.** FANUC is a major industrial robot and CNC supplier with a deep factory-automation installed base. Its official IR page provides current fiscal 2026 results and guidance materials. [FANUC IR results](https://www.fanuc.co.jp/en/ir/announce/)

**Bull case.** Reliability, global service, integrator familiarity, and customer qualification are meaningful advantages in high-uptime factories. A broader automation cycle benefits both robots and CNC. AI can lower programming barriers while FANUC retains control and safety expertise.

**Bear case.** China is both a major end market and a source of increasingly capable competitors. Mature robot hardware can see price compression. Automotive and electronics cycles can create long order downturns.

**Catalysts.** China order recovery, factory-automation capex, new robot introductions, software/service mix improvement.

**Valuation considerations.** FANUC should be valued as a high-quality cyclical industrial technology company, not a software company. Net cash and cycle position matter. The robotics thesis is credible without assigning a speculative humanoid TAM.

**Exposure classification:** direct robot OEM.

### 8.5 THK — linear motion as a broad automation picks-and-shovels play

**Role and exposure.** THK's lineup includes LM guides, ball screws, actuators, cross-roller rings, splines, joints, and robot solutions. [THK IR/product index](https://www.thk.com/jp/en/ir/) The products are relevant to robots but also machine tools, semiconductor equipment, and general automation.

**Bull case.** Linear and rotary motion components benefit from the proliferation of automated axes across factories, warehouses, and equipment. This is a “more machines moving more precisely” thesis rather than a bet on one robot form factor.

**Bear case.** Broad industrial exposure makes THK vulnerable to capex downturns. Standard linear-motion products can face price competition and substitution.

**Catalysts.** Machine-tool recovery, semiconductor-equipment spending, automation capex, improved asset utilization.

**Valuation considerations.** Analyze segment margins and returns through the cycle. Robotics optionality should justify only a modest premium unless disclosed robotics mix rises materially.

**Exposure classification:** diversified picks-and-shovels.

### 8.6 SMC — factory-automation installed-base compounder

**Role and exposure.** SMC supplies pneumatic and automatic-control equipment used across factory automation. Company materials report FY ended March 2026 sales of ¥842.5 billion and an estimated 36% global sales share in its market. [SMC overview](https://www.smcworld.com/ir/ja-jp/investor.html)

**Bull case.** Robotics adoption rarely removes the rest of the automated cell. Gripping, clamping, air preparation, valves, actuators, sensors, and control components proliferate as factories automate. SMC's broad catalog, distribution, inventory, and installed base can benefit regardless of which robot OEM wins.

**Bear case.** Pneumatics can lose share in some applications to electric actuators; factory capex is cyclical; high market share can limit incremental share gains.

**Catalysts.** Global manufacturing recovery, labor-saving capex, semiconductor equipment, energy-efficient product adoption.

**Valuation considerations.** Focus on organic growth, margins, inventory efficiency, and cash returns. Treat robotics as one contributor within a much larger automation franchise.

**Exposure classification:** diversified automation, high quality.

### 8.7 KEYENCE — premium sensing and machine-vision economics

**Role and exposure.** KEYENCE develops sensors, machine vision, measurement, code readers, and inspection equipment and uses a direct-sales engineering model. Its 2026 annual report and FY2026 first-quarter materials are available on the official IR site. [KEYENCE annual reports](https://www.keyence.co.jp/investor/library/annualreport.jsp) [KEYENCE IR news](https://www.keyence.co.jp/investor/en/news.jsp)

**Bull case.** More automation creates more sensing and inspection points. As factories become more flexible, the value of easy-to-deploy, high-performance sensing rises. Direct application support can preserve pricing power even as raw sensor hardware commoditizes.

**Bear case.** KEYENCE's quality and profitability can command a premium valuation, leaving less room for execution errors. Industrial capex downturns and cheaper AI-enabled competitors can pressure growth.

**Catalysts.** Factory automation recovery, machine-vision AI adoption, international customer expansion, new product cycles.

**Valuation considerations.** The central question is not whether KEYENCE benefits from robotics; it does indirectly. The question is how much of that benefit is already reflected in a premium multiple. Require durable revenue growth and margin quality rather than paying for a humanoid narrative.

**Exposure classification:** indirect but high-quality automation enabler.

### 8.8 Cognex — AI machine vision as a robotics sensory layer

**Role and exposure.** Cognex is a pure industrial machine-vision specialist relative to diversified sensor peers. It serves manufacturing and distribution, and its 2026 strategy emphasizes AI-enabled vision. [Cognex 2025 10-K filed 2026](https://investor.cognex.com/financial-reports/sec-filings/sec-filings-details/default.aspx?FilingId=19139108)

**Bull case.** AI expands the set of defects and objects that vision can recognize while reducing the application-engineering burden. Robotics, logistics, and quality inspection all need robust perception. OneVision can create a software layer across deployed systems, potentially improving stickiness.

**Bear case.** Machine vision is exposed to consumer-electronics and automotive capex; cheaper cameras and open AI models can erode hardware differentiation; enterprise AI deployments may remain slow.

**Catalysts.** OneVision adoption, edge-AI product growth, customer expansion, electronics capex recovery, cross-site deployments.

**Valuation considerations.** Monitor the mix between hardware, software, and service, along with operating margins and cyclicality. The strongest valuation case requires evidence that AI increases lifetime customer value rather than merely replacing legacy products.

**Exposure classification:** direct enabling layer.

### 8.9 Teradyne — direct cobot and AMR exposure inside a semiconductor-test company

**Role and exposure.** Teradyne's Robotics segment comprises Universal Robots and Mobile Industrial Robots. Its 2026 10-K reports more than 110,000 cobots sold worldwide since the commercial launch of Universal Robots' platform. [Teradyne 10-K](https://investors.teradyne.com/sec-filings/all-sec-filings/content/0001193125-26-059002/ter-20251231.htm)

**Bull case.** Cobots and AMRs address measurable labor and flexibility problems in factories and logistics. Teradyne owns recognizable brands, application ecosystems, and distribution. If robotics profitability improves, the segment can become more material within a company whose semiconductor test business already has scale.

**Bear case.** Robotics is competitive and has historically faced uneven profitability. Chinese cobot and AMR suppliers can compress prices. Teradyne's valuation may be dominated by semiconductor test cycles, making robotics hard to isolate.

**Catalysts.** Robotics revenue acceleration, segment margin improvement, new UR products, AMR fleet growth, application-center productivity.

**Valuation considerations.** Use sum-of-the-parts discipline. Do not assign software-like multiples to a hardware business until recurring software/service economics are visible. Conversely, avoid valuing Robotics at zero if installed base and growth become durable.

**Exposure classification:** direct, diversified conglomerate.

### 8.10 NVIDIA — physical-AI platform optionality, not a robotics pure play

**Role and exposure.** NVIDIA supplies compute and is building robotics software, simulation, data, and safety infrastructure. In 2026 it announced a physical-AI data-factory blueprint and Halos for Robotics. [NVIDIA data factory](https://investor.nvidia.com/news/press-release-details/2026/NVIDIA-Announces-Open-Physical-AI-Data-Factory-Blueprint-to-Accelerate-Robotics-Vision-AI-Agents-and-Autonomous-Vehicle-Development/default.aspx) [NVIDIA Halos](https://investor.nvidia.com/news/press-release-details/2026/NVIDIA-Announces-Halos-for-Robotics-the-Industrys-First-Full-Stack-Safety-System-for-Physical-AI/default.aspx)

**Bull case.** If physical AI becomes a major compute category, NVIDIA can monetize training, simulation, synthetic data, edge inference, and developer tooling across many robot OEMs. Cross-platform tools may be more valuable than owning a robot brand.

**Bear case.** Robotics could remain small relative to data-center AI for years. Customers may use lower-cost inference silicon, open-source simulation, or vertically integrated stacks. NVIDIA's overall valuation can overwhelm any robotics-specific upside.

**Catalysts.** Production deployments using NVIDIA robotics software, edge-compute revenue disclosures, developer adoption, safety-platform integration, major OEM standardization.

**Valuation considerations.** Do not justify NVIDIA's entire multiple with robotics. Treat physical AI as an option whose value rises only as robotics revenue and utilization become visible.

**Exposure classification:** indirect platform.

### 8.11 TSMC — foundry bottleneck with robotics as incremental demand

**Role and exposure.** TSMC manufactures advanced semiconductors for a broad customer base. Q2 2026 revenue was US$40.20 billion with gross margin of 67.7% and operating margin of 60.3%. [TSMC Q2 2026](https://investor.tsmc.com/english/quarterly-results/2026/q2)

**Bull case.** Physical AI adds edge-compute and accelerator demand on top of larger AI, mobile, and HPC markets. Robot fleets can increase the number of intelligent endpoints without requiring TSMC to choose winning robot brands.

**Bear case.** Robotics is too small to move TSMC fundamentals near term. Geopolitical concentration is the dominant risk. Advanced-node capex can overshoot if AI demand slows.

**Catalysts.** Continued leading-node demand, advanced-packaging expansion, geographic fab ramp, AI edge-device growth.

**Valuation considerations.** Robotics should be treated as incremental optionality. The core valuation rests on foundry leadership, customer concentration, capex, margins, and geopolitics.

**Exposure classification:** indirect infrastructure.

### 8.12 ABB — automation quality, but direct robotics exposure is being sold

**Role and exposure.** ABB agreed to sell its Robotics division to SoftBank for an enterprise value of US$5.375 billion, with expected closing in mid-to-late 2026. Robotics has been reported as discontinued operations since Q4 2025. [ABB transaction](https://new.abb.com/news/detail/129776/abb-to-divest-robotics-division-to-softbank-group)

**Bull case.** ABB remains a major automation and motion company, and proceeds can be redeployed into higher-synergy areas. Its installed automation base still benefits from factory digitization and motion-control demand.

**Bear case.** Investors seeking direct robotics exposure will lose it after closing. Capital allocation of sale proceeds becomes more important than robot-industry growth.

**Catalysts.** Transaction close, capital return or accretive redeployment, automation growth, integration of acquisitions.

**Valuation considerations.** Value ABB on continuing businesses and expected net proceeds, not on ownership of a robotics segment scheduled for divestment.

**Exposure classification:** indirect / transition.

### Portfolio construction implication

A robotics supply-chain portfolio should avoid stacking eleven versions of the same macro exposure. Precision reducers, robot OEMs, machine vision, pneumatics, compute, and foundry respond differently to cycles and competitive pressure. A balanced basket could combine:
- precision motion: Nabtesco / Harmonic Drive;
- factory automation: SMC / KEYENCE / THK;
- robot OEM: FANUC / Yaskawa / Teradyne;
- perception: Cognex;
- compute/foundry optionality: NVIDIA / TSMC.

This is a framework, not a recommended allocation. Entry valuation and investor risk tolerance remain decisive.

---

## 9. Scenario analysis: base, bull, and bear

Scenario analysis is more useful than a single robotics CAGR because it forces investors to specify what must happen operationally.

### Base case: automation broadens; humanoids remain selective

**Assumptions.**
- Industrial robot installations grow through the cycle but do not explode.
- Chinese robot OEM share continues rising.
- Cobots and AMRs expand in machine tending, palletizing, logistics, and smaller manufacturers.
- Humanoids achieve real deployments in selected factories and logistics environments but remain a small fraction of total robot units.
- AI reduces programming and perception costs, but safety and integration remain material.
- Semiconductor export controls persist with periodic revisions rather than disappearing.
- Component price pressure coexists with unit growth.

**Supply-chain consequences.**
Precision motion grows moderately, with share pressure from China. Machine vision enters an AI upgrade cycle. Integrators and service remain important. Robot OEM margins are mixed: leaders with ecosystems defend profitability, while undifferentiated hardware compresses. NVIDIA and TSMC see robotics as an incremental, not primary, AI demand source.

**Likely winners.**
SMC, KEYENCE, Cognex, THK, select precision-reducer suppliers, established robot OEMs with service ecosystems, and cross-platform physical-AI tooling.

**Indicators.**
IFR installations; robot OEM orders; reducer bookings; machine-vision growth; AMR fleet deployments; integrator backlogs; evidence of production rather than pilot humanoids; factory payback periods.

### Bull case: physical AI lowers deployment cost and humanoids become a real volume category

**Assumptions.**
- Foundation-model control materially improves task generalization.
- Simulation and synthetic data cut training and validation time.
- Humanoid uptime, safety, battery life, and dexterity reach acceptable industrial thresholds.
- Multiple OEMs move from thousands to tens or hundreds of thousands of annual units.
- Robot-friendly factory redesign accelerates.
- Labor shortages and reshoring reinforce demand.

**Supply-chain consequences.**
The number of actuated axes and sensors rises sharply. Precision reducers, motors, encoders, bearings, force sensors, compute, power electronics, and thermal management see stronger demand. Manufacturing equipment for robot production becomes a second-order winner. Data-center training and edge inference expand. Qualification bottlenecks can temporarily increase pricing power.

The bull case is not equally bullish for all robot OEMs. Rapid industry growth can still produce poor returns if too many manufacturers compete and hardware prices collapse. The safest beneficiaries may remain components and platforms shared across winners.

**Likely winners.**
Precision motion suppliers with capacity and quality; NVIDIA-like physical-AI platforms; TSMC and advanced semiconductor infrastructure; machine vision; high-reliability automation components; integrators that productize deployment.

**Bull-case warning.**
Capacity overbuild is likely. Investors should watch book-to-bill ratios and customer concentration rather than extrapolate first-wave shortages forever.

### Bear case: capex contraction, humanoid disappointment, and aggressive commoditization

**Assumptions.**
- Global manufacturing enters a prolonged capex downturn.
- Humanoid pilots fail ROI or safety thresholds.
- AI manipulation improvements plateau in unstructured environments.
- Chinese robot hardware pricing falls faster than unit growth rises.
- Export controls fragment supply chains and reduce addressable markets.
- Customers defer automation because financing costs or demand uncertainty outweigh labor savings.

**Supply-chain consequences.**
Robot OEM orders fall; utilization at component suppliers drops; high fixed-cost precision manufacturers experience margin compression; inventory corrections spread through distributors. Companies valued on humanoid optionality derate sharply. Diversified automation suppliers with strong balance sheets and service revenue fare better.

**Likely relative winners.**
High-cash, high-margin diversified automation leaders; service and maintenance; suppliers with exposure to semiconductor or non-robot end markets that remain healthy.

**Indicators.**
Falling IFR installations; shrinking OEM backlogs; reducer orders below shipments; inventory growth; project cancellations; humanoid fleet removals; rising failure rates; customers declining to expand pilots.

### Scenario monitor table

| Indicator | Base | Bull | Bear |
|---|---|---|---|
| Industrial robot installations | cyclical growth | sustained high growth | multi-year decline |
| China domestic OEM share | rises | rises with export growth | rises mainly via price war |
| Humanoid production | selective | broad scaled production | pilots / niche only |
| Precision reducer lead times | normal | tight | excess capacity |
| Machine vision AI adoption | steady | rapid cross-site scaling | slow upgrades |
| Integrator backlog | healthy | constrained by labor | weak |
| Edge AI compute | gradual | large new endpoint category | niche |
| Robot OEM pricing | pressured | volume offsets price | severe compression |
| Safety/regulatory burden | manageable | standardized | delays deployment |

---

## 10. Risks and thesis breakers

### 10.1 Technical risk

The central technical risk is that manipulation and generalization remain much harder than perception and language. A robot must act in a continuous physical world where errors break objects, stop production, or injure people. If reliability requires extensive task-specific engineering, the market can still grow, but the “general-purpose robot” thesis weakens.

**Thesis breaker:** after several years of pilots, general-purpose systems fail to achieve economically useful uptime and task-transfer performance outside controlled demos.

### 10.2 Economic risk

Automation only works when total lifecycle cost beats labor or alternative machinery. Purchase price is one piece; integration, tooling, downtime, maintenance, supervision, financing, energy, and facility changes matter.

**Thesis breaker:** real customer disclosures show payback periods lengthening despite falling robot prices because integration and maintenance costs rise faster.

### 10.3 Competitive and commoditization risk

China's rising domestic share is already visible in industrial robots. If similar localization occurs in reducers, servos, sensors, and vision, incumbent component margins could fall more than unit growth offsets.

**Thesis breaker:** qualified Chinese precision-motion and perception suppliers win broad global OEM adoption at materially lower prices without sacrificing reliability.

### 10.4 Semiconductor and geopolitical risk

Robotics increasingly depends on compute, while semiconductor supply chains are geopolitically concentrated. U.S. export controls and Chinese industrial policy can split architectures and limit products across borders. BIS rules on advanced computing and semiconductor manufacturing equipment show that policy can change product eligibility and customer access. [BIS](https://www.bis.gov/press-release/commerce-strengthens-export-controls-restrict-chinas-capability-produce-advanced-semiconductors-military)

**Thesis breaker for specific holdings, not robotics overall:** export restrictions remove a material profit pool or force costly redesigns that destroy return on capital.

### 10.5 Taiwan concentration

TSMC's manufacturing leadership creates a powerful economic moat and a geographic tail risk. A cross-Strait crisis would overwhelm ordinary valuation analysis and affect the entire electronics and robotics stack.

This is not a robotics-specific risk; it is a systemic supply-chain risk that becomes more important as robots use more advanced compute.

### 10.6 Regulatory and liability risk

Operating around people creates liability. The EU's treatment of AI safety components illustrates the direction: safety-critical AI can require high-risk controls and conformity processes. [EU AI Act guidance](https://digital-strategy.ec.europa.eu/en/faqs/navigating-ai-act)

**Thesis breaker:** major safety incidents lead to regulations that make broad deployment uneconomic or materially slow adoption.

### 10.7 Cybersecurity risk

Networked robots combine IT, operational technology, sensors, and physical actuation. A compromise can cause production disruption or physical harm. As fleets become centrally orchestrated, software supply-chain security and identity become more important.

Investment implication: cybersecurity and signed-update architecture are costs today but may become differentiators and recurring service opportunities.

### 10.8 Valuation risk

A correct industry thesis can produce a poor investment if purchased at an extreme price. Robotics enthusiasm can capitalize ten years of optimistic growth before revenue exists, especially in suppliers loosely associated with humanoids.

**Discipline:** separate base earnings, cyclical recovery, and robotics optionality. Pay explicitly for each, rather than embedding a vague “AI/robotics premium.”

### 10.9 Supplier-relationship misinformation

The robotics market is unusually vulnerable to rumor. A teardown or photograph can lead to claims that a supplier has a production contract. This report does not treat those as confirmed.

**Thesis breaker for a company-specific idea:** the investment requires a named OEM relationship that management has never confirmed. Such a thesis is not evidence-based.

### 10.10 Capital-intensity and overbuild risk

Precision components and semiconductors can experience shortage-driven capacity additions. If many suppliers extrapolate the same demand curve, shortages can become gluts.

Monitor capital expenditure, utilization, inventory, customer deposits, and cancellations. The most attractive supplier is not necessarily the one adding the most capacity; it is the one earning high returns across the cycle.

### 10.11 Software disintermediation risk

Open-source robotics middleware and models can compress proprietary software economics. Conversely, proprietary platforms can disintermediate smaller tooling vendors.

The durable software moat is likely to be in data, deployment, safety, fleet operations, and integration with customer workflows—not merely an algorithm that can be replicated.

### 10.12 Macro and interest-rate risk

Factory automation is capex. High rates and weak end demand can defer projects even when long-term ROI is positive. Japanese component suppliers with high fixed costs can experience significant earnings volatility.

The long-term thesis should therefore be implemented with cycle awareness rather than assuming straight-line growth.

---

## 11. Ranked conclusions and 12–24 month monitor

### 11.1 Ranked supply-chain segments

**#1 Precision motion and high-reliability joint components.**  
Why: qualification, process know-how, high cost of failure, and increasing content per multi-axis robot. Primary candidates: Nabtesco, Harmonic Drive Systems, selected THK exposure. Risk: Chinese substitution and cyclicality.

**#2 Machine vision, sensing, and deployment tooling.**  
Why: every autonomous machine must perceive and verify. AI expands use cases but still requires industrial-grade hardware, calibration, software, and support. Primary candidates: Cognex and KEYENCE. Risk: commoditized cameras and open models.

**#3 Broad factory-automation components.**  
Why: robots are installed inside automated systems; more robots often mean more valves, actuators, sensors, fixtures, controls, and safety equipment. Primary candidate: SMC, plus THK/KEYENCE overlap. Risk: capex cycle and electric-vs-pneumatic substitution.

**#4 Physical-AI compute, simulation, data, and safety platforms.**  
Why: embodied models create training, synthetic-data, evaluation, edge-inference, and safety workloads. Primary candidate: NVIDIA; TSMC as foundry infrastructure. Risk: robotics remains too small relative to broader AI, and valuation already reflects large expectations.

**#5 Established robot OEMs with ecosystems and service.**  
Why: installed base, application knowledge, and global support are real. Primary candidates: FANUC, Yaskawa, and Teradyne Robotics exposure. Risk: machine commoditization and Chinese price competition.

**#6 Integrators, maintenance, and application-specific software.**  
Why: deployment friction is persistent. The segment can capture value even when hardware prices fall. Public pure-play exposure is less clean, so this is strategically attractive but harder to express through listed equities.

**#7 Batteries and commodity electromechanical parts.**  
Why lower: robotics adds demand, but these markets are dominated by larger end uses and often have weaker robotics-specific pricing power. Attractive niches exist in power density, thermal management, and high-cycle reliability.

### 11.2 Highest-conviction public-market ideas by role

The report's highest-conviction **business exposures**, independent of current entry price, are:

1. **Nabtesco** — direct precision-reducer scarcity and qualification.
2. **SMC** — broad factory-automation picks-and-shovels with large installed-market presence.
3. **KEYENCE** — high-value sensing/inspection plus application engineering.
4. **Cognex** — focused machine-vision beneficiary of AI-enabled automation.
5. **Yaskawa** — integrated servo/motion/robot exposure.
6. **FANUC** — industrial robot installed-base and service quality.
7. **THK** — broad precision motion across automation.
8. **Teradyne** — direct cobot/AMR option within a diversified test-equipment company.
9. **Harmonic Drive Systems** — high-beta precision gearing with upside if multi-axis robotics scales.
10. **NVIDIA** — physical-AI platform option, but only as part of a much larger AI investment case.
11. **TSMC** — foundry infrastructure option, with robotics a small incremental driver.

The ranking is about strategic exposure, not expected 12-month stock return. A high-quality company can be a poor purchase at an excessive valuation.

### 11.3 Developments to monitor over the next 12–24 months

**Industrial robot installations.** IFR's next releases will show whether the >500,000 annual-installation regime persists and how China, Europe, and the Americas diverge.

**China supplier share outside China.** Domestic share within China has already risen. The next key question is export competitiveness and qualification at multinational customers.

**Precision reducer orders and margins.** Nabtesco and Harmonic Drive disclosures can reveal whether robotics demand translates into sustained utilization or a temporary order spike.

**Humanoid production evidence.** Ignore demo counts. Track paid deployments, repeat orders, fleet size, uptime, task throughput, warranty/service cost, and whether customers expand after pilots.

**Safety and regulation.** Watch EU high-risk AI implementation for machinery, emerging humanoid standards in China, and industrial safety certification practices elsewhere.

**Physical-AI software monetization.** NVIDIA's named physical-AI programs are strategically relevant. The investment question is whether developers move from tool adoption to production-scale compute and software revenue.

**Machine-vision AI deployment.** Cognex's OneVision and embedded AI products offer a measurable test of whether AI creates incremental inspection demand and multi-site standardization.

**Teradyne Robotics profitability.** Revenue growth without acceptable returns would weaken the OEM thesis. Improvement would strengthen the case that cobots and AMRs can be durable profit pools.

**Japan policy implementation.** METI's 2026 robotics strategy emphasizes real-world deployment, data, and foundation models. Monitor budget execution, field deployments, and private investment rather than only policy targets.

**Semiconductor controls.** BIS policy has changed repeatedly. Any new restriction or relaxation can alter compute availability, China sales, and localization incentives.

**ABB Robotics transaction.** Closing of the SoftBank acquisition will provide an observable valuation and strategic signal for a scaled robotics asset.

### Final conclusion

The robotics supply chain is investable today without assuming science-fiction adoption curves. The strongest evidence is in installed industrial robots, logistics automation, machine vision, precision motion, and factory-automation components. Physical AI can expand the opportunity, but the portfolio should not require humanoids to reach millions of units on an arbitrary schedule.

The most defensible long-run economic rents are likely to accrue where three conditions overlap: **technical difficulty, qualification friction, and cross-platform demand**. Precision reducers and motion components fit that pattern. Industrial vision and sensing can fit it when software and application support prevent commoditization. Compute and simulation platforms fit it if they become standards across robot OEMs. Integrators and maintenance fit it because the physical world resists frictionless deployment.

Complete robot OEMs remain important, but their economics are more exposed to hardware competition. China's scale makes that risk unavoidable. As unit prices fall, investors should ask who gains from more axes, more sensors, more compute, more safety validation, and more installed machines—not merely who has the most compelling robot video.

The central thesis would be invalidated if broad automation spending stagnates for years, qualified precision components commoditize rapidly, AI fails to improve deployment economics, and customers do not expand real robot fleets after pilots. Until that evidence appears, the highest-quality supply-chain franchises offer a more grounded way to participate in robotics than attempting to predict the single winning humanoid platform.

---

## 12. Full source list

### Industry and adoption
1. International Federation of Robotics, **World Robotics 2025 – Industrial Robots**: https://ifr.org/worldrobotics/report-2025  
2. International Federation of Robotics, **Service Robots See Global Growth Boom**: https://ifr.org/news/service-robots-see-global-growth-boom/1st-  
3. International Federation of Robotics, **World Robotics – Service Robots**: https://ifr.org/wr-service-robots  

### Compute, semiconductors, and physical AI
4. NVIDIA Investor Relations, **Annual Reports and Proxies**: https://investor.nvidia.com/financial-info/annual-reports-and-proxies/default.aspx  
5. NVIDIA, **Open Physical AI Data Factory Blueprint**, 16 March 2026: https://investor.nvidia.com/news/press-release-details/2026/NVIDIA-Announces-Open-Physical-AI-Data-Factory-Blueprint-to-Accelerate-Robotics-Vision-AI-Agents-and-Autonomous-Vehicle-Development/default.aspx  
6. NVIDIA, **Halos for Robotics**, 22 June 2026: https://investor.nvidia.com/news/press-release-details/2026/NVIDIA-Announces-Halos-for-Robotics-the-Industrys-First-Full-Stack-Safety-System-for-Physical-AI/default.aspx  
7. TSMC Investor Relations: https://investor.tsmc.com/english  
8. TSMC, **Q2 2026 Results**: https://investor.tsmc.com/english/quarterly-results/2026/q2  

### Robot OEMs and motion
9. FANUC, **IR Announcements / Results**: https://www.fanuc.co.jp/en/ir/announce/  
10. Yaskawa Electric, **Investor Relations**: https://www.yaskawa-global.com/ir  
11. Yaskawa Electric, **IR News**: https://www.yaskawa-global.com/ir/news  
12. Teradyne, **2025 Form 10-K filed 19 February 2026**: https://investors.teradyne.com/sec-filings/all-sec-filings/content/0001193125-26-059002/ter-20251231.htm  
13. Teradyne, **Annual Reports**: https://investors.teradyne.com/sec-filings/annual-reports  
14. Nabtesco, **Precision Reduction Gears / Robot Products**: https://www.nabtesco.com/en/products/robot/  
15. Nabtesco, **Investor Relations**: https://www.nabtesco.com/en/about/ir/  
16. Harmonic Drive Systems, **Investor Relations**: https://www.hds.co.jp/english/ir/  
17. Harmonic Drive Systems, **Orders and Sales**: https://www.hds.co.jp/english/ir/achievements/accounts/  
18. Harmonic Drive Systems, **Forecast**: https://www.hds.co.jp/english/ir/achievements/forecast/  
19. Harmonic Drive Systems, **IR FAQ**: https://www.hds.co.jp/english/ir/faq/  
20. THK, **Investor Relations**: https://www.thk.com/jp/en/ir/  
21. THK, **Financial Statements Related Data**: https://www.thk.com/jp/en/ir/library/results/  

### Sensors, vision, and automation
22. Cognex, **2025 Form 10-K filed 12 February 2026**: https://investor.cognex.com/financial-reports/sec-filings/sec-filings-details/default.aspx?FilingId=19139108  
23. Cognex, **Investor Relations**: https://investor.cognex.com/home/default.aspx  
24. Cognex, **OneVision General Availability**, 13 May 2026: https://investor.cognex.com/news/news-details/2026/Cognex-OneVision-Adoption-Ramps-as-Manufacturers-Scale-AI-Vision-Globally/default.aspx  
25. Cognex, **In-Sight 3900 Embedded AI Vision**, 5 May 2026: https://investor.cognex.com/news/news-details/2026/Cognex-Launches-Highest-Performance-Embedded-AI-Vision-System-Powered-by-Qualcomm/default.aspx  
26. KEYENCE, **Investor Relations**: https://www.keyence.co.jp/investor/en/  
27. KEYENCE, **Annual Reports**: https://www.keyence.co.jp/investor/library/annualreport.jsp  
28. KEYENCE, **IR News / FY2026 materials**: https://www.keyence.co.jp/investor/en/news.jsp  
29. KEYENCE, **Corporate Overview / Automation and Inspection Portfolio**: https://www.keyence.com/about-us/corporate/  
30. SMC, **Investor Relations**: https://www.smcworld.com/ir/en-jp/  
31. SMC, **Company overview for investors**: https://www.smcworld.com/ir/ja-jp/investor.html  
32. SMC, **Financial Indicators**: https://www.smcworld.com/ir/en-jp/indicato.html  

### Policy, regulation, and geopolitics
33. Japan METI, **Robotics Policy / 2026 AI Robotics Strategy**: https://www.meti.go.jp/policy/mono_info_service/mono/robot/index.html  
34. Japan METI, **AI Robotics Strategy press conference**, 30 June 2026: https://www.meti.go.jp/english/speeches/press_conferences/2026/0630001.html  
35. Japan METI / NEDO, **GENIAC robotics foundation-model and AI-ready data R&D**, 14 May 2026: https://www.meti.go.jp/english/press/2026/0514_001.html  
36. China MIIT / SASAC, **2026 humanoid robot and embodied intelligence real-world training action**: https://www.miit.gov.cn/zwgk/zcwj/wjfb/tz/art/2026/art_f291ccd3da4c47ce95741de63cc088e6.html  
37. China MIIT, **2025 industrial and information development review / humanoid priorities**: https://www.miit.gov.cn/xwfb/bldhd/art/2026/art_4ba01aa6d2f54ced8ba3490ea4fb52c4.html  
38. U.S. Bureau of Industry and Security, **Advanced semiconductor and manufacturing-equipment controls**, 2 December 2024: https://www.bis.gov/press-release/commerce-strengthens-export-controls-restrict-chinas-capability-produce-advanced-semiconductors-military  
39. U.S. Bureau of Industry and Security, **Advanced-computing foundry due diligence**, 15 January 2025: https://www.bis.gov/press-release/commerce-strengthens-restrictions-advanced-computing-semiconductors-enhance-foundry-due-diligence-prevent  
40. U.S. Bureau of Industry and Security, **License review policy for certain semiconductors exported to China**, 13 January 2026: https://media.bis.gov/press-release/department-commerce-revises-license-review-policy-semiconductors-exported-china  
41. European Commission, **Navigating the AI Act**: https://digital-strategy.ec.europa.eu/en/faqs/navigating-ai-act  
42. European Commission, **Draft guidelines on high-risk AI classification**, 19 May 2026: https://digital-strategy.ec.europa.eu/en/library/draft-commission-guidelines-classification-high-risk-ai-systems  
43. EU AI Act Service Desk, **High-risk AI in regulated products**: https://ai-act-service-desk.ec.europa.eu/en/high-risk-ai-regulated-products  

### Corporate transaction / comparative economics
44. ABB, **Robotics division sale to SoftBank**, 8 October 2025: https://new.abb.com/news/detail/129776/abb-to-divest-robotics-division-to-softbank-group  
45. ABB, **Annual-report Chairman's Letter**: https://www.abb.com/global/en/company/annual-reporting-suite/chairmans-letter  
46. ABB, **CEO Q&A / Robotics transaction**: https://www.abb.com/global/en/company/annual-reporting-suite/ceo-qa  

---

## Methodology and limitations summary

This report prioritizes primary sources and does not infer undisclosed supplier relationships. Company market-share figures explicitly identified as company estimates are labeled as such. IFR industrial-robot data is treated as an industry adoption benchmark; IFR service-robot figures are treated as sample-based directional evidence. Government deployment targets are policy objectives, not market forecasts. Private-company mentions are watch-list items rather than investable recommendations.

No third-party long-range humanoid TAM is used as the base of the valuation thesis. Where exact live equity valuation is required, investors should update share prices, enterprise values, consensus estimates, and foreign-exchange rates immediately before acting. The report's role is to identify where economic value is structurally likely to accrue and what evidence would confirm or falsify that view.
