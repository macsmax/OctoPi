# maxihome-octopi

Verify your Octopus Energy billing by comparing smart meter consumption data against tariff rates. Part of the [maxihome](https://github.com/macsmax/maxihome-hub) ecosystem.

## What it does

- Pulls half-hourly consumption data from the Octopus Energy API (electricity + gas)
- Calculates what you *should* have been charged based on your tariff
- Compares actual payments vs billed amounts with credit/debit tracking
- Detects data quality issues: gaps, zero-consumption periods, spikes, low coverage
- Flags periods where your meter may not have been reporting correctly
- **EV Charging** (optional): detects home charging from off-peak consumption patterns, shows Electroverse public charging costs

## Setup

1. Get your API key from: https://octopus.energy/dashboard/new/accounts/personal-details/api-access

2. Create `.env`:
```bash
cp .env.example .env
# Edit .env with your API key and account number
```

3. Run with Docker:
```bash
docker compose up -d
```

4. Open http://localhost:8501

## EV Charging

If you have an electric car on Intelligent Octopus, enable the EV tab:

```bash
EV_CHARGING=true
EV_CHARGER_KW=3.68   # Your charger's draw rate (16A × 230V = 3.68kW)
```

This adds:
- Home charging detection from off-peak high-draw slots (23:30–05:30)
- Monthly kWh and cost at the Intelligent Octopus off-peak rate
- Electroverse (public charging) session history and spend
- Total cost split with cost-per-mile estimate

## Running without Docker

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Notes

- Meter details are auto-discovered from your account via the API
- Data is fetched in half-hourly intervals; the API typically has a 24-48h lag
- Use `docker compose` (v2 plugin), not the legacy `docker-compose` (Python-based)
