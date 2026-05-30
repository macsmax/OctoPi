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


@st.cache_data(ttl=3600)
def fetch_billing_data(api_key: str, account_number: str) -> dict:
    client = OctopusClient(api_key, account_number)
    return {
        "balance": client.get_balance(),
        "payments": client.get_payments(),
        "bills": client.get_bills(),
    }


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
EV_ENABLED = os.getenv("EV_CHARGING", "false").lower() in ("true", "1", "yes")
tab_labels = ["💷 Paid vs Consumed", "⚡ Electricity", "🔥 Gas"]
if EV_ENABLED:
    tab_labels.append("🚗 EV Charging")
tab_labels.append("📊 Billing Summary")
tabs = st.tabs(tab_labels)

# --- Paid vs Consumed tab ---
with tabs[0]:
    st.subheader("What You Paid vs What You Actually Used")

    try:
        billing = fetch_billing_data(api_key, account_number)
    except Exception as e:
        st.error(f"Failed to fetch billing data: {e}")
        st.stop()

    balance_data = billing["balance"]
    payments = billing["payments"]
    bills = billing["bills"]

    # Current balance
    balance_pence = balance_data.get("balance", 0)
    balance_pounds = balance_pence / 100
    overdue_pounds = balance_data.get("overdueBalance", 0) / 100

    if balance_pounds > 0:
        st.success(f"### Account Balance: **£{balance_pounds:,.2f} IN CREDIT**")
    elif balance_pounds < 0:
        st.error(f"### Account Balance: **£{abs(balance_pounds):,.2f} IN DEBIT**")
    else:
        st.info("### Account Balance: £0.00")

    if overdue_pounds > 0:
        st.warning(f"Overdue: £{overdue_pounds:,.2f}")

    st.divider()

    # Payment summary
    cleared_payments = [p for p in payments if p.get("status") in ("CLEARED", "PENDING")]
    total_paid = sum(p["amount"] for p in cleared_payments) / 100

    # Bills summary
    total_charged = sum((b.get("totalCharges", {}).get("grossTotal", 0) or 0) for b in bills) / 100
    total_credits = sum((b.get("totalCredits", {}).get("grossTotal", 0) or 0) for b in bills) / 100
    net_billed = total_charged - total_credits

    col1, col2, col3 = st.columns(3)
    col1.metric("Total Paid", f"£{total_paid:,.2f}", help="All cleared + pending direct debit payments")
    col2.metric("Total Billed (net)", f"£{net_billed:,.2f}", help="Total charges minus credits applied")
    col3.metric("Overpayment", f"£{total_paid - net_billed:,.2f}", delta=f"£{total_paid - net_billed:,.2f}", help="Paid minus billed")

    st.divider()

    # Monthly comparison: what you paid vs what was billed vs calculated from readings
    st.subheader("📊 Monthly: Paid vs Billed vs Calculated from Readings")

    # Build monthly payments
    payments_df = pd.DataFrame(cleared_payments)
    if not payments_df.empty:
        payments_df["date"] = pd.to_datetime(payments_df["paymentDate"])
        payments_df["month"] = payments_df["date"].dt.to_period("M")
        payments_df["amount_pounds"] = payments_df["amount"] / 100
        monthly_payments = payments_df.groupby("month")["amount_pounds"].sum().reset_index()
        monthly_payments["month_str"] = monthly_payments["month"].astype(str)
    else:
        monthly_payments = pd.DataFrame(columns=["month_str", "amount_pounds"])

    # Build monthly bills
    bills_with_dates = [b for b in bills if b.get("fromDate")]
    if bills_with_dates:
        bills_df = pd.DataFrame(bills_with_dates)
        bills_df["from"] = pd.to_datetime(bills_df["fromDate"])
        bills_df["month"] = bills_df["from"].dt.to_period("M")
        bills_df["net_charge"] = bills_df.apply(
            lambda r: ((r.get("totalCharges") or {}).get("grossTotal", 0) or 0) / 100
            - ((r.get("totalCredits") or {}).get("grossTotal", 0) or 0) / 100,
            axis=1,
        )
        monthly_bills = bills_df.groupby("month")["net_charge"].sum().reset_index()
        monthly_bills["month_str"] = monthly_bills["month"].astype(str)
    else:
        monthly_bills = pd.DataFrame(columns=["month_str", "net_charge"])

    # Merge for chart
    all_months_set = set()
    if not monthly_payments.empty:
        all_months_set.update(monthly_payments["month_str"].tolist())
    if not monthly_bills.empty:
        all_months_set.update(monthly_bills["month_str"].tolist())

    if all_months_set:
        chart_df = pd.DataFrame({"month_str": sorted(all_months_set)})
        chart_df = chart_df.merge(
            monthly_payments[["month_str", "amount_pounds"]].rename(columns={"amount_pounds": "Paid (DD)"}),
            on="month_str", how="left",
        ).merge(
            monthly_bills[["month_str", "net_charge"]].rename(columns={"net_charge": "Billed (net)"}),
            on="month_str", how="left",
        ).fillna(0)

        fig = go.Figure()
        fig.add_trace(go.Bar(x=chart_df["month_str"], y=chart_df["Paid (DD)"], name="Paid (Direct Debit)", marker_color="#22c55e"))
        fig.add_trace(go.Bar(x=chart_df["month_str"], y=chart_df["Billed (net)"], name="Billed (net charges)", marker_color="#ef4444"))
        fig.update_layout(barmode="group", height=400, margin=dict(t=10), yaxis_title="£", xaxis_tickangle=-45)
        st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # Cumulative view
    st.subheader("📈 Cumulative: Running Credit/Debit Over Time")

    if not monthly_payments.empty and not monthly_bills.empty:
        cumul_df = chart_df.copy()
        cumul_df["cumul_paid"] = cumul_df["Paid (DD)"].cumsum()
        cumul_df["cumul_billed"] = cumul_df["Billed (net)"].cumsum()
        cumul_df["running_balance"] = cumul_df["cumul_paid"] - cumul_df["cumul_billed"]

        fig_cumul = go.Figure()
        fig_cumul.add_trace(go.Scatter(x=cumul_df["month_str"], y=cumul_df["cumul_paid"], name="Cumulative Paid", line=dict(color="#22c55e", width=2)))
        fig_cumul.add_trace(go.Scatter(x=cumul_df["month_str"], y=cumul_df["cumul_billed"], name="Cumulative Billed", line=dict(color="#ef4444", width=2)))
        fig_cumul.add_trace(go.Scatter(x=cumul_df["month_str"], y=cumul_df["running_balance"], name="Balance (credit/debit)", line=dict(color="#6366f1", width=3), fill="tozeroy"))
        fig_cumul.add_hline(y=0, line_dash="dash", line_color="gray")
        fig_cumul.update_layout(height=400, margin=dict(t=10), yaxis_title="£", xaxis_tickangle=-45)
        st.plotly_chart(fig_cumul, use_container_width=True)

    st.divider()

    # Payment history table
    st.subheader("💳 Payment History")
    if cleared_payments:
        pay_table = pd.DataFrame(cleared_payments)[["paymentDate", "amount", "status"]].copy()
        pay_table["amount"] = pay_table["amount"].apply(lambda x: f"£{x/100:.2f}")
        pay_table.columns = ["Date", "Amount", "Status"]
        st.dataframe(pay_table, use_container_width=True, hide_index=True)

    # Bills table
    st.subheader("📄 Bill History")
    if bills_with_dates:
        bill_rows = []
        for b in bills_with_dates:
            charges = (b.get("totalCharges", {}).get("grossTotal", 0) or 0) / 100
            credits = (b.get("totalCredits", {}).get("grossTotal", 0) or 0) / 100
            bill_rows.append({
                "Period": f"{b['fromDate']} → {b['toDate']}",
                "Charges": f"£{charges:.2f}",
                "Credits": f"£{credits:.2f}",
                "Net": f"£{charges - credits:.2f}",
                "Issued": b.get("issuedDate", ""),
            })
        st.dataframe(pd.DataFrame(bill_rows), use_container_width=True, hide_index=True)

