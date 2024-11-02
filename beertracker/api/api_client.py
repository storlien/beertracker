from beertracker.api.tokenmanager import TokenManager
from beertracker import API_URL_BASE

import requests

class APIClient:

    def __init__(self, tokenmanager: TokenManager):
        self.tokenmanager = tokenmanager

    def get_response(self, parameters: dict[str, str]) -> dict:
        headers = {
            "Authorization": f"Bearer {self.tokenmanager.token}",
            "Content-Type": "application/json"
        }

        parameters_str = ""
        for key, value in parameters.items():
            parameters_str += f"{key}={value}&"

        response = requests.get(f"{API_URL_BASE}/purchases/v2?{parameters_str}", headers=headers)

        if response.status_code == 200:
            return response.json()
        else:
            print("Error")