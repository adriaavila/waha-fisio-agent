import os
import logging
from datetime import datetime
from dotenv import load_dotenv
import google.generativeai as genai
from openai import OpenAI

from calcom import CalcomClient
from database import add_chat_message, get_chat_history, add_log, save_booking

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

logger = logging.getLogger(__name__)
calcom = CalcomClient()

# --- Definición de herramientas para el LLM ---

def check_availability(start_date: str, end_date: str) -> str:
    """
    Consulta los horarios disponibles para citas de fisioterapia entre dos fechas.
    
    Args:
        start_date: Fecha de inicio en formato YYYY-MM-DD (ej: '2026-06-25')
        end_date: Fecha de fin en formato YYYY-MM-DD (ej: '2026-06-26')
        
    Returns:
        Un resumen legible de los horarios disponibles en español.
    """
    logger.info(f"Herramienta: check_availability convocada para {start_date} a {end_date}")
    try:
        slots = calcom.get_available_slots(start_date, end_date)
        if not slots:
            return "No hay disponibilidad para el rango de fechas seleccionado."
        
        summary = "Horarios disponibles encontrados:\n"
        has_slots = False
        for date, times in slots.items():
            if not times:
                continue
            has_slots = True
            # Formatear fecha legible
            dt_obj = datetime.strptime(date, "%Y-%m-%d")
            date_str = dt_obj.strftime("%d de %B de %Y")
            summary += f"\n📅 *{date_str}*:\n"
            for t in times[:10]:  # Mostrar un máximo de 10 por día
                time_iso = t["time"]
                # Extraer hora legible HH:MM desde ISO
                # ej: 2026-06-25T14:30:00Z -> 14:30
                try:
                    time_part = time_iso.split("T")[1][:5]
                    summary += f"  - {time_part} (UTC/Local)\n"
                except Exception:
                    summary += f"  - {time_iso}\n"
        
        if not has_slots:
            return "No hay disponibilidad para el rango de fechas seleccionado."
            
        return summary
    except Exception as e:
        logger.error(f"Error en herramienta check_availability: {str(e)}")
        return "Hubo un error al consultar la disponibilidad en la agenda."

def book_appointment(start_time: str, name: str, email: str, phone: str) -> str:
    """
    Reserva una cita de fisioterapia en el horario seleccionado.
    
    Args:
        start_time: Fecha y hora de inicio de la cita en formato ISO UTC, ej: '2026-06-25T14:30:00Z'. Debe ser un horario disponible.
        name: Nombre completo del paciente.
        email: Correo electrónico del paciente.
        phone: Teléfono del paciente (ej: '+56912345678').
        
    Returns:
        Un mensaje de éxito con los detalles de la reserva o un mensaje de error.
    """
    logger.info(f"Herramienta: book_appointment convocada para {name} a las {start_time}")
    try:
        booking = calcom.create_booking(start_time, name, email, phone)
        if not booking:
            return "No se pudo realizar la reserva. Por favor verifica que el horario esté libre e intenta de nuevo."
        
        booking_uid = booking.get("uid")
        start_iso = booking.get("start")
        
        # Guardar en base de datos local para dashboard y recordatorios
        save_booking(
            cal_booking_id=booking_uid,
            name=name,
            phone=phone,
            email=email,
            start_time=start_iso,
            status="confirmed"
        )
        
        add_log(phone, "BOOKING_CREATED", f"Cita creada para {name} el {start_time}. UID: {booking_uid}")
        
        return f"🎉 ¡Cita agendada con éxito!\n\n📋 *Detalles de la Reserva:*\n👤 *Paciente:* {name}\n📅 *Fecha/Hora:* {start_iso}\n🆔 *Código de Cita:* {booking_uid}\n\nTe hemos enviado un correo de confirmación y te recordaremos la cita por este medio."
    except Exception as e:
        logger.error(f"Error en herramienta book_appointment: {str(e)}")
        return "Hubo un error interno al intentar agendar la cita."

# --- Clase del Agente de IA ---

