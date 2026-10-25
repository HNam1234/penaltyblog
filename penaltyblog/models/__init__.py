"""Keep public model names while importing only the requested model."""
from importlib import import_module

_MODULES = {
    'BaseBayesianModel': 'base_bayesian_model',
    'BayesianGoalModel': 'bayesian_goal_model',
    'BivariatePoissonGoalModel': 'bivariate_poisson',
    'DixonColesGoalModel': 'dixon_coles',
    'FootballProbabilityGrid': 'football_probability_grid',
    'create_dixon_coles_grid': 'football_probability_grid',
    'goal_expectancy': 'goal_expectancy',
    'goal_expectancy_extended': 'goal_expectancy',
    'HierarchicalBayesianGoalModel': 'hierarchical_bayesian_goal_model',
    'NegativeBinomialGoalModel': 'negative_binomial',
    'PoissonGoalsModel': 'poisson',
    'dixon_coles_weights': 'utils',
    'WeibullCopulaGoalsModel': 'weibull_copula',
    'ZeroInflatedPoissonGoalsModel': 'zero_inf_poisson',
}
__all__ = list(_MODULES)


def __getattr__(name):
    if name not in _MODULES:
        raise AttributeError(name)
    value = getattr(import_module('.' + _MODULES[name], __name__), name)
    globals()[name] = value
    return value
