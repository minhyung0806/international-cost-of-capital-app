"""International cost of capital | Streamlit Community Cloud."""
import io
import re
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests
import streamlit as st

BETAS_URL = 'https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls'
RATINGS_URL = 'https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/ratings.html'
OECD_URL = 'https://www.oecd.org/en/publications/corporate-tax-statistics-2024_9c27d6e8-en/full-report/statutory-corporate-income-tax-rates_440f9286.html#chapter-d1e2342-08e74a33fb'
BONDS_URL = 'https://tradingeconomics.com/bonds'
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; CostOfCapitalResearch/1.0)', 'Accept-Language': 'en-US,en;q=0.9'}
DEFAULT_INDUSTRY = 'Software (System & Application)'
COUNTRIES = ['United States','Australia','Austria','Belgium','Brazil','Canada','Chile','China','Colombia','Czech Republic','Denmark','Finland','France','Germany','Greece','Hungary','India','Indonesia','Ireland','Israel','Italy','Japan','Malaysia','Mexico','Netherlands','New Zealand','Norway','Philippines','Poland','Portugal','Romania','Saudi Arabia','Singapore','South Africa','South Korea','Spain','Sweden','Switzerland','Thailand','Turkey','United Kingdom','Vietnam']
ALIASES = {'US':'United States','USA':'United States','United States of America':'United States','UK':'United Kingdom','Britain':'United Kingdom','Korea':'South Korea','Korea, Rep.':'South Korea','Korea (Republic of)':'South Korea','Republic of Korea':'South Korea','Türkiye':'Turkey','Turkiye':'Turkey','Czechia':'Czech Republic','Russian Federation':'Russia','Slovak Republic':'Slovakia','The Netherlands':'Netherlands'}

def normalize_country(s):
    s = re.sub(r'\s+', ' ', str(s)).strip()
    return ALIASES.get(s, s)

def fetch(url):
    r = requests.get(url.split('#')[0], headers=HEADERS, timeout=22)
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

@st.cache_data(ttl=3600, show_spinner=False)
def get_oecd_taxes():
    tables = table_rows(fetch(OECD_URL))
    best = {}
    for df in tables:
        df.columns = [' '.join(map(str,c)) if isinstance(c,tuple) else str(c) for c in df.columns]
        cols = list(df.columns)
        # Select latest explicit year; OECD 2024 source is historical, not necessarily current.
        year_cols = [(int(y),c) for c in cols for y in re.findall(r'20(?:1\d|2\d)',c)]
        if not year_cols: continue
        year, col = max(year_cols)
        country_col = next((c for c in cols if 'country' in c.lower() or 'jurisdiction' in c.lower() or 'economy' in c.lower()), cols[0])
        temp = {}
        for _,row in df.iterrows():
            country = normalize_country(row[country_col]); value = clean_number(row[col])
            if country in COUNTRIES and np.isfinite(value) and 0 <= value <= 60:
                temp[country] = value/100
        if len(temp)>len(best): best = temp; selected_year = year
    if not best: raise ValueError('OECD table could not be parsed reliably; enter tax rates manually.')
    return best, str(selected_year)

@st.cache_data(ttl=1800, show_spinner=False)
def get_bond_yields():
    tables = table_rows(fetch(BONDS_URL))
    found = {}
    for df in tables:
        df.columns = [' '.join(map(str,c)) if isinstance(c,tuple) else str(c) for c in df.columns]
        cols = list(df.columns)
        name_col = next((c for c in cols if 'country' in c.lower() or 'bond' in c.lower()), cols[0])
        yield_col = next((c for c in cols if c.lower().strip() in ('yield','last','price')), None)
        if yield_col is None: continue
        for _, row in df.iterrows():
            name = str(row[name_col]).strip()
            name = re.sub(r'\s+(?:10Y|10 Year|10-Year|Government Bond).*$', '', name, flags=re.I)
            name = normalize_country(name)
            val = clean_number(row[yield_col])
            if name in COUNTRIES and np.isfinite(val) and -5 < val < 50:
                found[name] = val/100  # Trading Economics bond table yields are quoted in percent.
    if not found: raise ValueError('No reliable country 10-year yields found; Trading Economics may block scraping.')
    return found

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

