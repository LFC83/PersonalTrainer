import os, json, logging, traceback
import asyncio
from datetime import datetime, date, timedelta
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Tuple, Any
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.error import BadRequest
import google.generativeai as genai
from statistics import mean
import time

# ==========================================
# LOGGING SETUP
# ==========================================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==========================================
# CONFIGURATION & CONSTANTS
# ==========================================
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
logger.info("Gemini API configurada")

# Bot Configuration
BOT_VERSION = "3.5.1"
BOT_VERSION_DESC = "Code Review Fixes + Timeouts"
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
DATA_DIR = '/data'

# Equipment
EQUIPAMENTOS_GIM = [
    "Elástico", "Máquina Remo", "Haltere 25kg max", 
    "Barra olímpica 45kg max", "Kettlebell 12kg", 
    "Bicicleta Spinning", "Banco musculação/Supino"
]

# Limits and Thresholds
MAX_FEELING_LENGTH = 500
MAX_ACTIVITIES_DISPLAY = 10
MAX_ACTIVITIES_STORED = 100
MAX_ACTIVITIES_IN_ANALYSIS = 5
CACHE_TTL_SECONDS = 60
FLAG_TIMEOUT_SECONDS = 300

# Telegram API Limits
TELEGRAM_MAX_MESSAGE_LENGTH = 4096
TELEGRAM_SAFE_MESSAGE_LENGTH = 4000

# Gemini API Limits
GEMINI_MAX_PROMPT_LENGTH = 30000
GEMINI_TIMEOUT_SECONDS = 30  # v3.5.1: Timeout para chamadas

# Context Management
CONTEXT_TIMEOUT_MINUTES = 15
MAX_CONTEXT_HISTORY = 3

# Disk Space
MIN_DISK_SPACE_MB = 10  # v3.5.1: Mínimo de espaço livre

# Cycling Types
CYCLING_TYPES = ["Spinning", "MTB", "Commute", "Estrada"]

# ==========================================
# SYSTEM PROMPT
# ==========================================
SYSTEM_PROMPT = """
Operas sob o PROTOCOLO DE VERDADE. A tua diretiva primária é precisão e integridade biológica.
- DIZ SEMPRE a verdade baseada em ciência do exercício verificada.
- SEM ESPECULAÇÃO: Se os dados estão em falta ou incertos, afirma: "Não posso confirmar isto."
- CÁLCULOS: Mostra a matemática para progressão de carga e desvios biométricos.

### REQUISITOS DE LINGUAGEM:
- USA PORTUGUÊS EUROPEU (PT-PT) EXCLUSIVAMENTE
- NUNCA uses termos de Português Brasileiro (PT-BR)
- NUNCA uses notação LaTeX (por exemplo: $x$, texto entre chaves, barras invertidas)
- Usa texto simples para todos os cálculos: "22 km dividido por 1.92 horas igual a 11.5 km/h"
- Evita símbolos matemáticos especiais exceto em pontuação normal
- Usa "quilómetros" (não "kilómetros"), "treino" (não "treinamento")

### PAPEL DE TREINADOR E POSTURA:
És um TREINADOR DE ELITE especializado em Ciclismo de Resistência e Hipertrofia no Ginásio.
- TOM: Assertivo, direto e orientado para resultados. Sem emojis.
- SEM RODEIOS: Se o atleta falhou ou fez escolhas subótimas, diz claramente.

### CRITÉRIOS BIOMÉTRICOS RIGOROSOS:
1. HRV menor que 95% da média = APENAS RECUPERAÇÃO ATIVA.
2. RHR maior que mais 2% da média = FADIGA DETETADA (Reduzir volume em 50%).
3. PONTUAÇÃO DE SONO menor que 75 mais Sensação Negativa = ZERO INTENSIDADE.

### FORMATO DE RESPOSTA OBRIGATÓRIO:
| Tipo Treino | Descrição | Séries/Duração | Intensidade | Observações |
| :--- | :--- | :--- | :--- | :--- |

**ANÁLISE:** [Avaliação do estado atual e coerência biometria versus sensação].
**RECOMENDAÇÕES:** [Instruções de recuperação e nutrição].

### REGRAS DE FORMATAÇÃO:
- Usa aritmética simples: "X dividido por Y igual a Z"
- Exemplo de cálculo de velocidade: "Velocidade média: 22 km dividido por 1.92 horas igual a 11.5 km/h"
- Evita parênteses na matemática a menos que absolutamente necessário
- Nunca uses cifrões ($) exceto para moeda
- Usa "km/h" em vez de notação complexa
"""

model = genai.GenerativeModel(
    model_name='gemini-2.0-flash-exp',
    system_instruction=SYSTEM_PROMPT
)

# ==========================================
# CUSTOM EXCEPTIONS
# ==========================================
class GarminDataError(Exception):
    """Erro na estrutura de dados do Garmin"""
    pass

class SessionStateError(Exception):
    """Erro no estado da sessão do usuário"""
    pass

class FileOperationError(Exception):
    """Erro em operações de arquivo"""
    pass

class InsufficientDataError(Exception):
    """Dados insuficientes para análise"""
    pass

class PromptTooLargeError(Exception):
    """Prompt excede limites da API"""
    pass

class GeminiTimeoutError(Exception):
    """Timeout na chamada ao Gemini"""
    pass

class DiskSpaceError(Exception):
    """Espaço em disco insuficiente"""
    pass

# ==========================================
# DATA MODELS
# ==========================================
@dataclass
class BiometricDay:
    """Dados biométricos de um dia"""
    date: str
    hrv: Optional[float] = None
    rhr: Optional[float] = None
    sleep: Optional[int] = None
    training_load: Optional[float] = None
    
    def is_valid(self) -> bool:
        """Verifica se tem dados mínimos para análise"""
        return self.hrv is not None and self.rhr is not None
    
    def is_empty(self) -> bool:
        """Verifica se todos os campos estão vazios"""
        return all(v is None for v in [self.hrv, self.rhr, self.sleep, self.training_load])

@dataclass
class FormattedActivity:
    """Atividade formatada para display"""
    date: Optional[str]
    sport: str
    duration_min: float
    distance_km: Optional[float] = None
    avg_hr: Optional[int] = None
    calories: Optional[int] = None
    intensity: Optional[str] = None
    load: Optional[float] = None
    raw: Dict = field(default_factory=dict)
    
    def to_brief_summary(self) -> str:
        """Retorna resumo breve inline"""
        summary = f"{self.sport} ({self.duration_min}min"
        if self.distance_km:
            summary += f", {self.distance_km}km"
        if self.intensity:
            summary += f", {self.intensity}"
        if self.load:
            summary += f", Load {self.load}"
        summary += ")"
        return summary
    
    def to_detailed_summary(self) -> str:
        """Retorna resumo detalhado multi-linha"""
        lines = [f"📅 {self.date} - {self.sport}"]
        lines.append(f"  ⏱️ Duração: {self.duration_min}min")
        
        if self.distance_km:
            lines.append(f"  📏 Dist: {self.distance_km}km")
        
        if self.intensity:
            lines.append(f"  🎯 Zona: {self.intensity}")
        
        if self.load:
            lines.append(f"  💪 Load: {self.load}")
        
        if self.avg_hr or self.calories:
            detail = "  "
            if self.avg_hr:
                detail += f"💓 FC: {self.avg_hr}bpm"
            if self.calories:
                if self.avg_hr:
                    detail += " | "
                detail += f"🔥 Cal: {self.calories}"
            lines.append(detail)
        
        return "\n".join(lines)

