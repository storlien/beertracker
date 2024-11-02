from beertracker import CLIENT_ID, API_KEY, API_URL_AUTH

import requests
import time
from threading import Thread

class TokenManager:
    def __init__(self):
        self.client_id = CLIENT_ID
        self.api_key = API_KEY
        self.token = None
        self.expires_in = None
        self._running = False

        if CLIENT_ID is None or API_KEY is None:
            raise ValueError('You must provide a CLIENT_ID and an API_KEY')

        print("TokenGetter initialized")
        self.get_token()
        self.start_refresh_token()

    def get_token(self):
        headers = {
            "Content-Type": "application/x-www-form-urlencoded"
        }

        data = {
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "client_id": self.client_id,
            "assertion": self.api_key
        }

        response = requests.post(API_URL_AUTH, headers=headers, data=data)

        if response.status_code == 200:
            self.token = response.json()['access_token']
            self.expires_in = int(response.json()['expires_in'])
        else:
            # TODO: Error handling
            print("Trouble getting token")

    def start_refresh_token(self):
        print("Starting refresh token")
        self._running = True
        Thread(target=self._refresh_loop, daemon=True).start()

    def stop_refresh_token(self):
        print("Stopping refresh token")
        self._running = False

    def _refresh_loop(self):
        while self._running:
            self.get_token()
            time.sleep(self.expires_in - 60)