errors = {}
def load(label, func, default):
    try: return func()
    except Exception as exc:
        errors[label] = str(exc)
        return default

betas, beta_note = load('Damodaran industry betas',get_betas,({},'Unavailable'))
spread_data = load('Damodaran BBB spread',get_bbb_spread,(None,'Unavailable'))
taxes, tax_year = load('OECD statutory tax rates',get_oecd_taxes,({},'Unavailable'))
yields = load('Trading Economics 10-year yields',get_bond_yields,{})

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
    st.caption('Enable an override to replace an online value. No fabricated fallback data are used.')
    def override(label,auto,default=0.0,help_text=None):
        enabled = st.checkbox('Override '+label,key='enable_'+label)
        value = st.number_input(label, value=float(auto if auto is not None and np.isfinite(auto) else default),
                                step=0.1,format='%.3f',key='value_'+label,disabled=not enabled,help=help_text)
        return value,enabled
    bu_auto = betas.get(industry)
    bu_val,bu_on = override('Unlevered beta',bu_auto,1.0)
    tax_country = 'United States' if tax_basis=='United States' else country
    tax_auto = taxes.get(tax_country)
    tax_val,tax_on = override('Corporate tax rate (%)',tax_auto*100 if tax_auto is not None else None,25.0)
    spread_auto = spread_data[0]
    spread_val,spread_on = override('USD BBB borrowing spread (%)',spread_auto*100 if spread_auto is not None else None,1.5)
    us_auto = yields.get('United States')
    us_val,us_on = override('U.S. 10-year yield (%)',us_auto*100 if us_auto is not None else None,4.0)
    foreign_auto = yields.get(country)
    foreign_val,foreign_on = override('Selected-country 10-year yield (%)',foreign_auto*100 if foreign_auto is not None else None,4.0)
    foreign_override_on = st.checkbox('Override foreign-currency BBB spread',value=False)
    foreign_spread_val = st.number_input('Foreign BBB spread (%)',value=1.5,step=0.1,format='%.3f',disabled=not foreign_override_on)

beta_u = bu_val if bu_on else bu_auto
tax = tax_val/100 if tax_on else tax_auto
spread = spread_val/100 if spread_on else spread_auto
us_yield = us_val/100 if us_on else us_auto
foreign_yield = foreign_val/100 if foreign_on else foreign_auto
if country=='United States' and not foreign_on:
    foreign_yield = us_yield  # identical country should use identical yield unless explicitly overridden
foreign_spread_override = foreign_spread_val/100 if foreign_override_on else None

st.subheader('Data sources & availability')
source_rows = [
    ('Unlevered industry beta','Damodaran',BETAS_URL,'Available' if betas else 'Unavailable',beta_note),
    ('BBB borrowing spread','Damodaran',RATINGS_URL,'Available' if spread_data[0] is not None else 'Unavailable',spread_data[1]),
    ('Corporate income tax','OECD 2024 publication',OECD_URL,'Available' if taxes else 'Unavailable',f'Latest parsed table year: {tax_year}; not a live 2026 tax feed'),
    ('10-year sovereign yields','Trading Economics',BONDS_URL,'Available' if yields else 'Unavailable','Live webpage; scraping may be restricted or delayed')]
status_df = pd.DataFrame(source_rows,columns=['Data','Source','URL','Status','Data date / detail'])
st.dataframe(status_df[['Data','Source','Status','Data date / detail']],hide_index=True,use_container_width=True)
st.caption('Fetched or cache-checked at '+datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')+' (not necessarily the publication or market observation time). Source links: '+ ' · '.join(f'[{r[1]}]({r[2]})' for r in source_rows))
if errors:
    with st.expander('Source warnings (expand to troubleshoot)',expanded=True):
        for name,err in errors.items(): st.warning(f'{name}: {err}')

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

**Limitations:** A 10-year government yield is a nominal sovereign-yield proxy, not necessarily a default-free rate. Sovereign credit, inflation, liquidity and term-premium differences may affect comparisons. A BBB spread may vary by maturity, issuer and currency. OECD's linked 2024 publication is historical; confirm statutory rates for the actual investment year. This model excludes incremental country-risk premiums, withholding taxes and project-specific debt adjustments.
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
    'Calculated UTC':datetime.now(timezone.utc).isoformat()}])
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
