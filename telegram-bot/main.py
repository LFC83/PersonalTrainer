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
import hashlib
from collections import defaultdict

# ==========================================
# LOGGING SETUP
# ==========================================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==========================================
# CONFIGURATION & CONSTANTS (v3.9.0)
# ==========================================
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
logger.info("Gemini API configurada")

# Bot Configuration
BOT_VERSION = "3.9.0"
BOT_VERSION_DESC = "Fixed Garmin Parser + UX Improvements + Biometric Deep Nested Access"
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
GEMINI_TIMEOUT_SECONDS = 45  # v3.9.0: Retornado para 45s conforme pedido

# Context Management
CONTEXT_TIMEOUT_MINUTES = 15
MAX_CONTEXT_HISTORY = 3

# Disk Space
MIN_DISK_SPACE_MB = 10

# Cycling Types
CYCLING_TYPES = ["Spinning", "MTB", "Commute", "Estrada"]

# v3.6+: Retry & Circuit Breaker
MAX_RETRIES = 3
RETRY_DELAYS = [2, 5, 10]
CIRCUIT_BREAKER_THRESHOLD = 5
CIRCUIT_BREAKER_TIMEOUT = 60

# v3.6+: Rate Limiting
RATE_LIMIT_WINDOW = 60
RATE_LIMIT_MAX_REQUESTS = 10

# v3.6+: Cache
RESPONSE_CACHE_SIZE = 100

# v3.8.0: Health Check
GEMINI_LATENCY_HISTORY_SIZE = 10

# ==========================================
# SYSTEM PROMPT (v3.9.0 - ATUALIZADO)
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

### CRITÉRIOS BIOMÉTRICOS RIGOROSOS (v3.9.0 - PRIORIDADE ABSOLUTA):
**REGRA FUNDAMENTAL:** Quando os dados biométricos (HRV baixa/RHR elevada) indicarem fadiga, mas o feedback subjetivo do utilizador for positivo (ex: "sinto-me bem"), DEVES DAR PRIORIDADE ABSOLUTA AOS DADOS BIOLÓGICOS e alertar para o risco de fadiga mascarada.

1. HRV menor que 95% da média = APENAS RECUPERAÇÃO ATIVA.
2. RHR maior que mais 2% da média = FADIGA DETETADA (Reduzir volume em 50%).
3. PONTUAÇÃO DE SONO menor que 75 mais Sensação Negativa = ZERO INTENSIDADE.
4. **FADIGA MASCARADA (v3.9.0):** Se HRV está baixa OU RHR está elevada, mas o atleta reporta sentir-se bem:
   - Explica a discrepância entre sensação subjetiva e realidade fisiológica
   - Alerta para o perigo de ignorar os sinais biométricos
   - Prescreve treino baseado nos dados objetivos (HRV/RHR), NÃO no sentimento
   - Exemplo: "Reportas sentir-te fresco, mas a tua HRV está 12% abaixo da média. Isto indica fadiga neuromuscular que ainda não percebes conscientemente. APENAS recuperação ativa hoje."

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
    model_name='gemini-3-flash-preview',
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

class CircuitBreakerOpen(Exception):
    """Circuit breaker está aberto"""
    pass

class RateLimitExceeded(Exception):
    """Rate limit excedido"""
    pass

# ==========================================
# DATA MODELS (v3.9.0)
# ==========================================
@dataclass
class BiometricDay:
    """Dados biométricos de um dia"""
    date: str
    hrv: Optional[float] = None
    rhr: Optional[int] = None
    sleep: Optional[int] = None
    steps: Optional[int] = None
    training_load: Optional[float] = None
    
    def is_valid(self) -> bool:
        """Verifica se tem dados mínimos para análise"""
        return self.hrv is not None and self.rhr is not None
    
    def is_empty(self) -> bool:
        """Verifica se todos os campos estão vazios"""
        return all(v is None for v in [self.hrv, self.rhr, self.sleep, self.steps, self.training_load])

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
    elevation_gain: Optional[float] = None
    avg_cadence: Optional[int] = None
    max_cadence: Optional[int] = None
    bike_cadence: Optional[int] = None
    raw: Dict = field(default_factory=dict)
    
    def to_brief_summary(self) -> str:
        """Retorna resumo breve formatado"""
        parts = [f"{self.date or 'N/A'}", self.sport, f"{self.duration_min}min"]
        
        if self.distance_km:
            parts.append(f"{self.distance_km}km")
        
        if self.avg_hr:
            parts.append(f"{self.avg_hr}bpm")
        
        if self.elevation_gain:
            parts.append(f"D+{int(self.elevation_gain)}m")
        
        if self.bike_cadence:
            parts.append(f"{self.bike_cadence}rpm")
        
        return " | ".join(parts)
    
    def to_detailed_text(self) -> str:
        """Retorna texto detalhado formatado"""
        lines = [f"📅 {self.date or 'N/A'}"]
        lines.append(f"🏃 {self.sport}")
        lines.append(f"⏱️ {self.duration_min}min")
        
        if self.distance_km:
            lines.append(f"📏 {self.distance_km}km")
        
        if self.avg_hr:
            lines.append(f"❤️ {self.avg_hr}bpm")
        
        if self.calories:
            lines.append(f"🔥 {self.calories}kcal")
        
        if self.elevation_gain:
            lines.append(f"⛰️ D+ {int(self.elevation_gain)}m")
        
        if self.bike_cadence:
            lines.append(f"🚴 {self.bike_cadence}rpm (cadência)")
        
        if self.avg_cadence:
            lines.append(f"👟 {self.avg_cadence}spm (cadência)")
            if self.max_cadence:
                lines.append(f"👟 Max: {self.max_cadence}spm")
        
        if self.intensity:
            lines.append(f"💪 {self.intensity}")
        
        if self.load:
            lines.append(f"📊 Carga: {self.load}")
        
        return "\n".join(lines)

# ==========================================
# GLOBAL STATE (v3.9.0: + session state)
# ==========================================
class CircuitBreaker:
    def __init__(self):
        self.failure_count = 0
        self.last_failure_time = 0
        self.state = 'closed'
    
    def can_attempt(self) -> bool:
        if self.state == 'closed':
            return True
        
        if time.time() - self.last_failure_time > CIRCUIT_BREAKER_TIMEOUT:
            self.state = 'half-open'
            return True
        
        return False
    
    def call_succeeded(self):
        self.failure_count = 0
        self.state = 'closed'
    
    def call_failed(self):
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.failure_count >= CIRCUIT_BREAKER_THRESHOLD:
            self.state = 'open'
            logger.warning(f"Circuit breaker OPEN após {self.failure_count} falhas")

class RateLimiter:
    def __init__(self):
        self.requests = defaultdict(list)
    
    def can_proceed(self, user_id: int) -> bool:
        now = time.time()
        user_requests = self.requests[user_id]
        
        # Remove requests antigos
        user_requests = [t for t in user_requests if now - t < RATE_LIMIT_WINDOW]
        self.requests[user_id] = user_requests
        
        if len(user_requests) >= RATE_LIMIT_MAX_REQUESTS:
            return False
        
        user_requests.append(now)
        return True

class ResponseCache:
    def __init__(self):
        self.cache = {}
        self.timestamps = {}
    
    def make_key(self, prompt: str) -> str:
        return hashlib.md5(prompt.encode()).hexdigest()
    
    def get(self, key: str) -> Optional[str]:
        if key not in self.cache:
            return None
        
        if time.time() - self.timestamps[key] > CACHE_TTL_SECONDS:
            del self.cache[key]
            del self.timestamps[key]
            return None
        
        return self.cache[key]
    
    def set(self, key: str, value: str):
        if len(self.cache) >= RESPONSE_CACHE_SIZE:
            oldest_key = min(self.timestamps.keys(), key=lambda k: self.timestamps[k])
            del self.cache[oldest_key]
            del self.timestamps[oldest_key]
        
        self.cache[key] = value
        self.timestamps[key] = time.time()

class HealthCheckState:
    def __init__(self):
        self.gemini_latencies = []
        self.last_check_time = 0
        self.last_disk_check = None
        self.last_integrity_check = None
    
    def record_gemini_latency(self, latency: float):
        """Regista latência de chamada ao Gemini"""
        self.gemini_latencies.append(latency)
        if len(self.gemini_latencies) > GEMINI_LATENCY_HISTORY_SIZE:
            self.gemini_latencies.pop(0)
    
    def get_avg_gemini_latency(self) -> Optional[float]:
        """Retorna latência média do Gemini"""
        if not self.gemini_latencies:
            return None
        return round(mean(self.gemini_latencies), 2)

