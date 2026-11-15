"""International cost of capital | Streamlit Community Cloud."""
import io
import json
import os
import time
from pathlib import Path
from xml.etree import ElementTree as ET
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd
import requests
import streamlit as st

BETAS_URL = 'https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls'
RATINGS_URL = 'https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/ratings.html'
OECD_TAX_URL = 'https://sdmx.oecd.org/public/rest/data/OECD.CTP.TPS,DSD_TAX_CIT@DF_CIT,2.0/.A..ST..S13+S1311+S13M...?startPeriod=2023&format=csvfile'
OECD_YIELDS_URL = 'https://sdmx.oecd.org/public/rest/data/OECD.SDD.STES,DSD_STES@DF_FINMARK,4.0/.M.IRLT.PA.....?startPeriod=2025-01&format=csvfile'
TREASURY_URL = 'https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve&page&_format=csv'
TREASURY_XML_URL = 'https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?data=daily_treasury_yield_curve&field_tdr_date_value_month={yyyymm}'
# Editable placeholders are NOT market observations. They are used only if fetching fails.
PLACEHOLDER_TAX_PCT = 25.0
PLACEHOLDER_YIELD_PCT = 4.0
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; CostOfCapitalResearch/1.0)', 'Accept-Language': 'en-US,en;q=0.9'}
DEFAULT_INDUSTRY = 'Software (System & Application)'
COUNTRIES = ['United States','Australia','Austria','Belgium','Brazil','Canada','Chile','China','Colombia','Czech Republic','Denmark','Finland','France','Germany','Greece','Hungary','India','Indonesia','Ireland','Israel','Italy','Japan','Malaysia','Mexico','Netherlands','New Zealand','Norway','Philippines','Poland','Portugal','Romania','Saudi Arabia','Singapore','South Africa','South Korea','Spain','Sweden','Switzerland','Thailand','Turkey','United Kingdom','Vietnam']
ALIASES = {'US':'United States','USA':'United States','United States of America':'United States','UK':'United Kingdom','Britain':'United Kingdom','Korea':'South Korea','Korea, Rep.':'South Korea','Korea (Republic of)':'South Korea','Republic of Korea':'South Korea','Türkiye':'Turkey','Turkiye':'Turkey','Czechia':'Czech Republic','Russian Federation':'Russia','Slovak Republic':'Slovakia','The Netherlands':'Netherlands'}

ISO_COUNTRY = {
 'AUS':'Australia','AUT':'Austria','BEL':'Belgium','BRA':'Brazil','CAN':'Canada',
 'CHL':'Chile','CHN':'China','COL':'Colombia','CZE':'Czech Republic',
 'DNK':'Denmark','FIN':'Finland','FRA':'France','DEU':'Germany','GRC':'Greece',
 'HUN':'Hungary','IND':'India','IDN':'Indonesia','IRL':'Ireland','ISR':'Israel',
 'ITA':'Italy','JPN':'Japan','MYS':'Malaysia','MEX':'Mexico','NLD':'Netherlands',
 'NZL':'New Zealand','NOR':'Norway','PHL':'Philippines','POL':'Poland',
 'PRT':'Portugal','ROU':'Romania','SAU':'Saudi Arabia','SGP':'Singapore',
 'ZAF':'South Africa','KOR':'South Korea','ESP':'Spain','SWE':'Sweden',
 'CHE':'Switzerland','THA':'Thailand','TUR':'Turkey','GBR':'United Kingdom',
 'USA':'United States','VNM':'Vietnam',
}
SOURCE_TTL = 3600
SNAPSHOT_FILE = Path(os.getenv('COC_SNAPSHOT_PATH', '/tmp/international_coc_verified_snapshot_v4.json'))

def _snapshot_read():
    try:
        raw=json.loads(SNAPSHOT_FILE.read_text())
        return raw if isinstance(raw,dict) else {}
    except (OSError, ValueError):
        return {}