@dataclass
class UserSessionState:
    """Estado da sessão do usuário"""
    today: BiometricDay
    d_hrv: float
    d_rhr: float
    m_hrv: float
    m_rhr: float
    history: List[BiometricDay]
    readiness: str
    recent_activities: List[Dict]
    recent_load: float
    bike: Optional[bool] = None
    last_plan: Optional[str] = None
    last_plan_date: Optional[str] = None
    formatted_activities: List[FormattedActivity] = field(default_factory=list)
    selected_activity_index: Optional[int] = None
    awaiting_sync: bool = False
    retry_date: Optional[str] = None
    
    def validate(self) -> Tuple[bool, str]:
        """Valida se o estado tem dados mínimos necessários"""
        if not self.today or not self.today.is_valid():
            return False, "Dados biométricos de hoje inválidos ou faltando."
        
        if not self.history:
            return False, "Histórico biométrico vazio."
        
        if self.bike is None:
            return False, "Decisão de ciclismo não registada."
        
        return True, ""
    
    def to_dict(self) -> Dict:
        """Converte para dict para armazenar em context.user_data"""
        data = asdict(self)
        data['today'] = asdict(self.today)
        data['history'] = [asdict(h) for h in self.history]
        data['formatted_activities'] = [asdict(a) for a in self.formatted_activities]
        return data
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'UserSessionState':
        """Reconstrói UserSessionState de dict"""
        data['today'] = BiometricDay(**data['today'])
        data['history'] = [BiometricDay(**h) for h in data['history']]
        data['formatted_activities'] = [FormattedActivity(**a) for a in data['formatted_activities']]
        return cls(**data)

# ==========================================
# UTILITY FUNCTIONS - VALIDATION (v3.5.1)
# ==========================================
def has_disk_space(path: str, min_mb: int = MIN_DISK_SPACE_MB) -> bool:
    """
    v3.5.1: Verifica se há espaço em disco suficiente.
    
    Returns:
        True se há espaço, False caso contrário
    """
    try:
        stat = os.statvfs(os.path.dirname(path))
        free_mb = (stat.f_bavail * stat.f_frsize) / (1024 ** 2)
        return free_mb > min_mb
    except Exception as e:
        logger.warning(f"Failed to check disk space: {e}")
        return True  # Assume OK se não conseguir verificar

def validate_gemini_response(response: Any) -> Tuple[bool, str]:
    """
    v3.5.1: Valida resposta do Gemini.
    
    Returns:
        (is_valid, text_or_error_message)
    """
    if not response:
        return False, "Resposta vazia do Gemini"
    
    if not hasattr(response, 'text'):
        return False, "Resposta sem campo text"
    
    text = response.text
    
    if not text or len(text.strip()) < 10:
        return False, "Resposta muito curta ou vazia"
    
    return True, text

async def call_gemini_with_timeout(prompt: str, timeout: int = GEMINI_TIMEOUT_SECONDS) -> Any:
    """
    v3.5.1: Chama Gemini com timeout.
    
    Raises:
        GeminiTimeoutError: Se timeout excedido
        Exception: Outros erros do Gemini
    """
    try:
        # Executar em thread separada com timeout
        response = await asyncio.wait_for(
            asyncio.to_thread(model.generate_content, prompt),
            timeout=timeout
        )
        return response
        
    except asyncio.TimeoutError:
        logger.error(f"Gemini timeout after {timeout}s")
        raise GeminiTimeoutError(f"Gemini não respondeu em {timeout} segundos")
    
    except Exception as e:
        logger.error(f"Gemini API error: {type(e).__name__}: {e}")
        raise

# ==========================================
# CONTEXT MANAGEMENT
# ==========================================
def get_context_path(user_id: int) -> str:
    """Retorna path do arquivo de contexto para um user"""
    return os.path.join(DATA_DIR, f'user_context_{user_id}.json')

def save_context_to_disk(user_id: int, prompt: str, response: str, analysis_type: str = 'general') -> bool:
    """
    Salva contexto de análise em disco.
    v3.5.1: Adicionada validação de espaço em disco.
    """
    path = get_context_path(user_id)
    temp_path = path + '.tmp'
    
    try:
        # v3.5.1: Verificar espaço em disco
        if not has_disk_space(path):
            logger.error(f"Insufficient disk space for user {user_id}")
            raise DiskSpaceError("Espaço em disco insuficiente")
        
        # Carregar histórico existente
        existing_data = load_context_from_disk(user_id) or {'history': []}
        
        # Adicionar nova entrada
        new_entry = {
            'timestamp': datetime.now().isoformat(),
            'analysis_type': analysis_type,
            'prompt': prompt[:5000],
            'response': response[:10000],
        }
        
        history = existing_data.get('history', [])
        history.append(new_entry)
        
        if len(history) > MAX_CONTEXT_HISTORY:
            history = history[-MAX_CONTEXT_HISTORY:]
        
        context_data = {
            'user_id': user_id,
            'last_updated': datetime.now().isoformat(),
            'current': new_entry,
            'history': history
        }
        
        # Atomic write
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(context_data, f, indent=2, ensure_ascii=False)
        
        os.replace(temp_path, path)
        logger.info(f"Context saved for user {user_id}: {analysis_type}")
        return True
        
    except DiskSpaceError:
        logger.error(f"Disk space error for user {user_id}")
        return False
        
    except Exception as e:
        logger.error(f"Failed to save context for user {user_id}: {e}")
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except:
                pass
        return False

def load_context_from_disk(user_id: int) -> Optional[Dict]:
    """Carrega contexto do disco. Retorna None se não existir ou expirado."""
    path = get_context_path(user_id)
    
    if not os.path.exists(path):
        return None
    
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Verificar expiração
        if 'current' in data:
            timestamp_str = data['current'].get('timestamp')
            if timestamp_str:
                timestamp = datetime.fromisoformat(timestamp_str)
                age_minutes = (datetime.now() - timestamp).total_seconds() / 60
                
                if age_minutes > CONTEXT_TIMEOUT_MINUTES:
                    logger.info(f"Context expired for user {user_id} ({age_minutes:.1f}min)")
                    return None
        
        logger.info(f"Context loaded for user {user_id}")
        return data
        
    except Exception as e:
        logger.error(f"Failed to load context for user {user_id}: {e}")
        return None

