"""Venue eligibility review for the configured Pennsylvania/US spot bot.

Technical accessibility is not eligibility or legal clearance. Unknown venues
cannot receive new orders. Do not treat routing as a geographic workaround.
"""
REVIEWS={
 'pumpswap':{'new_entries':True,'status':'spot terms reviewed; US/PA not listed in published prohibited jurisdictions',
             'source':'https://pump.fun/docs/terms-and-conditions','kyc':'wallet screening may apply; identity verification may be requested'},
 'jupiter':{'new_entries':False,'status':'published terms exclude US wallets; API scope not independently cleared',
            'source':'https://developers.jup.ag/docs/legal/terms-of-use'},
 'raydium':{'new_entries':False,'status':'published disclaimer excludes US residents',
            'source':'https://v2.raydium.io/docs/disclaimer/'},
 'orca':{'new_entries':True,'status':'spot terms reviewed: no explicit US/PA exclusion found; direct SOL/USDC exact-output adapter and combined simulation validated','source':'https://www.orca.so/terms-of-use',
         'keyless_sdk':True,'terms_reviewed':'2026-10-28'},
 'meteora':{'new_entries':False,'status':'current primary eligibility terms not verified','source':'https://www.meteora.ag/'},
 'manifest':{'new_entries':False,'status':'candidate only; no PA-specific eligibility confirmation or audited execution adapter',
             'source':'https://app.manifest.trade/'},
}


def entry_allowed(venue):
    return REVIEWS.get(venue,{}).get('new_entries',False)
