import streamlit as st
import requests
import pandas as pd
from bs4 import BeautifulSoup
import os
import re
import urllib.parse

# --- CONFIGURATION ---
SOLVER_URL = os.getenv("SOLVER_URL", "http://localhost:8191/v1")
st.set_page_config(page_title="PewPewTracker | v10.0", page_icon="🎯", layout="wide")

# --- TACTICAL THEME ---
st.markdown("""
<style>
    .stApp { background-color: #0b0c10; color: #c5c6c7; }
    h1, h2, h3 { color: #66fcf1 !important; font-family: 'Courier New', monospace; text-transform: uppercase; }
    .stButton button { background-color: #1f2833; color: #66fcf1; border: 1px solid #66fcf1; width: 100%; font-weight: bold; }
    .stButton button:hover { background-color: #66fcf1; color: #000; }
    div[data-testid="stMetric"] { background-color: #1f2833; border: 1px solid #45a29e; }
    a { color: #45a29e !important; text-decoration: none; font-weight: bold; }
</style>
""", unsafe_allow_html=True)

# --- RELEVANCE ENGINE (THE FIX) ---
def validate_item(name, comp_type, search_val):
    """
    The 'Bouncer'. Returns True ONLY if the item matches the search criteria.
    """
    name = name.lower()
    val = search_val.lower()
    
    # 0. GLOBAL JUNK FILTER
    # Removes generic site links that mess up Gun.Deals results
    if name in ["products", "deals", "search", "login", "register", "cart"]: return False
    if "storewide" in name or "off site" in name: return False

    # 1. PRIMER LOGIC (Strict)
    if comp_type == "Primers":
        if "small" in val and "small" not in name: return False
        if "large" in val and "large" not in name: return False
        if "pistol" in val and "pistol" not in name: return False
        if "rifle" in val and "rifle" not in name: return False
        if "209" in val and "209" not in name: return False
        # Cross-check exclusions
        if "pistol" in val and ("rifle" in name or "209" in name): return False
        if "rifle" in val and ("pistol" in name or "209" in name): return False

    # 2. POWDER LOGIC (Fuzzy)
    elif comp_type == "Powder":
        # If searching "Varget", ensure "Varget" is in title
        if val not in name: return False

    # 3. CALIBER LOGIC (Bullets, Brass, Ammo) - NEW FIX
    elif comp_type in ["Bullets", "Brass", "Loaded Ammo"]:
        # We must ensure the caliber matches to filter out sponsored ads
        
        # 9mm
        if "9mm" in val:
            if "9mm" not in name and "luger" not in name: return False
        
        # .45 ACP
        elif "45" in val and "acp" in val:
            if "45" not in name and "acp" not in name: return False
            
        # .223 / 5.56
        elif "223" in val or "5.56" in val:
            if "223" not in name and "5.56" not in name: return False
            
        # .308 / 7.62
        elif "308" in val:
            if "308" not in name and "7.62" not in name: return False
            
        # 6.5 Creedmoor
        elif "6.5" in val:
            # Must have '6.5' OR 'creedmoor'
            if "6.5" not in name and "creedmoor" not in name: return False
            
        # 300 Blackout
        elif "300" in val:
            if "300" not in name and "blackout" not in name: return False

    return True

# --- ENGINE ---
def get_html_via_solver(url):
    post_body = {"cmd": "request.get", "url": url, "maxTimeout": 60000}
    try:
        response = requests.post(SOLVER_URL, json=post_body, timeout=70)
        if response.status_code == 200:
            json_resp = response.json()
            if json_resp.get('status') == 'ok':
                return json_resp['solution']['response']
    except Exception as e:
        st.error(f"⚠️ COMMS LINK FAILURE: {e}")
    return None