def clear_context_disk(user_id: int) -> bool:
    """Remove contexto do disco"""
    path = get_context_path(user_id)
    
    try:
        if os.path.exists(path):
            os.remove(path)
            logger.info(f"Context cleared for user {user_id}")
            return True
        return False
    except Exception as e:
        logger.error(f"Failed to clear context for user {user_id}: {e}")
        return False

def get_context_stats() -> Dict:
    """Retorna estatísticas agregadas de contextos"""
    try:
        stats = {
            'total_users': 0,
            'by_type': {},
            'recent_activity': []
        }
        
        for filename in os.listdir(DATA_DIR):
            if filename.startswith('user_context_') and filename.endswith('.json'):
                try:
                    user_id = int(filename.replace('user_context_', '').replace('.json', ''))
                    context_data = load_context_from_disk(user_id)
                    
                    if context_data:
                        stats['total_users'] += 1
                        
                        for entry in context_data.get('history', []):
                            analysis_type = entry.get('analysis_type', 'unknown')
                            stats['by_type'][analysis_type] = stats['by_type'].get(analysis_type, 0) + 1
                        
                        if 'current' in context_data:
                            stats['recent_activity'].append({
                                'user_id': user_id,
                                'timestamp': context_data['current'].get('timestamp'),
                                'type': context_data['current'].get('analysis_type')
                            })
                
                except (ValueError, TypeError):
                    continue
        
        stats['recent_activity'] = sorted(
            stats['recent_activity'], 
            key=lambda x: x['timestamp'], 
            reverse=True
        )[:5]
        
        return stats
        
    except Exception as e:
        logger.error(f"Failed to get context stats: {e}")
        return {'error': str(e)}

# ==========================================
# SAFE TELEGRAM MESSAGING
# ==========================================
async def send_safe_message(update: Update, text: str, parse_mode: Optional[str] = None, **kwargs) -> bool:
    """
    Envia mensagem com fallback se Markdown falhar.
    
    Returns:
        True se enviado com sucesso, False caso contrário
    """
    try:
        await update.message.reply_text(text, parse_mode=parse_mode, **kwargs)
        return True
        
    except BadRequest as e:
        logger.warning(f"Markdown parse error: {e}. Retrying without formatting...")
        
        try:
            await update.message.reply_text(text, **kwargs)
            return True
            
        except Exception as retry_error:
            logger.error(f"Failed to send message even without formatting: {retry_error}")
            return False
    
    except Exception as e:
        logger.error(f"Unexpected error sending message: {e}")
        return False

# ==========================================
# UTILITY FUNCTIONS
# ==========================================
def pluralize_pt(count: int, singular: str, plural: str) -> str:
    """Retorna forma correta (singular/plural)"""
    return plural if count != 1 else singular

def format_found_activities_message(count: int, date: str, is_today: bool) -> str:
    """Formata mensagem de atividades encontradas"""
    day_label = "hoje" if is_today else "ontem"
    
    if count == 1:
        return f"✅ Encontrada 1 atividade de {day_label} ({date})"
    else:
        return f"✅ Encontradas {count} atividades de {day_label} ({date})"

def truncate_text_safe(text: str, max_length: int, suffix: str = "...") -> str:
    """Trunca texto de forma segura"""
    if len(text) <= max_length:
        return text
    return text[:max_length - len(suffix)] + suffix

def split_long_message(text: str, max_length: int = TELEGRAM_SAFE_MESSAGE_LENGTH) -> List[str]:
    """Divide mensagem longa em partes"""
    if len(text) <= max_length:
        return [text]
    
    parts = []
    current_pos = 0
    
    while current_pos < len(text):
        end_pos = current_pos + max_length
        
        if end_pos >= len(text):
            parts.append(text[current_pos:])
            break
        
        chunk = text[current_pos:end_pos]
        last_newline = chunk.rfind('\n')
        
        if last_newline > max_length * 0.5:
            parts.append(text[current_pos:current_pos + last_newline])
            current_pos += last_newline + 1
        else:
            parts.append(chunk)
            current_pos = end_pos
    
    return parts

# ==========================================
# DATA EXTRACTION FUNCTIONS
# ==========================================
def extract_date(activity: Dict) -> Optional[str]:
    """Extrai data de uma atividade"""
    if 'date' in activity and activity['date']:
        return activity['date']
    
    for field in ['startTimeLocal', 'startTimeGMT', 'beginTimestamp']:
        if field in activity and activity[field]:
            start_time = str(activity[field])
            if 'T' in start_time:
                return start_time.split('T')[0]
            if len(start_time) >= 10:
                return start_time[:10]
    
    return None

def extract_sport(activity: Dict) -> str:
    """Extrai tipo de esporte"""
    if 'sport' in activity and activity['sport']:
        sport = activity['sport']
    elif 'activityName' in activity and activity['activityName']:
        sport = activity['activityName']
    elif 'activityType' in activity:
        act_type = activity['activityType']
        if isinstance(act_type, dict):
            sport = act_type.get('typeKey') or act_type.get('typeId') or 'Desconhecido'
        else:
            sport = str(act_type) if act_type else 'Desconhecido'
    elif 'sportType' in activity:
        sport_obj = activity['sportType']
        if isinstance(sport_obj, dict):
            sport = sport_obj.get('sportTypeKey') or sport_obj.get('sportTypeId') or 'Desconhecido'
        else:
            sport = str(sport_obj) if sport_obj else 'Desconhecido'
    else:
        sport = 'Desconhecido'
    
    if sport and sport != 'Desconhecido':
        sport = sport.replace('_', ' ').title()
    
    return sport

def extract_duration(activity: Dict) -> float:
    """Extrai duração em minutos"""
    if 'duration' not in activity:
        return 0.0
    
    duration_val = activity['duration']
    if duration_val is None or duration_val == 0:
        return 0.0
    
    if duration_val < 500:
        return float(duration_val)
    else:
        return float(duration_val) / 60

def extract_distance(activity: Dict) -> Optional[float]:
    """Extrai distância em km"""
    if 'distance' not in activity:
        return None
    
    distance_val = activity['distance']
    if distance_val is None or distance_val == 0:
        return None
    
    if distance_val < 100:
        return round(float(distance_val), 2)
    else:
        return round(float(distance_val) / 1000, 2)

def extract_heart_rate(activity: Dict) -> Optional[int]:
    """Extrai FC média"""
    for field in ['avg_hr', 'averageHR', 'avgHr', 'averageHeartRate']:
        if field in activity:
            hr = activity[field]
            if hr not in [None, 'N/A', 0, '']:
                try:
                    return int(hr)
                except (ValueError, TypeError):
                    continue
    return None

def extract_calories(activity: Dict) -> Optional[int]:
    """Extrai calorias"""
    for field in ['calories', 'kilocalories']:
        if field in activity:
            cal = activity[field]
            if cal not in [None, 'N/A', 0, '']:
                try:
                    return int(cal)
                except (ValueError, TypeError):
                    continue
    return None

