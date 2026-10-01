# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Law enforcement profile: free-text criminal charges to FBI NIBRS offense codes.

This is the default profile and the worked example from the accompanying blog
post. The input field name (charge_text) matches the post; outputs use the
generic code and description fields shared by every profile.
"""

from . import ClassificationProfile, ContextField

NIBRS_CODES = {
    "09A": "Murder & Nonnegligent Manslaughter",
    "09B": "Negligent Manslaughter",
    "100": "Kidnapping/Abduction",
    "11A": "Rape",
    "11B": "Sodomy",
    "11C": "Sexual Assault With An Object",
    "11D": "Fondling",
    "120": "Robbery",
    "13A": "Aggravated Assault",
    "13B": "Simple Assault",
    "13C": "Intimidation",
    "200": "Arson",
    "210": "Extortion/Blackmail",
    "220": "Burglary/Breaking & Entering",
    "23A": "Pocket-picking",
    "23B": "Purse-snatching",
    "23C": "Shoplifting",
    "23D": "Theft From Building",
    "23E": "Theft From Coin-Operated Machine",
    "23F": "Theft From Motor Vehicle",
    "23G": "Theft of Motor Vehicle Parts",
    "23H": "All Other Larceny",
    "240": "Motor Vehicle Theft",
    "250": "Counterfeiting/Forgery",
    "26A": "False Pretenses/Swindle",
    "26B": "Credit Card/ATM Fraud",
    "26C": "Impersonation",
    "26D": "Welfare Fraud",
    "26E": "Wire Fraud",
    "26F": "Identity Theft",
    "26G": "Hacking/Computer Invasion",
    "270": "Embezzlement",
    "280": "Stolen Property Offenses",
    "290": "Destruction/Damage/Vandalism",
    "35A": "Drug/Narcotic Violations",
    "35B": "Drug Equipment Violations",
    "36A": "Incest",
    "36B": "Statutory Rape",
    "370": "Pornography/Obscene Material",
    "40A": "Prostitution",
    "40B": "Assisting/Promoting Prostitution",
    "40C": "Purchasing Prostitution",
    "510": "Bribery",
    "520": "Weapon Law Violations",
    "90A": "Bad Checks",
    "90B": "Curfew/Loitering/Vagrancy",
    "90C": "Disorderly Conduct",
    "90D": "Driving Under the Influence",
    "90E": "Drunkenness",
    "90F": "Family Offenses, Nonviolent",
    "90G": "Liquor Law Violations",
    "90H": "Peeping Tom",
    "90I": "Runaway",
    "90J": "Trespass of Real Property",
    "90Z": "All Other Offenses"
}


PROFILE = ClassificationProfile(
    name='law_enforcement',
    title='Criminal charge to NIBRS offense code',
    taxonomy=NIBRS_CODES,
    fallback_code='90Z',
    primary_field='charge_text',
    context_fields=(
        ContextField('state'),
        ContextField('county'),
        # statute_code is accepted as an alias for charge_code.
        ContextField('charge_code', aliases=('statute_code',)),
        ContextField('offense_type'),
        ContextField('date_of_offense'),
    ),
    code_field='code',
    description_field='description',
    extra_output_fields={'statute_reference': 'State statute reference if found'},
    expert='an expert in criminal law and FBI NIBRS offense classification',
    item='criminal charge',
    taxonomy_label='NIBRS',
    research_steps=(
        'If a state and charge code are provided, search for the specific statute '
        '(e.g., "Texas Penal Code 49.04 statute definition")',
        'Search for NIBRS classification guidance (e.g., "DWI NIBRS code FBI classification")',
    ),
    guidance=(
        'Consider jurisdiction-specific terminology and abbreviations',
    ),
    data_tag='charge_data',
    search_tool_name='web_search_statute',
    search_tool_description=(
        'Search the web for statute and legal information. Use this tool to look up '
        'state-specific statutes, charge codes, and legal definitions from '
        'authoritative legal sources. Example query: "Texas Penal Code 49.04 DWI statute".'
    ),
)
