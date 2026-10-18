# Around the world (Twist 2)

The knock is physics. It works anywhere a battery can be commanded and a transformer can be metered. What changes from place to place is the local blunt move, the kind of crisis, the command protocol, the language and who owns the batteries. Each preset in `backend/knock/regions.py` changes the simulation itself.

## Johannesburg, South Africa
- **The blunt move:** "load reduction". Eskom cuts whole suburbs when the local transformer is overloaded, in blocks of 05:00–09:00 and 17:00–22:00. This is exactly what KNOCK replaces.
- **Scale:** Eskom's exit program targets 971 feeders and about 1.69 million customers; more than 1 million had been removed by July 2026. National load shedding has been suspended since May 2025.
- **Simulated:** a winter evening peak, an announced 2-hour cut on two streets (KNOCK pre-charges and goes essentials-only), and the blunt operator cutting overloaded streets for 2 hours.

## Adelaide, Australia
- **The fleet:** about 400,000 home batteries (11.2 GWh) were installed under the Cheaper Home Batteries program, but only about 10% of owners are in a virtual power plant.
- **The blunt move:** South Australia's emergency backstop lets SA Power Networks remotely curtail about 100,000 rooftop solar systems. In November 2022, a storm cut the state off from the rest of the national grid.
- **Simulated:** a 45 °C heatwave, the state islanded with a frequency dip that trips inverters on three streets, and a VPP cloud outage. Commands go over CSIP-AUS.

## Chiba, Japan
- **The precedent:** Typhoon Faxai (2019) toppled transmission towers and almost 2,000 poles, and more than 900,000 customers lost power. Japan has since encouraged home batteries paired with solar as backup networks.
- **Simulated:** typhoon winds silence half the fleet, a street loses grid power without warning, and inverters trip. Commands go over ECHONET Lite.

## Zhejiang, China
- **The context:** China issued its first national VPP standard (GB/T 47241-2026) and targets 50 GW of VPP regulation capacity by 2030.
- **A second fleet type:** China Tower's Zhejiang branch uses base-station backup batteries for peak shaving. The same knock-and-diagnose engine fits that fleet.
- **Simulated:** a summer peak, an operator network fault, and "orderly power-use" rationing as the blunt move.

## Sources
- [Eskom load reduction in Gauteng, Sept 2026 (EnergyBee)](https://energybee.co.za/news/eskom-load-reduction-schedule-gauteng-september-2026)
- [400,000 home batteries installed but barely a VPP in sight (Energy Magazine, May 2026)](https://www.energymagazine.com.au/400000-home-batteries-installed-but-barely-a-vpp-in-sight/)
- [SA Power Networks to test emergency solar shutdown capability (PV Tech, Aug 2026)](https://www.pv-tech.org/sa-power-networks-to-test-emergency-solar-shutdown-capability-across-south-australia/)
- [As extreme storms wreck power grids, Japan seeks home battery networks (T&D World / Bloomberg)](https://www.tdworld.com/energy-storage/extreme-storms-wreck-power-grids-japan-seeks-home-battery-networks)
- [China issues first national standard for virtual power plants (SXCoal)](https://en.sxcoal.com/news/detail/2095424253918203906)
- [Case study: China Tower and Huawei (GSMA)](https://www.gsma.com/solutions-and-impact/technologies/networks/gsma_resources/case-study-china-tower-huawei/)