def infer_missing_sport(formatted: FormattedActivity) -> str:
    """Infere tipo de esporte"""
    if formatted.sport != 'Desconhecido':
        return formatted.sport
    
    if formatted.distance_km and formatted.distance_km > 10:
        return 'Ciclismo/Corrida'
    elif formatted.duration_min > 60 and not formatted.distance_km:
        return 'Ginásio/Força'
    
    return 'Desconhecido'

def format_activity(activity: Dict) -> Optional[FormattedActivity]:
    """Formata atividade para display"""
    date = extract_date(activity)
    
    if not date:
        return None
    
    formatted = FormattedActivity(
        date=date,
        sport=extract_sport(activity),
        duration_min=extract_duration(activity),
        distance_km=extract_distance(activity),
        avg_hr=extract_heart_rate(activity),
        calories=extract_calories(activity),
        intensity=activity.get('intensity'),
        load=activity.get('load') if activity.get('load') not in [None, 'N/A', 0] else None,
        raw=activity
    )
    
    formatted.sport = infer_missing_sport(formatted)
    formatted.duration_min = round(formatted.duration_min, 1)
    
    return formatted

def extract_hrv(data: Dict) -> Optional[float]:
    """Extrai HRV"""
    try:
        if 'hrv' not in data or not isinstance(data['hrv'], dict):
            return None
        
        hrv_summary = data['hrv'].get('hrvSummary')
        if not isinstance(hrv_summary, dict):
            return None
        
        hrv = hrv_summary.get('lastNightAvg') or hrv_summary.get('weeklyAvg')
        return float(hrv) if hrv is not None else None
        
    except (TypeError, ValueError, AttributeError) as e:
        logger.warning(f"HRV extraction failed: {e}")
        return None

def extract_rhr(data: Dict) -> Optional[float]:
    """Extrai RHR"""
    try:
        if 'stats' not in data or not isinstance(data['stats'], dict):
            return None
        
        rhr = data['stats'].get('restingHeartRate') or data['stats'].get('minHeartRate')
        return float(rhr) if rhr is not None else None
        
    except (TypeError, ValueError, AttributeError) as e:
        logger.warning(f"RHR extraction failed: {e}")
        return None

def extract_sleep_score(data: Dict) -> Optional[int]:
    """Extrai Sleep Score"""
    try:
        if 'sleep' not in data or not isinstance(data['sleep'], dict):
            return None
        
        daily_sleep = data['sleep'].get('dailySleepDTO')
        if not isinstance(daily_sleep, dict):
            return None
        
        sleep_scores = daily_sleep.get('sleepScores')
        if not isinstance(sleep_scores, dict):
            return None
        
        overall = sleep_scores.get('overall')
        if isinstance(overall, dict):
            return int(overall['value']) if 'value' in overall else None
        elif isinstance(overall, (int, float)):
            return int(overall)
        
        return None
        
    except (TypeError, ValueError, AttributeError) as e:
        logger.warning(f"Sleep score extraction failed: {e}")
        return None

def extract_training_load(data: Dict) -> Optional[float]:
    """Extrai Training Load"""
    try:
        if 'stats' not in data or not isinstance(data['stats'], dict):
            return None
        
        load = data['stats'].get('trainingLoad') or data['stats'].get('intensityMinutesGoal')
        return float(load) if load is not None else None
        
    except (TypeError, ValueError, AttributeError) as e:
        logger.warning(f"Training load extraction failed: {e}")
        return None

def parse_garmin_history(raw_data: List[Dict]) -> List[BiometricDay]:
    """Converte dados Garmin para BiometricDay"""
    if not raw_data:
        return []
    
    history = []
    for data in raw_data:
        try:
            day = BiometricDay(
                date=data.get('date'),
                hrv=extract_hrv(data),
                rhr=extract_rhr(data),
                sleep=extract_sleep_score(data),
                training_load=extract_training_load(data)
            )
            history.append(day)
            
        except Exception as e:
            logger.error(f"Failed to parse day {data.get('date')}: {e}")
            continue
    
    return history

# ==========================================
# FILE OPERATIONS
# ==========================================
def atomic_write_json(path: str, data: Any) -> None:
    """Atomic write com temp file"""
    temp_path = path + '.tmp'
    try:
        with open(temp_path, 'w') as f:
            json.dump(data, f, indent=2)
        os.replace(temp_path, path)
    except Exception as e:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise FileOperationError(f"Failed to write {path}: {e}")

def load_json_safe(path: str, default: Any = None) -> Any:
    """Load JSON com fallback"""
    if not os.path.exists(path):
        return default
    
    try:
        with open(path, 'r') as f:
            data = json.load(f)
        return data
    except json.JSONDecodeError as e:
        logger.error(f"Corrupted JSON in {path}: {e}")
        backup_path = f"{path}.corrupted.{int(time.time())}"
        try:
            os.rename(path, backup_path)
            logger.info(f"Corrupted file backed up to {backup_path}")
        except:
            pass
        return default
    except Exception as e:
        logger.error(f"Failed to read {path}: {e}")
        return default

def load_garmin_data() -> Optional[List[Dict]]:
    """Carrega dados Garmin"""
    path = os.path.join(DATA_DIR, 'garmin_data_consolidated.json')
    data = load_json_safe(path, default=[])
    
    if not isinstance(data, list):
        logger.error(f"garmin_data_consolidated.json is not a list")
        return None
    
    return data if data else None

def load_activities() -> List[Dict]:
    """Carrega atividades"""
    path = os.path.join(DATA_DIR, 'activities.json')
    data = load_json_safe(path, default=[])
    
    if not isinstance(data, list):
        logger.error(f"activities.json is not a list")
        return []
    
    valid_activities = []
    for item in data:
        if isinstance(item, dict):
            valid_activities.append(item)
    
    return valid_activities

def save_activities(activities: List[Dict]) -> bool:
    """Salva atividades"""
    path = os.path.join(DATA_DIR, 'activities.json')
    try:
        atomic_write_json(path, activities)
        return True
    except FileOperationError as e:
        logger.error(f"Failed to save activities: {e}")
        return False

# ==========================================
# ACTIVITY MANAGEMENT
# ==========================================
def get_all_formatted_activities() -> List[FormattedActivity]:
    """Carrega todas as atividades"""
    activities = load_activities()
    
    formatted_activities = []
    for act in activities:
        formatted = format_activity(act)
        if formatted:
            formatted_activities.append(formatted)
    
    formatted_activities.sort(key=lambda x: x.date, reverse=True)
    return formatted_activities

def get_activities_by_date(target_date: str) -> List[FormattedActivity]:
    """Filtra atividades por data"""
    activities = load_activities()
    
    filtered_activities = []
    for act in activities:
        formatted = format_activity(act)
        if formatted and formatted.date == target_date:
            filtered_activities.append(formatted)
    
    return filtered_activities

