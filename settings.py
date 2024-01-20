from core.domain.distribution import Distribution, EqualDistribution, CustomDistribution


crypto_assets = [
    'BTC',
    'ETH',
    'XAI',
    'SEI',
    'SOL',
    'MATIC',
    'PEPE',
    'DOGE',
    'BNB',
    'LDO'
]

fiat_asset = 'USDT'
fiat_decimals = 2
fiat_untouched = float(len(crypto_assets) * 6)
exposure = 1.0  # max 1.0, min 0.0
distribution = CustomDistribution(
    crypto_assets=crypto_assets,
    percentages={
        'BTC': 25,
        'ETH': 20,
        'XAI': 10,
        'SEI': 10,
        'SOL': 15,
        'MATIC': 10,
        'PEPE': 1,
        'DOGE': 1,
        'BNB': 3,
        'LDO': 5
    }
)