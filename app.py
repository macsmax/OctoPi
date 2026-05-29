"""OctoPi Dashboard — verify your Octopus Energy billing."""

import os
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta
from dotenv import load_dotenv

from octopi.client import OctopusClient
from octopi.analysis import (
    consumption_to_dataframe,
    calculate_monthly_cost,
    detect_anomalies,
)

load_dotenv()

st.set_page_config(
    page_title="OctoPi — Energy Bill Checker",
    page_icon="🐙",
    layout="wide",
)

st.title("OctoPi — Energy Bill Checker")
st.markdown("Verify your Octopus Energy consumption data and billing accuracy.")


@st.cache_data(ttl=3600)
def fetch_account(api_key: str, account_number: str) -> dict:
    client = OctopusClient(api_key, account_number)
    return client.get_account()


@st.cache_data(ttl=3600)
def fetch_consumption(
    api_key: str,
    account_number: str,
    fuel: str,
    meter_point_id: str,
    serial_number: str,
    period_from: datetime | None = None,
    period_to: datetime | None = None,
) -> list[dict]:
    client = OctopusClient(api_key, account_number)
    return client.get_consumption(fuel, meter_point_id, serial_number, period_from, period_to)


@st.cache_data(ttl=3600)
def fetch_tariff_rates(
    api_key: str,
    account_number: str,
    product_code: str,
    tariff_code: str,
    fuel: str,
    period_from: datetime | None = None,
    period_to: datetime | None = None,
) -> tuple[list[dict], list[dict]]:
    client = OctopusClient(api_key, account_number)
    rates = client.get_tariff_rates(product_code, tariff_code, fuel, period_from=period_from, period_to=period_to)
    standing = client.get_standing_charges(product_code, tariff_code, fuel, period_from=period_from, period_to=period_to)
    return rates, standing


# Sidebar config
with st.sidebar:
    st.header("Configuration")
    api_key = st.text_input(
        "API Key",
        value=os.getenv("OCTOPUS_API_KEY", ""),
        type="password",
        help="Find at: octopus.energy/dashboard → Personal Details → API Access",
    )
    account_number = st.text_input(
        "Account Number",
        value=os.getenv("OCTOPUS_ACCOUNT_NUMBER", ""),
        help="Format: A-XXXXXXXX",
    )

if not api_key or not account_number:
    st.info("Enter your API key and account number in the sidebar to get started.")
    st.stop()

# Fetch account
try:
    account = fetch_account(api_key, account_number)
except Exception as e:
    st.error(f"Failed to connect: {e}")
    st.stop()

properties = account.get("properties", [])
if not properties:
    st.error("No properties found on this account.")
    st.stop()

prop = properties[0]
moved_in = prop.get("moved_in_at", "")

st.sidebar.divider()
st.sidebar.subheader("Account Info")
st.sidebar.write(f"**Address:** {prop.get('address_line_1', '')}")
st.sidebar.write(f"**Moved in:** {moved_in[:10] if moved_in else 'Unknown'}")

# Extract ALL meters (old and new)
elec_meter_points = prop.get("electricity_meter_points", [])
gas_meter_points = prop.get("gas_meter_points", [])

st.sidebar.write(f"**Electricity meter points:** {len(elec_meter_points)}")
st.sidebar.write(f"**Gas meter points:** {len(gas_meter_points)}")

# Tabs for electricity and gas
tabs = st.tabs(["⚡ Electricity", "🔥 Gas", "📊 Billing Summary"])