# v3.9.0: Session state para feeling prompt
class SessionState:
    def __init__(self):
        self.user_sessions = {}
    
    def set_waiting_feeling(self, user_id: int):
        """Marca que estamos aguardando resposta de feeling"""
        self.user_sessions[user_id] = {
            'state': 'waiting_feeling',
            'timestamp': time.time()
        }
    
    def get_user_state(self, user_id: int) -> Optional[str]:
        """Retorna estado atual do utilizador"""
        if user_id not in self.user_sessions:
            return None
        
        session = self.user_sessions[user_id]
        # Expira após 5 minutos
        if time.time() - session['timestamp'] > 300:
            del self.user_sessions[user_id]
            return None
        
        return session.get('state')
    
    def clear_user_state(self, user_id: int):
        """Limpa estado do utilizador"""
        if user_id in self.user_sessions:
            del self.user_sessions[user_id]

# Global instances
circuit_breaker = CircuitBreaker()
rate_limiter = RateLimiter()
response_cache = ResponseCache()
health_state = HealthCheckState()
session_state = SessionState()  # v3.9.0

# ==========================================
# FILESYSTEM OPERATIONS (v3.9.0)
# ==========================================
def ensure_data_dir():
    """Garante que DATA_DIR existe"""
    os.makedirs(DATA_DIR, exist_ok=True)

def load_garmin_data() -> Optional[Dict]:
    """Carrega dados do Garmin (garmin_dump.json)"""
    ensure_data_dir()
    try:
        path = os.path.join(DATA_DIR, 'garmin_dump.json')
        if not os.path.exists(path):
            return None
        
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Erro ao carregar garmin_dump.json: {e}")
        return None

def load_garmin_consolidated() -> Optional[Dict]:
    """
    v3.9.0: Carrega dados consolidados do Garmin (garmin_data_consolidated.json)
    Este ficheiro tem a estrutura aninhada correta para biometria
    """
    ensure_data_dir()
    try:
        path = os.path.join(DATA_DIR, 'garmin_data_consolidated.json')
        if not os.path.exists(path):
            logger.debug("garmin_data_consolidated.json não encontrado")
            return None
        
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Erro ao carregar garmin_data_consolidated.json: {e}")
        return None

def load_activities_index() -> Dict:
    """
    Carrega índice de atividades
    v3.8.0: BLINDAGEM - Converte list para dict se necessário
    """
    ensure_data_dir()
    try:
        path = os.path.join(DATA_DIR, 'activities.json')
        if not os.path.exists(path):
            return {}
        
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # v3.8.0: BLINDAGEM CRÍTICA - Se for lista, converte para dict
        if isinstance(data, list):
            logger.warning("⚠️ activities.json é uma lista! Convertendo para dict...")
            converted = {}
            for item in data:
                if isinstance(item, dict) and 'id' in item:
                    converted[str(item['id'])] = item
                elif isinstance(item, dict) and 'activityId' in item:
                    converted[str(item['activityId'])] = item
            
            logger.info(f"✅ Convertidos {len(converted)} itens de list para dict")
            
            # Salva imediatamente no formato correto
            save_activities_index(converted)
            return converted
        
        # Se já for dict, retorna normalmente
        if isinstance(data, dict):
            return data
        
        # Se não for nem list nem dict, retorna vazio e loga erro
        logger.error(f"❌ activities.json tem tipo inválido: {type(data)}")
        return {}
        
    except Exception as e:
        logger.error(f"Erro ao carregar activities.json: {e}")
        return {}

def save_activities_index(activities: Dict):
    """
    Salva índice de atividades
    v3.8.0: ATOMIC WRITE + VALIDAÇÃO
    """
    ensure_data_dir()
    
    # v3.8.0: VALIDAÇÃO PRÉ-ESCRITA
    if not isinstance(activities, dict):
        logger.error(f"❌ CRÍTICO: Tentativa de salvar activities como {type(activities)}")
        raise FileOperationError(f"Activities deve ser dict, não {type(activities)}")
    
    try:
        path = os.path.join(DATA_DIR, 'activities.json')
        temp_path = path + '.tmp'
        
        # v3.8.0: ATOMIC WRITE
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(activities, f, ensure_ascii=False, indent=2)
        
        # Move atomicamente
        os.replace(temp_path, path)
        
        logger.debug(f"✅ activities.json salvo com {len(activities)} entradas")
        
    except Exception as e:
        logger.error(f"Erro ao salvar activities.json: {e}")
        temp_path = os.path.join(DATA_DIR, 'activities.json.tmp')
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except:
                pass
        raise FileOperationError(f"Falha ao salvar atividades: {e}")

def check_disk_space() -> Tuple[bool, float]:
    """Verifica espaço em disco"""
    try:
        stat = os.statvfs(DATA_DIR)
        free_mb = (stat.f_bavail * stat.f_frsize) / (1024 * 1024)
        return free_mb >= MIN_DISK_SPACE_MB, free_mb
    except Exception as e:
        logger.error(f"Erro ao verificar espaço em disco: {e}")
        return True, -1

def check_activities_integrity() -> Tuple[bool, str]:
    """
    v3.8.0: Verifica integridade do activities.json
    """
    try:
        path = os.path.join(DATA_DIR, 'activities.json')
        
        if not os.path.exists(path):
            return True, "Ficheiro não existe (OK)"
        
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        if isinstance(data, dict):
            return True, f"Dict válido com {len(data)} entradas"
        elif isinstance(data, list):
            return False, f"CORRUPTO: É lista com {len(data)} itens (será corrigido no próximo load)"
        else:
            return False, f"CORRUPTO: Tipo inválido {type(data)}"
            
    except json.JSONDecodeError as e:
        return False, f"JSON INVÁLIDO: {str(e)[:50]}"
    except Exception as e:
        return False, f"ERRO: {str(e)[:50]}"

# ==========================================
# FLAG MANAGEMENT
# ==========================================
def get_flag_path(flag_name: str) -> str:
    """Retorna caminho para arquivo de flag"""
    ensure_data_dir()
    return os.path.join(DATA_DIR, f"{flag_name}.flag")

def create_flag(flag_name: str, data: Optional[Dict] = None) -> bool:
    """Cria flag com dados opcionais"""
    try:
        path = get_flag_path(flag_name)
        with open(path, 'w') as f:
            json.dump(data or {'created': time.time()}, f)
        return True
    except Exception as e:
        logger.error(f"Erro ao criar flag {flag_name}: {e}")
        return False

def flag_exists(flag_name: str) -> bool:
    """Verifica se flag existe"""
    return os.path.exists(get_flag_path(flag_name))

def remove_flag(flag_name: str) -> bool:
    """Remove flag"""
    try:
        path = get_flag_path(flag_name)
        if os.path.exists(path):
            os.remove(path)
            return True
        return False
    except Exception as e:
        logger.error(f"Erro ao remover flag {flag_name}: {e}")
        return False

def cleanup_old_flags() -> Tuple[int, List[str]]:
    """Remove flags antigas"""
    cleaned = 0
    messages = []
    
    try:
        ensure_data_dir()
        now = time.time()
        
        for file in os.listdir(DATA_DIR):
            if not file.endswith('.flag'):
                continue
            
            path = os.path.join(DATA_DIR, file)
            age = now - os.path.getmtime(path)
            
            if age > FLAG_TIMEOUT_SECONDS:
                os.remove(path)
                cleaned += 1
                messages.append(f"✓ {file}: {int(age/60)}min")
        
        if cleaned == 0:
            messages.append("✓ Sem flags antigas")
        
    except Exception as e:
        logger.error(f"Erro no cleanup: {e}")
        messages.append(f"✗ Erro: {e}")
    
    return cleaned, messages

# ==========================================
# IMPORT/SYNC REQUESTS
# ==========================================
def create_import_request(days: int = 7) -> bool:
    """Cria pedido de importação"""
    return create_flag('import_request', {'days': days})

def create_sync_request() -> bool:
    """Cria pedido de sync"""
    return create_flag('sync_request')

# ==========================================
# DATA EXTRACTION (v3.9.0: CORRIGIDO)
# ==========================================
def extract_elevation_from_raw(raw_data: Dict, sport: str) -> Optional[float]:
    """Extrai ganho de elevação do JSON raw da Garmin"""
    try:
        elevation_fields = [
            'elevationGain',
            'totalElevationGain',
            'totalAscent',
            'gainElevation'
        ]
        
        for field in elevation_fields:
            value = raw_data.get(field)
            if value is not None and isinstance(value, (int, float)):
                return round(float(value), 1)
        
        return None
    except Exception as e:
        logger.debug(f"Erro ao extrair elevação: {e}")
        return None

