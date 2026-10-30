"""Data-use restrictions verified 2026-10-30; no web-host scraping fallback."""
from urllib.parse import urlparse


class SourceAccessBlocked(ValueError):
    pass


def is_pump_url(value):
    if not isinstance(value,str) or not value.startswith(('https://','http://')):return False
    host=(urlparse(value).hostname or '').lower()
    return host=='pump.fun' or host.endswith('.pump.fun')


def require_permitted_url(url):
    if is_pump_url(url):
        raise SourceAccessBlocked('Pump web collectors disabled: authorization unverified; Developer API terms prohibit model training/evaluation')


def contains_pump_data(value):
    if isinstance(value,dict):return any(contains_pump_data(item) for item in value.values())
    if isinstance(value,(list,tuple)):return any(contains_pump_data(item) for item in value)
    return is_pump_url(value)


def require_learning_rights(value):
    if contains_unreviewed_learning_data(value):
        raise SourceAccessBlocked('Recorded provider data quarantined from further model use pending data-rights/storage review; history retained')


def contains_unreviewed_learning_data(value):
    if isinstance(value,dict):return any(contains_unreviewed_learning_data(item) for item in value.values())
    if isinstance(value,(list,tuple)):return any(contains_unreviewed_learning_data(item) for item in value)
    if not isinstance(value,str) or not value.startswith(('https://','http://')):return False
    return is_pump_url(value) or (urlparse(value).hostname or '').lower()=='api.geckoterminal.com'