def _snapshot_save(kind, value):
    try:
        data=_snapshot_read()
        data[kind]=value
        SNAPSHOT_FILE.parent.mkdir(parents=True,exist_ok=True)
        temp=SNAPSHOT_FILE.with_suffix('.tmp')
        temp.write_text(json.dumps(data))
        temp.replace(SNAPSHOT_FILE)
    except (OSError, TypeError, ValueError):
        pass  # App remains usable if disk is read-only.

def resilient(kind, call, empty):
    try:
        value=call()
        _snapshot_save(kind,value)
        return value, 'Live fetch', None
    except Exception as exc:
        cached=_snapshot_read().get(kind)
        if cached is not None:
            return cached, 'Previously verified local cache', str(exc)
        return empty, 'Unavailable', str(exc)

def fetch_csv(url):
    data=fetch(url)
    df=pd.read_csv(io.BytesIO(data),low_memory=False)
    if df.empty or 'OBS_VALUE' not in df and 'Date' not in df.columns:
        raise ValueError('No recognizable data columns returned from the official endpoint')
    return df

def oecd_records(df, country_col='REF_AREA'):
    for field in (country_col,'TIME_PERIOD','OBS_VALUE'):
        if field not in df.columns:
            raise ValueError('Missing OECD SDMX column: '+field)
    t=df.copy()
    t['COUNTRY_NAME']=t[country_col].map(ISO_COUNTRY)
    t=t[t['COUNTRY_NAME'].notna()]
    t['OBS_VALUE']=pd.to_numeric(t['OBS_VALUE'],errors='coerce')
    t=t[t['OBS_VALUE'].notna()]
    t=t.sort_values('TIME_PERIOD')
    return t

def normalize_country(s):
    s = re.sub(r'\s+', ' ', str(s)).strip()
    return ISO_COUNTRY.get(s, ALIASES.get(s, s))

def fetch(url):
    r = requests.get(url.split('#')[0], headers=HEADERS, timeout=(5, 12))
    r.raise_for_status()
    return r.content

def clean_number(x):
    if pd.isna(x): return np.nan
    s = str(x).strip().replace(',', '').replace('%', '').replace('−', '-')
    if s in ('','-','—','..','n.a.','nan','None'): return np.nan
    m = re.search(r'-?\d+(?:\.\d+)?', s)
    return float(m.group()) if m else np.nan

def as_rate(x, percent_likely=False):
    v = clean_number(x)
    if not np.isfinite(v): return np.nan
    return v/100 if percent_likely or abs(v)>1 else v

def table_rows(html):
    return pd.read_html(io.BytesIO(html), displayed_only=False)

@st.cache_data(ttl=3600, show_spinner=False)
def get_betas():
    raw = fetch(BETAS_URL)
    xls = pd.ExcelFile(io.BytesIO(raw), engine='xlrd')
    for sheet in xls.sheet_names:
        df = pd.read_excel(xls, sheet_name=sheet, header=None)
        for idx in range(min(len(df), 25)):
            row = [str(v).strip().lower() for v in df.iloc[idx].tolist()]
            industry_col = next((i for i,v in enumerate(row) if v in ('industry name','industry')), None)
            beta_col = next((i for i,v in enumerate(row) if 'unlevered beta' in v and 'cash' not in v), None)
            if industry_col is None or beta_col is None: continue
            out = {}
            for j in range(idx+1,len(df)):
                name = str(df.iat[j,industry_col]).strip()
                beta = clean_number(df.iat[j,beta_col])
                if name and name.lower() not in ('nan','total market','total') and np.isfinite(beta) and 0 <= beta < 10:
                    out[name] = beta
            if out: return out, f'{sheet}; header row {idx+1}'
    raise ValueError('Could not identify industry and unlevered-beta columns in the downloaded workbook.')

