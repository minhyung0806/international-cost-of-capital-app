"""International Cost of Capital — FINC 470, Rebelo lecture pp. 22–29."""
import io
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import numpy as np
import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="International Cost of Capital", page_icon="🌐", layout="wide")
BETAS_URL = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls"
RATINGS_URL = "https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/ratings.html"
OECD_URL = "https://www.oecd.org/en/publications/corporate-tax-statistics-2024_9c27d6e8-en/full-report/statutory-corporate-income-tax-rates_440f9286.html#chapter-d1e2342-08e74a33fb"
OECD_CURRENT = "https://www.oecd.org/en/topics/sub-issues/corporate-tax-statistics.html"
BONDS_URL = "https://tradingeconomics.com/bonds"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; educational-finance-app/1.0)", "Accept": "text/html,application/xhtml+xml,*/*"}
TIMEOUT = 22
DEFAULT_INDUSTRY = "Software (System & Application)"
COUNTRY_ALIASES = {"US":"United States","USA":"United States","UK":"United Kingdom","South Korea":"South Korea","Korea":"South Korea","Republic of Korea":"South Korea","Czech Republic":"Czechia","Turkey":"Türkiye","Turkiye":"Türkiye","UAE":"United Arab Emirates","Hong Kong SAR":"Hong Kong","Russian Federation":"Russia"}

def normalize_country(name):
    x = re.sub(r"\s+", " ", str(name)).strip()
    return COUNTRY_ALIASES.get(x, x)

def numeric(value):
    if pd.isna(value): return np.nan
    x = str(value).replace(",", "").replace("%", "").strip()
    x = re.sub(r"[^0-9.\-]", "", x)
    try: return float(x)
    except (ValueError, TypeError): return np.nan

def get(url):
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r

def parse_year(text):
    years = re.findall(r"\b20(?:2[0-9]|3[0-9])\b", str(text))
    return max(years) if years else "Not stated"

def fetch_betas():
    r = get(BETAS_URL)
    book = pd.ExcelFile(io.BytesIO(r.content))
    for sheet in book.sheet_names:
        raw = pd.read_excel(book, sheet_name=sheet, header=None)
        for idx in range(min(len(raw), 30)):
            header = [str(x).strip() for x in raw.iloc[idx]]
            if not any("industry" in x.lower() for x in header): continue
            beta_cols = [j for j,x in enumerate(header) if "unlevered beta" in x.lower() and "cash" not in x.lower()]
            if not beta_cols: continue
            industry_col = next(j for j,x in enumerate(header) if "industry" in x.lower())
            frame = raw.iloc[idx+1:,[industry_col,beta_cols[0]]].copy()
            frame.columns = ["Industry", "Unlevered beta"]
            frame["Industry"] = frame["Industry"].astype(str).str.strip()
            frame["Unlevered beta"] = frame["Unlevered beta"].map(numeric)
            frame = frame.dropna(subset=["Unlevered beta"])
            frame = frame[~frame["Industry"].str.contains("total market|all firms|nan",case=False,regex=True)]
            frame = frame[(frame["Unlevered beta"] >= 0) & (frame["Unlevered beta"] < 10)]
            if len(frame)>10:
                return frame.drop_duplicates("Industry").sort_values("Industry").reset_index(drop=True), f"Workbook: {sheet}; {parse_year(raw.head(12).to_string())} (if identified)"
    raise ValueError("Could not identify the unlevered beta column in Damodaran's workbook")

def fetch_spread():
    r = get(RATINGS_URL)
    tables = pd.read_html(io.StringIO(r.text))
    for tab in tables:
        t = tab.copy()
        t.columns = [" ".join(map(str,c)) if isinstance(c,tuple) else str(c) for c in t.columns]
        mask = t.astype(str).apply(lambda c:c.str.contains(r"Baa2\s*/\s*BBB|Baa2|BBB",case=False,regex=True)).any(axis=1)
        for _,row in t[mask].iterrows():
            cells = [str(v) for v in row.tolist()]
            if not any(re.search(r"Baa2|BBB",v,re.I) for v in cells):continue
            # Damodaran's large-firm ratings table generally has a single spread column.
            cols = [(c,v) for c,v in zip(t.columns,row) if re.search("spread",c,re.I)]
            if cols:
                spread = numeric(cols[0][1]);
                if np.isfinite(spread): return spread, parse_year(r.text[:30000])
            ix = next(i for i,v in enumerate(cells) if re.search(r"Baa2|BBB",v,re.I))
            candidates = [numeric(v) for v in cells[ix+1:]]
            candidates = [v for v in candidates if np.isfinite(v) and 0 < v < 20]
            if candidates: return candidates[-1], parse_year(r.text[:30000])
    raise ValueError("BBB spread not identifiable; enter it manually")

