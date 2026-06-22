import os
import logging
import asyncio
import threading
from datetime import datetime, timezone as dt_timezone, timedelta
from fastapi import FastAPI, Request, Depends, HTTPException, status, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from dotenv import load_dotenv

from waha_client import WahaClient
from calcom import CalcomClient
from agent import BookingAgent
import database as db

load_dotenv()

# Configure Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="KineLife AI Booking Agent")

# HTTP Basic Auth setup for Dashboard
security = HTTPBasic()
DASHBOARD_USERNAME = os.getenv("DASHBOARD_USERNAME", "admin")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "admin123")

# Jinja2 Templates setup
templates_dir = os.path.join(os.path.dirname(__file__), "templates")
os.makedirs(templates_dir, exist_ok=True)
templates = Jinja2Templates(directory=templates_dir)

# Initialize clients
waha = WahaClient()
calcom = CalcomClient()
agent = BookingAgent()

def get_current_user(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = credentials.username == DASHBOARD_USERNAME
    correct_password = credentials.password == DASHBOARD_PASSWORD
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

# --- Webhook: WhatsApp (WAHA) ---
@app.post("/webhook/whatsapp")
async def waha_webhook(request: Request):
    """
    Recibe eventos de WAHA. Filtra por mensajes entrantes (no enviados por nosotros)
    y los envía al agente de IA para responder.
    """
    payload = await request.json()
    logger.info(f"Webhook WAHA recibido: {payload.get('event')}")
    
    # WAHA envía diferentes eventos. Nos interesa 'message' o 'message.any'
    # Dependiendo de la configuración de webhook en WAHA, el formato varía un poco.
    event = payload.get("event")
    
    # Procesar si el evento es de mensaje
    if event in ["message", "message.any", "message.create"]:
        msg_data = payload.get("payload", {})
        
        # Omitir si es un mensaje enviado por nosotros mismos (fromMe == True)
        # Esto es crítico para evitar bucles infinitos de respuesta
        if msg_data.get("fromMe", False):
            logger.info("Ignorando mensaje saliente (fromMe = True)")
            return {"status": "ignored", "reason": "from_me"}
            
        from_phone = msg_data.get("from")
        body = msg_data.get("body", "")
        
        if not from_phone or not body:
            logger.warning(f"Mensaje incompleto recibido en webhook: {msg_data}")
            return {"status": "error", "message": "missing fields"}
            
        # Limpiar el chatId de WhatsApp (ej: 56912345678@c.us -> 56912345678)
        phone_number = from_phone.split("@")[0]
        
        # Procesar con el Agente de IA en segundo plano o asíncronamente
        # para responder rápidamente con status 200 a WAHA
        asyncio.create_task(process_and_reply(phone_number, from_phone, body))
        
        return {"status": "queued"}
        
    return {"status": "ignored", "reason": "unhandled_event"}

async def process_and_reply(phone_number: str, full_chat_id: str, body: str):
    """
    Tarea asíncrona para que el agente procese el mensaje y envíe la respuesta.
    """
    try:
        # 1. El agente procesa el mensaje y genera respuesta (usando herramientas si es necesario)
        reply_text = agent.process_message(phone_number, body)
        
        # 2. Enviar respuesta por WhatsApp
        waha.send_message(full_chat_id, reply_text)
    except Exception as e:
        logger.error(f"Error procesando mensaje para {phone_number}: {str(e)}")
        # En caso de error crítico, enviar mensaje genérico de disculpa
        waha.send_message(full_chat_id, "Lo siento, tuve un inconveniente procesando tu solicitud. Por favor intenta de nuevo.")

# --- Webhook: Cal.com ---
@app.post("/webhook/calcom")
async def calcom_webhook(request: Request):
    """
    Recibe notificaciones de eventos desde Cal.com (creación, cancelación de reservas).
    Sincroniza la base de datos local y envía notificaciones por WhatsApp.
    """
    try:
        data = await request.json()
        trigger = data.get("triggerEvent")
        payload = data.get("payload", {})
        
        logger.info(f"Webhook Cal.com recibido: {trigger}")
        
        booking_uid = payload.get("uid")
        start_time = payload.get("startTime")
        title = payload.get("title", "")
        
        # Obtener datos del asistente/paciente
        attendees = payload.get("attendees", [])
        attendee_name = "Paciente"
        attendee_phone = ""
        attendee_email = ""
        
        if attendees:
            attendee = attendees[0]
            attendee_name = attendee.get("name", attendee_name)
            attendee_phone = attendee.get("phoneNumber", "")
            attendee_email = attendee.get("email", "")
            
        if not booking_uid:
            return {"status": "error", "message": "missing uid"}
            
        if trigger == "BOOKING_CREATED":
            # Guardar/Actualizar en base de datos
            db.save_booking(booking_uid, attendee_name, attendee_phone, attendee_email, start_time, "confirmed")
            db.add_log(attendee_phone, "CALCOM_WEBHOOK_CREATED", f"Cita {booking_uid} confirmada desde Cal.com")
            
            # Enviar mensaje de confirmación al paciente si tiene teléfono
            if attendee_phone:
                msg = (
                    f"📅 *¡Cita Confirmada!*\n\n"
                    f"Hola {attendee_name}, te confirmamos que tu cita de fisioterapia ha sido agendada con éxito.\n\n"
                    f"🕒 *Hora:* {start_time}\n"
                    f"📝 *Detalle:* {title}\n"
                    f"🆔 *ID Reserva:* {booking_uid}\n\n"
                    f"Si necesitas cancelarla o reagendarla, por favor avísanos por este medio con anticipación. ¡Que tengas un excelente día!"
                )
                waha.send_message(attendee_phone, msg)
                
        elif trigger == "BOOKING_CANCELLED":
            db.save_booking(booking_uid, attendee_name, attendee_phone, attendee_email, start_time, "cancelled")
            db.add_log(attendee_phone, "CALCOM_WEBHOOK_CANCELLED", f"Cita {booking_uid} cancelada en Cal.com")
            
            if attendee_phone:
                msg = (
                    f"❌ *Cita Cancelada*\n\n"
                    f"Hola {attendee_name}, te informamos que tu cita de fisioterapia para el {start_time} ha sido cancelada.\n\n"
                    f"Si deseas agendar una nueva hora, puedes volver a solicitarla por aquí cuando gustes."
                )
                waha.send_message(attendee_phone, msg)
                
        return {"status": "processed"}
    except Exception as e:
        logger.error(f"Error procesando webhook de Cal.com: {str(e)}")
        return {"status": "error", "message": str(e)}

# --- Dashboard: Vistas ---
@app.get("/dashboard", response_class=HTMLResponse)
async def view_dashboard(request: Request, user: str = Depends(get_current_user)):
    """
    Muestra el panel de administración para el Fisioterapeuta.
    """
    # 1. Obtener estado de WAHA
    waha_status_info = waha.get_session_status()
    waha_status = waha_status_info.get("status", "DESCONECTADO")
    
    # Intentar obtener QR si requiere escaneo
    qr_code_data = None
    if waha_status == "SCAN_QR_CODE":
        try:
            qr_info = waha.get_qr_code()
            if qr_info:
                # WAHA v2 devuelve {"raw": "...", "image": "data:image/png;base64,...", "qr": "..."}
                # A veces devuelve una imagen raw o base64, pasamos lo que venga
                qr_code_data = qr_info.get("image") or qr_info.get("qr")
        except Exception as e:
            logger.error(f"Error cargando QR: {e}")
            
    # 2. Cargar reservas del sistema
    bookings = db.get_upcoming_bookings_db()
    
    # 3. Cargar logs recientes
    logs = db.get_recent_logs(limit=30)
    
    # Formatear fechas para mostrar legibles
    formatted_bookings = []
    for b in bookings:
        try:
            # ej: 2026-06-25T14:30:00.000Z o similar
            dt = datetime.fromisoformat(b["start_time"].replace("Z", "+00:00"))
            b["readable_time"] = dt.strftime("%d/%m/%Y %H:%M")
        except Exception:
            b["readable_time"] = b["start_time"]
        formatted_bookings.append(b)
        
    # Cargar valores actuales de settings
    settings = {
        "waha_base_url": db.get_setting("WAHA_BASE_URL", os.getenv("WAHA_BASE_URL", "http://localhost:3000")),
        "waha_api_key": db.get_setting("WAHA_API_KEY", os.getenv("WAHA_API_KEY", "")),
        "waha_session_name": db.get_setting("WAHA_SESSION_NAME", os.getenv("WAHA_SESSION_NAME", "default")),
        "calcom_api_key": db.get_setting("CALCOM_API_KEY", os.getenv("CALCOM_API_KEY", "")),
        "calcom_event_type_id": db.get_setting("CALCOM_EVENT_TYPE_ID", os.getenv("CALCOM_EVENT_TYPE_ID", "")),
        "gemini_api_key": db.get_setting("GEMINI_API_KEY", os.getenv("GEMINI_API_KEY", "")),
        "openai_api_key": db.get_setting("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY", "")),
        "system_instruction": db.get_setting("SYSTEM_INSTRUCTION", "")
    }
    
    # Parámetros del request de FastAPI
    success_param = request.query_params.get("success")
    error_param = request.query_params.get("error")
        
    return templates.TemplateResponse(
        request,
        "index.html", 
        {
            "waha_status": waha_status,
            "waha_session": waha.session_name,
            "bookings": formatted_bookings,
            "logs": logs,
            "qr_code_data": qr_code_data,
            "settings": settings,
            "success_param": success_param,
            "error_param": error_param
        }
    )

