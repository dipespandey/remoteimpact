"""Deterministic gross FTE conversion; no currency conversion or tax estimate."""
from decimal import Decimal, InvalidOperation


def calculate_pro_rata(annual_fte, full_time_hours, actual_hours, months=12):
    try:
        salary, full, actual, term = map(Decimal, map(str, (annual_fte, full_time_hours, actual_hours, months)))
    except (InvalidOperation, ValueError):
        raise ValueError('Enter finite numeric amounts.') from None
    if not all(x.is_finite() for x in (salary, full, actual, term)):
        raise ValueError('Enter finite numeric amounts.')
    if salary < 0 or salary > 1000000000:
        raise ValueError('Annual FTE pay must be between 0 and 1 billion.')
    if not 0 < actual <= full <= 168:
        raise ValueError('Actual hours must be positive and no greater than full-time hours (maximum 168).')
    if not 0 < term <= 120:
        raise ValueError('Contract length must be greater than 0 and at most 120 months.')
    annual = salary * actual / full
    return {'fte_fraction': actual / full, 'annual_gross': annual,
            'monthly_gross': annual / 12, 'contract_gross': annual * term / 12}
