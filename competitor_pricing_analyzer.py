#!/usr/bin/env python3
"""
Automated Competitor Price & Product Monitoring System
Uses Python, Google Shopping API (via SerpApi), BeautifulSoup, fuzzy matching
"""

import csv
import time
import os
import requests
from bs4 import BeautifulSoup
from thefuzz import fuzz
from dotenv import load_dotenv
from pathlib import Path

# -------------------------------------------------------------------------------------------------
# 1) Determine script directory (so files are always found)
# -------------------------------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).parent.resolve()

# -------------------------------------------------------------------------------------------------
# 2) Load API key from .env (must be in same folder as this script)
# -------------------------------------------------------------------------------------------------
load_dotenv(SCRIPT_DIR / ".env")

SERPAPI_KEY = os.getenv("SERPAPI_KEY")
if not SERPAPI_KEY:
    raise ValueError(
        "SERPAPI_KEY is missing from .env file.\n"
        "Make sure '.env' exists in the same folder as this script "
        "and contains: SERPAPI_KEY=your_key_here"
    )

# -------------------------------------------------------------------------------------------------
# 3) File paths
# -------------------------------------------------------------------------------------------------
OUR_PRICES_FILE = SCRIPT_DIR / "our_products.csv"
OUTPUT_FILE      = SCRIPT_DIR / "competitor_analysis.csv"
SEARCH_ENGINE    = "google_shopping"
COUNTRY          = "us"

# -------------------------------------------------------------------------------------------------
# Functions
# -------------------------------------------------------------------------------------------------
def fetch_google_shopping(query, num_results=10):
    """Query Google Shopping via SerpApi and return raw JSON"""
    url = "https://serpapi.com/search"
    params = {
        "api_key": SERPAPI_KEY,
        "engine": SEARCH_ENGINE,
        "q": query,
        "num": num_results,
        "country": COUNTRY,
        "tbm": "shop"
    }
    response = requests.get(url, params=params, timeout=20)
    response.raise_for_status()
    return response.json()


def parse_shopping_results(serp_data):
    """Parse SerpApi JSON using BeautifulSoup (HTML snippets)"""
    products = []
    shopping_results = serp_data.get("shopping_results", [])
    for item in shopping_results:
        title_html = item.get("title", "")
        if title_html:
            soup = BeautifulSoup(title_html, "html.parser")
            title = soup.get_text(strip=True)
        else:
            title = "N/A"

        price = item.get("extracted_price")
        if not price:
            price_str = item.get("price", "")
            try:
                price = float(price_str.replace("$", "").replace(",", ""))
            except (ValueError, TypeError):
                price = 0.0

        source = item.get("source", "")
        link = item.get("link", "")
        shipping = item.get("shipping", "")

        products.append({
            "title": title,
            "price": price,
            "source": source,
            "link": link,
            "shipping": shipping,
        })
    return products


def load_our_products(filepath):
    """Read CSV and return list of dicts with title, price, cost, search_term"""
    if not filepath.is_file():
        print(f"ERROR: File not found: {filepath}")
        return []
    products = []
    with open(filepath, newline='', encoding='utf-8-sig') as f:  # utf-8-sig handles BOM
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            print("ERROR: CSV has no header row.")
            return []
        print("CSV columns found:", reader.fieldnames)  # debug line
        # Check required columns
        required = {'title', 'price', 'cost'}
        if not required.issubset(set(reader.fieldnames)):
            print(f"ERROR: CSV missing required columns. Need: {required}")
            print(f"Your columns: {reader.fieldnames}")
            return []
        for row_num, row in enumerate(reader, start=2):
            try:
                title = row.get('title', '').strip()
                if not title:
                    continue
                price = float(row.get('price', 0))
                cost = float(row.get('cost', 0))
                search_term = row.get('search_term', '').strip() or title
                products.append({
                    "title": title,
                    "price": price,
                    "cost": cost,
                    "search_term": search_term,
                })
            except (ValueError, TypeError) as e:
                print(f"  Skipping row {row_num}: {e}")
                continue
    return products



def find_matching_competitor(our_title, competitors, threshold=75):
    """Fuzzy token set ratio match. Returns (best_match, score) or (None, 0)"""
    best_score = 0
    best_match = None
    for comp in competitors:
        score = fuzz.token_set_ratio(our_title.lower(), comp["title"].lower())
        if score > best_score:
            best_score = score
            best_match = comp
    if best_score >= threshold:
        return best_match, best_score
    return None, 0