@st.cache_data(ttl=3600, show_spinner=False)
def get_bbb_spread():
    tables = table_rows(fetch(RATINGS_URL))
    for df in tables:
        if df.shape[1]<2: continue
        flat = df.astype(str)
        for i in range(len(flat)):
            vals = [str(v).strip() for v in flat.iloc[i]]
            if any(re.search(r'(?<!\w)(?:Baa2|BBB)(?![+\-\w])', v, re.I) for v in vals):
                # Damodaran's ratings table typically has: interest coverage, rating, spread.
                hits = [j for j,v in enumerate(vals) if re.search(r'(?<!\w)(?:Baa2|BBB)(?![+\-\w])',v,re.I)]
                for j in hits:
                    for k in range(j+1, min(len(vals),j+4)):
                        raw = vals[k]
                        val = clean_number(raw)
                        if np.isfinite(val) and 0 < val < 20:
                            return val/100 if '%' in raw or val >= 1 else val, 'Baa2/BBB row; spread column after rating'
    raise ValueError('Could not safely locate the Baa2/BBB spread in the ratings table.')

@st.cache_data(ttl=SOURCE_TTL, show_spinner=False)
def get_oecd_taxes():
    """Latest available OECD main statutory combined CIT rate, annual percent.

    SDMX dimensions: geography.A.<other>.ST.<other>.S13/S1311/S13M.
    Explicitly require the statutory-rate code ST and general-government coverage;
    never use other corporate tax measures as proxies.
    """
    df = fetch_csv(OECD_TAX_URL)
    for field, target in [('FREQ','A'),('MEASURE','ST')]:
        if field in df.columns:
            df=df[df[field].astype(str).eq(target)]
    if 'SECTOR' in df.columns:
        df=df[df['SECTOR'].isin(['S13','S1311','S13M'])]
    t=oecd_records(df)
    rates, dates={},{}
    for name, subset in t.groupby('COUNTRY_NAME'):
        # Prefer general government for combined statutory tax, if available.
        if 'SECTOR' in subset.columns:
            for sector in ['S13','S13M','S1311']:
                cand=subset[subset['SECTOR']==sector]
                if not cand.empty:
                    subset=cand
                    break
        row=subset.iloc[-1]
        rate=float(row['OBS_VALUE'])
        if 0<=rate<=65:
            rates[name]=rate/100
            dates[name]=str(row['TIME_PERIOD'])
    if not rates:
        raise ValueError('OECD API returned no recognized statutory CIT observations; check API dimension layout.')
    return rates, dates

@st.cache_data(ttl=SOURCE_TTL, show_spinner=False)
def get_oecd_yields():
    """OECD 10-year long-term government yield, monthly percent per annum.
    OECD defines IRLT as 10-year government bonds. Monthly averages, not live ticks.
    """
    df=fetch_csv(OECD_YIELDS_URL)
    for field, value in [('FREQ','M'),('MEASURE','IRLT'),('UNIT_MEASURE','PA')]:
        if field in df.columns:
            df=df[df[field].astype(str).eq(value)]
    t=oecd_records(df)
    rates,dates={},{}
    for name, subset in t.groupby('COUNTRY_NAME'):
        row=subset.iloc[-1]
        rate=float(row['OBS_VALUE'])
        if -10 < rate < 70:
            rates[name]=rate/100
            dates[name]=str(row['TIME_PERIOD'])
    if not rates:
        raise ValueError('OECD API returned no recognized monthly IRLT bond observations.')
    return rates,dates

@st.cache_data(ttl=SOURCE_TTL, show_spinner=False)
def get_treasury_us_yield():
    """Latest U.S. 10-year nominal par Treasury yield (daily). CSV then XML."""
    year=datetime.now(timezone.utc).year
    failures=[]
    for y in (year,year-1):
        try:
            r=fetch(TREASURY_URL.format(year=y))
            df=pd.read_csv(io.BytesIO(r),low_memory=False)
            date_col=next((c for c in df if str(c).strip().lower()=='date'),None)
            yield_col=next((c for c in df if re.fullmatch(r'10\s*(?:yr|year)',str(c).strip(),re.I)),None)
            if not date_col or not yield_col:
                raise ValueError('Treasury CSV missing Date or 10 Yr column')
            df['PARSED_DATE']=pd.to_datetime(df[date_col],errors='coerce')
            df['YIELD']=pd.to_numeric(df[yield_col],errors='coerce')
            df=df.dropna(subset=['PARSED_DATE','YIELD']).sort_values('PARSED_DATE')
            if not df.empty:
                row=df.iloc[-1]
                return float(row['YIELD'])/100,str(row['PARSED_DATE'].date())
        except Exception as exc:
            failures.append(f'CSV {y}: {exc}')
    # Official XML feed as alternate representation.
    for offset in (0,1):
        month=(datetime.now(timezone.utc).replace(day=1)-timedelta(days=offset*2)).strftime('%Y%m')
        try:
            root=ET.fromstring(fetch(TREASURY_XML_URL.format(yyyymm=month)))
            candidates=[]
            for entry in root.iter():
                if entry.tag.split('}')[-1] != 'entry': continue
                fields={node.tag.split('}')[-1]: (node.text or '') for node in entry.iter()}
                date=fields.get('NEW_DATE') or fields.get('DATE')
                val=fields.get('BC_10YEAR')
                if date and val:
                    candidates.append((date,float(val)))
            if candidates:
                date,yield_pct=max(candidates,key=lambda x:x[0])
                return yield_pct/100,date[:10]
        except Exception as exc:
            failures.append(f'XML {month}: {exc}')
    raise ValueError('U.S. Treasury CSV and XML failed: '+' | '.join(failures)[:500])