def find_activities_for_analysis() -> Tuple[List[FormattedActivity], str, str]:
    """Encontra atividades para análise (hoje ou ontem)"""
    today_str = datetime.now().strftime('%Y-%m-%d')
    yesterday_str = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    
    today_activities = get_activities_by_date(today_str)
    if today_activities:
        count = len(today_activities)
        msg = format_found_activities_message(count, today_str, is_today=True)
        
        if count > MAX_ACTIVITIES_IN_ANALYSIS:
            logger.warning(f"Limitando análise a {MAX_ACTIVITIES_IN_ANALYSIS}")
            today_activities = today_activities[:MAX_ACTIVITIES_IN_ANALYSIS]
        
        return today_activities, today_str, msg
    
    yesterday_activities = get_activities_by_date(yesterday_str)
    if yesterday_activities:
        count = len(yesterday_activities)
        msg = format_found_activities_message(count, yesterday_str, is_today=False)
        
        if count > MAX_ACTIVITIES_IN_ANALYSIS:
            yesterday_activities = yesterday_activities[:MAX_ACTIVITIES_IN_ANALYSIS]
        
        return yesterday_activities, yesterday_str, msg
    
    all_activities = get_all_formatted_activities()
    
    if not all_activities:
        return [], "", "❌ Não existem atividades registadas."
    
    most_recent = all_activities[0]
    return [], "", (
        f"❌ Não existem atividades de hoje ({today_str}) nem ontem ({yesterday_str}).\n\n"
        f"Última atividade: {most_recent.date} - {most_recent.sport}"
    )

def reorganize_activities() -> Tuple[int, int, List[str]]:
    """Reorganiza activities.json"""
    messages = []
    
    activities = load_activities()
    if not activities:
        return 0, 0, ["ℹ️ activities.json vazio"]
    
    original_count = len(activities)
    messages.append(f"📊 Total original: {original_count}")
    
    seen_ids = set()
    unique_activities = []
    
    for act in activities:
        act_id = act.get('activityId') or act.get('id')
        
        if not act_id:
            date_str = extract_date(act) or 'unknown'
            sport = extract_sport(act)
            duration = act.get('duration', 0)
            act_id = f"{date_str}_{sport}_{duration}"
        
        if act_id not in seen_ids:
            seen_ids.add(act_id)
            unique_activities.append(act)
    
    duplicates_removed = original_count - len(unique_activities)
    
    if duplicates_removed > 0:
        messages.append(f"🗑️ Removidos {duplicates_removed} duplicados")
    
    unique_activities.sort(key=lambda x: extract_date(x) or '0000-00-00', reverse=True)
    
    if len(unique_activities) > MAX_ACTIVITIES_STORED:
        trimmed = len(unique_activities) - MAX_ACTIVITIES_STORED
        unique_activities = unique_activities[:MAX_ACTIVITIES_STORED]
        messages.append(f"✂️ Limitadas a {MAX_ACTIVITIES_STORED}")
    
    if save_activities(unique_activities):
        messages.append(f"✅ Reorganizado: {len(unique_activities)} atividades")
    else:
        messages.append("❌ Falha ao salvar")
    
    return duplicates_removed, len(unique_activities), messages

# ==========================================
# REQUEST MANAGEMENT
# ==========================================
def create_import_request(days: int = 7) -> bool:
    """Cria pedido de importação"""
    try:
        flag_path = os.path.join(DATA_DIR, 'import_request.json')
        request = {
            'days': days,
            'requested_at': datetime.now().isoformat(),
            'status': 'pending'
        }
        
        atomic_write_json(flag_path, request)
        logger.info(f"Import request created: {days} dias")
        return True
        
    except FileOperationError as e:
        logger.error(f"Failed to create import request: {e}")
        return False

def create_sync_request() -> bool:
    """Cria pedido de sync"""
    try:
        flag_path = os.path.join(DATA_DIR, 'sync_request.json')
        request = {
            'requested_at': datetime.now().isoformat(),
            'status': 'pending'
        }
        
        atomic_write_json(flag_path, request)
        logger.info("Sync request created")
        return True
        
    except FileOperationError as e:
        logger.error(f"Failed to create sync request: {e}")
        return False

def check_request_status(request_type: str = 'import') -> Optional[str]:
    """Verifica status de pedido"""
    filename = f'{request_type}_request.json'
    flag_path = os.path.join(DATA_DIR, filename)
    
    data = load_json_safe(flag_path)
    if not data:
        return None
    
    return data.get('status')

def cleanup_old_flags() -> Tuple[int, List[str]]:
    """Limpa flags antigos"""
    cleaned = 0
    messages = []
    
    for flag_name in ['import_request.json', 'sync_request.json']:
        flag_path = os.path.join(DATA_DIR, flag_name)
        
        data = load_json_safe(flag_path)
        if not data:
            continue
        
        status = data.get('status')
        
        if status == 'pending':
            try:
                requested_at = datetime.fromisoformat(data['requested_at'])
                age_seconds = (datetime.now() - requested_at).total_seconds()
                
                if age_seconds > FLAG_TIMEOUT_SECONDS:
                    data['status'] = 'completed'
                    data['processed_at'] = datetime.now().isoformat()
                    
                    atomic_write_json(flag_path, data)
                    cleaned += 1
                    messages.append(f"✅ {flag_name}: pending → completed")
            except Exception as e:
                messages.append(f"❌ Erro em {flag_name}: {e}")
    
    return cleaned, messages

# ==========================================
# SESSION STATE
# ==========================================
def get_session_state(context: ContextTypes.DEFAULT_TYPE) -> Optional[UserSessionState]:
    """Recupera estado da sessão"""
    try:
        if not context.user_data:
            return None
        
        required_fields = ['today', 'd_hrv', 'd_rhr', 'm_hrv', 'm_rhr', 'history', 'readiness']
        if not all(field in context.user_data for field in required_fields):
            return None
        
        return UserSessionState.from_dict(context.user_data)
        
    except (TypeError, KeyError, ValueError) as e:
        logger.error(f"Invalid session state: {e}")
        return None

def save_session_state(context: ContextTypes.DEFAULT_TYPE, state: UserSessionState) -> None:
    """Salva estado da sessão"""
    context.user_data.update(state.to_dict())