def fetch_taxes():
    # OECD's historical article may be older than the latest release. Parse only tables
    # whose headings explicitly identify combined statutory corporate tax rates.
    errors=[]
    for url in [OECD_CURRENT, OECD_URL.split('#')[0]]:
        try:
            r=get(url)
            tables=pd.read_html(io.StringIO(r.text))
            for t in tables:
                cols=[str(c).lower() for c in t.columns]
                if not any("country" in c or "jurisdiction" in c or "economy" in c for c in cols): continue
                rate_cols=[i for i,c in enumerate(cols) if ("combined" in c or "statutory" in c) and ("rate" in c or "202" in c)]
                if not rate_cols: continue
                country_col=next(i for i,c in enumerate(cols) if "country" in c or "jurisdiction" in c or "economy" in c)
                rc=rate_cols[-1]
                df=pd.DataFrame({"Country":t.iloc[:,country_col].map(normalize_country),"Tax rate (%)":t.iloc[:,rc].map(numeric)})
                df=df.dropna(subset=["Tax rate (%)"])
                df=df[(df["Tax rate (%)"]>=0)&(df["Tax rate (%)"]<=60)]
                if len(df)>=15: return df.drop_duplicates("Country"), url, parse_year(r.text[:50000])
        except Exception as e: errors.append(str(e))
    raise ValueError("OECD table could not be parsed reliably. Enter tax rates manually; no values were invented.")

def fetch_yields():
    r=get(BONDS_URL)
    tables=pd.read_html(io.StringIO(r.text))
    for t in tables:
        names=[str(c).lower() for c in t.columns]
        country_cols=[i for i,c in enumerate(names) if "country" in c or "bond" in c]
        yield_cols=[i for i,c in enumerate(names) if "yield" in c]
        if not country_cols or not yield_cols: continue
        rows=[]
        for _,row in t.iterrows():
            raw=str(row.iloc[country_cols[0]])
            country=re.sub(r"\s*(?:10Y|10-Year|10 Year|Government Bond).*", "", raw, flags=re.I).strip()
            country=normalize_country(country)
            val=numeric(row.iloc[yield_cols[0]])
            if country and np.isfinite(val) and -5<val<50: rows.append((country,val))
        if len(rows)>=10:
            df=pd.DataFrame(rows,columns=["Country","10Y yield (%)"]).drop_duplicates("Country")
            return df, "Trading Economics live page; retrieved " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    raise ValueError("Trading Economics yield table could not be parsed. Enter yields manually.")

@st.cache_data(ttl=3600,show_spinner=False)
def load_data():
    out={}
    for key,fn in [("betas",fetch_betas),("spread",fetch_spread),("taxes",fetch_taxes),("yields",fetch_yields)]:
        try: out[key]={"value":fn(),"error":None}
        except Exception as e: out[key]={"value":None,"error":str(e)}
    out["retrieved"]=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return out

