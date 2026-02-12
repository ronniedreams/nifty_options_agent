
import requests
import json
import os
from dotenv import load_dotenv

# Load .env
load_dotenv('baseline_v1_live/.env')

API_KEY = os.getenv('OPENALGO_API_KEY')
HOST = "https://openalgo.ronniedreams.in"

def fetch_orderbook():
    url = f"{HOST}/api/v1/orderbook"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "apikey": API_KEY
    }
    
    print(f"Fetching orderbook from {url}...")
    try:
        response = requests.post(url, json=payload, headers=headers)
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            return response.json()
        else:
            print(f"Error: {response.text}")
            return None
    except Exception as e:
        print(f"Exception: {e}")
        return None

if __name__ == "__main__":
    orders = fetch_orderbook()
    if orders:
        print(json.dumps(orders, indent=2))
    else:
        print("Failed to fetch orderbook.")