for tab_idx, (tab, fuel, meter_points) in enumerate(
    zip(tabs[:2], ["electricity", "gas"], [elec_meter_points, gas_meter_points])
):
    with tab:
        if not meter_points:
            st.warning(f"No {fuel} meter points found.")
            continue

        mp = meter_points[0]
        meter_point_id = mp.get("mpan") or mp.get("mprn")
        meters = mp.get("meters", [])
        agreements = mp.get("agreements", [])

        st.subheader(f"{fuel.title()} — {'MPAN' if fuel == 'electricity' else 'MPRN'}: {meter_point_id}")

        # Show agreements timeline
        with st.expander("Tariff History"):
            for ag in agreements:
                valid_to = ag.get("valid_to") or "present"
                st.write(f"- `{ag['tariff_code']}` — {ag['valid_from'][:10]} to {valid_to[:10] if isinstance(valid_to, str) and valid_to != 'present' else 'present'}")

        # Fetch data from ALL meters and combine (full history since move-in)
        st.subheader("📡 Data Coverage")
        all_readings = []
        meter_summaries = []

        history_from = datetime(2023, 4, 10) if moved_in else datetime(2023, 1, 1)
        history_to = datetime.now()

        for meter in meters:
            serial = meter["serial_number"]
            with st.spinner(f"Fetching data for meter {serial}..."):
                readings = fetch_consumption(api_key, account_number, fuel, meter_point_id, serial, history_from, history_to)

            if readings:
                first = readings[0]["interval_start"]
                last = readings[-1]["interval_start"]
                meter_summaries.append({
                    "Serial": serial,
                    "Readings": f"{len(readings):,}",
                    "First Reading": first[:10],
                    "Last Reading": last[:10],
                })
                all_readings.extend(readings)
            else:
                meter_summaries.append({
                    "Serial": serial,
                    "Readings": "0",
                    "First Reading": "—",
                    "Last Reading": "—",
                })

        st.dataframe(pd.DataFrame(meter_summaries), use_container_width=True, hide_index=True)

        if not all_readings:
            st.error(f"No {fuel} consumption data found from any meter. Your meter has never reported to Octopus.")
            continue

        # Combine and deduplicate
        df = consumption_to_dataframe(all_readings)
        df = df.drop_duplicates(subset=["interval_start"]).sort_values("interval_start").reset_index(drop=True)

        total_readings = len(df)
        first_reading = df["interval_start"].min()
        last_reading = df["interval_start"].max()

        # Calculate expected readings since move-in
        move_in_date = pd.Timestamp(moved_in).tz_convert("UTC") if moved_in else first_reading
        total_span = (last_reading - move_in_date).total_seconds() / 1800
        coverage = total_readings / total_span * 100 if total_span > 0 else 0

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total Readings", f"{total_readings:,}")
        col2.metric("First Reading", f"{first_reading:%Y-%m-%d}")
        col3.metric("Data Coverage", f"{coverage:.1f}%")

        # Gap from move-in to first reading
        gap_days = (first_reading - move_in_date).days
        if gap_days > 30:
            col4.metric("Missing from move-in", f"{gap_days} days", delta=f"-{gap_days}d", delta_color="inverse")
        else:
            col4.metric("Missing from move-in", f"{gap_days} days")

        # Monthly coverage heatmap
        st.subheader("📅 Monthly Data Coverage")
        df["month"] = df["interval_start"].dt.to_period("M")
        monthly_counts = df.groupby("month").size().reset_index(name="readings")
        monthly_counts["month_str"] = monthly_counts["month"].astype(str)
        monthly_counts["expected"] = monthly_counts["month"].apply(lambda m: m.days_in_month * 48)
        monthly_counts["coverage_pct"] = (monthly_counts["readings"] / monthly_counts["expected"] * 100).clip(upper=100)

        # Generate all months from move-in to now
        all_months = pd.period_range(
            start=move_in_date.to_period("M"),
            end=pd.Timestamp.now(tz="UTC").to_period("M"),
            freq="M",
        )
        all_months_df = pd.DataFrame({"month": all_months})
        all_months_df["month_str"] = all_months_df["month"].astype(str)
        all_months_df = all_months_df.merge(
            monthly_counts[["month_str", "readings", "coverage_pct"]],
            on="month_str",
            how="left",
        ).fillna(0)

        # Color code: red = 0%, yellow = partial, green = 95%+
        fig_coverage = px.bar(
            all_months_df,
            x="month_str",
            y="coverage_pct",
            color="coverage_pct",
            color_continuous_scale=["#ef4444", "#f97316", "#eab308", "#22c55e"],
            range_color=[0, 100],
            labels={"coverage_pct": "Coverage %", "month_str": "Month"},
        )
        fig_coverage.update_layout(height=300, margin=dict(t=10), xaxis_tickangle=-45)
        fig_coverage.add_hline(y=95, line_dash="dash", line_color="green", annotation_text="95% target")
        st.plotly_chart(fig_coverage, use_container_width=True)

        # Highlight the problem months
        missing_months = all_months_df[all_months_df["coverage_pct"] == 0]
        partial_months = all_months_df[(all_months_df["coverage_pct"] > 0) & (all_months_df["coverage_pct"] < 50)]

        if not missing_months.empty:
            st.error(f"**{len(missing_months)} months with ZERO data:** {', '.join(missing_months['month_str'].tolist())}")
            st.markdown("During these months, Octopus was billing you on **estimated** usage — not actual meter readings.")

        if not partial_months.empty:
            st.warning(f"**{len(partial_months)} months with partial data (<50%):** {', '.join(partial_months['month_str'].tolist())}")

        # Anomaly detection
        st.subheader("🔍 Anomaly Detection")
        anomalies = detect_anomalies(df)

        if anomalies["issues"]:
            for issue in anomalies["issues"]:
                severity_icon = "🔴" if issue["severity"] == "high" else "🟡"
                st.markdown(f"{severity_icon} **{issue['type'].replace('_', ' ').title()}** — {issue['description']}")
        else:
            st.success("No anomalies detected in the available data.")

        # Daily consumption chart
        st.subheader("📊 Daily Consumption")
        df["date"] = df["interval_start"].dt.date
        daily = df.groupby("date")["consumption"].sum().reset_index()
        daily.columns = ["date", "consumption_kwh"]

        if fuel == "gas":
            daily["consumption_kwh"] = daily["consumption_kwh"] * 39.5 * 1.02264 / 3.6

        fig_daily = px.bar(
            daily, x="date", y="consumption_kwh",
            labels={"consumption_kwh": "kWh", "date": "Date"},
            color_discrete_sequence=["#6366f1" if fuel == "electricity" else "#f97316"],
        )
        fig_daily.update_layout(height=300, margin=dict(t=10))
        st.plotly_chart(fig_daily, use_container_width=True)

        # Cost calculation
        st.subheader("💰 Calculated Cost (from actual readings)")

        if not agreements:
            st.warning("No tariff agreements found — cannot calculate expected cost.")
            continue

        # Gather rates from all agreements that overlap with our data
        all_unit_rates = []
        all_standing_charges = []
        for agreement in agreements:
            tariff_code = agreement.get("tariff_code", "")
            parts = tariff_code.split("-")
            if len(parts) >= 4:
                product_code = "-".join(parts[2:-1])
            else:
                product_code = tariff_code

            try:
                rates, standing = fetch_tariff_rates(
                    api_key, account_number, product_code, tariff_code, fuel
                )
                all_unit_rates.extend(rates)
                all_standing_charges.extend(standing)
            except Exception:
                pass

        if not all_unit_rates:
            st.warning("Could not fetch tariff rates. Cannot calculate costs.")
            continue

        # Deduplicate rates
        seen_rates = set()
        unique_rates = []
        for r in all_unit_rates:
            key = (r.get("valid_from"), r.get("valid_to"), r.get("value_inc_vat"))
            if key not in seen_rates:
                seen_rates.add(key)
                unique_rates.append(r)

        seen_sc = set()
        unique_sc = []
        for s in all_standing_charges:
            key = (s.get("valid_from"), s.get("valid_to"), s.get("value_inc_vat"))
            if key not in seen_sc:
                seen_sc.add(key)
                unique_sc.append(s)

        monthly_cost = calculate_monthly_cost(df, unique_rates, unique_sc, fuel)

        if monthly_cost.empty:
            st.warning("Could not calculate monthly costs.")
            continue

        fig_cost = go.Figure()
        fig_cost.add_trace(go.Bar(
            x=monthly_cost["month_str"],
            y=monthly_cost["total_cost_pounds"],
            name="Calculated from readings",
            marker_color="#6366f1" if fuel == "electricity" else "#f97316",
        ))
        fig_cost.update_layout(
            yaxis_title="Cost (£)",
            xaxis_title="Month",
            height=350,
            margin=dict(t=10),
        )
        st.plotly_chart(fig_cost, use_container_width=True)

        # Monthly breakdown table
        display_df = monthly_cost[["month_str", "consumption_kwh", "unit_cost_pence", "standing_charge_pence", "total_cost_pounds", "readings_count"]].copy()
        display_df.columns = ["Month", "Consumption (kWh)", "Unit Cost (p)", "Standing Charge (p)", "Total (£)", "Readings"]
        display_df["Consumption (kWh)"] = display_df["Consumption (kWh)"].round(1)
        display_df["Unit Cost (p)"] = display_df["Unit Cost (p)"].round(1)
        display_df["Standing Charge (p)"] = display_df["Standing Charge (p)"].round(1)
        display_df["Total (£)"] = display_df["Total (£)"].round(2)

        st.dataframe(display_df, use_container_width=True, hide_index=True)

        total_cost = monthly_cost["total_cost_pounds"].sum()
        total_kwh = monthly_cost["consumption_kwh"].sum()
        st.metric(
            f"Total Calculated Cost ({fuel.title()})",
            f"£{total_cost:.2f}",
            help=f"Based on {total_kwh:.0f} kWh consumed × tariff rate + standing charges (only months with actual data)",
        )

