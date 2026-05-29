# OctoPi

Verify your Octopus Energy billing by comparing smart meter consumption data against tariff rates.

## What it does

- Pulls half-hourly consumption data from the Octopus Energy API (electricity + gas)
- Calculates what you *should* have been charged based on your tariff
- Detects data quality issues: gaps, zero-consumption periods, spikes, low coverage
- Flags periods where your meter may not have been reporting correctly
- Interactive Streamlit dashboard with charts and breakdowns

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

## Running without Docker

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Notes

- Meter serial numbers and MPANs are auto-discovered from your account via the API
- Data is fetched in half-hourly intervals; the API typically has a 24-48h lag
- Use `docker compose` (v2 plugin), not the legacy `docker-compose` (Python-based) which has known event-stream bugs
