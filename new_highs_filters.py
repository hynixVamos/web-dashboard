"""Security-type exclusions; do not exclude ordinary companies by industry alone."""
import re


def excluded_security(name):
    name = re.sub(r'\s+', '', str(name or ''))
    if re.search(r'스팩|기업인수목적|\bSPAC\b', name, re.I):
        return '스팩'
    if re.search(r'(?<!메)리츠|부동산투자회사|\bREIT\b', name, re.I):
        return '리츠'
    return None