def calc(beta_u, tax, debt, erp, usd_yield, foreign_yield, usd_spread, foreign_spread=None):
    if debt>=100: raise ValueError("Debt share must be less than 100%")
    tax/=100; debt/=100; erp/=100; usd_yield/=100; foreign_yield/=100; usd_spread/=100
    beta_e=beta_u*(1+(1-tax)*debt/(1-debt))
    re_us=usd_yield+beta_e*erp
    rd_us=usd_yield+usd_spread
    factor=(1+foreign_yield)/(1+usd_yield)
    re_fx=(1+re_us)*factor-1
    spread_fx=usd_spread*factor if foreign_spread is None else foreign_spread/100
    rd_fx=foreign_yield+spread_fx
    w_us=(1-debt)*re_us+debt*rd_us*(1-tax)
    w_fx=(1-debt)*re_fx+debt*rd_fx*(1-tax)
    return {"Levered beta":beta_e,"FX conversion factor":factor,"USD BBB spread (%)":usd_spread*100,"Foreign BBB spread (%)":spread_fx*100,"USD equity (%)":re_us*100,"Foreign equity (%)":re_fx*100,"USD debt (%)":rd_us*100,"Foreign debt (%)":rd_fx*100,"USD after-tax debt (%)":rd_us*(1-tax)*100,"Foreign after-tax debt (%)":rd_fx*(1-tax)*100,"USD WACC (%)":w_us*100,"Foreign WACC (%)":w_fx*100}

st.title("🌐 International Cost of Capital")
st.caption("FINC 470 • Professor Sergio Rebelo • International Valuation, pp. 22–29")
with st.sidebar:
    st.header("Market data")
    if st.button("↻ Refresh all sources",use_container_width=True):
        load_data.clear(); st.rerun()
    with st.spinner("Retrieving source data…"):
        data=load_data()
    st.caption("Last retrieval attempt: "+data["retrieved"])
    for k, label in [("betas","Damodaran industry betas"),("spread","Damodaran BBB spread"),("taxes","OECD corporate taxes"),("yields","Trading Economics yields")]:
        st.caption(("✅ " if data[k]["error"] is None else "⚠️ ")+label)
        if data[k]["error"]: st.caption(data[k]["error"])

betas=data["betas"]["value"][0] if data["betas"]["value"] is not None else None
industry_names=betas["Industry"].tolist() if betas is not None else [DEFAULT_INDUSTRY]
industry_default=next((i for i,x in enumerate(industry_names) if x.lower()==DEFAULT_INDUSTRY.lower()),0)
yield_data=data["yields"]["value"][0] if data["yields"]["value"] is not None else None
tax_data=data["taxes"]["value"][0] if data["taxes"]["value"] is not None else None
countries=sorted(set((["United States"]+ (yield_data["Country"].tolist() if yield_data is not None else []) + (tax_data["Country"].tolist() if tax_data is not None else []))))

def lookup(df,key,col):
    if df is None:return None
    rows=df[df["Country"].str.casefold()==key.casefold()]
    return float(rows.iloc[0][col]) if not rows.empty else None

c1,c2=st.columns(2)
with c1:
    industry=st.selectbox("Industry (Damodaran U.S. unlevered beta)",industry_names,index=industry_default)
    country=st.selectbox("Target country",countries,index=countries.index("United States"))
with c2:
    erp=st.number_input("Equity risk premium (%)",min_value=0.,max_value=50.,value=6.5,step=0.25)
    debt=st.slider("Target debt financing (%)",min_value=0,max_value=95,value=30)

st.subheader("Source inputs and overrides")
base_beta=float(betas.loc[betas["Industry"]==industry,"Unlevered beta"].iloc[0]) if betas is not None else None
spread_raw=data["spread"]["value"]
base_spread=spread_raw[0] if spread_raw is not None else None
us_tax=lookup(tax_data,"United States","Tax rate (%)")
fx_tax=lookup(tax_data,country,"Tax rate (%)")
us_yield=lookup(yield_data,"United States","10Y yield (%)")
fx_yield=lookup(yield_data,country,"10Y yield (%)")

