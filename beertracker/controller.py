from beertracker.api.tokenmanager import TokenManager
from beertracker.api.api_client import APIClient

class Controller:
    def __init__(self):
        pass

    def setup(self):
        print("Setting up")
        self.tm = TokenManager()
        self.api = APIClient(self.tm)
        print("Setup done")

    def service(self):
        print("Service running")
        if purchase_hash := self.api.get_response({"startDate": "2024-10-20"})["firstPurchaseHash"]:
            print(purchase_hash)


def main():
    controller = Controller()

    controller.setup()
    controller.service()

if __name__ == '__main__':
    main()