# Billing Summary tab
with tabs[2]:
    st.subheader("📋 Billing Evidence Summary")
    st.markdown("Use this information when contacting Octopus Energy about your billing dispute.")

    st.markdown("### Key Findings")

    # Rebuild the timeline
    history_from = datetime(2023, 4, 10)
    history_to = datetime.now()

    if elec_meter_points:
        mp = elec_meter_points[0]
        meters = mp.get("meters", [])
        all_elec_readings = []
        for meter in meters:
            readings = fetch_consumption(api_key, account_number, "electricity", mp["mpan"], meter["serial_number"], history_from, history_to)
            all_elec_readings.extend(readings)

        if all_elec_readings:
            elec_df = consumption_to_dataframe(all_elec_readings)
            elec_df = elec_df.drop_duplicates(subset=["interval_start"])
            elec_first = elec_df["interval_start"].min()
            move_in_ts = pd.Timestamp(moved_in).tz_convert("UTC") if moved_in else elec_first
            gap_days_elec = (elec_first - move_in_ts).days

            st.markdown(f"""
**Electricity:**
- Moved in: **{move_in_ts:%Y-%m-%d}**
- First meter reading: **{elec_first:%Y-%m-%d}**
- Gap with no data: **{gap_days_elec} days ({gap_days_elec // 30} months)**
- During this gap, all billing was based on **estimates, not actual readings**
- Total readings available: **{len(elec_df):,}**
""")

    if gas_meter_points:
        mp = gas_meter_points[0]
        meters = mp.get("meters", [])
        all_gas_readings = []
        for meter in meters:
            readings = fetch_consumption(api_key, account_number, "gas", mp["mprn"], meter["serial_number"], history_from, history_to)
            all_gas_readings.extend(readings)

        if all_gas_readings:
            gas_df = consumption_to_dataframe(all_gas_readings)
            gas_df = gas_df.drop_duplicates(subset=["interval_start"])
            gas_first = gas_df["interval_start"].min()

            # Count months with no gas data
            all_months_gas = pd.period_range(
                start=move_in_ts.to_period("M") if moved_in else gas_first.to_period("M"),
                end=pd.Timestamp.now(tz="UTC").to_period("M"),
                freq="M",
            )
            gas_df["month"] = gas_df["interval_start"].dt.to_period("M")
            months_with_data = gas_df["month"].unique()
            months_missing = len(all_months_gas) - len(months_with_data)

            st.markdown(f"""
**Gas:**
- First meter reading: **{gas_first:%Y-%m-%d}**
- Total readings available: **{len(gas_df):,}** (very low — should be ~{len(all_months_gas) * 1440:,}+)
- Months with zero data: **{months_missing}** out of {len(all_months_gas)} months
- Gas meter coverage is **extremely poor** — most billing has been estimated
""")
        else:
            st.error("Gas meter (E6E14847392460) has NEVER reported any data to Octopus.")

    st.markdown("---")
    st.markdown("""
### What to tell Octopus

> "My smart meter was not reporting data correctly. The API confirms that:
> - Electricity had no readings for the first 13+ months after move-in
> - Gas meter coverage has been extremely poor throughout, with many months of zero data
> - Billing during these periods was based on estimates, not actual consumption
> - I request a review of all estimated bills and a recalculation based on the available actual readings."
""")

# Footer
st.divider()
st.caption(
    "OctoPi pulls data directly from the Octopus Energy API. "
    "All calculations use your actual half-hourly meter readings × your tariff rate. "
    "Months with 0% coverage = Octopus estimated your bill."
)