class BookingAgent:
    def __init__(self):
        self.system_instruction = (
            "Eres 'KineBot', el asistente inteligente de la clínica de Fisioterapia KineLife.\n"
            "Tu objetivo principal es ayudar a los pacientes a consultar horarios disponibles y agendar citas de forma amable, eficiente y profesional.\n\n"
            "Instrucciones de comportamiento:\n"
            "1. Saluda amablemente al inicio y sé empático. Los pacientes pueden tener dolores o lesiones.\n"
            "2. Para agendar una cita, utiliza la herramienta `check_availability` para ver qué horarios están libres. "
            "Pídele al usuario que te diga qué fecha o rango de fechas le acomoda (ej: 'esta semana', 'el próximo jueves', etc.).\n"
            "3. Cuando te digan una fecha general, consulta la disponibilidad de esa fecha o un rango de 2-3 días alrededor usando `check_availability`.\n"
            "4. Cuando el usuario elija una hora específica, pídele su nombre completo y correo electrónico (si no los tienes ya en el historial de chat) antes de proceder.\n"
            "5. Llama a la herramienta `book_appointment` para concretar la cita. Necesitarás el horario en formato ISO UTC (ej: '2026-06-25T14:30:00Z'), el nombre, email y teléfono.\n"
            "6. Si el usuario pregunta cosas generales sobre fisioterapia o dolores, responde de manera informativa y clara, pero siempre sugiriéndoles agendar una cita para una evaluación profesional.\n"
            "7. Hoy es {today_date}."
        )

    def _get_system_instruction(self) -> str:
        today = datetime.now().strftime("%A, %d de %B de %Y")
        return self.system_instruction.format(today_date=today)

    def process_message(self, phone_number: str, message_text: str) -> str:
        """
        Procesa el mensaje del cliente, interactúa con el LLM y realiza las llamadas a funciones necesarias.
        """
        add_log(phone_number, "RECEIVE_MESSAGE", message_text)
        
        # 1. Guardar mensaje del usuario en la base de datos
        add_chat_message(phone_number, "user", message_text)
        
        # 2. Cargar historial
        history = get_chat_history(phone_number, limit=10)
        
        # Si no hay llaves configuradas, devolvemos un asistente mockeado para la demo
        if not GEMINI_API_KEY and not OPENAI_API_KEY:
            logger.warning("No API Keys configured for Gemini or OpenAI. Using Mock Agent.")
            return self._mock_respond(phone_number, message_text)

        try:
            if GEMINI_API_KEY:
                return self._run_gemini(phone_number, message_text, history)
            else:
                return self._run_openai(phone_number, message_text, history)
        except Exception as e:
            logger.error(f"Error procesando mensaje con LLM: {str(e)}")
            add_log(phone_number, "AGENT_ERROR", str(e))
            return "Lo siento, tuve un pequeño problema procesando tu mensaje. ¿Podrías intentar de nuevo o escribir más tarde?"

    def _run_gemini(self, phone_number: str, message_text: str, history: list) -> str:
        """
        Ejecuta la conversación usando Gemini con Automatic Function Calling.
        """
        genai.configure(api_key=GEMINI_API_KEY)
        
        # Formatear el historial para Gemini
        gemini_history = []
        for msg in history[:-1]:  # No incluir el último mensaje porque lo pasaremos en send_message
            role = "user" if msg["role"] == "user" else "model"
            gemini_history.append({
                "role": role,
                "parts": [msg["content"]]
            })
            
        model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            tools=[check_availability, book_appointment],
            system_instruction=self._get_system_instruction()
        )
        
        chat = model.start_chat(history=gemini_history)
        response = chat.send_message(
            f"Teléfono del usuario: {phone_number}. Mensaje: {message_text}",
            enable_automatic_function_calling=True
        )
        
        reply = response.text
        # Guardar respuesta del asistente en base de datos
        add_chat_message(phone_number, "assistant", reply)
        add_log(phone_number, "SEND_RESPONSE", reply[:100] + "...")
        return reply

    def _run_openai(self, phone_number: str, message_text: str, history: list) -> str:
        """
        Ejecuta la conversación usando OpenAI.
        """
        client = OpenAI(api_key=OPENAI_API_KEY)
        
        # Formatear mensajes
        messages = [{"role": "system", "content": self._get_system_instruction()}]
        for msg in history:
            messages.append({"role": msg["role"], "content": msg["content"]})
            
        # Añadir el número de teléfono como contexto en el sistema
        messages.append({
            "role": "system",
            "content": f"El número de teléfono del usuario actual es {phone_number}. Úsalo al agendar la cita."
        })

        # Herramientas de OpenAI
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "check_availability",
                    "description": "Consulta los horarios disponibles para citas de fisioterapia entre dos fechas.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "start_date": {"type": "string", "description": "Fecha inicio YYYY-MM-DD"},
                            "end_date": {"type": "string", "description": "Fecha fin YYYY-MM-DD"}
                        },
                        "required": ["start_date", "end_date"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "book_appointment",
                    "description": "Reserva una cita de fisioterapia en el horario seleccionado.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "start_time": {"type": "string", "description": "Fecha y hora ISO UTC ej: 2026-06-25T14:30:00Z"},
                            "name": {"type": "string", "description": "Nombre completo del paciente"},
                            "email": {"type": "string", "description": "Email del paciente"},
                            "phone": {"type": "string", "description": "Teléfono del paciente"}
                        },
                        "required": ["start_time", "name", "email", "phone"]
                    }
                }
            }
        ]

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            tools=tools,
            tool_choice="auto"
        )
        
        response_message = response.choices[0].message
        tool_calls = response_message.tool_calls
        
        if tool_calls:
            # Procesar llamadas a herramientas
            messages.append(response_message)
            
            for tool_call in tool_calls:
                function_name = tool_call.function.name
                import json
                function_args = json.loads(tool_call.function.arguments)
                
                logger.info(f"Ejecutando herramienta OpenAI: {function_name}")
                if function_name == "check_availability":
                    tool_result = check_availability(
                        start_date=function_args.get("start_date"),
                        end_date=function_args.get("end_date")
                    )
                elif function_name == "book_appointment":
                    tool_result = book_appointment(
                        start_time=function_args.get("start_time"),
                        name=function_args.get("name"),
                        email=function_args.get("email"),
                        phone=function_args.get("phone")
                    )
                else:
                    tool_result = "Función no encontrada."
                    
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": function_name,
                    "content": tool_result
                })
            
            # Obtener respuesta final con resultados de herramientas
            second_response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages
            )
            reply = second_response.choices[0].message.content
        else:
            reply = response_message.content

        add_chat_message(phone_number, "assistant", reply)
        add_log(phone_number, "SEND_RESPONSE", reply[:100] + "...")
        return reply

    def _mock_respond(self, phone_number: str, message_text: str) -> str:
        """
        Asistente de demostración mockeado (se activa si no hay API keys configuradas).
        """
        msg_lower = message_text.lower()
        
        if "hola" in msg_lower or "buenos" in msg_lower:
            reply = "👋 ¡Hola! Bienvenido a la clínica de Fisioterapia KineLife. Soy KineBot. ¿En qué te puedo ayudar hoy? ¿Te gustaría agendar una evaluación o sesión?"
        elif "agendar" in msg_lower or "cita" in msg_lower or "hora" in msg_lower or "disponibilidad" in msg_lower:
            reply = (
                "📅 Claro, con gusto te ayudo a agendar. Tengo los siguientes horarios disponibles esta semana:\n\n"
                "*Jueves 25 de Junio:*\n"
                " - 10:00 AM\n"
                " - 11:30 AM\n"
                " - 15:00 PM\n\n"
                "*Viernes 26 de Junio:*\n"
                " - 09:00 AM\n"
                " - 14:00 PM\n"
                " - 16:30 PM\n\n"
                "¿Te acomoda alguno de estos horarios? Si es así, indícame cuál y facilítame tu *Nombre Completo* y *Correo Electrónico*."
            )
        elif "10:00" in msg_lower or "11:30" in msg_lower or "15:00" in msg_lower or "jueves" in msg_lower or "viernes" in msg_lower:
            reply = "Perfecto. Para concretar tu reserva, por favor confírmame tu **Nombre Completo** y tu **Correo electrónico**."
        elif "@" in msg_lower:
            reply = (
                "🎉 ¡Cita agendada con éxito (Demo Mock)!\n\n"
                "📋 *Detalles de la Reserva:*\n"
                "👤 *Paciente:* Paciente Demo\n"
                "📅 *Fecha/Hora:* 2026-06-25T14:00:00Z (Jueves 25 de Junio a las 10:00 AM)\n"
                "🆔 *Código de Cita:* cal_demo_123456\n\n"
                "Te llegará un correo de confirmación. ¡Te esperamos!"
            )
        else:
            reply = "KineLife Fisioterapia: Entendido. Puedes agendar una sesión indicando la palabra 'agendar' o consultando la disponibilidad de horas libres."

        add_chat_message(phone_number, "assistant", reply)
        add_log(phone_number, "SEND_RESPONSE", reply[:100] + " (MOCK)...")
        return reply
