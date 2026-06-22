import os
import requests
import logging
from dotenv import load_dotenv

load_dotenv()

CALCOM_API_KEY = os.getenv("CALCOM_API_KEY", "")
CALCOM_EVENT_TYPE_ID = os.getenv("CALCOM_EVENT_TYPE_ID", "")
CAL_API_VERSION = "2024-08-13"  # Versión estable de API v2

logger = logging.getLogger(__name__)

class CalcomClient:
    def __init__(self):
        self.api_key = CALCOM_API_KEY
        self.event_type_id = int(CALCOM_EVENT_TYPE_ID) if CALCOM_EVENT_TYPE_ID.isdigit() else None
        self.base_url = "https://api.cal.com/v2"
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "cal-api-version": CAL_API_VERSION,
            "Content-Type": "application/json"
        }

    def is_configured(self) -> bool:
        return bool(self.api_key and self.event_type_id)

    def get_available_slots(self, start_date: str, end_date: str, timezone: str = "America/Santiago") -> dict:
        """
        Consulta la disponibilidad de slots en Cal.com para un rango de fechas.
        Rango de fechas en formato YYYY-MM-DD
        """
        if not self.is_configured():
            logger.error("Cal.com client is not fully configured (missing API Key or Event Type ID)")
            return {}

        url = f"{self.base_url}/slots"
        params = {
            "eventTypeId": self.event_type_id,
            "start": start_date,
            "end": end_date,
            "timeZone": timezone
        }

        try:
            logger.info(f"Consultando disponibilidad de {start_date} a {end_date} en timezone {timezone}...")
            response = requests.get(url, params=params, headers=self.headers, timeout=10)
            if response.status_code == 200:
                res_data = response.json()
                if res_data.get("status") == "success":
                    return res_data.get("data", {}).get("slots", {})
                logger.error(f"Cal.com returned success status false: {res_data}")
                return {}
            else:
                logger.error(f"Error consultando slots en Cal.com. Status: {response.status_code}. Response: {response.text}")
                return {}
        except Exception as e:
            logger.error(f"Excepción al consultar slots: {str(e)}")
            return {}

    def create_booking(self, start_time: str, name: str, email: str, phone: str, timezone: str = "America/Santiago") -> dict:
        """
        Crea una reserva en Cal.com
        start_time debe ser un string ISO UTC, ej: 2026-06-25T14:00:00Z
        """
        if not self.is_configured():
            logger.error("Cal.com client is not fully configured")
            return {}

        url = f"{self.base_url}/bookings"
        payload = {
            "eventTypeId": self.event_type_id,
            "start": start_time,
            "attendee": {
                "name": name,
                "email": email or f"{phone.replace('+', '')}@example.com",  # Email fallback
                "timeZone": timezone,
                "phoneNumber": phone
            }
        }

        try:
            logger.info(f"Creando reserva en Cal.com para {name} a las {start_time}...")
            response = requests.post(url, json=payload, headers=self.headers, timeout=10)
            if response.status_code in [200, 201]:
                res_data = response.json()
                if res_data.get("status") == "success":
                    return res_data.get("data", {})
                logger.error(f"Cal.com booking creation status was not success: {res_data}")
                return {}
            else:
                logger.error(f"Error creando reserva. Status: {response.status_code}. Response: {response.text}")
                return {}
        except Exception as e:
            logger.error(f"Excepción al crear reserva: {str(e)}")
            return {}

    def cancel_booking(self, booking_uid: str, reason: str = None) -> bool:
        """
        Cancela una reserva existente por su Booking UID.
        """
        if not self.is_configured():
            logger.error("Cal.com client is not fully configured")
            return False

        url = f"{self.base_url}/bookings/{booking_uid}/cancel"
        payload = {}
        if reason:
            payload["cancellationReason"] = reason

        try:
            logger.info(f"Cancelando reserva {booking_uid}...")
            response = requests.post(url, json=payload, headers=self.headers, timeout=10)
            if response.status_code in [200, 201]:
                res_data = response.json()
                return res_data.get("status") == "success"
            else:
                logger.error(f"Error cancelando reserva. Status: {response.status_code}. Response: {response.text}")
                return False
        except Exception as e:
            logger.error(f"Excepción al cancelar reserva {booking_uid}: {str(e)}")
            return False