def currency_convert(rate, us_yield, foreign_yield):
    return (1+rate)*(1+foreign_yield)/(1+us_yield)-1

def compute(beta_u, tax, debt, erp, us_yield, foreign_yield, spread_us, foreign_spread_override=None):
    de = debt/(1-debt) if debt < 1 else np.inf
    beta_l = beta_u*(1+(1-tax)*de)
    ke_us = us_yield+beta_l*erp
    kd_us = us_yield+spread_us
    ke_foreign = currency_convert(ke_us,us_yield,foreign_yield)
    # Default: convert total USD borrowing cost; override: foreign government yield + local spread.
    kd_foreign = (foreign_yield+foreign_spread_override if foreign_spread_override is not None
                  else currency_convert(kd_us,us_yield,foreign_yield))
    wacc_us = (1-debt)*ke_us+debt*kd_us*(1-tax)
    wacc_foreign = (1-debt)*ke_foreign+debt*kd_foreign*(1-tax)
    spread_foreign = kd_foreign-foreign_yield
    return dict(beta_l=beta_l, ke_us=ke_us, kd_us=kd_us, wacc_us=wacc_us,
                ke_foreign=ke_foreign, kd_foreign=kd_foreign, wacc_foreign=wacc_foreign,
                spread_foreign=spread_foreign)

st.set_page_config(page_title='International Cost of Capital', page_icon='🌐', layout='wide')
st.title('🌐 International Cost of Capital')
st.caption('Compare the same project in USD and a selected country’s currency. All inputs are annual nominal rates.')
if st.button('↻ Refresh source data', help='Clear cached web data and fetch sources again'):
    st.cache_data.clear()
    st.rerun()

# Network fetching is cached including failures so widget tweaks do not refetch.
# Sidebar is rendered after at most one concurrent fetch window.
SOURCE_FUNCS = {
 'Industry betas': (get_betas, ({}, 'Unavailable')),
 'USD BBB spread': (get_bbb_spread, (None, 'Unavailable')),
 'OECD CIT': (get_oecd_taxes, ({}, {})),
 'OECD monthly 10Y': (get_oecd_yields, ({}, {})),
 'U.S. Treasury daily 10Y': (get_treasury_us_yield, (None, 'Unavailable')),
}
@st.cache_data(ttl=1800,show_spinner=False)
def load_sources():
    result={}
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures={pool.submit(resilient, label, func, fallback):label for label,(func,fallback) in SOURCE_FUNCS.items()}
        for future in as_completed(futures):
            result[futures[future]]=future.result()
    return result

with st.spinner('Checking official data feeds…'):
    loaded=load_sources()
errors={label:entry[2] for label,entry in loaded.items() if entry[2]}
source_status={label:entry[1] for label,entry in loaded.items()}
betas,beta_note=loaded['Industry betas'][0]
spread_data=loaded['USD BBB spread'][0]
taxes,tax_dates=loaded['OECD CIT'][0]
monthly_yields,monthly_dates=loaded['OECD monthly 10Y'][0]
treasury_yield,treasury_date=loaded['U.S. Treasury daily 10Y'][0]
yields=dict(monthly_yields)
yield_dates=dict(monthly_dates)
if treasury_yield is not None:
    yields['United States']=treasury_yield
    yield_dates['United States']=treasury_date