@app.post("/dashboard/settings")
async def save_settings(
    request: Request,
    waha_base_url: str = Form(None),
    waha_api_key: str = Form(None),
    waha_session_name: str = Form(None),
    calcom_api_key: str = Form(None),
    calcom_event_type_id: str = Form(None),
    gemini_api_key: str = Form(None),
    openai_api_key: str = Form(None),
    system_instruction: str = Form(None),
    user: str = Depends(get_current_user)
):
    if waha_base_url is not None:
        db.set_setting("WAHA_BASE_URL", waha_base_url.strip())
    if waha_api_key is not None:
        db.set_setting("WAHA_API_KEY", waha_api_key.strip())
    if waha_session_name is not None:
        db.set_setting("WAHA_SESSION_NAME", waha_session_name.strip())
    if calcom_api_key is not None:
        db.set_setting("CALCOM_API_KEY", calcom_api_key.strip())
    if calcom_event_type_id is not None:
        db.set_setting("CALCOM_EVENT_TYPE_ID", calcom_event_type_id.strip())
    if gemini_api_key is not None:
        db.set_setting("GEMINI_API_KEY", gemini_api_key.strip())
    if openai_api_key is not None:
        db.set_setting("OPENAI_API_KEY", openai_api_key.strip())
    if system_instruction is not None:
        db.set_setting("SYSTEM_INSTRUCTION", system_instruction.strip())
        
    return RedirectResponse(url="/dashboard?success=settings", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/dashboard/whatsapp/start")
async def whatsapp_start(request: Request, user: str = Depends(get_current_user)):
    success = waha.start_session()
    if success:
        db.add_log(None, "WAHA_SESSION_START", "Comando de inicio de sesión enviado a WAHA")
        return RedirectResponse(url="/dashboard?success=whatsapp_start", status_code=status.HTTP_303_SEE_OTHER)
    else:
        db.add_log(None, "WAHA_SESSION_ERROR", "Error al intentar iniciar la sesión en WAHA")
        return RedirectResponse(url="/dashboard?error=whatsapp_start_failed", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/dashboard/whatsapp/logout")
async def whatsapp_logout(request: Request, user: str = Depends(get_current_user)):
    success = waha.logout_session()
    if success:
        db.add_log(None, "WAHA_SESSION_LOGOUT", "Comando de cierre de sesión (logout) enviado a WAHA")
        return RedirectResponse(url="/dashboard?success=whatsapp_logout", status_code=status.HTTP_303_SEE_OTHER)
    else:
        db.add_log(None, "WAHA_SESSION_ERROR", "Error al intentar cerrar la sesión en WAHA")
        return RedirectResponse(url="/dashboard?error=whatsapp_logout_failed", status_code=status.HTTP_303_SEE_OTHER)

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse(request, "landing.html")

# --- Recordatorios Automáticos en Segundo Plano ---
async def check_and_send_reminders():
    """
    Bucle en segundo plano que corre cada 5 minutos.
    Revisa si hay citas confirmadas próximas y envía recordatorios automáticos por WhatsApp.
    """
    while True:
        try:
            logger.info("Ejecutando verificador de recordatorios programados...")
            now_utc = datetime.now(dt_timezone.utc)
            bookings = db.get_upcoming_bookings_db()
            
            for b in bookings:
                # Omitir si ya se enviaron ambos recordatorios
                if b["reminder_24h_sent"] and b["reminder_2h_sent"]:
                    continue
                    
                try:
                    # Parsear la hora de la cita (esperamos formato UTC)
                    # ej: 2026-06-25T14:00:00Z -> timezone aware datetime
                    start_time_str = b["start_time"].replace("Z", "+00:00")
                    start_time = datetime.fromisoformat(start_time_str)
                    
                    time_diff = start_time - now_utc
                    hours_diff = time_diff.total_seconds() / 3600.0
                    
                    # 1. Recordatorio de 24 horas (enviar si quedan entre 22 y 24 horas)
                    if 22.0 <= hours_diff <= 24.0 and not b["reminder_24h_sent"]:
                        if b["client_phone"]:
                            msg = (
                                f"🔔 *Recordatorio de Cita (Mañana)*\n\n"
                                f"Hola {b['client_name']},\n"
                                f"Te recordamos tu cita de fisioterapia agendada para mañana.\n\n"
                                f"🕒 *Hora:* {start_time.strftime('%H:%M')} (UTC/Local)\n"
                                f"📅 *Fecha:* {start_time.strftime('%d/%m/%Y')}\n\n"
                                f"Por favor, confírmanos por este medio si asistirás respondiendo con un **Sí** o un **No**."
                            )
                            success = waha.send_message(b["client_phone"], msg)
                            if success:
                                db.mark_reminder_sent(b["id"], "24h")
                                db.add_log(b["client_phone"], "REMINDER_24H_SENT", f"Recordatorio enviado para cita {b['cal_booking_id']}")
                                
                    # 2. Recordatorio de 2 horas (enviar si quedan entre 1 y 2 horas)
                    elif 1.0 <= hours_diff <= 2.0 and not b["reminder_2h_sent"]:
                        if b["client_phone"]:
                            msg = (
                                f"⚡ *Recordatorio de Cita (Próxima)*\n\n"
                                f"Hola {b['client_name']},\n"
                                f"Tu cita de fisioterapia comienza en aproximadamente 2 horas.\n\n"
                                f"🕒 *Hora:* {start_time.strftime('%H:%M')} (UTC/Local)\n"
                                f"📍 *Lugar:* Clínica KineLife\n\n"
                                f"¡Te esperamos! Recuerda llegar 5 minutos antes."
                            )
                            success = waha.send_message(b["client_phone"], msg)
                            if success:
                                db.mark_reminder_sent(b["id"], "2h")
                                db.add_log(b["client_phone"], "REMINDER_2H_SENT", f"Recordatorio de 2 horas enviado para cita {b['cal_booking_id']}")
                except Exception as e:
                    logger.error(f"Error procesando recordatorio para reserva {b.get('id')}: {str(e)}")
                    
        except Exception as e:
            logger.error(f"Error en bucle de recordatorios: {str(e)}")
            
        # Esperar 5 minutos antes de volver a verificar (300 segundos)
        await asyncio.sleep(300)

def start_reminder_thread():
    """
    Inicia el bucle de recordatorios en un hilo secundario para no bloquear el servidor.
    """
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_until_complete, args=(check_and_send_reminders(),), daemon=True).start()

@app.on_event("startup")
async def startup_event():
    logger.info("Iniciando microservicio de Agendamiento KineLife...")
    # Iniciar el hilo de recordatorios en segundo plano
    start_reminder_thread()
