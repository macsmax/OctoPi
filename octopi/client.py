"""Octopus Energy API client."""

import requests
from datetime import datetime, timedelta
from typing import Optional


class OctopusClient:
    BASE_URL = "https://api.octopus.energy/v1"
    GRAPHQL_URL = "https://api.octopus.energy/v1/graphql/"

    def __init__(self, api_key: str, account_number: str):
        self.api_key = api_key
        self.account_number = account_number
        self.session = requests.Session()
        self.session.auth = (api_key, "")
        self._graphql_token: Optional[str] = None

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

    # --- GraphQL API (billing, payments, transactions) ---

    def _get_graphql_token(self) -> str:
        if self._graphql_token:
            return self._graphql_token
        query = """
        mutation obtainKrakenToken($input: ObtainJSONWebTokenInput!) {
            obtainKrakenToken(input: $input) {
                token
            }
        }
        """
        resp = requests.post(
            self.GRAPHQL_URL,
            json={"query": query, "variables": {"input": {"APIKey": self.api_key}}},
        )
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            raise Exception(f"GraphQL auth error: {data['errors']}")
        self._graphql_token = data["data"]["obtainKrakenToken"]["token"]
        return self._graphql_token

    def _graphql_request(self, query: str, variables: Optional[dict] = None) -> dict:
        token = self._get_graphql_token()
        resp = requests.post(
            self.GRAPHQL_URL,
            json={"query": query, "variables": variables or {}},
            headers={"Authorization": f"JWT {token}"},
        )
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            raise Exception(f"GraphQL error: {data['errors']}")
        return data["data"]

    def get_balance(self) -> dict:
        query = """
        query getBalance($accountNumber: String!) {
            account(accountNumber: $accountNumber) {
                balance
                overdueBalance
            }
        }
        """
        data = self._graphql_request(query, {"accountNumber": self.account_number})
        return data["account"]

    def get_payments(self, first: int = 100) -> list[dict]:
        payments = []
        cursor = None
        while True:
            query = """
            query getPayments($accountNumber: String!, $first: Int, $after: String) {
                account(accountNumber: $accountNumber) {
                    payments(first: $first, after: $after) {
                        totalCount
                        pageInfo { hasNextPage endCursor }
                        edges {
                            node {
                                amount
                                paymentDate
                                reference
                                status
                            }
                        }
                    }
                }
            }
            """
            variables = {"accountNumber": self.account_number, "first": first}
            if cursor:
                variables["after"] = cursor
            data = self._graphql_request(query, variables)
            page = data["account"]["payments"]
            payments.extend([edge["node"] for edge in page["edges"]])
            if not page["pageInfo"]["hasNextPage"]:
                break
            cursor = page["pageInfo"]["endCursor"]
        return payments

    def get_bills(self, first: int = 100) -> list[dict]:
        bills = []
        cursor = None
        while True:
            query = """
            query getBills($accountNumber: String!, $first: Int, $after: String) {
                account(accountNumber: $accountNumber) {
                    bills(first: $first, after: $after, includeHistoricStatements: true) {
                        totalCount
                        pageInfo { hasNextPage endCursor }
                        edges {
                            node {
                                ... on StatementType {
                                    id
                                    billType
                                    fromDate
                                    toDate
                                    issuedDate
                                    openingBalance
                                    closingBalance
                                    totalCharges { grossTotal }
                                    totalCredits { grossTotal }
                                }
                            }
                        }
                    }
                }
            }
            """
            variables = {"accountNumber": self.account_number, "first": first}
            if cursor:
                variables["after"] = cursor
            data = self._graphql_request(query, variables)
            page = data["account"]["bills"]
            bills.extend([edge["node"] for edge in page["edges"] if edge["node"]])
            if not page["pageInfo"]["hasNextPage"]:
                break
            cursor = page["pageInfo"]["endCursor"]
        return bills

    def get_transactions(self, first: int = 100) -> list[dict]:
        transactions = []
        cursor = None
        while True:
            query = """
            query getTransactions($accountNumber: String!, $first: Int, $after: String) {
                account(accountNumber: $accountNumber) {
                    transactions(first: $first, after: $after) {
                        totalCount
                        pageInfo { hasNextPage endCursor }
                        edges {
                            node {
                                id
                                postedDate
                                title
                                amounts { net tax gross }
                                balanceCarriedForward
                                isHeld
                                isReversed
                                note
                            }
                        }
                    }
                }
            }
            """
            variables = {"accountNumber": self.account_number, "first": first}
            if cursor:
                variables["after"] = cursor
            data = self._graphql_request(query, variables)
            page = data["account"]["transactions"]
            transactions.extend([edge["node"] for edge in page["edges"]])
            if not page["pageInfo"]["hasNextPage"]:
                break
            cursor = page["pageInfo"]["endCursor"]
        return transactions

    def get_ev_devices(self) -> list[dict]:
        query = """
        query {
            devices(accountNumber: "%s") {
                id
                deviceType
                provider
                status { current }
            }
        }
        """ % self.account_number
        data = self._graphql_request(query)
        return [d for d in data.get("devices", []) if d.get("deviceType") == "ELECTRIC_VEHICLES"]

    def get_electroverse_transactions(self) -> list[dict]:
        """Get all Electroverse charging transactions."""
        all_txns = self.get_transactions()
        return [
            t for t in all_txns
            if t.get("title") and "electroverse" in t["title"].lower()
        ]
