"""Which regional labels a country falls under, so "open to Africa" roles count for someone in Kenya."""

REGIONS = {
    "Africa": ["Algeria", "Angola", "Benin", "Botswana", "Burkina Faso", "Burundi", "Cameroon", "Cape Verde", "Cabo Verde",
               "Central African Republic", "Chad", "Comoros", "Congo", "Djibouti", "Egypt", "Equatorial Guinea", "Eritrea",
               "Eswatini", "Ethiopia", "Gabon", "Gambia", "Ghana", "Guinea", "Guinea-Bissau", "Ivory Coast", "Côte d'Ivoire",
               "Kenya", "Lesotho", "Liberia", "Libya", "Madagascar", "Malawi", "Mali", "Mauritania", "Mauritius", "Morocco",
               "Mozambique", "Namibia", "Niger", "Nigeria", "Rwanda", "Senegal", "Seychelles", "Sierra Leone", "Somalia",
               "South Africa", "South Sudan", "Sudan", "Tanzania", "Togo", "Tunisia", "Uganda", "Zambia", "Zimbabwe"],
    "Asia": ["Afghanistan", "Bangladesh", "Bhutan", "Brunei", "Cambodia", "China", "Hong Kong", "India", "Indonesia", "Japan",
             "Kazakhstan", "Kyrgyzstan", "Laos", "Malaysia", "Maldives", "Mongolia", "Myanmar", "Nepal", "North Korea",
             "Pakistan", "Philippines", "Singapore", "South Korea", "Sri Lanka", "Taiwan", "Tajikistan", "Thailand",
             "Timor-Leste", "Turkmenistan", "Uzbekistan", "Vietnam"],
    "Middle East": ["Bahrain", "Iran", "Iraq", "Israel", "Jordan", "Kuwait", "Lebanon", "Oman", "Palestine", "Qatar",
                    "Saudi Arabia", "Syria", "Turkey", "United Arab Emirates", "Yemen"],
    "Europe": ["Albania", "Andorra", "Austria", "Belarus", "Belgium", "Bosnia and Herzegovina", "Bulgaria", "Croatia", "Cyprus",
               "Czech Republic", "Czechia", "Denmark", "Estonia", "Finland", "France", "Germany", "Greece", "Hungary", "Iceland",
               "Ireland", "Italy", "Kosovo", "Latvia", "Liechtenstein", "Lithuania", "Luxembourg", "Malta", "Moldova", "Monaco",
               "Montenegro", "Netherlands", "North Macedonia", "Norway", "Poland", "Portugal", "Romania", "Serbia", "Slovakia",
               "Slovenia", "Spain", "Sweden", "Switzerland", "Ukraine", "United Kingdom"],
    "Latin America": ["Argentina", "Belize", "Bolivia", "Brazil", "Chile", "Colombia", "Costa Rica", "Cuba", "Dominican Republic",
                      "Ecuador", "El Salvador", "Guatemala", "Haiti", "Honduras", "Jamaica", "Mexico", "Nicaragua", "Panama",
                      "Paraguay", "Peru", "Uruguay", "Venezuela"],
    "North America": ["Canada", "Mexico", "United States"],
    "Oceania": ["Australia", "Fiji", "New Zealand", "Papua New Guinea", "Samoa", "Solomon Islands", "Tonga", "Vanuatu"],
}
EXTRA = {"Africa": ["EMEA", "Sub-Saharan Africa"], "Europe": ["EMEA"], "Middle East": ["EMEA", "MENA"],
         "Asia": ["APAC", "Asia Pacific"], "Oceania": ["APAC", "Asia Pacific"],
         "Latin America": ["LATAM", "Americas"], "North America": ["Americas"]}
ALIASES = {"usa": "united states", "us": "united states", "uk": "united kingdom", "uae": "united arab emirates",
           "united states of america": "united states", "great britain": "united kingdom"}

_COUNTRY_TO_REGIONS = {}
for _region, _names in REGIONS.items():
    for _name in _names:
        _COUNTRY_TO_REGIONS.setdefault(_name.lower(), []).append(_region)


def regions_for(country: str) -> list[str]:
    """Regional labels that include this country, e.g. Kenya -> ["Africa", "EMEA", "Sub-Saharan Africa"]."""
    key = (country or "").strip().lower()
    key = ALIASES.get(key, key)
    found = []
    for region in _COUNTRY_TO_REGIONS.get(key, []):
        found.append(region)
        found.extend(EXTRA.get(region, []))
    return list(dict.fromkeys(found))