# --- PARSER 1: AMMOSEEK ---
def parse_ammoseek(html, comp_type, search_val):
    items = []
    if not html: return items
    soup = BeautifulSoup(html, 'lxml')
    
    # Try finding main results table
    main_table = soup.find('table', class_='results-table')
    rows = main_table.find_all('tr') if main_table else soup.find_all('tr')
    
    for row in rows:
        if "Display Log" in str(row) or "google" in str(row): continue
        cols = row.find_all('td')
        if len(cols) < 5: continue 

        try:
            # Name Construction
            desc_text = cols[0].get_text(" ", strip=True) + " " + cols[1].get_text(" ", strip=True)
            
            # --- VALIDATION CHECK ---
            if not validate_item(desc_text, comp_type, search_val):
                continue

            # Image
            img_url = "https://ammoseek.com/img/as_logo_200.png"
            img_tag = row.find('img')
            if img_tag:
                src = img_tag.get('data-src') or img_tag.get('src')
                if src and "pixel" not in src:
                    if src.startswith('//'): src = "https:" + src
                    elif src.startswith('/'): src = "https://ammoseek.com" + src
                    img_url = src

            # Price
            text_all = row.get_text(" ", strip=True)
            prices = re.findall(r'\$\s?([0-9,]+(?:\.[0-9]+)?)', text_all)
            if not prices: continue
            clean_prices = [float(p.replace(',', '')) for p in prices]
            valid_prices = [p for p in clean_prices if p > 0.001]
            if not valid_prices: continue
            unit_price = min(valid_prices)

            # Link
            links = row.find_all('a', href=True)
            vendor_link = "#"
            for l in links:
                h = l['href']
                if '/ratings/' in h or '/review/' in h or 'login' in h: continue
                vendor_link = h
                if vendor_link.startswith('/'): vendor_link = f"https://ammoseek.com{vendor_link}"
                break
            
            # Qty
            qty = 1
            qty_match = re.search(r'(\d+)\s?(rds|rounds|cnt|count|pcs)', text_all, re.IGNORECASE)
            if qty_match: qty = int(qty_match.group(1))

            if unit_price > 30.0 and qty > 1: unit_price = unit_price / qty

            if 0.001 < unit_price < 2000.0:
                items.append({
                    'Source': 'AmmoSeek',
                    'Image': img_url,
                    'Name': desc_text[:80],
                    'Price': unit_price,
                    'Total': unit_price * qty,
                    'Link': vendor_link
                })
        except: continue
    return items

# --- PARSER 2: GUN.DEALS ---
def parse_gundeals(html, comp_type, search_val):
    items = []
    if not html: return items
    soup = BeautifulSoup(html, 'lxml')
    rows = soup.find_all(['div', 'tr'], class_=re.compile(r'row|view-content|views-row'))
    
    for row in rows:
        text = row.get_text(" ", strip=True)
        if len(text) < 5 or "Subscribe" in text: continue

        try:
            # Name Extraction (Look for Title specific class first)
            name = text[:80]
            title_div = row.find(class_="title") or row.find("h3")
            if title_div:
                name = title_div.get_text(strip=True)
            else:
                # Fallback to link text
                links = row.find_all('a', href=True)
                for l in links:
                    if "product" in l['href'] or "deal" in l['href']:
                        name = l.get_text(strip=True)
                        break

            # --- VALIDATION CHECK ---
            if not validate_item(name, comp_type, search_val):
                continue

            # Price
            prices = re.findall(r'\$\s?([0-9,]+(?:\.[0-9]+)?)', text)
            if not prices: continue
            clean_prices = [float(p.replace(',', '')) for p in prices]
            valid_prices = [p for p in clean_prices if p > 0.001]
            if not valid_prices: continue
            unit_price = min(valid_prices)

            # Link
            links = row.find_all('a', href=True)
            vendor_link = "#"
            for l in links:
                if "product" in l['href'] or "deal" in l['href']:
                    vendor_link = "https://gun.deals" + l['href']
                    break
            
            items.append({
                'Source': 'Gun.Deals',
                'Image': "https://gun.deals/sites/all/themes/gundeals/logo.png",
                'Name': name,
                'Price': unit_price,
                'Total': unit_price,
                'Link': vendor_link
            })
        except: continue
    return items

