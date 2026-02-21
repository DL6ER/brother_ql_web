import os

class HomeAssistantConfig:
    def __init__(self):
        self.api_url = os.getenv('HOMEASSISTANT_API_URL')
        self.api_key = os.getenv('HOMEASSISTANT_API_KEY')
        self.entity_id = os.getenv('HOMEASSISTANT_PRINTER_ENTITY_ID')

    def is_configured(self):
        return all([self.api_url, self.api_key, self.entity_id])