# Session-state overrides retain independence when country/industry changes.
def field(label,key,auto,minimum,maximum,fmt="%.3f",step=0.1):
    manual=st.checkbox("Manual override",key="toggle_"+key)
    if manual:
        return st.number_input(label,min_value=float(minimum),max_value=float(maximum),value=float(auto if auto is not None else 0),step=step,format=fmt,key="manual_"+key),"Manual"
    if auto is None:
        st.warning(f"{label}: unavailable. Enable manual override to continue.")
        return None,"Unavailable"
    st.text(f"{label}: {auto:.4f} (auto)")
    return float(auto),"Auto"

left,right=st.columns(2)
with left:
    beta,bs=field("Unlevered beta", "beta",base_beta,0,10)
    usd_s,ss=field("USD BBB spread (percentage points)","spread",base_spread,0,25)
    usd_y,ys=field("U.S. 10-year yield (%)","us_yield",us_yield,-5,40)
with right:
    tax_choice=st.radio("Project corporate tax jurisdiction",["United States",country] if country!="United States" else ["United States"],horizontal=True)
    chosen_tax=us_tax if tax_choice=="United States" else fx_tax
    tax,ts=field(f"Project tax rate — {tax_choice} (%)","tax",chosen_tax,0,60)
    foreign_y,fys=field(f"{country} 10-year yield (%)","foreign_yield",fx_yield,-5,40)

ready=all(v is not None for v in [beta,usd_s,usd_y,foreign_y,tax])
if ready:
    auto_spread=usd_s*(1+foreign_y/100)/(1+usd_y/100)
else: auto_spread=None
st.markdown("**Foreign-currency BBB borrowing spread**")
foreign_s,fs=field(f"{country} BBB spread (percentage points)","foreign_spread",auto_spread,0,25)

if ready and foreign_s is not None:
    r=calc(beta,tax,debt,erp,usd_y,foreign_y,usd_s,foreign_s if fs=="Manual" else None)
    st.divider()
    st.subheader("Cost of capital: USD vs. local currency")
    if country=="United States":st.info("The selected foreign country is the United States, so both columns represent USD and should be identical.")
    a,b=st.columns(2)
    with a:
        st.markdown("### USD")
        st.metric("WACC",f"{r['USD WACC (%)']:.2f}%")
        st.metric("Cost of equity",f"{r['USD equity (%)']:.2f}%")
        st.metric("Pretax cost of debt",f"{r['USD debt (%)']:.2f}%")
    with b:
        st.markdown("### "+country)
        st.metric("WACC",f"{r['Foreign WACC (%)']:.2f}%")
        st.metric("Cost of equity",f"{r['Foreign equity (%)']:.2f}%")
        st.metric("Pretax cost of debt",f"{r['Foreign debt (%)']:.2f}%")
    st.caption(f"Relevered beta = {r['Levered beta']:.4f} | D/E = {debt/(100-debt):.4f} | FX gross-rate factor = {r['FX conversion factor']:.6f} | Local BBB spread = {r['Foreign BBB spread (%)']:.4f} pp")
    detail=pd.DataFrame({"Measure":["Cost of equity","Pretax debt cost","After-tax debt cost","WACC"],"USD (%)":[r[k] for k in ["USD equity (%)","USD debt (%)","USD after-tax debt (%)","USD WACC (%)"]],f"{country} (%)":[r[k] for k in ["Foreign equity (%)","Foreign debt (%)","Foreign after-tax debt (%)","Foreign WACC (%)"]]})
    st.dataframe(detail.style.format(precision=4),hide_index=True,use_container_width=True)
    st.subheader("All-industry comparison")
    if betas is not None:
        rows=[]
        for _,entry in betas.iterrows():
            v=calc(float(entry["Unlevered beta"]),tax,debt,erp,usd_y,foreign_y,usd_s,foreign_s if fs=="Manual" else None)
            rows.append({"Industry":entry["Industry"],"Unlevered beta":entry["Unlevered beta"],"Levered beta":v["Levered beta"],"USD equity (%)":v["USD equity (%)"],"Local equity (%)":v["Foreign equity (%)"],"USD WACC (%)":v["USD WACC (%)"],"Local WACC (%)":v["Foreign WACC (%)"]})
        all_df=pd.DataFrame(rows)
        st.dataframe(all_df.style.format({c:"{:.4f}" for c in all_df.columns if c!="Industry"}),hide_index=True,use_container_width=True)
        st.download_button("Download all-industry comparison (CSV)",all_df.to_csv(index=False).encode(),"all_industries_cost_of_capital.csv","text/csv")
    export=pd.DataFrame([{"Industry":industry,"Country":country,"Tax jurisdiction":tax_choice,"Tax (%)":tax,"Debt share (%)":debt,"ERP (%)":erp,"Unlevered beta":beta,"USD yield (%)":usd_y,"Local yield (%)":foreign_y,"USD BBB spread (pp)":usd_s,"Local BBB spread (pp)":foreign_s,**r}])
    st.download_button("Download selected-project results (CSV)",export.to_csv(index=False).encode(),"international_cost_of_capital.csv","text/csv")