def extract_run_cadence_from_raw(raw_data: Dict, sport: str) -> Tuple[Optional[int], Optional[int]]:
    """Extrai cadência de corrida (spm) do JSON raw da Garmin"""
    try:
        avg_cadence = None
        max_cadence = None
        
        if "Corr" in sport or "Run" in sport or "run" in sport.lower():
            cadence_fields_avg = ['averageRunCadence', 'avgRunCadence', 'averageCadence']
            cadence_fields_max = ['maxRunCadence', 'maxCadence']
            
            for field in cadence_fields_avg:
                value = raw_data.get(field)
                if value is not None and isinstance(value, (int, float)):
                    if value < 120:
                        avg_cadence = int(value * 2)
                    else:
                        avg_cadence = int(value)
                    break
            
            for field in cadence_fields_max:
                value = raw_data.get(field)
                if value is not None and isinstance(value, (int, float)):
                    if value < 120:
                        max_cadence = int(value * 2)
                    else:
                        max_cadence = int(value)
                    break
        
        return avg_cadence, max_cadence
    except Exception as e:
        logger.debug(f"Erro ao extrair cadência de corrida: {e}")
        return None, None

def extract_bike_cadence_from_raw(raw_data: Dict, sport: str) -> Optional[int]:
    """
    v3.9.0: Extrai cadência de ciclismo (rpm) do JSON raw da Garmin
    CORRIGIDO: Acesso seguro com .get()
    """
    try:
        # Verifica se é ciclismo
        sport_lower = sport.lower()
        if not any(x in sport_lower for x in ['cicl', 'mtb', 'spin', 'bike', 'cycling', 'road_biking']):
            return None
        
        # v3.9.0: CORRIGIDO - Campos corretos
        bike_cadence_fields = [
            'averageBikingCadenceInRevolutionsPerMinute',
            'avgBikeCadence',
            'averageCadence',
            'cadence'
        ]
        
        for field in bike_cadence_fields:
            value = raw_data.get(field)
            if value is not None and isinstance(value, (int, float)) and value > 0:
                return int(value)
        
        return None
    except Exception as e:
        logger.debug(f"Erro ao extrair cadência de ciclismo: {e}")
        return None

def needs_data_enrichment(activity: Dict) -> bool:
    """Verifica se atividade precisa de enriquecimento de dados"""
    sport = activity.get('sport', '')
    sport_lower = sport.lower()
    
    is_cycling = any(x in sport_lower for x in ['cicl', 'mtb', 'spin', 'bike', 'cycling', 'road_biking'])
    is_running = any(x in sport_lower for x in ['corr', 'run'])
    
    missing_elevation = (is_cycling or is_running) and activity.get('elevation_gain') is None
    missing_run_cadence = is_running and activity.get('avg_cadence') is None
    missing_bike_cadence = is_cycling and activity.get('bike_cadence') is None
    
    return missing_elevation or missing_run_cadence or missing_bike_cadence

def enrich_activity_from_garmin(activity_id: str, garmin_data: Dict) -> Optional[Dict]:
    """
    Enriquece atividade com dados adicionais do Garmin
    """
    try:
        activities_list = garmin_data.get('activities', [])
        
        # Procura atividade pelo ID
        target_activity = None
        for act in activities_list:
            act_id = act.get('activityId')
            if str(act_id) == str(activity_id):
                target_activity = act
                break
        
        if not target_activity:
            return None
        
        sport = target_activity.get('activityType', {}).get('typeKey', 'Unknown')
        
        enrichment = {}
        
        # Elevação
        elevation = extract_elevation_from_raw(target_activity, sport)
        if elevation is not None:
            enrichment['elevation_gain'] = elevation
        
        # Cadência de corrida
        avg_cadence, max_cadence = extract_run_cadence_from_raw(target_activity, sport)
        if avg_cadence:
            enrichment['avg_cadence'] = avg_cadence
        if max_cadence:
            enrichment['max_cadence'] = max_cadence
        
        # Cadência de ciclismo
        bike_cadence = extract_bike_cadence_from_raw(target_activity, sport)
        if bike_cadence:
            enrichment['bike_cadence'] = bike_cadence
        
        return enrichment if enrichment else None
        
    except Exception as e:
        logger.error(f"Erro ao enriquecer atividade {activity_id}: {e}")
        return None

def check_and_enrich_activities():
    """
    Verifica e enriquece atividades que precisam de dados adicionais
    """
    try:
        activities = load_activities_index()
        garmin_data = load_garmin_data()
        
        if not garmin_data:
            logger.info("Sem dados Garmin para enriquecimento")
            return
        
        enriched_count = 0
        
        for activity_id, activity_data in activities.items():
            if needs_data_enrichment(activity_data):
                logger.info(f"Enriquecendo atividade {activity_id}...")
                enrichment = enrich_activity_from_garmin(activity_id, garmin_data)
                
                if enrichment:
                    activity_data.update(enrichment)
                    enriched_count += 1
        
        if enriched_count > 0:
            save_activities_index(activities)
            logger.info(f"✅ {enriched_count} atividades enriquecidas")
    except Exception as e:
        logger.error(f"Erro no enriquecimento automático: {e}")

# ==========================================
# BIOMETRIC DATA PARSING (v3.9.0: CORRIGIDO)
# ==========================================
def get_today_biometrics() -> Optional[BiometricDay]:
    """
    v3.9.0: NOVO - Obtém biometria de hoje do ficheiro consolidado
    Lê do garmin_data_consolidated.json com acesso seguro a campos aninhados
    """
    try:
        consolidated = load_garmin_consolidated()
        if not consolidated:
            logger.debug("Sem dados consolidados disponíveis")
            return None
        
        # Data de hoje
        today_str = date.today().isoformat()
        
        # v3.9.0: ACESSO SEGURO A CAMPOS ANINHADOS
        # HRV: hrv -> hrvSummary -> lastNightAvg
        hrv = None
        hrv_obj = consolidated.get('hrv')
        if hrv_obj and isinstance(hrv_obj, dict):
            hrv_summary = hrv_obj.get('hrvSummary')
            if hrv_summary and isinstance(hrv_summary, dict):
                hrv = hrv_summary.get('lastNightAvg')
        
        # RHR: stats -> restingHeartRate
        rhr = None
        stats_obj = consolidated.get('stats')
        if stats_obj and isinstance(stats_obj, dict):
            rhr = stats_obj.get('restingHeartRate')
        
        # Passos: stats -> totalSteps
        steps = None
        if stats_obj and isinstance(stats_obj, dict):
            steps = stats_obj.get('totalSteps')
        
        # Sono: sleep -> sleepSearchFullResponse -> sleepScore -> value
        # OU dailySleepDTO -> sleepScore -> value
        sleep_score = None
        sleep_obj = consolidated.get('sleep')
        if sleep_obj and isinstance(sleep_obj, dict):
            # Tenta sleepSearchFullResponse primeiro
            sleep_search = sleep_obj.get('sleepSearchFullResponse')
            if sleep_search and isinstance(sleep_search, dict):
                sleep_score_obj = sleep_search.get('sleepScore')
                if sleep_score_obj and isinstance(sleep_score_obj, dict):
                    sleep_score = sleep_score_obj.get('value')
            
            # Se não encontrou, tenta dailySleepDTO
            if sleep_score is None:
                daily_sleep = sleep_obj.get('dailySleepDTO')
                if daily_sleep and isinstance(daily_sleep, dict):
                    sleep_score_obj = daily_sleep.get('sleepScore')
                    if sleep_score_obj and isinstance(sleep_score_obj, dict):
                        sleep_score = sleep_score_obj.get('value')
        
        # Cria BiometricDay
        bio_day = BiometricDay(
            date=today_str,
            hrv=hrv,
            rhr=rhr,
            sleep=sleep_score,
            steps=steps
        )
        
        logger.info(f"📊 Biometria hoje: HRV={hrv}, RHR={rhr}, Sono={sleep_score}, Passos={steps}")
        
        return bio_day if not bio_day.is_empty() else None
        
    except Exception as e:
        logger.error(f"Erro ao obter biometria de hoje: {e}\n{traceback.format_exc()}")
        return None