for tab_idx, (tab, fuel, meter_points) in enumerate(
    zip(tabs[1:3], ["electricity", "gas"], [elec_meter_points, gas_meter_points])
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

# EV Charging tab
if EV_ENABLED:
    ev_tab_idx = tab_labels.index("🚗 EV Charging")
    with tabs[ev_tab_idx]:
        st.subheader("🚗 EV Charging Analysis")

        charger_kw = float(os.getenv("EV_CHARGER_KW", "7"))
        # Threshold: half the charger rate per 30-min slot (accounting for losses)
        ev_threshold = charger_kw * 0.5 * 0.7  # 70% of theoretical max per slot

        # Fetch EV device info
        try:
            client = OctopusClient(api_key, account_number)
            ev_devices = client.get_ev_devices()
            if ev_devices:
                dev = ev_devices[0]
                st.success(f"**{dev['provider']}** registered on Intelligent Octopus ({dev['status']['current']})")
            else:
                st.info("No EV device registered with Intelligent Octopus")
        except Exception:
            pass

        st.divider()

        # Home charging: detect from off-peak consumption spikes
        st.subheader("🏠 Home Charging (from meter data)")
        st.caption(f"Detecting slots with >{ev_threshold:.1f} kWh/30min during off-peak hours (23:30–05:30)")

        # Combine all electricity data
        all_elec_readings = []
        if elec_meter_points:
            emp = elec_meter_points[0]
            history_from_ev = datetime(2023, 4, 10)
            history_to_ev = datetime.now()
            for meter in emp.get("meters", []):
                readings = fetch_consumption(
                    api_key, account_number, "electricity",
                    emp["mpan"], meter["serial_number"],
                    history_from_ev, history_to_ev,
                )
                all_elec_readings.extend(readings)

        if all_elec_readings:
            ev_df = consumption_to_dataframe(all_elec_readings)
            ev_df = ev_df.drop_duplicates(subset=["interval_start"]).sort_values("interval_start").reset_index(drop=True)

            # Identify off-peak hours (22:30-04:30 UTC = 23:30-05:30 BST)
            ev_df["hour_utc"] = ev_df["interval_start"].dt.hour
            ev_df["minute_utc"] = ev_df["interval_start"].dt.minute
            ev_df["is_offpeak"] = (
                (ev_df["hour_utc"] >= 23) |
                (ev_df["hour_utc"] < 5) |
                ((ev_df["hour_utc"] == 22) & (ev_df["minute_utc"] >= 30))
            )

            # Off-peak high-draw = likely EV charging
            ev_charging = ev_df[ev_df["is_offpeak"] & (ev_df["consumption"] > ev_threshold)].copy()
            ev_charging["month"] = ev_charging["interval_start"].dt.tz_localize(None).dt.to_period("M")

            # Get off-peak rate
            offpeak_rate = 7.0  # p/kWh default for Intelligent Octopus
            try:
                rates = client.get_tariff_rates(
                    "INTELLI-VAR-22-10-14", "E-1R-INTELLI-VAR-22-10-14-J", "electricity"
                )
                if rates:
                    offpeak_rates = [r for r in rates if r.get("value_inc_vat", 99) < 15]
                    if offpeak_rates:
                        offpeak_rate = offpeak_rates[0]["value_inc_vat"]
            except Exception:
                pass

            total_ev_kwh = ev_charging["consumption"].sum()
            total_ev_cost = total_ev_kwh * offpeak_rate / 100

            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Home Charged", f"{total_ev_kwh:,.0f} kWh")
            col2.metric("Cost (off-peak)", f"£{total_ev_cost:,.2f}")
            col3.metric("Off-peak Rate", f"{offpeak_rate:.2f}p/kWh")
            col4.metric("Charging Sessions", f"{len(ev_charging):,} slots")

            # Monthly home charging chart
            monthly_ev = ev_charging.groupby("month").agg(
                kwh=("consumption", "sum"),
                sessions=("consumption", "count"),
            ).reset_index()
            monthly_ev["month_str"] = monthly_ev["month"].astype(str)
            monthly_ev["cost"] = monthly_ev["kwh"] * offpeak_rate / 100

            fig_ev = go.Figure()
            fig_ev.add_trace(go.Bar(
                x=monthly_ev["month_str"],
                y=monthly_ev["kwh"],
                name="kWh Charged",
                marker_color="#22c55e",
            ))
            fig_ev.update_layout(
                height=350, margin=dict(t=10),
                yaxis_title="kWh", xaxis_title="Month", xaxis_tickangle=-45,
            )
            st.plotly_chart(fig_ev, use_container_width=True)

            # Monthly breakdown table
            ev_table = monthly_ev[["month_str", "kwh", "cost", "sessions"]].copy()
            ev_table.columns = ["Month", "kWh Charged", "Cost (£)", "Slots"]
            ev_table["kWh Charged"] = ev_table["kWh Charged"].round(1)
            ev_table["Cost (£)"] = ev_table["Cost (£)"].round(2)
            st.dataframe(ev_table, use_container_width=True, hide_index=True)

        else:
            st.warning("No electricity data available to detect home charging.")

        st.divider()

        # Electroverse
        st.subheader("⚡ Electroverse (public charging)")

        try:
            ev_transactions = client.get_electroverse_transactions()

            if ev_transactions:
                total_electroverse = sum(t["amounts"]["gross"] for t in ev_transactions) / 100

                col1, col2 = st.columns(2)
                col1.metric("Electroverse Sessions", len(ev_transactions))
                col2.metric("Total Electroverse Spend", f"£{total_electroverse:.2f}")

                ev_rows = []
                for t in ev_transactions:
                    ev_rows.append({
                        "Date": t["postedDate"],
                        "Cost": f"£{t['amounts']['gross'] / 100:.2f}",
                        "Note": t.get("note") or "",
                    })

                st.dataframe(pd.DataFrame(ev_rows), use_container_width=True, hide_index=True)
            else:
                st.info("No Electroverse charging sessions found.")
        except Exception as e:
            st.warning(f"Could not fetch Electroverse data: {e}")

        st.divider()

        # Total EV costs summary
        st.subheader("💰 Total EV Charging Costs")
        home_cost = total_ev_cost if all_elec_readings else 0
        electroverse_cost = total_electroverse if ev_transactions else 0
        total_all_ev = home_cost + electroverse_cost

        fig_pie = go.Figure(data=[go.Pie(
            labels=["Home (off-peak)", "Electroverse (public)"],
            values=[home_cost, electroverse_cost],
            marker_colors=["#22c55e", "#6366f1"],
            hole=0.4,
        )])
        fig_pie.update_layout(height=300, margin=dict(t=10, b=10))
        st.plotly_chart(fig_pie, use_container_width=True)

        st.metric("Total EV Charging Cost", f"£{total_all_ev:.2f}")
        if total_ev_kwh > 0:
            avg_cost_per_kwh = total_all_ev / total_ev_kwh * 100
            avg_cost_per_mile = total_all_ev / (total_ev_kwh * 3.5)  # ~3.5 mi/kWh for Tesla
            st.caption(f"Average: {avg_cost_per_kwh:.1f}p/kWh | ~£{avg_cost_per_mile:.2f}/mile (estimated)")

# Billing Summary tab
with tabs[-1]:
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
