import os
import requests
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

class WahaClient:
    def __init__(self):
        pass

    @property
    def base_url(self) -> str:
        import database as db
        return db.get_setting("WAHA_BASE_URL", os.getenv("WAHA_BASE_URL", "http://localhost:3000")).rstrip("/")

    @property
    def api_key(self) -> str:
        import database as db
        return db.get_setting("WAHA_API_KEY", os.getenv("WAHA_API_KEY", os.getenv("WHATSAPP_API_KEY", "")))

    @property
    def session_name(self) -> str:
        import database as db
        return db.get_setting("WAHA_SESSION_NAME", os.getenv("WAHA_SESSION_NAME", "default"))

    @property
    def headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "X-Api-Key": self.api_key
        }

    def _format_phone(self, phone: str) -> str:
        # Limpiar el número de teléfono
        clean_phone = "".join(filter(str.isdigit, phone))
        # Si no termina con @c.us, agregarlo
        if not clean_phone.endswith("@c.us"):
            # Para números internacionales, asegurar el formato.
            # En la mayoría de las APIs de WhatsApp Web, se requiere el código de país.
            clean_phone = f"{clean_phone}@c.us"
        return clean_phone

    def send_message(self, phone: str, text: str) -> bool:
        """
        Envia un mensaje de texto por WhatsApp.
        """
        chat_id = self._format_phone(phone)
        url = f"{self.base_url}/api/sendText"
        payload = {
            "chatId": chat_id,
            "text": text,
            "session": self.session_name
        }
        
        try:
            logger.info(f"Enviando mensaje a {chat_id}...")
            response = requests.post(url, json=payload, headers=self.headers, timeout=10)
            if response.status_code in [200, 201]:
                logger.info(f"Mensaje enviado con éxito a {chat_id}")
                return True
            else:
                logger.error(f"Error al enviar mensaje. Status: {response.status_code}. Response: {response.text}")
                return False
        except Exception as e:
            logger.error(f"Excepción al enviar mensaje a {chat_id}: {str(e)}")
            return False

    def get_session_status(self) -> dict:
        """
        Obtiene el estado de la sesión actual.
        """
        url = f"{self.base_url}/api/sessions/{self.session_name}"
        try:
            response = requests.get(url, headers=self.headers, timeout=5)
            if response.status_code == 200:
                return response.json()
            return {"status": "DISCONNECTED", "error": f"HTTP {response.status_code}"}
        except Exception as e:
            return {"status": "DISCONNECTED", "error": str(e)}

    def get_sessions(self) -> list:
        """
        Obtiene el listado de todas las sesiones registradas.
        """
        url = f"{self.base_url}/api/sessions"
        try:
            response = requests.get(url, headers=self.headers, timeout=5)
            if response.status_code == 200:
                return response.json()
            return []
        except Exception as e:
            logger.error(f"Error obteniendo sesiones: {str(e)}")
            return []

    def start_session(self) -> bool:
        """
        Inicia la sesión configurada.
        """
        url = f"{self.base_url}/api/sessions"
        payload = {
            "name": self.session_name,
            "start": True
        }
        try:
            response = requests.post(url, json=payload, headers=self.headers, timeout=10)
            if response.status_code in [200, 201]:
                return True
            
            # Si ya existe, intentar iniciarla explícitamente
            start_url = f"{self.base_url}/api/sessions/{self.session_name}/start"
            start_resp = requests.post(start_url, headers=self.headers, timeout=10)
            return start_resp.status_code in [200, 201]
        except Exception as e:
            logger.error(f"Error iniciando sesión {self.session_name}: {str(e)}")
            return False

    def logout_session(self) -> bool:
        """
        Cierra sesión y detiene el navegador Puppeteer.
        """
        url = f"{self.base_url}/api/sessions/{self.session_name}/logout"
        try:
            response = requests.post(url, headers=self.headers, timeout=10)
            return response.status_code in [200, 201]
        except Exception as e:
            logger.error(f"Error en logout de sesión {self.session_name}: {str(e)}")
            return False

    def get_qr_code(self) -> dict:
        """
        Obtiene el string del código QR en formato JSON/Base64 para poder escanearlo.
        """
        url = f"{self.base_url}/api/{self.session_name}/auth/qr"
        try:
            response = requests.get(url, headers=self.headers, timeout=5)
            if response.status_code == 200:
                return response.json()
            return {}
        except Exception as e:
            logger.error(f"Error obteniendo QR para {self.session_name}: {str(e)}")
            return {}