def parse_garmin_history(data: Dict) -> List[BiometricDay]:
    """
    v3.9.0: CORRIGIDO - Parse dados históricos do Garmin
    Tenta primeiro o consolidado, depois o dump
    """
    history = []
    
    try:
        # Tenta carregar do consolidado primeiro
        consolidated = load_garmin_consolidated()
        if consolidated:
            # Adiciona dados de hoje
            today_bio = get_today_biometrics()
            if today_bio and not today_bio.is_empty():
                history.append(today_bio)
        
        # Adiciona dados do dump histórico
        daily_data = data.get('dailySummaries', [])
        
        for day in daily_data:
            calendar_date = day.get('calendarDate')
            if not calendar_date:
                continue
            
            # Usa .get() para acesso seguro
            hrv = day.get('avgWakingHeartRateVariability')
            rhr = day.get('restingHeartRate')
            sleep_score = day.get('sleepScore')
            steps = day.get('totalSteps')
            training_load = day.get('moderateIntensityMinutes')
            
            bio_day = BiometricDay(
                date=calendar_date,
                hrv=hrv,
                rhr=rhr,
                sleep=sleep_score,
                steps=steps,
                training_load=training_load
            )
            
            if not bio_day.is_empty():
                # Evita duplicar se já temos dados de hoje
                if not any(h.date == calendar_date for h in history):
                    history.append(bio_day)
        
        # Ordena por data (mais recente primeiro)
        history.sort(key=lambda x: x.date, reverse=True)
        
    except Exception as e:
        logger.error(f"Erro ao parsear histórico: {e}")
    
    return history

def get_recent_biometrics(days: int = 7) -> List[BiometricDay]:
    """
    v3.9.0: CORRIGIDO - Obtém biometria recente
    """
    data = load_garmin_data()
    if not data:
        # Se não tem dump, tenta só o consolidado
        today_bio = get_today_biometrics()
        return [today_bio] if today_bio and not today_bio.is_empty() else []
    
    history = parse_garmin_history(data)
    return history[:days]

def calculate_biometric_baseline(history: List[BiometricDay]) -> Dict[str, float]:
    """Calcula baseline biométrico"""
    if not history:
        return {}
    
    valid_days = [d for d in history if d.is_valid()]
    if not valid_days:
        return {}
    
    hrvs = [d.hrv for d in valid_days if d.hrv]
    rhrs = [d.rhr for d in valid_days if d.rhr]
    
    baseline = {}
    
    if hrvs:
        baseline['hrv_mean'] = round(mean(hrvs), 1)
        baseline['hrv_count'] = len(hrvs)
    
    if rhrs:
        baseline['rhr_mean'] = round(mean(rhrs), 1)
        baseline['rhr_count'] = len(rhrs)
    
    return baseline

def format_biometric_context(history: List[BiometricDay], baseline: Dict) -> str:
    """
    Formata contexto biométrico para o prompt do Gemini
    """
    if not history or not baseline:
        return "⚠️ DADOS BIOMÉTRICOS INSUFICIENTES - Usar médias históricas como referência"
    
    latest = history[0]
    
    lines = ["📊 CONTEXTO BIOMÉTRICO (Últimos 7 dias):"]
    lines.append("")
    
    # Baseline
    if 'hrv_mean' in baseline:
        lines.append(f"HRV Média (7d): {baseline['hrv_mean']} ms")
    if 'rhr_mean' in baseline:
        lines.append(f"RHR Média (7d): {baseline['rhr_mean']} bpm")
    
    lines.append("")
    lines.append("📅 HOJE:")
    
    # Dados de hoje
    if latest.hrv:
        hrv_deviation = 0
        if 'hrv_mean' in baseline:
            hrv_deviation = ((latest.hrv - baseline['hrv_mean']) / baseline['hrv_mean']) * 100
        
        status = "✅" if hrv_deviation >= -5 else "⚠️" if hrv_deviation >= -10 else "🔴"
        lines.append(f"{status} HRV: {latest.hrv} ms ({hrv_deviation:+.1f}%)")
    
    if latest.rhr:
        rhr_deviation = 0
        if 'rhr_mean' in baseline:
            rhr_deviation = ((latest.rhr - baseline['rhr_mean']) / baseline['rhr_mean']) * 100
        
        status = "✅" if rhr_deviation <= 2 else "⚠️" if rhr_deviation <= 5 else "🔴"
        lines.append(f"{status} RHR: {latest.rhr} bpm ({rhr_deviation:+.1f}%)")
    
    if latest.sleep:
        status = "✅" if latest.sleep >= 75 else "⚠️" if latest.sleep >= 60 else "🔴"
        lines.append(f"{status} Sono: {latest.sleep}/100")
    
    if latest.steps:
        lines.append(f"👣 Passos: {latest.steps}")
    
    return "\n".join(lines)

# ==========================================
# ACTIVITY PARSING (v3.9.0: CORRIGIDO)
# ==========================================
def parse_activities_from_garmin(data: Dict) -> List[Dict]:
    """
    v3.9.0: CORRIGIDO - Parse atividades do JSON da Garmin
    Acesso correto aos campos aninhados
    """
    activities = []
    
    try:
        activities_data = data.get('activities', [])
        
        for act in activities_data:
            # ID da atividade
            activity_id = act.get('activityId')
            if not activity_id:
                continue
            
            # Data
            date_str = None
            start_time = act.get('startTimeLocal')
            if start_time:
                date_str = start_time[:10]
            
            # v3.9.0: CORRIGIDO - Nome e Tipo
            # Nome: usa activityName, se None usa activityType.typeKey
            activity_name = act.get('activityName')
            
            sport_data = act.get('activityType', {})
            if isinstance(sport_data, dict):
                sport_type = sport_data.get('typeKey', 'Unknown')
            else:
                sport_type = 'Unknown'
            
            # Se não tem nome, usa o tipo
            sport = activity_name if activity_name else sport_type
            
            # v3.9.0: CORRIGIDO - Duração (está em segundos, converter para minutos)
            duration_sec = act.get('duration', 0)
            duration_min = round(duration_sec / 60, 1) if duration_sec else 0
            
            # Distância
            distance_m = act.get('distance')
            distance_km = round(distance_m / 1000, 2) if distance_m else None
            
            # Frequência cardíaca
            avg_hr = act.get('averageHR')
            
            # Calorias
            calories = act.get('calories')
            
            # v3.9.0: CORRIGIDO - Altimetria (campo correto: elevationGain)
            elevation_gain = extract_elevation_from_raw(act, sport)
            
            # Cadências
            avg_cadence, max_cadence = extract_run_cadence_from_raw(act, sport)
            bike_cadence = extract_bike_cadence_from_raw(act, sport)
            
            activity = {
                'id': str(activity_id),
                'date': date_str,
                'sport': sport,
                'duration_min': duration_min,
                'distance_km': distance_km,
                'avg_hr': avg_hr,
                'calories': calories,
                'elevation_gain': elevation_gain,
                'avg_cadence': avg_cadence,
                'max_cadence': max_cadence,
                'bike_cadence': bike_cadence,
                'raw': act
            }
            
            activities.append(activity)
        
        # Ordena por data (mais recente primeiro)
        activities.sort(key=lambda x: x.get('date', ''), reverse=True)
        
    except Exception as e:
        logger.error(f"Erro ao parsear atividades: {e}\n{traceback.format_exc()}")
    
    return activities

def get_all_formatted_activities() -> List[FormattedActivity]:
    """
    v3.9.0: Retorna todas as atividades formatadas
    Ultra-tolerante a campos nulos
    """
    activities_index = load_activities_index()
    formatted = []
    
    if not isinstance(activities_index, dict):
        logger.error(f"❌ activities_index não é dict: {type(activities_index)}")
        return []
    
    for activity_id, activity_data in activities_index.items():
        try:
            # v3.9.0: Usa .get() com defaults seguros
            formatted_act = FormattedActivity(
                date=activity_data.get('date'),
                sport=activity_data.get('sport', 'Unknown'),
                duration_min=activity_data.get('duration_min', 0),
                distance_km=activity_data.get('distance_km'),
                avg_hr=activity_data.get('avg_hr'),
                calories=activity_data.get('calories'),
                intensity=activity_data.get('intensity'),
                load=activity_data.get('load'),
                elevation_gain=activity_data.get('elevation_gain'),
                avg_cadence=activity_data.get('avg_cadence'),
                max_cadence=activity_data.get('max_cadence'),
                bike_cadence=activity_data.get('bike_cadence'),
                raw=activity_data.get('raw', {})
            )
            formatted.append(formatted_act)
        except Exception as e:
            logger.error(f"Erro ao formatar atividade {activity_id}: {e}")
    
    # Ordena por data
    formatted.sort(key=lambda x: x.date or '', reverse=True)
    
    return formatted