def compute_market_stats(matched_products):
    """Average, min, max price from matched competitors"""
    prices = [p["price"] for p in matched_products if p["price"] > 0]
    if not prices:
        return {"avg_price": 0, "min_price": 0, "max_price": 0, "count": 0}
    return {
        "avg_price": round(sum(prices) / len(prices), 2),
        "min_price": min(prices),
        "max_price": max(prices),
        "count": len(prices)
    }


def recommend_price(our_price, cost, market_avg):
    """Generate pricing recommendation using actual margin vs. market average

    Compares current profit (our_price - cost) against a target of 50% margin on cost
    """
    if cost <= 0 or our_price <= 0:
        return "Invalid price or cost data.", None

    current_margin = our_price - cost
    target_margin  = cost * 0.5          # 50% profit on cost
    ideal_price    = cost + target_margin

    if market_avg <= 0:
        # No competitors found – recommend cost‑based price
        return f"Set price based on cost – ${ideal_price:.2f} (no market data)", ideal_price

    # Prefer to meet or beat market average, but not below cost
    if ideal_price <= market_avg:
        suggested = ideal_price
        recommendation = (
            f"Market avg ${market_avg:.2f}. "
            f"Current margin ${current_margin:.2f}. "
            f"Set at ${suggested:.2f} to achieve 50% profit (${target_margin:.2f} margin)."
        )
    else:
        suggested = market_avg
        recommendation = (
            f"Target price ${ideal_price:.2f} (50% margin) above market avg ${market_avg:.2f}. "
            f"Cap at market avg ${suggested:.2f} to stay competitive. "
            f"Current margin ${current_margin:.2f}."
        )
    return recommendation, suggested


# -------------------------------------------------------------------------------------------------
# Main execution
# -------------------------------------------------------------------------------------------------
def main():
    print(f"Looking for CSV at: {OUR_PRICES_FILE}")
    our_products = load_our_products(OUR_PRICES_FILE)
    if not our_products:
        return

    results = []

    for product in our_products:
        our_title = product["title"]
        our_price = product["price"]
        cost      = product["cost"]
        search_query = product.get("search_term", our_title)

        print(f"\nProcessing: {our_title}")
        print(f"  Searching for: {search_query}")

        # Query Google Shopping
        try:
            serp_data = fetch_google_shopping(search_query)
            competitors = parse_shopping_results(serp_data)
        except requests.RequestException as e:
            print(f"  Network error: {e}")
            continue
        except ValueError as e:
            print(f"  Data parse error: {e}")
            continue

        if not competitors:
            print("  No competitors found – using cost‑based recommendation only.")
            rec, suggested = recommend_price(our_price, cost, 0)
            results.append({
                "our_title": our_title,
                "our_price": our_price,
                "cost": cost,
                "competitor_name": "N/A",
                "competitor_price": 0,
                "competitor_source": "N/A",
                "market_min": 0,
                "market_avg": 0,
                "market_max": 0,
                "total_competitors": 0,
                "recommendation": rec,
                "suggested_price": suggested if suggested else ""
            })
            continue

        # Compute market stats from ALL competitors
        stats = compute_market_stats(competitors)

        # Pick top 3 competitors (first 3 from search results)
        top_3 = competitors[:3]
        print(f"  Top 3 competitors found:")
        for i, comp in enumerate(top_3, 1):
            score = fuzz.token_set_ratio(our_title.lower(), comp["title"].lower())
            print(f"    {i}. {comp['title']} - ${comp['price']:.2f} ({comp['source']}) [fuzzy: {score}]")

        # Create ONE ROW PER COMPETITOR
        for comp in top_3:
            rec, suggested = recommend_price(our_price, cost, stats["avg_price"])
            results.append({
                "our_title": our_title,
                "our_price": our_price,
                "cost": cost,
                "competitor_name": comp["title"],
                "competitor_price": comp["price"],
                "competitor_source": comp["source"],
                "market_min": stats["min_price"],
                "market_avg": stats["avg_price"],
                "market_max": stats["max_price"],
                "total_competitors": stats["count"],
                "recommendation": rec,
                "suggested_price": suggested if suggested else ""
            })

        time.sleep(1)

    # Output CSV
    if results:
        keys = [
            "our_title", "our_price", "cost",
            "competitor_name", "competitor_price", "competitor_source",
            "market_min", "market_avg", "market_max", "total_competitors",
            "recommendation", "suggested_price"
        ]
        with open(OUTPUT_FILE, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            writer.writerows(results)
        print(f"\n✅ Analysis saved to {OUTPUT_FILE}")
    else:
        print("No results generated.")


if __name__ == "__main__":
    main()