with st.sidebar:
    st.header('Project settings')
    industries = sorted(set(betas)|{DEFAULT_INDUSTRY})
    industry = st.selectbox('Industry',industries,index=industries.index(DEFAULT_INDUSTRY))
    country = st.selectbox('Target country',COUNTRIES,index=COUNTRIES.index('United States'))
    debt = st.slider('Target debt financing (%)',0,95,30,1)/100
    erp = st.number_input('Equity risk premium (%)',min_value=0.0,max_value=30.0,value=6.5,step=0.1)/100
    tax_basis = st.radio('Project tax rate', ['United States','Selected country'],horizontal=True)
    st.divider()
    st.subheader('Manual overrides')
    st.caption('If a source is unavailable, editable illustrative inputs are activated automatically. They are NOT live data; verify before relying on results.')
    def override(label,auto,default=0.0,help_text=None):
        unavailable = auto is None or not np.isfinite(auto)
        enabled = st.checkbox('Override '+label,key='enable_'+label + ('_'+tax_country if label=='Corporate tax rate (%)' else '_'+country if label=='Selected-country 10-year yield (%)' else ''), value=unavailable, disabled=unavailable, help='Required because automatic data are unavailable' if unavailable else None)
        value = st.number_input(label, value=float(auto if auto is not None and np.isfinite(auto) else default),
                                step=0.1,format='%.3f',key='value_'+label + ('_'+tax_country if label=='Corporate tax rate (%)' else '_'+country if label=='Selected-country 10-year yield (%)' else ''),disabled=not enabled,help=help_text)
        return value,enabled
    bu_auto = betas.get(industry)
    bu_val,bu_on = override('Unlevered beta',bu_auto,1.0)
    tax_country = 'United States' if tax_basis=='United States' else country
    tax_auto = taxes.get(tax_country)
    tax_val,tax_on = override('Corporate tax rate (%)',tax_auto*100 if tax_auto is not None else None,PLACEHOLDER_TAX_PCT)
    spread_auto = spread_data[0]
    spread_val,spread_on = override('USD BBB borrowing spread (%)',spread_auto*100 if spread_auto is not None else None,1.5)
    us_auto = yields.get('United States')
    us_val,us_on = override('U.S. 10-year yield (%)',us_auto*100 if us_auto is not None else None,PLACEHOLDER_YIELD_PCT)
    foreign_auto = yields.get(country)
    foreign_val,foreign_on = override('Selected-country 10-year yield (%)',foreign_auto*100 if foreign_auto is not None else None,us_val if country == 'United States' else PLACEHOLDER_YIELD_PCT)
    foreign_override_on = st.checkbox('Override foreign-currency BBB spread',value=False)
    foreign_spread_val = st.number_input('Foreign BBB spread (%)',value=1.5,step=0.1,format='%.3f',disabled=not foreign_override_on)

beta_u = bu_val if bu_on else bu_auto
tax = tax_val/100 if tax_on else tax_auto
spread = spread_val/100 if spread_on else spread_auto
us_yield = us_val/100 if us_on else us_auto
foreign_yield = foreign_val/100 if foreign_on else foreign_auto
if country == 'United States':
    # Selecting the same currency must always give the same government yield.
    # A foreign-spread override is still allowed as an explicit credit adjustment.
    foreign_yield = us_yield
foreign_spread_override = foreign_spread_val/100 if foreign_override_on else None

st.subheader('Data sources & availability')
source_rows = [
 ('Industry beta','Damodaran',BETAS_URL,source_status['Industry betas'],beta_note),
 ('USD BBB spread','Damodaran',RATINGS_URL,source_status['USD BBB spread'],spread_data[1]),
 ('Corporate income tax','OECD SDMX CIT',OECD_TAX_URL,source_status['OECD CIT'],f'{tax_country}: {tax_dates.get(tax_country,"not available")}'),
 ('US 10-year yield','U.S. Treasury',TREASURY_URL.format(year=datetime.now(timezone.utc).year),source_status['U.S. Treasury daily 10Y'],f'Daily observation: {treasury_date}'),
 ('Other-country 10-year yields','OECD SDMX IRLT',OECD_YIELDS_URL,source_status['OECD monthly 10Y'],f'{country}: {monthly_dates.get(country,"not available")} (monthly average)')]
