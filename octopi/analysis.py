"""Consumption analysis and anomaly detection."""

import pandas as pd
import numpy as np
from datetime import datetime


def consumption_to_dataframe(consumption_data: list[dict]) -> pd.DataFrame:
    if not consumption_data:
        return pd.DataFrame(columns=["consumption", "interval_start", "interval_end"])
    df = pd.DataFrame(consumption_data)
    df["interval_start"] = pd.to_datetime(df["interval_start"], utc=True)
    df["interval_end"] = pd.to_datetime(df["interval_end"], utc=True)
    df = df.sort_values("interval_start").reset_index(drop=True)
    return df


def calculate_monthly_cost(
    consumption_df: pd.DataFrame,
    unit_rates: list[dict],
    standing_charges: list[dict],
    fuel: str,
) -> pd.DataFrame:
    """Calculate expected cost per month from consumption × tariff rates."""
    if consumption_df.empty:
        return pd.DataFrame()

    rates_df = pd.DataFrame(unit_rates)
    rates_df["valid_from"] = pd.to_datetime(rates_df["valid_from"], utc=True)
    rates_df["valid_to"] = pd.to_datetime(rates_df["valid_to"], utc=True, errors="coerce")
    rates_df = rates_df.sort_values("valid_from")

    sc_df = pd.DataFrame(standing_charges)
    sc_df["valid_from"] = pd.to_datetime(sc_df["valid_from"], utc=True)
    sc_df["valid_to"] = pd.to_datetime(sc_df["valid_to"], utc=True, errors="coerce")
    sc_df = sc_df.sort_values("valid_from")

    df = consumption_df.copy()

    # For gas, consumption is in cubic meters from API — convert to kWh
    # Octopus uses: volume × calorific_value × correction_factor / 3.6
    # Standard: CV=39.5, correction=1.02264
    if fuel == "gas":
        df["consumption_kwh"] = df["consumption"] * 39.5 * 1.02264 / 3.6
    else:
        df["consumption_kwh"] = df["consumption"]

    # Match each interval to its unit rate
    df["rate_p_per_kwh"] = np.nan
    for _, rate in rates_df.iterrows():
        valid_to = rate["valid_to"] if pd.notna(rate["valid_to"]) else pd.Timestamp.max.tz_localize("UTC")
        mask = (df["interval_start"] >= rate["valid_from"]) & (df["interval_start"] < valid_to)
        df.loc[mask, "rate_p_per_kwh"] = rate["value_inc_vat"]

    # Forward-fill any gaps
    df["rate_p_per_kwh"] = df["rate_p_per_kwh"].ffill()

    df["cost_pence"] = df["consumption_kwh"] * df["rate_p_per_kwh"]
    df["month"] = df["interval_start"].dt.tz_localize(None).dt.to_period("M")

    monthly = df.groupby("month").agg(
        consumption_kwh=("consumption_kwh", "sum"),
        unit_cost_pence=("cost_pence", "sum"),
        readings_count=("consumption", "count"),
    ).reset_index()

    # Add standing charges per month
    monthly["standing_charge_pence"] = 0.0
    for _, sc in sc_df.iterrows():
        valid_to = sc["valid_to"] if pd.notna(sc["valid_to"]) else pd.Timestamp.max.tz_localize("UTC")
        for idx, row in monthly.iterrows():
            month_start = row["month"].start_time.tz_localize("UTC")
            month_end = row["month"].end_time.tz_localize("UTC")
            if month_start >= sc["valid_from"] and month_start < valid_to:
                days_in_month = (month_end - month_start).days + 1
                monthly.loc[idx, "standing_charge_pence"] = sc["value_inc_vat"] * days_in_month

    monthly["total_cost_pence"] = monthly["unit_cost_pence"] + monthly["standing_charge_pence"]
    monthly["total_cost_pounds"] = monthly["total_cost_pence"] / 100
    monthly["month_str"] = monthly["month"].astype(str)

    return monthly


def detect_anomalies(consumption_df: pd.DataFrame) -> dict:
    """Detect data quality issues and anomalies."""
    if consumption_df.empty:
        return {"issues": [], "summary": "No data available"}

    issues = []
    df = consumption_df.copy()

    # Check for significant gaps in data (> 1 day)
    expected_interval = pd.Timedelta(minutes=30)
    time_diffs = df["interval_start"].diff()
    gaps = df[time_diffs > pd.Timedelta(days=1)]
    if not gaps.empty:
        for _, gap in gaps.iterrows():
            prev_idx = df.index[df.index.get_loc(gap.name) - 1]
            prev_end = df.loc[prev_idx, "interval_end"]
            gap_duration = gap["interval_start"] - prev_end
            issues.append({
                "type": "gap",
                "severity": "high" if gap_duration > pd.Timedelta(days=7) else "medium",
                "start": prev_end,
                "end": gap["interval_start"],
                "duration": gap_duration,
                "description": f"Missing data: {gap_duration.days} day gap ({prev_end:%Y-%m-%d} to {gap['interval_start']:%Y-%m-%d})",
            })

    # Check for zero-consumption periods (meter not reporting)
    df["date"] = df["interval_start"].dt.date
    daily = df.groupby("date")["consumption"].sum()
    zero_days = daily[daily == 0]
    if not zero_days.empty:
        issues.append({
            "type": "zero_consumption",
            "severity": "high",
            "count": len(zero_days),
            "dates": zero_days.index.tolist(),
            "description": f"{len(zero_days)} days with zero consumption (meter may not have been reporting)",
        })

    # Check for sudden spikes (> 3 standard deviations from rolling mean)
    if len(daily) > 7:
        rolling_mean = daily.rolling(7, min_periods=3).mean()
        rolling_std = daily.rolling(7, min_periods=3).std()
        spikes = daily[(daily - rolling_mean) > 3 * rolling_std]
        if not spikes.empty:
            issues.append({
                "type": "spike",
                "severity": "medium",
                "count": len(spikes),
                "dates": spikes.index.tolist(),
                "values": spikes.values.tolist(),
                "description": f"{len(spikes)} days with abnormally high consumption (>3σ from 7-day mean)",
            })

    # Check data coverage
    if len(df) > 0:
        first_reading = df["interval_start"].min()
        last_reading = df["interval_start"].max()
        total_span = (last_reading - first_reading).total_seconds() / 1800  # in 30-min slots
        coverage = len(df) / total_span * 100 if total_span > 0 else 0
        if coverage < 95:
            issues.append({
                "type": "low_coverage",
                "severity": "high" if coverage < 80 else "medium",
                "coverage_pct": round(coverage, 1),
                "expected_readings": int(total_span),
                "actual_readings": len(df),
                "description": f"Only {coverage:.1f}% data coverage ({len(df)}/{int(total_span)} expected readings)",
            })

    summary = f"Found {len(issues)} issue(s)" if issues else "No anomalies detected"
    return {"issues": issues, "summary": summary}