# --- UI HEADER ---
st.title("PEWPEWTRACKER [v10.0]")
st.markdown("AGGREGATOR SYSTEM: **ONLINE** | FILTERING: **PRECISION**")

# --- SEARCH BUILDER ---
with st.container():
    c1, c2, c3, c4 = st.columns([1, 1, 1, 1])
    with c1:
        comp_type = st.selectbox("COMPONENT", ["Bullets", "Brass", "Primers", "Powder", "Loaded Ammo"])
    with c2:
        if comp_type == "Powder":
            search_val = st.text_input("POWDER NAME", "Varget")
        elif comp_type == "Primers":
            search_val = st.selectbox("SIZE", ["Small Pistol", "Large Pistol", "Small Rifle", "Large Rifle", "209 Shotshell"])
        else:
            search_val = st.selectbox("CALIBER", ["9mm", ".45-acp", ".223-rem", "5.56x45mm-nato", ".308-win", "6.5-creedmoor", "300-blackout"])
    with c3:
        extra_param = ""
        if comp_type == "Bullets":
            gr = st.number_input("MIN GRAIN", 115)
            extra_param = f"&grains={gr}-1000"
        elif comp_type == "Brass":
            cond = st.selectbox("CONDITION", ["Unprocessed", "New", "Once-Fired"])
            cond_map = {"New": "&condition=new", "Once-Fired": "&condition=oncefired", "Unprocessed": ""}
            extra_param = cond_map.get(cond, "")
    with c4:
        st.write("") 
        st.write("")
        scan = st.button("INITIATE MULTI-SCAN", type="primary")

# --- EXECUTION ---
if scan:
    results = []
    safe_search = urllib.parse.quote(search_val)
    
    # 1. URL GEN
    as_url = ""
    if comp_type == "Powder": as_url = f"https://ammoseek.com/reloading/powder?k={safe_search}"
    elif comp_type == "Primers": as_url = f"https://ammoseek.com/reloading/primers?type={safe_search.lower().replace(' ', '-')}"
    elif comp_type == "Bullets": as_url = f"https://ammoseek.com/reloading/bullets?caliber={safe_search}{extra_param}"
    elif comp_type == "Brass": as_url = f"https://ammoseek.com/reloading/brass?caliber={safe_search}{extra_param}"
    elif comp_type == "Loaded Ammo": as_url = f"https://ammoseek.com/ammo/{safe_search}{extra_param}"

    gd_url = f"https://gun.deals/search/apachesolr_search/{safe_search}"

    # 2. SCAN
    st.write(f"📡 SCANNING AMMOSEEK & GUN.DEALS...")
    
    html_as = get_html_via_solver(as_url)
    results += parse_ammoseek(html_as, comp_type, search_val)
    
    html_gd = get_html_via_solver(gd_url)
    results += parse_gundeals(html_gd, comp_type, search_val)

    # 3. DISPLAY
    if results:
        df = pd.DataFrame(results)
        df = df.sort_values(by='Price')
        
        st.success(f"AGGREGATION COMPLETE: {len(df)} VALID TARGETS FOUND")
        st.dataframe(
            df,
            column_config={
                "Image": st.column_config.ImageColumn("Preview", width="small"),
                "Source": st.column_config.TextColumn("Intel Source"),
                "Link": st.column_config.LinkColumn("Vendor Uplink", display_text="GO TO STORE"),
                "Price": st.column_config.NumberColumn("Unit Cost", format="$%.4f"),
                "Total": st.column_config.NumberColumn("Est Total", format="$%.2f"),
                "Name": st.column_config.TextColumn("Product Intel", width="large"),
            },
            width="stretch",
            hide_index=True,
            height=800
        )
    else:
        st.error("NO MATCHING TARGETS FOUND.")