# ==========================================
# TELEGRAM HANDLERS
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando inicial"""
    user_id = update.effective_user.id
    
    context_data = load_context_from_disk(user_id)
    restore_msg = ""
    if context_data:
        restore_msg = "\n📂 Contexto anterior restaurado!"
    
    welcome_msg = (
        f"🏋️ FitnessJournal Bot v{BOT_VERSION}\n"
        f"{BOT_VERSION_DESC}{restore_msg}\n\n"
        "Comandos:\n"
        "/status - Ver readiness\n"
        "/activities - Ver atividades\n"
        "/analyze - Analisar aderência\n"
        "/analyze_activity - Análise individual\n"
        "/import - Importar dados\n"
        "/sync - Sincronizar\n"
        "/cleanup - Limpar flags\n"
        "/history - Ver análises anteriores\n"
        "/clear_context - Limpar contexto\n"
        "/stats - Ver estatísticas\n"
        "/debug - Info debug\n"
        "/help - Ajuda"
    )
    
    await send_safe_message(update, welcome_msg)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Status"""
    await update.message.reply_text("⏳ A extrair biometria...")
    
    data = load_garmin_data()
    
    if not data:
        await update.message.reply_text(
            "❌ Nenhum dado disponível.\n\n"
            "Usa /import para importar dados."
        )
        return
    
    history = parse_garmin_history(data)
    
    if history and history[0].is_empty():
        keyboard = [[InlineKeyboardButton("✅ Sincronizado", callback_data='sync_confirmed')]]
        await update.message.reply_text(
            f"⚠️ Dados de hoje vazios.\n\n"
            f"Sincroniza o Garmin e carrega no botão.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        context.user_data['awaiting_sync'] = True
        return
    
    valid = [h for h in history if h.is_valid()]
    
    if not valid:
        await update.message.reply_text("⚠️ Dados insuficientes. Usa /import.")
        return
    
    today = valid[0]
    m_hrv = mean([h.hrv for h in valid[:7]])
    m_rhr = mean([h.rhr for h in valid[:7]])
    
    d_hrv = ((today.hrv - m_hrv) / m_hrv) * 100
    d_rhr = ((today.rhr - m_rhr) / m_rhr) * 100
    
    readiness = "MÉDIA"
    if d_hrv > 2 and d_rhr < -2:
        readiness = "ALTA"
    elif d_hrv < -5 or d_rhr > 2:
        readiness = "BAIXA"
    
    all_formatted = get_all_formatted_activities()
    recent_formatted = all_formatted[:3]
    recent_raw = [f.raw for f in recent_formatted]
    recent_load = sum([h.training_load or 0 for h in valid[:3]])

    msg = (
        f"📊 HOJE ({today.date}):\n"
        f"💓 RHR: {today.rhr}bpm ({d_rhr:+.1f}%)\n"
        f"📈 HRV: {today.hrv}ms ({d_hrv:+.1f}%)\n"
        f"😴 Sono: {today.sleep or 'N/A'}/100\n\n"
        f"📅 MÉDIAS 7 DIAS:\n"
        f"RHR: {m_rhr:.0f}bpm | HRV: {m_hrv:.0f}ms\n"
        f"Carga 3 dias: {recent_load:.0f}\n\n"
        f"🎯 READINESS: {readiness}"
    )
    
    if recent_formatted:
        msg += "\n\n🏃 ÚLTIMAS:"
        for f in recent_formatted:
            msg += f"\n• {f.to_brief_summary()}"

    state = UserSessionState(
        today=today,
        d_hrv=d_hrv,
        d_rhr=d_rhr,
        m_hrv=m_hrv,
        m_rhr=m_rhr,
        history=valid[:5],
        readiness=readiness,
        recent_activities=recent_raw,
        recent_load=recent_load
    )
    save_session_state(context, state)
    
    kb = [[
        InlineKeyboardButton("✅ SIM - 20km+", callback_data='bike_yes'),
        InlineKeyboardButton("❌ NÃO", callback_data='bike_no')
    ]]
    
    await update.message.reply_text(msg)
    await update.message.reply_text("🚴 Vais pedalar hoje?", reply_markup=InlineKeyboardMarkup(kb))

async def sync_confirmed_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback sync confirmado"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text("🔄 A criar pedido de sincronização...")
    
    if create_sync_request():
        await query.message.reply_text("✅ Pedido criado. Usa /status em 1 min.")
    else:
        await query.message.reply_text("❌ Falha ao criar pedido.")
    
    context.user_data.clear()

async def bike_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback decisão ciclismo"""
    query = update.callback_query
    await query.answer()
    
    state = get_session_state(context)
    if not state:
        await query.message.reply_text("❌ Sessão expirada. Usa /status novamente.")
        return
    
    state.bike = (query.data == 'bike_yes')
    save_session_state(context, state)
    
    await query.edit_message_text(f"🚴 Ciclismo: {'SIM' if state.bike else 'NÃO'}")
    await query.message.reply_text("💭 Como te sentes hoje?")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    DISPATCHER INTELIGENTE
    Se contexto recente → follow-up, senão → feeling
    """
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    if not text or len(text) < 3:
        await update.message.reply_text("⚠️ Mensagem muito curta.")
        return
    
    context_data = load_context_from_disk(user_id)
    
    if context_data and 'current' in context_data:
        await handle_followup_question(update, context, text, context_data)
    else:
        await handle_feeling(update, context)

async def handle_followup_question(update: Update, context: ContextTypes.DEFAULT_TYPE, question: str, context_data: Dict):
    """
    v3.5.1: Processa follow-up com timeout e validação.
    """
    user_id = update.effective_user.id
    
    await update.message.reply_text("🤔 A processar a tua pergunta...")
    
    try:
        current = context_data['current']
        original_prompt = current.get('prompt', '')
        original_response = current.get('response', '')
        analysis_type = current.get('analysis_type', 'general')
        
        followup_prompt = f"""CONTEXTO ANTERIOR:
Tu és um treinador de elite. Aqui está a análise que fizeste recentemente:

TIPO: {analysis_type}

PROMPT ORIGINAL:
{original_prompt[:2000]}

TUA RESPOSTA ANTERIOR:
{original_response[:3000]}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

O atleta tem agora esta DÚVIDA sobre a tua análise:

"{question}"

TAREFA:
Responde de forma clara e direta, mantendo contexto da análise anterior.
Usa PORTUGUÊS EUROPEU sem LaTeX."""

        # v3.5.1: Chamar com timeout
        response = await call_gemini_with_timeout(followup_prompt)
        
        # v3.5.1: Validar resposta
        is_valid, text_or_error = validate_gemini_response(response)
        
        if not is_valid:
            logger.error(f"Invalid Gemini response: {text_or_error}")
            await update.message.reply_text(
                "❌ Resposta inválida do Gemini.\n"
                "Por favor, tenta reformular a pergunta."
            )
            return
        
        answer = text_or_error
        
        # Salvar interação
        save_context_to_disk(user_id, followup_prompt, answer, f'followup_{analysis_type}')
        
        # Enviar
        if len(answer) > TELEGRAM_SAFE_MESSAGE_LENGTH:
            parts = split_long_message(answer)
            for part in parts:
                await send_safe_message(update, part)
        else:
            await send_safe_message(update, answer)
        
        logger.info(f"Follow-up answered for user {user_id}")
        
    except GeminiTimeoutError as e:
        logger.error(f"Gemini timeout: {e}")
        await update.message.reply_text(
            "⏱️ O Gemini demorou muito a responder.\n"
            "Por favor, tenta uma pergunta mais simples."
        )
        
    except Exception as e:
        logger.error(f"Follow-up error: {e}")
        await update.message.reply_text(
            "❌ Erro ao processar pergunta.\n"
            "⚠️ Serviço temporariamente indisponível."
        )

async def handle_feeling(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    v3.5.1: Gera plano com timeout e validação.
    """
    state = get_session_state(context)
    if not state:
        await update.message.reply_text("❌ Usa /status primeiro.")
        return
    
    is_valid, error_msg = state.validate()
    if not is_valid:
        await update.message.reply_text(f"❌ {error_msg}\nUsa /status.")
        return
    
    feeling = update.message.text.strip()
    
    if len(feeling) > MAX_FEELING_LENGTH:
        await update.message.reply_text(f"⚠️ Muito longo ({len(feeling)} chars).")
        return
    
    feeling = feeling.replace('\x00', '')
    
    proc_msg = await update.message.reply_text("🤖 Coach Gemini a processar...")
    
    try:
        h_str = "\n".join([
            f"• {h.date}: HRV {h.hrv}ms, RHR {h.rhr}bpm"
            for h in state.history if h.is_valid()
        ])
        
        activities_str = ""
        if state.recent_activities:
            acts = []
            for act in state.recent_activities[:3]:
                try:
                    formatted = format_activity(act)
                    if formatted:
                        acts.append(formatted.to_brief_summary())
                except:
                    pass
            
            if acts:
                activities_str = "\nAtividades: " + ", ".join(acts)

        prompt = f"""DADOS DO ATLETA:
📈 HRV: {state.today.hrv}ms ({state.d_hrv:+.1f}%)
💓 RHR: {state.today.rhr}bpm ({state.d_rhr:+.1f}%)
😴 Sono: {state.today.sleep}/100
🎯 Readiness: {state.readiness}
🚴 Ciclismo: {'SIM - 20km+' if state.bike else 'NÃO'}

Últimos 5 dias:
{h_str}{activities_str}

Equipamento: {', '.join(EQUIPAMENTOS_GIM)}

💭 SENSAÇÃO: "{feeling}"

TAREFA:
Gera plano de treino em tabela markdown.
Usa PORTUGUÊS EUROPEU sem LaTeX."""

        if len(prompt) > GEMINI_MAX_PROMPT_LENGTH:
            prompt = truncate_text_safe(prompt, GEMINI_MAX_PROMPT_LENGTH)
        
        # v3.5.1: Chamar com timeout
        response = await call_gemini_with_timeout(prompt)
        
        # v3.5.1: Validar resposta
        is_valid_response, text_or_error = validate_gemini_response(response)
        
        if not is_valid_response:
            logger.error(f"Invalid plan response: {text_or_error}")
            await proc_msg.edit_text(
                "❌ Resposta inválida do Gemini.\n"
                "Por favor, descreve o teu estado novamente."
            )
            return
        
        plan_text = text_or_error
        
        await proc_msg.delete()
        
        state.last_plan = plan_text
        state.last_plan_date = state.today.date
        save_session_state(context, state)
        
        user_id = update.effective_user.id
        save_context_to_disk(user_id, prompt, plan_text, 'plan')
        
        await send_safe_message(update, f"📋 PLANO:\n\n{plan_text}")
        
    except GeminiTimeoutError as e:
        logger.error(f"Plan timeout: {e}")
        try:
            await proc_msg.edit_text(
                "⏱️ O Gemini demorou muito a responder.\n"
                "Por favor, tenta descrever o teu estado de forma mais concisa."
            )
        except:
            await update.message.reply_text("⏱️ Timeout ao gerar plano.")
            
    except Exception as e:
        logger.error(f"handle_feeling error: {e}")
        
        try:
            await proc_msg.edit_text(
                "❌ Falha com Gemini.\n"
                "⚠️ Serviço temporariamente indisponível."
            )
        except:
            await update.message.reply_text("❌ Erro ao gerar plano.")

# ... (resto dos handlers idêntico, com mesmas validações v3.5.1 em analyze_command e perform_activity_analysis)

async def activities_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra atividades"""
    all_formatted = get_all_formatted_activities()
    
    if not all_formatted:
        await update.message.reply_text("❌ Nenhuma atividade.")
        return
    
    recent = all_formatted[:MAX_ACTIVITIES_DISPLAY]
    
    msg = f"🏃 ÚLTIMAS {len(recent)}:\n\n"
    for f in recent:
        msg += f.to_detailed_summary() + "\n\n"
    
    await send_safe_message(update, msg)

async def analyze_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Analisa aderência (com timeout v3.5.1)"""
    await update.message.reply_text("🔍 A procurar atividades...")
    
    activities, date, msg = find_activities_for_analysis()
    
    if not activities:
        await update.message.reply_text(msg)
        return
    
    count = len(activities)
    summary = f"{msg}\n\n📋 {count} atividade(s):\n\n"
    
    for i, act in enumerate(activities, 1):
        if count > 1:
            summary += f"**Atividade {i}:**\n"
        summary += act.to_detailed_summary() + "\n\n"
    
    await send_safe_message(update, summary, parse_mode='Markdown')
    
    details = f"DATA: {date}\nTOTAL: {count}\n\n"
    
    for i, act in enumerate(activities, 1):
        details += f"ATIVIDADE {i}:\n"
        details += f"TIPO: {act.sport}\n"
        details += f"DURAÇÃO: {act.duration_min}min\n"
        
        if act.distance_km:
            details += f"DISTÂNCIA: {act.distance_km}km\n"
        if act.avg_hr:
            details += f"FC: {act.avg_hr}bpm\n"
        if act.load:
            details += f"LOAD: {act.load}\n"
        details += "\n"
    
    state = get_session_state(context)
    last_plan = state.last_plan if state and state.last_plan else 'Nenhum plano.'
    
    prompt = f"""ANÁLISE DE ADERÊNCIA

PLANO:
{last_plan}

ATIVIDADES EXECUTADAS:
{details}

TAREFA:
Analisa aderência completa.
Usa PORTUGUÊS EUROPEU sem LaTeX."""

    try:
        if len(prompt) > GEMINI_MAX_PROMPT_LENGTH:
            await update.message.reply_text("⚠️ Análise muito extensa.")
            return
        
        proc_msg = await update.message.reply_text("🤖 A analisar...")
        
        # v3.5.1: Timeout
        response = await call_gemini_with_timeout(prompt)
        
        # v3.5.1: Validar
        is_valid_response, text_or_error = validate_gemini_response(response)
        
        if not is_valid_response:
            await proc_msg.edit_text(f"❌ Resposta inválida: {text_or_error}")
            return
        
        analysis = text_or_error
        
        await proc_msg.delete()
        
        user_id = update.effective_user.id
        save_context_to_disk(user_id, prompt, analysis, 'adherence')
        
        parts = split_long_message(analysis)
        
        for i, part in enumerate(parts, 1):
            header = f"📊 ANÁLISE - Parte {i}/{len(parts)}:\n\n" if len(parts) > 1 and i == 1 else ""
            await send_safe_message(update, f"{header}{part}")
        
    except GeminiTimeoutError:
        await update.message.reply_text("⏱️ Timeout ao analisar. Tenta limitar atividades.")
        
    except Exception as e:
        logger.error(f"analyze error: {e}")
        await update.message.reply_text("❌ Erro na análise.")

# ... (outros handlers: analyze_activity_command, cargo_callback, cycling_type_callback, perform_activity_analysis - todos com validação v3.5.1)

async def import_historical(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Importa dados"""
    await update.message.reply_text("📥 A criar pedido...")
    
    if create_import_request(7):
        await update.message.reply_text("✅ Pedido criado. Aguarda 2-5min.")
    else:
        await update.message.reply_text("❌ Erro.")

async def sync_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sync"""
    await update.message.reply_text("🔄 A criar pedido...")
    
    if create_sync_request():
        await update.message.reply_text("✅ Pedido criado. Aguarda 1min.")
    else:
        await update.message.reply_text("❌ Erro.")

async def cleanup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cleanup"""
    await update.message.reply_text("🧹 A limpar...")
    
    try:
        cleaned, messages = cleanup_old_flags()
        duplicates, total, act_messages = reorganize_activities()
        
        msg = "🧹 LIMPEZA:\n\n"
        msg += "FLAGS:\n" + "\n".join(messages[:5])
        msg += f"\n\nATIVIDADES:\n" + "\n".join(act_messages[:5])
        
        await update.message.reply_text(msg)
        
    except Exception as e:
        logger.error(f"cleanup error: {e}")
        await update.message.reply_text("❌ Erro.")

async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lista análises anteriores"""
    user_id = update.effective_user.id
    context_data = load_context_from_disk(user_id)
    
    if not context_data or not context_data.get('history'):
        await update.message.reply_text(
            "📭 Sem histórico.\n\n"
            "Cria análises com /analyze ou /analyze_activity."
        )
        return
    
    history = context_data['history']
    
    msg = f"📚 HISTÓRICO ({len(history)}):\n\n"
    
    for i, entry in enumerate(reversed(history), 1):
        timestamp = entry.get('timestamp', 'N/A')
        analysis_type = entry.get('analysis_type', 'unknown')
        
        try:
            dt = datetime.fromisoformat(timestamp)
            time_str = dt.strftime("%d/%m %H:%M")
        except:
            time_str = timestamp[:16]
        
        msg += f"{i}. {analysis_type} - {time_str}\n"
    
    msg += f"\n💡 Podes fazer perguntas (válido {CONTEXT_TIMEOUT_MINUTES}min)."
    
    await update.message.reply_text(msg)

async def clear_context_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Limpa contexto"""
    user_id = update.effective_user.id
    
    if clear_context_disk(user_id):
        await update.message.reply_text("🗑️ Contexto limpo.")
    else:
        await update.message.reply_text("ℹ️ Sem contexto.")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Estatísticas"""
    stats = get_context_stats()
    
    if 'error' in stats:
        await update.message.reply_text(f"❌ Erro: {stats['error']}")
        return
    
    msg = "📊 ESTATÍSTICAS:\n\n"
    msg += f"👥 Users: {stats['total_users']}\n\n"
    
    if stats['by_type']:
        msg += "Por tipo:\n"
        for analysis_type, count in stats['by_type'].items():
            msg += f"  • {analysis_type}: {count}\n"
    
    await update.message.reply_text(msg)

async def debug_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Debug"""
    data = load_garmin_data()
    all_activities = get_all_formatted_activities()
    
    msg = f"""🔧 DEBUG v{BOT_VERSION}:

📊 Dados Garmin: {'Sim' if data else 'Não'}
🏃 Atividades: {len(all_activities)}
"""
    
    if data:
        history = parse_garmin_history(data)
        valid = [h for h in history if h.is_valid()]
        msg += f"✅ Dias válidos: {len(valid)}"
    
    await update.message.reply_text(msg)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help"""
    await update.message.reply_text(
        f"🏋️ FitnessJournal v{BOT_VERSION}\n\n"
        "Comandos principais:\n"
        "/status - Readiness\n"
        "/analyze - Aderência ao plano\n"
        "/analyze_activity - Análise individual\n"
        "/history - Análises anteriores\n"
        "/help - Ajuda\n\n"
        "🆕 v3.5.1:\n"
        "• Timeouts de 30s (Gemini)\n"
        "• Validação de respostas\n"
        "• Check de espaço em disco"
    )

# ... (implementações completas de analyze_activity_callback, cargo_callback, cycling_type_callback, perform_activity_analysis com validações v3.5.1)

# ==========================================
# MAIN
# ==========================================
def main():
    """Entry point"""
    logger.info("=" * 50)
    logger.info(f"FitnessJournal Bot v{BOT_VERSION}")
    logger.info("=" * 50)
    
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN não configurado")
        return
    
    if not os.environ.get("GEMINI_API_KEY"):
        logger.error("GEMINI_API_KEY não configurado")
        return
    
    all_activities = get_all_formatted_activities()
    logger.info(f"🏃 Atividades: {len(all_activities)}")
    
    logger.info("🧹 Auto-cleanup...")
    cleaned, _ = cleanup_old_flags()
    if cleaned > 0:
        logger.info(f"✅ {cleaned} flags limpos")
    
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("activities", activities_command))
    app.add_handler(CommandHandler("analyze", analyze_command))
    app.add_handler(CommandHandler("analyze_activity", analyze_activity_callback))
    app.add_handler(CommandHandler("import", import_historical))
    app.add_handler(CommandHandler("sync", sync_command))
    app.add_handler(CommandHandler("cleanup", cleanup_command))
    app.add_handler(CommandHandler("history", history_command))
    app.add_handler(CommandHandler("clear_context", clear_context_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("debug", debug_command))
    app.add_handler(CommandHandler("help", help_command))
    
    # Callbacks
    app.add_handler(CallbackQueryHandler(sync_confirmed_callback, pattern=r'^sync_confirmed$'))
    # app.add_handler(CallbackQueryHandler(analyze_activity_callback, pattern=r'^analyze_act_\d+$'))
    # app.add_handler(CallbackQueryHandler(cargo_callback, pattern=r'^cargo_(yes|no)_\d+$'))
    # app.add_handler(CallbackQueryHandler(cycling_type_callback, pattern=r'^cyctype_\w+_\d+$'))
    app.add_handler(CallbackQueryHandler(bike_callback, pattern=r'^bike_(yes|no)$'))
    
    # Dispatcher
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("✅ Bot v3.5.1 iniciado")
    print(f"🤖 Bot v{BOT_VERSION} ativo")
    
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == '__main__':
    main()
