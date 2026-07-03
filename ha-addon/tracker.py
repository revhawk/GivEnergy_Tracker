import json
import requests
from datetime import datetime
import config  # Imports your hidden credentials file natively

# Pull rates using variables from your hidden config.py file
URL = f"https://api.octopus.energy/v1/products/{config.AGILE_PRODUCT_CODE}/electricity-tariffs/{config.AGILE_TARIFF_CODE}/standard-unit-rates/"

def fetch_agile_rates():
    print("Connecting to Octopus API to retrieve current unit rates...")
    try:
        # Request data using your verified API key layout
        response = requests.get(URL, auth=(config.OCTOPUS_API_KEY, ""))
        response.raise_for_status()
        data = response.json()
        
        # Pull out the pricing blocks array
        results = data.get('results', [])
        print(f"Successfully retrieved {len(results)} half-hourly pricing slots.\n")
        
        # Print the next 3 upcoming slots as a quick connection test
        print("--- Upcoming Agile Pricing Slots ---")
        for slot in results[:3]:
            time_str = slot['valid_from'].replace('T', ' ').replace('Z', '')
            price = slot['value_inc_vat']
            print(f"Time: {time_str} | Price: {price:.2f}p/kWh")
            
    except Exception as e:
        print(f"Error communicating with Octopus API: {e}")

if __name__ == "__main__":
    fetch_agile_rates()