status_df = pd.DataFrame(source_rows,columns=['Data','Source','URL','Retrieval','Data date / detail'])
st.dataframe(status_df[['Data','Source','Retrieval','Data date / detail']],hide_index=True,use_container_width=True)
st.caption('Checked at '+datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')+'. Observation dates above are different from retrieval time. Source links: '+ ' · '.join(f'[{r[1]}]({r[2]})' for r in source_rows))
if country != 'United States':
    st.info('U.S. Treasury data are daily observations; foreign government yields from OECD are monthly averages. Their observation dates may not match. For same-day comparisons, override the yields with verified observations from the same date.')
fallbacks=[]
if bu_on and bu_auto is None: fallbacks.append('Unlevered beta')
if spread_on and spread_auto is None: fallbacks.append('USD BBB spread')
if tax_on and tax_auto is None: fallbacks.append('Corporate tax rate')
if us_on and us_auto is None: fallbacks.append('U.S. 10-year yield')
if foreign_on and foreign_auto is None and country != 'United States': fallbacks.append('Foreign 10-year yield')
if fallbacks:
    st.warning('Illustrative, NOT verified market inputs: '+', '.join(fallbacks)+'. Verify or edit these values before relying on results.')
if errors:
    with st.expander('Source diagnostics (failed refreshes)', expanded=False):
        for name,err in errors.items():
            st.warning(f'{name} ({source_status[name]}): {err}')

missing = [name for name,val in [('Unlevered beta',beta_u),('Corporate tax rate',tax),('USD BBB spread',spread),('U.S. 10-year yield',us_yield),('Selected-country 10-year yield',foreign_yield)] if val is None or not np.isfinite(val)]
if missing:
    st.error('Calculation needs inputs: '+', '.join(missing)+'. Enable the corresponding manual overrides in the sidebar.')
    st.stop()

res = compute(beta_u,tax,debt,erp,us_yield,foreign_yield,spread,foreign_spread_override)
fmt = lambda x:f'{x*100:.2f}%'
st.subheader(f'Project results: USD vs {country}')
left,right=st.columns(2)
for col,title,suffix in [(left,'USD','us'),(right,country,'foreign')]:
    with col:
        st.markdown(f'#### {title}')
        st.metric('Cost of equity',fmt(res['ke_'+suffix]))
        st.metric('Pre-tax cost of debt',fmt(res['kd_'+suffix]))
        st.metric('WACC',fmt(res['wacc_'+suffix]))
        if suffix=='foreign': st.caption('Implied foreign BBB spread: '+fmt(res['spread_foreign']))
st.caption(f'Unlevered beta: {beta_u:.3f} · Relevered beta: {res["beta_l"]:.3f} · Project tax rate ({tax_country}): {fmt(tax)} · Debt share: {fmt(debt)} · ERP: {fmt(erp)}')

with st.expander('Methodology & assumptions',expanded=True):
    st.markdown(r'''
**Relevered beta:** $\beta_L=\beta_U[1+(1-T)D/E]$, where $D/E=w_D/(1-w_D)$.

**USD equity:** $K_{e,USD}=Y_{US}+\beta_L\times ERP$.

**USD debt:** $K_{d,USD}=Y_{US}+s_{BBB,USD}$.

**Currency conversion:** $1+K_{foreign}=(1+K_{USD})(1+Y_{foreign})/(1+Y_{US})$; applied separately to equity and debt by default. This is a nominal government-yield-ratio approximation, **not** a separate country-risk premium or a forecast of actual FX returns.

**Foreign debt override:** when enabled, $K_{d,foreign}=Y_{foreign}+s_{BBB,foreign}$ instead of the converted USD debt cost. This intentionally breaks the simple currency-conversion equality for debt.

**WACC:** $WACC=(1-w_D)K_e+w_D K_d(1-T)$. The **same selected project tax rate**, debt weight, ERP and underlying business beta are used in both currencies.

**Data frequency:** U.S. Treasury yields are daily; OECD IRLT yields are monthly averages. OECD statutory rates are annual. Compare dates before drawing currency conclusions.

**Limitations:** A 10-year government yield is a nominal sovereign-yield proxy, not necessarily a default-free rate. Sovereign credit, inflation, liquidity and term-premium differences may affect comparisons. A BBB spread may vary by maturity, issuer and currency. OECD's official SDMX data may lag the current investment year; confirm the actual project tax treatment. This model excludes incremental country-risk premiums, withholding taxes and project-specific debt adjustments.
''')

st.subheader('All-industry comparison')
if betas:
    records=[]
    for name,beta in sorted(betas.items()):
        x=compute(beta,tax,debt,erp,us_yield,foreign_yield,spread,foreign_spread_override)
        records.append({'Industry':name,'Unlevered beta':beta,'Relevered beta':x['beta_l'],
                        'USD cost of equity (%)':100*x['ke_us'],'Foreign cost of equity (%)':100*x['ke_foreign'],
                        'USD cost of debt (%)':100*x['kd_us'],'Foreign cost of debt (%)':100*x['kd_foreign'],
                        'USD WACC (%)':100*x['wacc_us'],'Foreign WACC (%)':100*x['wacc_foreign']})
    industry_df=pd.DataFrame(records)
    st.dataframe(industry_df,hide_index=True,use_container_width=True)
    st.download_button('Download all industries (CSV)',industry_df.to_csv(index=False).encode('utf-8-sig'),'international_cost_of_capital_all_industries.csv','text/csv')
else:
    st.info('All-industry comparison requires the Damodaran industry-beta download. The selected project can still be calculated with a manual beta.')

result_df=pd.DataFrame([{'Industry':industry,'Country':country,'Tax basis':tax_country,'Unlevered beta':beta_u,
    'Relevered beta':res['beta_l'],'Project tax (%)':tax*100,'Debt share (%)':debt*100,'ERP (%)':erp*100,
    'US 10Y (%)':us_yield*100,'Foreign 10Y (%)':foreign_yield*100,'USD BBB spread (%)':spread*100,
    'Foreign BBB spread (%)':res['spread_foreign']*100,'Foreign spread overridden':foreign_override_on,
    'USD cost of equity (%)':res['ke_us']*100,'Foreign cost of equity (%)':res['ke_foreign']*100,
    'USD cost of debt (%)':res['kd_us']*100,'Foreign cost of debt (%)':res['kd_foreign']*100,
    'USD WACC (%)':res['wacc_us']*100,'Foreign WACC (%)':res['wacc_foreign']*100,
    'Illustrative fallback inputs':'; '.join(fallbacks),'Calculated UTC':datetime.now(timezone.utc).isoformat()}])
st.download_button('Download selected-project results (CSV)',result_df.to_csv(index=False).encode('utf-8-sig'),'international_cost_of_capital_project.csv','text/csv')

with st.expander('Model validation checks'):
    tol=1e-10
    equal=compute(beta_u,tax,debt,erp,us_yield,us_yield,spread)
    zero=compute(beta_u,tax,0,erp,us_yield,foreign_yield,spread)
    override=compute(beta_u,tax,debt,erp,us_yield,foreign_yield,spread,0.03)
    checks={
        'Equal yields imply equal USD/foreign outputs (no foreign spread override)':all(abs(equal['%s_us'%k]-equal['%s_foreign'%k])<tol for k in ('ke','kd','wacc')),
        'Zero debt implies WACC equals cost of equity (both currencies)':abs(zero['wacc_us']-zero['ke_us'])<tol and abs(zero['wacc_foreign']-zero['ke_foreign'])<tol,
        'Foreign spread override does not change USD results':all(abs(res[k]-override[k])<tol for k in ('ke_us','kd_us','wacc_us')),
    }
    for label,ok in checks.items(): st.write(('✅' if ok else '❌')+' '+label)