else:
    st.error("One or more inputs could not be retrieved. Turn on manual override for each unavailable input to calculate results.")

with st.expander("Model equations and assumptions",expanded=True):
    st.latex(r"\beta_E=\beta_U\left(1+(1-\tau)\frac{d}{1-d}\right)")
    st.latex(r"R_{e,USD}=R_{f,USD}+\beta_E\,ERP")
    st.latex(r"R_{d,USD}=R_{f,USD}+s_{USD}")
    st.latex(r"F=\frac{1+R_{f,local}}{1+R_{f,USD}},\quad R_{e,local}=(1+R_{e,USD})F-1")
    st.latex(r"s_{local}=s_{USD}F,\quad R_{d,local}=R_{f,local}+s_{local}")
    st.latex(r"WACC=(1-d)R_e+dR_d(1-\tau)")
    st.markdown("Government 10-year yields are **risk-free proxies**, not necessarily default-free rates. U.S. industry betas proxy for project operating risk. No separate country equity risk premium or systematic FX premium is added. The same selected project tax rate, ERP, and debt share are applied in both currencies. Currency conversion assumes equal real rates and relative PPP. Debt and the interest tax shield are assumed to have zero beta; interest deductions are fully usable. These assumptions follow lecture pp. 22–29.")
    st.warning("A sovereign yield may include credit, liquidity and term premia. Scraped source layouts and availability can change. OECD rates are not assumed current merely because the linked article is accessible. Validate all source dates before relying on the results.")

st.subheader("Data provenance")
for label,url,note in [
    ("Damodaran — U.S. industry unlevered betas",BETAS_URL, data["betas"]["value"][1] if data["betas"]["value"] is not None else "Unavailable"),
    ("Damodaran — large-firm Baa2/BBB default spread",RATINGS_URL, str(data["spread"]["value"][1]) if data["spread"]["value"] is not None else "Unavailable"),
    ("OECD — statutory corporate tax rates",data["taxes"]["value"][1] if data["taxes"]["value"] is not None else OECD_CURRENT, str(data["taxes"]["value"][2]) if data["taxes"]["value"] is not None else "Unavailable; manually override"),
    ("Trading Economics — 10-year government yields",BONDS_URL,data["yields"]["value"][1] if data["yields"]["value"] is not None else "Unavailable")]:
    st.markdown(f"- [{label}]({url}) — {note}")
st.caption("Retrieved at "+data["retrieved"]+". Source publication dates and retrieval timestamps are distinct. Manual inputs are labeled and are not represented as fetched observations.")
