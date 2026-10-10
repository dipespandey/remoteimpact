"""Where people using Remote Impact come from, in aggregate, for the homepage."""
from datetime import timedelta

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import F, Sum
from django.utils import timezone

FIELDS = {"views", "apply_clicks"}
MIN_PEOPLE = 3  # a country appears only once at least this many people were active
CACHE_KEY = "country_activity:v1"

NAMES = dict(line.split(" ", 1) for line in """AF Afghanistan
AL Albania
DZ Algeria
AO Angola
AR Argentina
AM Armenia
AU Australia
AT Austria
AZ Azerbaijan
BD Bangladesh
BY Belarus
BE Belgium
BJ Benin
BT Bhutan
BO Bolivia
BA Bosnia and Herzegovina
BW Botswana
BR Brazil
BG Bulgaria
BF Burkina Faso
BI Burundi
KH Cambodia
CM Cameroon
CA Canada
CL Chile
CN China
CO Colombia
CD DR Congo
CR Costa Rica
CI Côte d'Ivoire
HR Croatia
CU Cuba
CY Cyprus
CZ Czechia
DK Denmark
DO Dominican Republic
EC Ecuador
EG Egypt
SV El Salvador
EE Estonia
ET Ethiopia
FI Finland
FR France
GE Georgia
DE Germany
GH Ghana
GR Greece
GT Guatemala
HT Haiti
HN Honduras
HK Hong Kong
HU Hungary
IS Iceland
IN India
ID Indonesia
IR Iran
IQ Iraq
IE Ireland
IL Israel
IT Italy
JM Jamaica
JP Japan
JO Jordan
KZ Kazakhstan
KE Kenya
KR South Korea
KW Kuwait
KG Kyrgyzstan
LA Laos
LV Latvia
LB Lebanon
LR Liberia
LY Libya
LT Lithuania
LU Luxembourg
MG Madagascar
MW Malawi
MY Malaysia
ML Mali
MT Malta
MX Mexico
MD Moldova
MN Mongolia
ME Montenegro
MA Morocco
MZ Mozambique
MM Myanmar
NA Namibia
NP Nepal
NL Netherlands
NZ New Zealand
NI Nicaragua
NE Niger
NG Nigeria
MK North Macedonia
NO Norway
OM Oman
PK Pakistan
PS Palestine
PA Panama
PG Papua New Guinea
PY Paraguay
PE Peru
PH Philippines
PL Poland
PT Portugal
PR Puerto Rico
QA Qatar
RO Romania
RU Russia
RW Rwanda
SA Saudi Arabia
SN Senegal
RS Serbia
SL Sierra Leone
SG Singapore
SK Slovakia
SI Slovenia
SO Somalia
ZA South Africa
SS South Sudan
ES Spain
LK Sri Lanka
SD Sudan
SE Sweden
CH Switzerland
SY Syria
TW Taiwan
TJ Tajikistan
TZ Tanzania
TH Thailand
TG Togo
TT Trinidad and Tobago
TN Tunisia
TR Türkiye
UG Uganda
UA Ukraine
AE UAE
GB UK
US USA
UY Uruguay
UZ Uzbekistan
VE Venezuela
VN Vietnam
YE Yemen
ZM Zambia
ZW Zimbabwe""".splitlines())


def request_country(request):
    code = (request.META.get("HTTP_CF_IPCOUNTRY") or "").upper()
    return code if len(code) == 2 and code.isalpha() and code not in {"XX", "T1"} else ""


def record(request, field):
    country = request_country(request)
    if field not in FIELDS or not country:
        return
    from jobs.models import CountryDailyActivity

    today = timezone.localdate()
    try:
        if not CountryDailyActivity.objects.filter(date=today, country=country).update(**{field: F(field) + 1}):
            try:
                with transaction.atomic():
                    CountryDailyActivity.objects.create(date=today, country=country, **{field: 1})
            except IntegrityError:
                CountryDailyActivity.objects.filter(date=today, country=country).update(**{field: F(field) + 1})
    except Exception:  # noqa: BLE001 - counting must never break a page
        pass


def weekly_summary(days=7, top=6):
    """Last ``days`` days: countries with enough activity, biggest first."""
    summary = cache.get(CACHE_KEY)
    if summary is not None:
        return summary
    from jobs.models import CountryDailyActivity

    since = timezone.localdate() - timedelta(days=days - 1)
    rows = (CountryDailyActivity.objects.filter(date__gte=since).values("country")
            .annotate(views=Sum("views"), clicks=Sum("apply_clicks")).order_by("-clicks", "-views"))
    shown = [r for r in rows if (r["views"] or 0) >= MIN_PEOPLE and r["country"] in NAMES]
    clicks = sum(r["clicks"] or 0 for r in shown)
    peak = max((r["clicks"] or 0 for r in shown[:top]), default=0) or 1
    summary = {
        "countries": len(shown),
        "clicks": clicks,
        "top": [{"name": NAMES[r["country"]], "clicks": r["clicks"] or 0,
                 "share": round(100 * (r["clicks"] or 0) / peak)} for r in shown[:top]],
        "more": max(0, len(shown) - top),
        "days": days,
    }
    cache.set(CACHE_KEY, summary, 60 * 60)
    return summary
