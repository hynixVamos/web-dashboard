"""Yahoo industry -> short Korean labels. No NAVER classification dependency."""
import json
from pathlib import Path

INDUSTRIES = [
    ('반도체', ('semiconductor',)),
    ('바이오', ('biotechnology', 'drug manufacturers')),
    ('의료기기', ('medical devices', 'diagnostics', 'medical instruments')),
    ('헬스케어', ('healthcare', 'medical', 'pharmaceutical')),
    ('소프트웨어', ('software', 'information technology services')),
    ('인터넷', ('internet content', 'internet retail')),
    ('통신', ('telecom', 'communication equipment')),
    ('전력·유틸리티', ('utilities', 'electrical equipment')),
    ('자동차', ('auto manufacturers', 'auto parts', 'auto dealerships')),
    ('항공·방산', ('aerospace', 'defense', 'airlines')),
    ('에너지', ('oil & gas', 'solar', 'uranium', 'thermal coal')),
    ('금융', ('banks', 'insurance', 'capital markets', 'asset management', 'credit services')),
    ('리츠·부동산', ('reit', 'real estate')),
    ('소재', ('steel', 'aluminum', 'copper', 'gold', 'silver', 'chemicals', 'mining')),
    ('운송', ('railroads', 'trucking', 'shipping', 'logistics')),
    ('유통·소비재', ('retail', 'apparel', 'restaurants', 'beverages', 'packaged foods', 'household')),
    ('미디어·엔터', ('entertainment', 'gaming', 'broadcasting', 'publishing')),
    ('하드웨어', ('computer hardware', 'consumer electronics', 'electronic components')),
]
SECTORS = {'Technology': 'IT·전자', 'Healthcare': '헬스케어', 'Financial Services': '금융',
           'Consumer Cyclical': '소비재', 'Consumer Defensive': '필수소비재',
           'Communication Services': '통신·미디어', 'Industrials': '산업재',
           'Energy': '에너지', 'Basic Materials': '소재', 'Real Estate': '리츠·부동산',
           'Utilities': '전력·유틸리티'}


def classify(ticker, info, overrides=None):
    if overrides is None:
        with Path(__file__).with_name('us_sector_overrides.json').open(encoding='utf-8') as f:
            overrides = json.load(f)
    if overrides.get(ticker):
        return overrides[ticker]
    industry = (info.get('industry') or '').lower()
    for label, terms in INDUSTRIES:
        if any(term in industry for term in terms):
            return label
    return SECTORS.get(info.get('sector'), '미분류')
