"""Octopus Energy API client."""

import requests
from datetime import datetime, timedelta
from typing import Optional


class OctopusClient:
    BASE_URL = "https://api.octopus.energy/v1"

    def __init__(self, api_key: str, account_number: str):
        self.api_key = api_key
        self.account_number = account_number
        self.session = requests.Session()
        self.session.auth = (api_key, "")

    def get_account(self) -> dict:
        resp = self.session.get(f"{self.BASE_URL}/accounts/{self.account_number}/")
        resp.raise_for_status()
        return resp.json()

    def get_consumption(
        self,
        fuel: str,
        meter_point_id: str,
        serial_number: str,
        period_from: Optional[datetime] = None,
        period_to: Optional[datetime] = None,
        group_by: Optional[str] = None,
    ) -> list[dict]:
        if fuel == "electricity":
            url = f"{self.BASE_URL}/electricity-meter-points/{meter_point_id}/meters/{serial_number}/consumption/"
        else:
            url = f"{self.BASE_URL}/gas-meter-points/{meter_point_id}/meters/{serial_number}/consumption/"

        params = {"page_size": 25000, "order_by": "period"}
        if period_from:
            params["period_from"] = period_from.isoformat()
        if period_to:
            params["period_to"] = period_to.isoformat()
        if group_by:
            params["group_by"] = group_by

        results = []
        while url:
            resp = self.session.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            results.extend(data.get("results", []))
            url = data.get("next")
            params = {}
        return results

    def get_tariff_rates(
        self,
        product_code: str,
        tariff_code: str,
        fuel: str,
        rate_type: str = "standard-unit-rates",
        period_from: Optional[datetime] = None,
        period_to: Optional[datetime] = None,
    ) -> list[dict]:
        if fuel == "electricity":
            url = f"{self.BASE_URL}/products/{product_code}/electricity-tariffs/{tariff_code}/{rate_type}/"
        else:
            url = f"{self.BASE_URL}/products/{product_code}/gas-tariffs/{tariff_code}/{rate_type}/"

        params = {"page_size": 1500}
        if period_from:
            params["period_from"] = period_from.isoformat()
        if period_to:
            params["period_to"] = period_to.isoformat()

        results = []
        while url:
            resp = self.session.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            results.extend(data.get("results", []))
            url = data.get("next")
            params = {}
        return results

    def get_standing_charges(
        self,
        product_code: str,
        tariff_code: str,
        fuel: str,
        period_from: Optional[datetime] = None,
        period_to: Optional[datetime] = None,
    ) -> list[dict]:
        return self.get_tariff_rates(
            product_code, tariff_code, fuel, "standing-charges", period_from, period_to
        )