def reorganize_activities() -> Tuple[int, int, List[str]]:
    """Reorganiza e limpa atividades duplicadas"""
    messages = []
    
    try:
        data = load_garmin_data()
        if not data:
            messages.append("✗ Sem dados Garmin")
            return 0, 0, messages
        
        raw_activities = parse_activities_from_garmin(data)
        
        # Cria índice por ID
        activities_dict = {}
        for act in raw_activities:
            activities_dict[act['id']] = act
        
        # Limita ao máximo
        if len(activities_dict) > MAX_ACTIVITIES_STORED:
            sorted_ids = sorted(
                activities_dict.keys(),
                key=lambda x: activities_dict[x].get('date', ''),
                reverse=True
            )
            
            kept_ids = sorted_ids[:MAX_ACTIVITIES_STORED]
            activities_dict = {k: v for k, v in activities_dict.items() if k in kept_ids}
            
            messages.append(f"✓ Limitado a {MAX_ACTIVITIES_STORED} atividades")
        
        # Salva
        save_activities_index(activities_dict)
        
        duplicates_removed = len(raw_activities) - len(activities_dict)
        messages.append(f"✓ {len(activities_dict)} atividades únicas")
        
        if duplicates_removed > 0:
            messages.append(f"✓ {duplicates_removed} duplicados removidos")
        
        return duplicates_removed, len(activities_dict), messages
        
    except Exception as e:
        logger.error(f"Erro ao reorganizar: {e}")
        messages.append(f"✗ Erro: {e}")
        return 0, 0, messages

# ==========================================
# GEMINI API (v3.9.0)
# ==========================================
async def call_gemini_with_retry(prompt: str, user_id: int) -> str:
    """
    Chama Gemini com retry inteligente
    """
    # Check circuit breaker
    if not circuit_breaker.can_attempt():
        raise CircuitBreakerOpen("Serviço temporariamente indisponível")
    
    # Check rate limit
    if not rate_limiter.can_proceed(user_id):
        raise RateLimitExceeded("Rate limit excedido. Aguarda 1 minuto.")
    
    # Check cache
    cache_key = response_cache.make_key(prompt)
    cached = response_cache.get(cache_key)
    if cached:
        circuit_breaker.call_succeeded()
        return cached
    
    last_error = None
    was_timeout = False
    
    for attempt in range(MAX_RETRIES):
        try:
            if attempt > 0:
                delay = RETRY_DELAYS[attempt - 1]
                
                if was_timeout:
                    delay = delay * 2
                    logger.info(f"Timeout anterior. Delay aumentado para {delay}s")
                
                logger.info(f"Retry {attempt}/{MAX_RETRIES} após {delay}s...")
                await asyncio.sleep(delay)
            
            # Medir latência
            start_time = time.time()
            
            response = await asyncio.wait_for(
                asyncio.to_thread(model.generate_content, prompt),
                timeout=GEMINI_TIMEOUT_SECONDS
            )
            
            # Registar latência
            latency = time.time() - start_time
            health_state.record_gemini_latency(latency)
            
            if not response or not response.text:
                raise Exception("Resposta vazia do Gemini")
            
            result = response.text.strip()
            
            # Cache e sucesso
            response_cache.set(cache_key, result)
            circuit_breaker.call_succeeded()
            
            if attempt > 0:
                logger.info(f"✅ Sucesso no retry {attempt}")
            
            return result
            
        except asyncio.TimeoutError:
            was_timeout = True
            last_error = GeminiTimeoutError(f"Timeout após {GEMINI_TIMEOUT_SECONDS}s")
            logger.warning(f"⏱️ Timeout na tentativa {attempt + 1}")
            circuit_breaker.call_failed()
            
        except Exception as e:
            was_timeout = False
            last_error = e
            logger.error(f"❌ Erro na tentativa {attempt + 1}: {e}")
            circuit_breaker.call_failed()
    
    # Todas as tentativas falharam
    raise last_error if last_error else Exception("Falha desconhecida no Gemini")

# ==========================================
# CONTEXT MANAGEMENT
# ==========================================
def get_context_file(user_id: int) -> str:
    """Retorna path do ficheiro de contexto"""
    ensure_data_dir()
    return os.path.join(DATA_DIR, f'context_{user_id}.json')

def load_context_from_disk(user_id: int) -> Optional[Dict]:
    """Carrega contexto do disco"""
    try:
        path = get_context_file(user_id)
        if not os.path.exists(path):
            return None
        
        with open(path, 'r', encoding='utf-8') as f:
            context_data = json.load(f)
        
        # Verifica se expirou
        if time.time() - context_data.get('timestamp', 0) > CONTEXT_TIMEOUT_MINUTES * 60:
            return None
        
        return context_data
    except Exception as e:
        logger.error(f"Erro ao carregar contexto: {e}")
        return None

