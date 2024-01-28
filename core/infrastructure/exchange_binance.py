from datetime import datetime, timedelta
from typing import List, Tuple

import requests
import time
import os

from core.domain.entities import Operation, Candle
from core.domain.interfaces import AbstractExchange
from binance import Client

from shared.domain.decorators import execution_with_attempts


class BinanceExchange(AbstractExchange):
    def __init__(self, api_key=None, api_secret=None):
        self._api_key = api_key
        self._api_secret = api_secret
        self._client = None
        self._exchange_info = None
        self.sync_time()

    @property
    def client(self):
        # lazy client instantiation
        if self._client is None:
            self._client = Client(self._api_key, self._api_secret)
        return self._client

    def sync_time(self):
        binance_server_time = int(self.client.get_server_time()["serverTime"])
        binance_server_time = binance_server_time / 1000
        local_time = time.time()
        time_diff = local_time - binance_server_time
        if abs(time_diff) > 1:
            os.system(f"sudo date -s '@{binance_server_time}'")

    @execution_with_attempts(attempts=3, wait_seconds=5)
    def get_asset_balance(self, asset: str) -> float:
        return float(self.client.get_asset_balance(asset=asset)["free"])

    @execution_with_attempts(attempts=3, wait_seconds=5)
    def get_asset_price(self, base_asset: str, quote_asset: str, **kwargs) -> float:
        return float(
            self.client.get_avg_price(symbol=f"{base_asset}{quote_asset}")["price"]
        )

    @execution_with_attempts(attempts=3, wait_seconds=5)
    def place_buy_order(
        self, base_asset: str, quote_asset: str, quote_amount: float, **kwargs
    ):
        quote_amount = "{:.8f}".format(quote_amount)
        self.client.order_market_buy(
            symbol=f"{base_asset}{quote_asset}", quoteOrderQty=quote_amount
        )

    @execution_with_attempts(attempts=3, wait_seconds=5)
    def place_sell_order(
        self, base_asset: str, quote_asset: str, quote_amount: float, **kwargs
    ):
        quote_amount = "{:.8f}".format(quote_amount)
        self.client.order_market_sell(
            symbol=f"{base_asset}{quote_asset}", quoteOrderQty=quote_amount
        )

    def compute_fees(
        self, operations: List[Operation], fiat_asset: str, **kwargs
    ) -> float:
        total_fees = 0.0
        for operation in operations:
            if operation.quote_currency == fiat_asset:
                total_fees += operation.quote_amount * (0.1 / 100.0)
            else:
                counter_fiat_price = self.get_asset_price(
                    operation.quote_currency, fiat_asset, **kwargs
                )
                total_fees += (operation.quote_amount / counter_fiat_price) * (
                    0.1 / 100.0
                )
        return round(total_fees, 8)

    def get_exchange_valid_operations(
        self, operations: List[Operation]
    ) -> List[Operation]:
        exchange_info = self._get_exchange_info()

        result_operations = []
        for operation in operations:
            try:
                pair_info = exchange_info[
                    f"{operation.base_currency}{operation.quote_currency}"
                ]
            except KeyError:
                continue
            cloned_operation = operation.clone()
            valid = True
            for filtr in pair_info["filters"]:
                filter_type = filtr["filterType"]
                if filter_type == "PRICE_FILTER":
                    """
                    ETHBUSD
                    "minPrice": "0.01000000",
                    "maxPrice": "100000.00000000",
                    "tickSize": "0.01000000" <--- step_size of quote asset
                    """
                    step_size = round(float(filtr["tickSize"]), 8)
                    cloned_operation.quote_amount = self._fix_amount_by_step_size(
                        cloned_operation.quote_amount, step_size
                    )
                elif filter_type == "MIN_NOTIONAL":
                    """
                    ETHBUSD
                    "minNotional": "10.00000000", <--- minimum amount of quote asset
                    "applyToMarket": true,
                    "avgPriceMins": 5
                    """
                    minimum_operation_amount = round(float(filtr["minNotional"]), 8)
                    if cloned_operation.quote_amount < minimum_operation_amount:
                        valid = False
            if valid:
                result_operations.append(cloned_operation)
        return result_operations

    def execute_operations(
        self, operations: List[Operation], **kwargs
    ) -> List[Operation]:
        unprocessed = []
        for operation in operations:
            try:
                if operation.type == Operation.TYPE_SELL:
                    self.place_sell_order(
                        operation.base_currency,
                        operation.quote_currency,
                        operation.quote_amount,
                    )
                elif operation.type == Operation.TYPE_BUY:
                    self.place_buy_order(
                        operation.base_currency,
                        operation.quote_currency,
                        operation.quote_amount,
                    )
            except Exception as e:
                print(e)
                unprocessed.append(operation)

        return unprocessed

    @execution_with_attempts(attempts=3, wait_seconds=5)
    def get_last_price_candles(
        self, pair: str, period: str, amount: int, now: datetime
    ) -> List[Candle]:
        end_date_str = (now + timedelta(days=1)).strftime("%d %b, %Y")
        if period == Candle.PERIOD_HOUR:
            delta = timedelta(hours=1)
            b_interval = Client.KLINE_INTERVAL_1HOUR
        else:
            return []
        start_date = now - (delta * amount)
        start_date_str = (start_date - timedelta(days=1)).strftime("%d %b, %Y")

        raw_data = self.client.get_historical_klines(
            f'{pair.split("/")[0]}{pair.split("/")[1]}',
            b_interval,
            start_date_str,
            end_date_str,
        )

        results = []
        current_date_check = datetime(
            start_date.year, start_date.month, start_date.day, start_date.hour, 0, 0
        )
        for item in raw_data:
            instant = datetime.utcfromtimestamp(item[0] / 1000)
            if instant > now or instant < start_date:
                continue

            current_date_check += delta

            if instant != current_date_check:
                results.append(None)
            else:
                current_candle = Candle(
                    instant=instant,
                    period=period,
                    pair=pair,
                    o=float(item[1]),
                    c=float(item[4]),
                    h=float(item[2]),
                    l=float(item[3]),
                    volume=float(item[5]),
                )
                results.append(current_candle)

        return results

    def exchange_pair_exist(self, base_asset, quote_asset) -> bool:
        return f"{base_asset}{quote_asset}" in self._get_exchange_info()

    @execution_with_attempts(attempts=3, wait_seconds=5)
    def _get_exchange_info(self):
        if self._exchange_info is None:
            response = requests.get("https://api.binance.com/api/v1/exchangeInfo")
            self._exchange_info = {
                symbol_data["symbol"]: symbol_data
                for symbol_data in response.json()["symbols"]
            }
        return self._exchange_info

    @classmethod
    def _fix_amount_by_step_size(cls, amount, step_size):
        # if step_size is 5, 99 will be converted to 95.0, which is the more conservative result.

        # If 99 with step_size of 5 need to be converted to 100,
        # amount // step_size could be changed to round(amount / step_size) before multiplying it by step_size again

        # the round(amount, 8) is to fix Python problems when amount // step_size * step_size
        # results in something like 99.0000000000000001.
        # round(99.0000000000000001, 8) results in 99.0 which is the right result
        # round(99.2200000000000001, 8) results in 99.22 which is the right result again
        return round(amount // step_size * step_size, 8)
