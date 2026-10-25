"""Load optional features on demand, including in a small headless worker."""
from importlib import import_module
from .version import __version__

__all__ = ['backtest', 'betting', 'fpl', 'implied', 'matchflow', 'metrics',
           'models', 'ratings', 'scrapers', 'viz', 'xt', '__version__']


def __getattr__(name):
    if name not in __all__ or name == '__version__':
        raise AttributeError(name)
    value = import_module('.' + name, __name__)
    globals()[name] = value
    return value