def save_context_to_disk(user_id: int, context_data: Dict):
    """Salva contexto no disco"""
    try:
        path = get_context_file(user_id)
        context_data['timestamp'] = time.time()
        
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(context_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Erro ao salvar contexto: {e}")

def add_to_context_history(user_id: int, command: str, prompt: str, response: str):
    """Adiciona análise ao histórico de contexto"""
    context_data = load_context_from_disk(user_id) or {'history': []}
    
    history = context_data.get('history', [])
    
    history.append({
        'command': command,
        'timestamp': time.time(),
        'prompt_preview': prompt[:200],
        'response_preview': response[:500]
    })
    
    # Limita histórico
    if len(history) > MAX_CONTEXT_HISTORY:
        history = history[-MAX_CONTEXT_HISTORY:]
    
    context_data['history'] = history
    save_context_to_disk(user_id, context_data)

def get_context_for_followup(user_id: int) -> str:
    """Retorna contexto formatado para followup"""
    context_data = load_context_from_disk(user_id)
    
    if not context_data or not context_data.get('history'):
        return ""
    
    lines = ["### CONTEXTO DAS ANÁLISES ANTERIORES:"]
    
    for i, entry in enumerate(context_data['history'], 1):
        lines.append(f"\n## Análise {i} ({entry['command']}):")
        lines.append(entry['response_preview'])
    
    return "\n".join(lines)

def clear_user_context(user_id: int):
    """Limpa contexto do utilizador"""
    try:
        path = get_context_file(user_id)
        if os.path.exists(path):
            os.remove(path)
    except Exception as e:
        logger.error(f"Erro ao limpar contexto: {e}")

# ==========================================
# v3.9.0: SYNC/IMPORT MONITORING
# ==========================================
async def wait_for_sync_completion(update: Update, timeout_seconds: int = 60) -> bool:
    """
    v3.9.0: Aguarda a conclusão do sync monitorando desaparecimento do flag
    Retorna True se completou, False se timeout
    """
    start_time = time.time()
    
    while time.time() - start_time < timeout_seconds:
        if not flag_exists('sync_request'):
            # Flag desapareceu, sync completou
            return True
        
        await asyncio.sleep(2)
    
    return False

async def send_sync_feedback(update: Update, flag_name: str):
    """
    v3.9.0: Envia feedback após sincronização
    """
    try:
        # Aguarda até 60s
        completed = await wait_for_sync_completion(update, timeout_seconds=60)
        
        if not completed:
            await update.message.reply_text(
                "⏱️ Sincronização ainda em progresso...\n"
                "Usa /activities para ver o estado."
            )
            return
        
        # Lê as atividades
        activities = get_all_formatted_activities()
        
        if activities:
            msg = (
                f"✅ Sincronização concluída!\n"
                f"📊 {len(activities)} atividades no total encontradas."
            )
        else:
            msg = "⚠️ Sincronização completou mas sem atividades encontradas."
        
        await update.message.reply_text(msg)
        
    except Exception as e:
        logger.error(f"Erro no feedback de sync: {e}")

# ==========================================
# TELEGRAM HANDLERS (v3.9.0: CORRIGIDOS)
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler /start"""
    await update.message.reply_text(
        f"🏋️ FitnessJournal v{BOT_VERSION}\n\n"
        f"{BOT_VERSION_DESC}\n\n"
        "Usa /help para ver comandos."
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    v3.9.0: CORRIGIDO - Handler /status
    Pergunta como o utilizador se sente ANTES de chamar Gemini
    """
    user_id = update.effective_user.id
    
    try:
        # v3.9.0: NOVO - Pergunta feeling primeiro
        session_state.set_waiting_feeling(user_id)
        
        await update.message.reply_text(
            "🤔 Como te sentes hoje? (0-10)\n\n"
            "0 = Exausto\n"
            "5 = Normal\n"
            "10 = Fresco e pronto\n\n"
            "Responde com um número de 0 a 10."
        )
        
    except Exception as e:
        logger.error(f"Erro em /status: {e}\n{traceback.format_exc()}")
        await update.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def process_status_with_feeling(update: Update, feeling: int):
    """
    v3.9.0: NOVO - Processa /status com o feeling do utilizador
    """
    user_id = update.effective_user.id
    
    try:
        await update.message.reply_text("🔍 Avaliando readiness...")
        
        # Biometria obrigatória
        history = get_recent_biometrics(7)
        baseline = calculate_biometric_baseline(history)
        bio_context = format_biometric_context(history, baseline)
        
        activities = get_all_formatted_activities()
        recent = activities[:MAX_ACTIVITIES_DISPLAY]
        
        prompt = f"""
{bio_context}

### SENSAÇÃO SUBJETIVA DO ATLETA:
Feeling de hoje: {feeling}/10

### ATIVIDADES RECENTES (Últimas {len(recent)}):

"""
        
        for act in recent:
            prompt += f"- {act.to_brief_summary()}\n"
        
        prompt += """

### TAREFA:
Avalia o readiness do atleta.
CRÍTICO: Se HRV/RHR indicarem fadiga mas o feeling for alto (>7), ALERTA para fadiga mascarada.
Se o feeling for baixo (<5) mas biometria OK, investiga recuperação inadequada.
Prescreve treino apropriado usando a tabela obrigatória.
"""
        
        response_text = await call_gemini_with_retry(prompt, user_id)
        
        # Envia resposta
        if len(response_text) <= TELEGRAM_SAFE_MESSAGE_LENGTH:
            await update.message.reply_text(response_text)
        else:
            chunks = [response_text[i:i+TELEGRAM_SAFE_MESSAGE_LENGTH] 
                     for i in range(0, len(response_text), TELEGRAM_SAFE_MESSAGE_LENGTH)]
            for chunk in chunks:
                await update.message.reply_text(chunk)
        
        # Salva contexto
        add_to_context_history(user_id, 'status', prompt, response_text)
        
        # Limpa estado
        session_state.clear_user_state(user_id)
        
    except GeminiTimeoutError:
        await update.message.reply_text(
            f"⏱️ Timeout após {GEMINI_TIMEOUT_SECONDS}s.\n"
            "Tenta novamente."
        )
    except CircuitBreakerOpen:
        await update.message.reply_text(
            "⚠️ Serviço temporariamente indisponível.\n"
            "Aguarda 1 minuto."
        )
    except RateLimitExceeded:
        await update.message.reply_text(
            "⚠️ Rate limit excedido.\n"
            "Aguarda 1 minuto."
        )
    except Exception as e:
        logger.error(f"Erro em process_status_with_feeling: {e}\n{traceback.format_exc()}")
        await update.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def activities_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler /activities - Lista atividades recentes"""
    try:
        all_activities = get_all_formatted_activities()
        
        if not all_activities:
            await update.message.reply_text(
                "📭 Sem atividades.\n\n"
                "Usa /sync ou /import."
            )
            return
        
        recent = all_activities[:MAX_ACTIVITIES_DISPLAY]
        
        msg = f"🏃 ATIVIDADES ({len(all_activities)} total, {len(recent)} recentes):\n\n"
        
        for i, act in enumerate(recent, 1):
            msg += f"{i}. {act.to_brief_summary()}\n"
        
        if len(all_activities) > MAX_ACTIVITIES_DISPLAY:
            msg += f"\n... e mais {len(all_activities) - MAX_ACTIVITIES_DISPLAY}"
        
        msg += "\n\n💡 /analyze_activity para análise individual"
        
        await update.message.reply_text(msg)
        
    except Exception as e:
        logger.error(f"Erro em /activities: {e}")
        await update.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def analyze_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler /analyze - Análise de aderência ao plano"""
    user_id = update.effective_user.id
    
    try:
        await update.message.reply_text("🔍 Analisando aderência...")
        
        # Biometria obrigatória
        history = get_recent_biometrics(7)
        baseline = calculate_biometric_baseline(history)
        bio_context = format_biometric_context(history, baseline)
        
        activities = get_all_formatted_activities()
        recent = activities[:MAX_ACTIVITIES_IN_ANALYSIS]
        
        if not recent:
            await update.message.reply_text("📭 Sem atividades para análise.")
            return
        
        prompt = f"""
{bio_context}

### ATIVIDADES (Últimas {len(recent)}):

"""
        
        for act in recent:
            prompt += act.to_detailed_text() + "\n\n"
        
        prompt += """
### TAREFA:
Analisa aderência ao plano de treino.
Identifica padrões, gaps e ajustes necessários.
PRIORIZA dados biométricos sobre sensação subjetiva.
"""
        
        response_text = await call_gemini_with_retry(prompt, user_id)
        
        # Envia resposta
        if len(response_text) <= TELEGRAM_SAFE_MESSAGE_LENGTH:
            await update.message.reply_text(response_text)
        else:
            chunks = [response_text[i:i+TELEGRAM_SAFE_MESSAGE_LENGTH] 
                     for i in range(0, len(response_text), TELEGRAM_SAFE_MESSAGE_LENGTH)]
            for chunk in chunks:
                await update.message.reply_text(chunk)
        
        # Salva contexto
        add_to_context_history(user_id, 'analyze', prompt, response_text)
        
    except GeminiTimeoutError:
        await update.message.reply_text(
            f"⏱️ Timeout após {GEMINI_TIMEOUT_SECONDS}s.\n"
            "Tenta novamente."
        )
    except Exception as e:
        logger.error(f"Erro em /analyze: {e}\n{traceback.format_exc()}")
        await update.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def analyze_activity_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    v3.9.0: CORRIGIDO - Handler /analyze_activity
    Se for ciclismo, pergunta sobre passageiro/carga
    """
    try:
        activities = get_all_formatted_activities()
        
        if not activities:
            await update.message.reply_text("📭 Sem atividades.\nUsa /sync primeiro.")
            return
        
        recent = activities[:MAX_ACTIVITIES_DISPLAY]
        
        keyboard = []
        for i, act in enumerate(recent):
            button_text = act.to_brief_summary()[:60]
            keyboard.append([InlineKeyboardButton(button_text, callback_data=f"analyze_act_{i}")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "📊 Escolhe uma atividade para analisar:",
            reply_markup=reply_markup
        )
        
    except Exception as e:
        logger.error(f"Erro em /analyze_activity: {e}")
        await update.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def analyze_activity_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    v3.9.0: CORRIGIDO - Callback para análise de atividade individual
    Pergunta sobre carga se for ciclismo
    """
    query = update.callback_query
    await query.answer()
    
    try:
        # Extrai índice
        index = int(query.data.split('_')[-1])
        
        activities = get_all_formatted_activities()
        if index >= len(activities):
            await query.edit_message_text("❌ Atividade não encontrada")
            return
        
        activity = activities[index]
        
        # v3.9.0: Verifica se é ciclismo
        sport_lower = activity.sport.lower()
        is_cycling = any(x in sport_lower for x in ['cicl', 'mtb', 'spin', 'bike', 'cycling', 'road_biking'])
        
        if is_cycling:
            # Pergunta sobre passageiro/carga
            keyboard = [
                [InlineKeyboardButton("Sim (tinha carga/passageiro)", callback_data=f"cargo_yes_{index}")],
                [InlineKeyboardButton("Não (solo)", callback_data=f"cargo_no_{index}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"🚴 Atividade: {activity.to_brief_summary()}\n\n"
                "Levaste passageiro ou carga adicional?",
                reply_markup=reply_markup
            )
        else:
            # Não é ciclismo, analisa diretamente
            await perform_activity_analysis(query, activity, has_cargo=False)
        
    except Exception as e:
        logger.error(f"Erro em analyze_activity_callback: {e}")
        await query.edit_message_text(f"❌ Erro: {str(e)[:100]}")

async def cargo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    v3.9.0: NOVO - Callback para resposta sobre carga em ciclismo
    """
    query = update.callback_query
    await query.answer()
    
    try:
        parts = query.data.split('_')
        has_cargo = parts[1] == 'yes'
        index = int(parts[2])
        
        activities = get_all_formatted_activities()
        if index >= len(activities):
            await query.edit_message_text("❌ Atividade não encontrada")
            return
        
        activity = activities[index]
        
        await perform_activity_analysis(query, activity, has_cargo=has_cargo)
        
    except Exception as e:
        logger.error(f"Erro em cargo_callback: {e}")
        await query.edit_message_text(f"❌ Erro: {str(e)[:100]}")

async def perform_activity_analysis(query, activity: FormattedActivity, has_cargo: bool):
    """
    v3.9.0: NOVO - Executa análise de atividade individual
    """
    user_id = query.from_user.id
    
    try:
        await query.edit_message_text("🔍 Analisando atividade...")
        
        # Biometria
        history = get_recent_biometrics(7)
        baseline = calculate_biometric_baseline(history)
        bio_context = format_biometric_context(history, baseline)
        
        cargo_context = ""
        if has_cargo:
            cargo_context = "\n⚠️ NOTA: Atividade realizada COM passageiro ou carga adicional."
        
        prompt = f"""
{bio_context}

### ATIVIDADE PARA ANÁLISE:
{activity.to_detailed_text()}
{cargo_context}

### TAREFA:
Analisa esta atividade individual em detalhe.
Avalia:
- Intensidade apropriada face à biometria
- Zona cardíaca se disponível
- Cadência (ciclismo ou corrida)
- Ganho de elevação vs esforço
{f"- Impacto da carga/passageiro no desempenho" if has_cargo else ""}
- Recomendações para próxima sessão similar
"""
        
        response_text = await call_gemini_with_retry(prompt, user_id)
        
        # Envia resposta
        await query.message.reply_text(response_text)
        
        # Salva contexto
        add_to_context_history(user_id, 'analyze_activity', prompt, response_text)
        
    except GeminiTimeoutError:
        await query.message.reply_text(
            f"⏱️ Timeout após {GEMINI_TIMEOUT_SECONDS}s.\n"
            "Tenta novamente."
        )
    except Exception as e:
        logger.error(f"Erro em perform_activity_analysis: {e}")
        await query.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def import_historical(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    v3.9.0: CORRIGIDO - Handler /import
    Com feedback após conclusão
    """
    try:
        await update.message.reply_text("🔄 A processar importação histórica...")
        
        if not create_import_request(days=30):
            await update.message.reply_text("❌ Erro ao criar pedido de importação")
            return
        
        # v3.9.0: NOVO - Feedback após conclusão
        asyncio.create_task(send_sync_feedback(update, 'import_request'))
        
    except Exception as e:
        logger.error(f"Erro em /import: {e}")
        await update.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def sync_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    v3.9.0: CORRIGIDO - Handler /sync
    Pede confirmação e depois dá feedback
    """
    keyboard = [
        [InlineKeyboardButton("✅ Sim, sincronizar", callback_data="sync_confirmed")],
        [InlineKeyboardButton("❌ Cancelar", callback_data="sync_cancel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🔄 Sincronizar dados do Garmin?\n\n"
        "Isto irá importar atividades e biometria recentes.",
        reply_markup=reply_markup
    )

async def sync_confirmed_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    v3.9.0: CORRIGIDO - Callback de confirmação de sync
    """
    query = update.callback_query
    await query.answer()
    
    try:
        await query.edit_message_text("🔄 A processar sincronização...")
        
        if not create_sync_request():
            await query.message.reply_text("❌ Erro ao criar pedido de sync")
            return
        
        # v3.9.0: NOVO - Feedback após conclusão
        asyncio.create_task(send_sync_feedback(query, 'sync_request'))
        
    except Exception as e:
        logger.error(f"Erro em sync_confirmed_callback: {e}")
        await query.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def cleanup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler /cleanup"""
    try:
        await update.message.reply_text("🧹 A limpar...")
        
        cleaned, messages = cleanup_old_flags()
        
        duplicates, total, reorg_messages = reorganize_activities()
        
        msg = "🧹 LIMPEZA:\n\n"
        msg += "FLAGS:\n" + "\n".join(messages) + "\n\n"
        msg += "ATIVIDADES:\n" + "\n".join(reorg_messages)
        
        await update.message.reply_text(msg)
        
    except Exception as e:
        logger.error(f"Erro em /cleanup: {e}")
        await update.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler /history - Mostra análises anteriores"""
    user_id = update.effective_user.id
    
    try:
        context_data = load_context_from_disk(user_id)
        
        if not context_data or not context_data.get('history'):
            await update.message.reply_text(
                "📭 Sem histórico de análises.\n\n"
                "Usa /status ou /analyze primeiro."
            )
            return
        
        history = context_data['history']
        
        msg = f"📚 HISTÓRICO ({len(history)} análises):\n\n"
        
        for i, entry in enumerate(history, 1):
            timestamp = datetime.fromtimestamp(entry['timestamp']).strftime('%d/%m %H:%M')
            msg += f"{i}. {entry['command']} - {timestamp}\n"
            msg += f"   {entry['response_preview'][:100]}...\n\n"
        
        msg += "💡 Faz perguntas sobre estas análises em texto livre"
        
        await update.message.reply_text(msg)
        
    except Exception as e:
        logger.error(f"Erro em /history: {e}")
        await update.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def clear_context_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler /clear_context"""
    user_id = update.effective_user.id
    
    try:
        clear_user_context(user_id)
        session_state.clear_user_state(user_id)
        
        await update.message.reply_text("✅ Contexto limpo")
        
    except Exception as e:
        logger.error(f"Erro em /clear_context: {e}")
        await update.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler /stats - Estatísticas básicas"""
    try:
        activities = get_all_formatted_activities()
        history = get_recent_biometrics(7)
        
        msg = "📊 ESTATÍSTICAS:\n\n"
        msg += f"Atividades: {len(activities)}\n"
        msg += f"Dias biometria: {len(history)}\n\n"
        
        if activities:
            total_time = sum(a.duration_min for a in activities)
            total_dist = sum(a.distance_km or 0 for a in activities)
            msg += f"Tempo total: {int(total_time)}min\n"
            msg += f"Distância total: {total_dist:.1f}km\n"
        
        await update.message.reply_text(msg)
        
    except Exception as e:
        logger.error(f"Erro em /stats: {e}")
        await update.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def debug_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler /debug"""
    try:
        activities = get_all_formatted_activities()
        history = get_recent_biometrics(7)
        baseline = calculate_biometric_baseline(history)
        
        msg = "🔧 DEBUG:\n\n"
        msg += f"Versão: {BOT_VERSION}\n"
        msg += f"Atividades: {len(activities)}\n"
        msg += f"Biometria (7d): {len(history)}\n"
        
        if baseline:
            msg += f"HRV média: {baseline.get('hrv_mean', 'N/A')}\n"
            msg += f"RHR média: {baseline.get('rhr_mean', 'N/A')}\n"
        
        # Verifica ficheiros
        files_status = []
        for fname in ['garmin_dump.json', 'garmin_data_consolidated.json', 'activities.json']:
            path = os.path.join(DATA_DIR, fname)
            exists = "✅" if os.path.exists(path) else "❌"
            files_status.append(f"{exists} {fname}")
        
        msg += "\nFicheiros:\n" + "\n".join(files_status)
        
        await update.message.reply_text(msg)
        
    except Exception as e:
        logger.error(f"Erro em /debug: {e}")
        await update.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    v3.9.0: CORRIGIDO - Handler /health
    Reporta se activities.json tem entradas válidas e última biometria
    """
    try:
        msg = "🏥 HEALTH CHECK:\n\n"
        
        # Sistema
        has_space, free_mb = check_disk_space()
        space_status = "✅" if has_space else "⚠️"
        msg += f"{space_status} Disco: {free_mb:.1f}MB\n"
        
        # Integridade
        is_valid, integrity_msg = check_activities_integrity()
        integrity_status = "✅" if is_valid else "⚠️"
        msg += f"{integrity_status} Integridade: {integrity_msg}\n"
        
        # v3.9.0: NOVO - Verifica atividades válidas
        activities = get_all_formatted_activities()
        valid_activities = [a for a in activities if a.sport != 'Unknown']
        activity_status = "✅" if valid_activities else "⚠️"
        msg += f"{activity_status} Atividades: {len(valid_activities)}/{len(activities)} válidas\n"
        
        # v3.9.0: NOVO - Última biometria
        today_bio = get_today_biometrics()
        if today_bio and not today_bio.is_empty():
            bio_status = "✅"
            msg += f"{bio_status} Biometria hoje: HRV={today_bio.hrv}, RHR={today_bio.rhr}\n"
        else:
            bio_status = "⚠️"
            msg += f"{bio_status} Biometria: Sem dados de hoje\n"
        
        # Gemini
        avg_latency = health_state.get_avg_gemini_latency()
        if avg_latency:
            gemini_status = "✅" if avg_latency < 10 else "⚠️"
            msg += f"{gemini_status} Gemini: {avg_latency}s latência média\n"
        else:
            msg += "⚠️ Gemini: Sem dados de latência\n"
        
        # Circuit breaker
        cb_status = "✅" if circuit_breaker.state == 'closed' else "⚠️"
        msg += f"{cb_status} Circuit breaker: {circuit_breaker.state}\n"
        
        await update.message.reply_text(msg)
        
    except Exception as e:
        logger.error(f"Erro em /health: {e}")
        await update.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler /help"""
    await update.message.reply_text(
        f"🏋️ FitnessJournal v{BOT_VERSION}\n\n"
        "Comandos principais:\n"
        "/status - Readiness (com biometria)\n"
        "/analyze - Aderência ao plano\n"
        "/analyze_activity - Análise individual\n"
        "/activities - Lista atividades\n"
        "/sync - Sincroniza dados\n"
        "/import - Importa histórico\n"
        "/cleanup - Limpeza de dados\n"
        "/history - Análises anteriores\n"
        "/clear_context - Limpa contexto\n"
        "/stats - Estatísticas\n"
        "/debug - Informações de debug\n"
        "/health - Health check do sistema\n"
        "/help - Esta ajuda\n\n"
        "🆕 v3.9.0:\n"
        "• Parser Garmin corrigido (campos aninhados)\n"
        "• Biometria lê do consolidado corretamente\n"
        "• /status pergunta feeling primeiro\n"
        "• /sync e /import dão feedback após conclusão\n"
        "• /analyze_activity pergunta sobre carga (ciclismo)\n"
        "• System prompt atualizado (fadiga mascarada)\n"
        "• Timeout Gemini: 45s\n"
        "• /health reporta atividades válidas e última biometria"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    v3.9.0: CORRIGIDO - Handler para mensagens de texto livre
    Verifica se está aguardando feeling para /status
    """
    user_id = update.effective_user.id
    message_text = update.message.text
    
    try:
        # v3.9.0: Verifica se está aguardando feeling
        user_state = session_state.get_user_state(user_id)
        
        if user_state == 'waiting_feeling':
            # Tenta parsear como número
            try:
                feeling = int(message_text.strip())
                if 0 <= feeling <= 10:
                    await process_status_with_feeling(update, feeling)
                    return
                else:
                    await update.message.reply_text(
                        "❌ Por favor, responde com um número entre 0 e 10."
                    )
                    return
            except ValueError:
                await update.message.reply_text(
                    "❌ Por favor, responde com um número entre 0 e 10."
                )
                return
        
        # Verifica se há contexto ativo para followup
        context_data = load_context_from_disk(user_id)
        
        if not context_data or not context_data.get('history'):
            await update.message.reply_text(
                "💡 Usa /status, /analyze ou /analyze_activity primeiro.\n"
                "Depois podes fazer perguntas sobre a análise."
            )
            return
        
        # Limita tamanho
        if len(message_text) > MAX_FEELING_LENGTH:
            await update.message.reply_text(
                f"❌ Mensagem demasiado longa.\n"
                f"Máximo: {MAX_FEELING_LENGTH} caracteres."
            )
            return
        
        await update.message.reply_text("🤔 A processar pergunta...")
        
        # Obtém contexto
        context_text = get_context_for_followup(user_id)
        
        # Biometria obrigatória
        history = get_recent_biometrics(7)
        baseline = calculate_biometric_baseline(history)
        bio_context = format_biometric_context(history, baseline)
        
        prompt = f"""
{bio_context}

{context_text}

### PERGUNTA DO ATLETA:
{message_text}

### TAREFA:
Responde à pergunta baseado no contexto das análises anteriores e dados biométricos.
PRIORIZA dados objetivos (HRV/RHR).
"""
        
        response_text = await call_gemini_with_retry(prompt, user_id)
        
        # Envia resposta
        if len(response_text) <= TELEGRAM_SAFE_MESSAGE_LENGTH:
            await update.message.reply_text(response_text)
        else:
            chunks = [response_text[i:i+TELEGRAM_SAFE_MESSAGE_LENGTH] 
                     for i in range(0, len(response_text), TELEGRAM_SAFE_MESSAGE_LENGTH)]
            for chunk in chunks:
                await update.message.reply_text(chunk)
        
        # Salva no contexto
        add_to_context_history(user_id, 'followup', prompt, response_text)
        
    except GeminiTimeoutError:
        await update.message.reply_text(
            f"⏱️ Timeout após {GEMINI_TIMEOUT_SECONDS}s.\n"
            "Tenta novamente."
        )
    except Exception as e:
        logger.error(f"Erro em handle_message: {e}\n{traceback.format_exc()}")
        await update.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler para comandos não reconhecidos"""
    command = update.message.text
    
    await update.message.reply_text(
        f"❓ Comando '{command}' não reconhecido.\n\n"
        "Usa /help para ver todos os comandos disponíveis."
    )

# Placeholder para callbacks não usados mas referenciados
async def bike_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Placeholder - não usado em v3.9.0"""
    pass

async def cycling_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Placeholder - não usado em v3.9.0"""
    pass

# ==========================================
# MAIN (v3.9.0)
# ==========================================
def main():
    """Entry point v3.9.0"""
    logger.info("=" * 50)
    logger.info(f"FitnessJournal Bot v{BOT_VERSION}")
    logger.info(f"{BOT_VERSION_DESC}")
    logger.info("=" * 50)
    
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN não configurado")
        return
    
    if not os.environ.get("GEMINI_API_KEY"):
        logger.error("GEMINI_API_KEY não configurado")
        return
    
    # Verificação de integridade no boot
    is_valid, integrity_msg = check_activities_integrity()
    if not is_valid:
        logger.warning(f"⚠️ INTEGRIDADE: {integrity_msg}")
        logger.info("🔧 Será corrigido automaticamente no próximo load...")
    else:
        logger.info(f"✅ INTEGRIDADE: {integrity_msg}")
    
    # Enriquecimento automático
    logger.info("🔍 Verificando atividades para enriquecimento...")
    check_and_enrich_activities()
    
    all_activities = get_all_formatted_activities()
    logger.info(f"🏃 Atividades: {len(all_activities)}")
    
    logger.info("🧹 Auto-cleanup...")
    cleaned, _ = cleanup_old_flags()
    if cleaned > 0:
        logger.info(f"✅ {cleaned} flags limpos")
    
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Command Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("activities", activities_command))
    app.add_handler(CommandHandler("analyze", analyze_command))
    app.add_handler(CommandHandler("analyze_activity", analyze_activity_command))
    app.add_handler(CommandHandler("import", import_historical))
    app.add_handler(CommandHandler("sync", sync_command))
    app.add_handler(CommandHandler("cleanup", cleanup_command))
    app.add_handler(CommandHandler("history", history_command))
    app.add_handler(CommandHandler("clear_context", clear_context_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("debug", debug_command))
    app.add_handler(CommandHandler("health", health_command))
    app.add_handler(CommandHandler("help", help_command))
    
    # Callback Query Handlers
    app.add_handler(CallbackQueryHandler(sync_confirmed_callback, pattern=r'^sync_confirmed$'))
    app.add_handler(CallbackQueryHandler(analyze_activity_callback, pattern=r'^analyze_act_\d+$'))
    app.add_handler(CallbackQueryHandler(cargo_callback, pattern=r'^cargo_(yes|no)_\d+$'))
    
    # Message Handler (texto livre)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Handler para comandos não reconhecidos
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))
    
    logger.info("✅ Bot v3.9.0 iniciado com:")
    logger.info(f"  - Timeout Gemini: {GEMINI_TIMEOUT_SECONDS}s")
    logger.info(f"  - Retry delays: {RETRY_DELAYS}")
    logger.info(f"  - Circuit breaker: {CIRCUIT_BREAKER_THRESHOLD} falhas threshold")
    logger.info(f"  - Cache: {RESPONSE_CACHE_SIZE} entradas, TTL {CACHE_TTL_SECONDS}s")
    logger.info(f"  - Rate limit: {RATE_LIMIT_MAX_REQUESTS} req/{RATE_LIMIT_WINDOW}s")
    logger.info(f"  - Biometric context: OBRIGATÓRIO")
    logger.info(f"  - Parser Garmin: CORRIGIDO (campos aninhados)")
    logger.info(f"  - Feeling prompt: ENABLED (/status)")
    logger.info(f"  - Sync feedback: ENABLED")
    logger.info(f"  - Cargo prompt: ENABLED (ciclismo)")
    logger.info(f"  - System prompt: UPDATED (fadiga mascarada)")
    
    print(f"🤖 Bot v{BOT_VERSION} ativo")
    
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == '__main__':
    main()
