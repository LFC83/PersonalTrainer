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
# CONFIGURATION & CONSTANTS (v3.8.0)
# ==========================================
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
logger.info("Gemini API configurada")

# Bot Configuration
BOT_VERSION = "3.8.0"
BOT_VERSION_DESC = "Data Resilience + Health Check + Bike Cadence + Command Recovery"
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
GEMINI_TIMEOUT_SECONDS = 60  # v3.8.0: Aumentado de 45 para 60 segundos

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
# SYSTEM PROMPT (v3.7.0 - MANTIDO)
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

### CRITÉRIOS BIOMÉTRICOS RIGOROSOS (v3.7.0 - PRIORIDADE ABSOLUTA):
**REGRA FUNDAMENTAL:** Quando os dados biométricos (HRV/RHR) indicarem fadiga, mas o feedback subjetivo do utilizador for positivo (ex: "sinto-me fresco"), DEVES DAR PRIORIDADE ABSOLUTA AOS DADOS BIOLÓGICOS e alertar para o risco de sobretreino ou fadiga mascarada.

1. HRV menor que 95% da média = APENAS RECUPERAÇÃO ATIVA.
2. RHR maior que mais 2% da média = FADIGA DETETADA (Reduzir volume em 50%).
3. PONTUAÇÃO DE SONO menor que 75 mais Sensação Negativa = ZERO INTENSIDADE.
4. **FADIGA MASCARADA:** Se HRV está baixa OU RHR está elevada, mas o atleta reporta sentir-se bem, DEVES:
   - Explicar a discrepância entre sensação subjetiva e realidade fisiológica
   - Alertar para o perigo de ignorar os sinais biométricos
   - Prescrever treino baseado nos dados objetivos (HRV/RHR), não no sentimento
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

class CircuitBreakerOpen(Exception):
    """Circuit breaker está aberto"""
    pass

class RateLimitExceeded(Exception):
    """Rate limit excedido"""
    pass

# ==========================================
# DATA MODELS (v3.8.0 - EXPANDIDO)
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
    """Atividade formatada para display (v3.8.0: + bike cadence)"""
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
    bike_cadence: Optional[int] = None  # v3.8.0: Cadência de ciclismo (rpm)
    raw: Dict = field(default_factory=dict)
    
    def to_brief_summary(self) -> str:
        """Retorna resumo breve inline"""
        summary = f"{self.sport} ({self.duration_min}min"
        
        if self.distance_km:
            summary += f", {self.distance_km}km"
        
        if self.avg_hr:
            summary += f", {self.avg_hr}bpm"
        
        # v3.8.0: Adicionar cadência de bike se disponível
        if self.bike_cadence:
            summary += f", {self.bike_cadence}rpm"
        
        # v3.7.0: Adicionar cadência de corrida se disponível
        if self.avg_cadence:
            summary += f", {self.avg_cadence}spm"
        
        # v3.7.0: Adicionar elevação se disponível
        if self.elevation_gain:
            summary += f", D+{int(self.elevation_gain)}m"
        
        summary += ")"
        return summary
    
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
        
        # v3.7.0: Elevação
        if self.elevation_gain:
            lines.append(f"⛰️ D+ {int(self.elevation_gain)}m")
        
        # v3.8.0: Cadência de ciclismo
        if self.bike_cadence:
            lines.append(f"🚴 {self.bike_cadence}rpm (cadência)")
        
        # v3.7.0: Cadência de corrida
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
# GLOBAL STATE (v3.8.0: + health check data)
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

# v3.8.0: Health Check State
class HealthCheckState:
    def __init__(self):
        self.gemini_latencies = []  # Últimas latências em segundos
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

# Global instances
circuit_breaker = CircuitBreaker()
rate_limiter = RateLimiter()
response_cache = ResponseCache()
health_state = HealthCheckState()  # v3.8.0

# ==========================================
# FILESYSTEM OPERATIONS (v3.8.0: BLINDAGEM CRÍTICA)
# ==========================================
def ensure_data_dir():
    """Garante que DATA_DIR existe"""
    os.makedirs(DATA_DIR, exist_ok=True)

def load_garmin_data() -> Optional[Dict]:
    """Carrega dados do Garmin"""
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
    v3.8.0: ATOMIC WRITE + VALIDAÇÃO - Garante que sempre salva dict
    """
    ensure_data_dir()
    
    # v3.8.0: VALIDAÇÃO PRÉ-ESCRITA
    if not isinstance(activities, dict):
        logger.error(f"❌ CRÍTICO: Tentativa de salvar activities como {type(activities)}")
        raise FileOperationError(f"Activities deve ser dict, não {type(activities)}")
    
    try:
        path = os.path.join(DATA_DIR, 'activities.json')
        temp_path = path + '.tmp'
        
        # v3.8.0: ATOMIC WRITE - Escreve para ficheiro temporário primeiro
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(activities, f, ensure_ascii=False, indent=2)
        
        # Move atomicamente (substitui o original)
        os.replace(temp_path, path)
        
        logger.debug(f"✅ activities.json salvo com {len(activities)} entradas")
        
    except Exception as e:
        logger.error(f"Erro ao salvar activities.json: {e}")
        # Remove ficheiro temporário se existir
        temp_path = os.path.join(DATA_DIR, 'activities.json.tmp')
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except:
                pass
        raise FileOperationError(f"Falha ao salvar atividades: {e}")

def check_disk_space() -> Tuple[bool, float]:
    """
    Verifica espaço em disco
    v3.8.0: Retorna também o espaço disponível para health check
    """
    try:
        stat = os.statvfs(DATA_DIR)
        free_mb = (stat.f_bavail * stat.f_frsize) / (1024 * 1024)
        return free_mb >= MIN_DISK_SPACE_MB, free_mb
    except Exception as e:
        logger.error(f"Erro ao verificar espaço em disco: {e}")
        return True, -1  # Assume OK se não conseguir verificar

def check_activities_integrity() -> Tuple[bool, str]:
    """
    v3.8.0: Verifica integridade do activities.json
    Retorna (is_valid, status_message)
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
# DATA EXTRACTION (v3.8.0: + CADÊNCIA CICLISMO)
# ==========================================
def extract_elevation_from_raw(raw_data: Dict, sport: str) -> Optional[float]:
    """Extrai ganho de elevação do JSON raw da Garmin"""
    try:
        elevation_fields = [
            'totalElevationGain',
            'elevationGain', 
            'totalAscent',
            'gainElevation'
        ]
        
        for field in elevation_fields:
            if field in raw_data and raw_data[field] is not None:
                value = raw_data[field]
                if isinstance(value, (int, float)):
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
        
        if "Corr" in sport or "Run" in sport:
            cadence_fields_avg = ['averageRunCadence', 'avgRunCadence', 'averageCadence']
            cadence_fields_max = ['maxRunCadence', 'maxCadence']
            
            for field in cadence_fields_avg:
                if field in raw_data and raw_data[field] is not None:
                    value = raw_data[field]
                    if isinstance(value, (int, float)):
                        if value < 120:  # Provavelmente strides per minute
                            avg_cadence = int(value * 2)
                        else:  # Já em steps per minute
                            avg_cadence = int(value)
                        break
            
            for field in cadence_fields_max:
                if field in raw_data and raw_data[field] is not None:
                    value = raw_data[field]
                    if isinstance(value, (int, float)):
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
    v3.8.0: Extrai cadência de ciclismo (rpm) do JSON raw da Garmin
    """
    try:
        # Verifica se é ciclismo
        if not any(x in sport for x in ['Cicl', 'MTB', 'Spin', 'Bike', 'Cycling']):
            return None
        
        # Campos possíveis para cadência de ciclismo
        bike_cadence_fields = [
            'averageBikingCadenceInRevolutionsPerMinute',
            'avgBikeCadence',
            'averageCadence',
            'cadence'
        ]
        
        for field in bike_cadence_fields:
            if field in raw_data and raw_data[field] is not None:
                value = raw_data[field]
                if isinstance(value, (int, float)):
                    # Cadência de ciclismo já deve estar em RPM
                    return int(value)
        
        return None
    except Exception as e:
        logger.debug(f"Erro ao extrair cadência de ciclismo: {e}")
        return None

def needs_data_enrichment(activity: Dict) -> bool:
    """Verifica se atividade precisa de enriquecimento de dados"""
    sport = activity.get('sport', '')
    
    is_cycling = any(x in sport for x in ['Cicl', 'MTB', 'Spin', 'Bike'])
    is_running = any(x in sport for x in ['Corr', 'Run'])
    
    missing_elevation = (is_cycling or is_running) and activity.get('elevation_gain') is None
    missing_run_cadence = is_running and activity.get('avg_cadence') is None
    missing_bike_cadence = is_cycling and activity.get('bike_cadence') is None  # v3.8.0
    
    return missing_elevation or missing_run_cadence or missing_bike_cadence

def enrich_activity_from_garmin(activity_id: str, garmin_data: Dict) -> Optional[Dict]:
    """
    Enriquece atividade com dados adicionais do Garmin
    v3.8.0: Inclui cadência de ciclismo
    """
    try:
        activities = garmin_data.get('activities', [])
        
        for act in activities:
            if str(act.get('activityId')) == str(activity_id):
                sport_data = act.get('activityType', {})
                sport = sport_data.get('typeKey', 'Unknown')
                
                enrichment = {}
                
                # Extrai elevação
                elevation = extract_elevation_from_raw(act, sport)
                if elevation is not None:
                    enrichment['elevation_gain'] = elevation
                
                # Extrai cadência de corrida
                avg_cad, max_cad = extract_run_cadence_from_raw(act, sport)
                if avg_cad is not None:
                    enrichment['avg_cadence'] = avg_cad
                if max_cad is not None:
                    enrichment['max_cadence'] = max_cad
                
                # v3.8.0: Extrai cadência de ciclismo
                bike_cad = extract_bike_cadence_from_raw(act, sport)
                if bike_cad is not None:
                    enrichment['bike_cadence'] = bike_cad
                
                return enrichment if enrichment else None
        
        return None
    except Exception as e:
        logger.error(f"Erro ao enriquecer atividade {activity_id}: {e}")
        return None

def check_and_enrich_activities():
    """
    Verifica e enriquece atividades que precisam de dados adicionais
    v3.8.0: Inclui cadência de ciclismo
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

def parse_garmin_history(data: Dict) -> List[BiometricDay]:
    """Parse dados históricos do Garmin"""
    history = []
    
    try:
        daily_data = data.get('dailySummaries', [])
        
        for day in daily_data:
            calendar_date = day.get('calendarDate')
            if not calendar_date:
                continue
            
            hrv = day.get('avgWakingHeartRateVariability')
            rhr = day.get('restingHeartRate')
            sleep_score = day.get('sleepScore')
            training_load = day.get('moderateIntensityMinutes')
            
            bio_day = BiometricDay(
                date=calendar_date,
                hrv=hrv,
                rhr=rhr,
                sleep=sleep_score,
                training_load=training_load
            )
            
            if not bio_day.is_empty():
                history.append(bio_day)
        
        history.sort(key=lambda x: x.date, reverse=True)
        
    except Exception as e:
        logger.error(f"Erro ao parsear histórico: {e}")
    
    return history

def get_recent_biometrics(days: int = 7) -> List[BiometricDay]:
    """Obtém biometria recente"""
    data = load_garmin_data()
    if not data:
        return []
    
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
    v3.8.0: OBRIGATÓRIO em todos os fluxos de análise
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
    
    return "\n".join(lines)

def parse_activities_from_garmin(data: Dict) -> List[Dict]:
    """
    Parse atividades do JSON da Garmin
    v3.8.0: Expandido para extrair cadência de ciclismo
    """
    activities = []
    
    try:
        activities_data = data.get('activities', [])
        
        for act in activities_data:
            activity_id = act.get('activityId')
            if not activity_id:
                continue
            
            date_str = act.get('startTimeLocal', '')[:10] if act.get('startTimeLocal') else None
            
            sport_data = act.get('activityType', {})
            sport = sport_data.get('typeKey', 'Unknown')
            
            duration_sec = act.get('duration', 0)
            duration_min = round(duration_sec / 60, 1) if duration_sec else 0
            
            distance_m = act.get('distance')
            distance_km = round(distance_m / 1000, 2) if distance_m else None
            
            avg_hr = act.get('averageHR')
            calories = act.get('calories')
            
            # Extrair altimetria
            elevation_gain = extract_elevation_from_raw(act, sport)
            
            # Extrair cadência de corrida
            avg_cadence, max_cadence = extract_run_cadence_from_raw(act, sport)
            
            # v3.8.0: Extrair cadência de ciclismo
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
                'bike_cadence': bike_cadence,  # v3.8.0
                'raw': act
            }
            
            activities.append(activity)
        
        # Ordena por data (mais recente primeiro)
        activities.sort(key=lambda x: x.get('date', ''), reverse=True)
        
    except Exception as e:
        logger.error(f"Erro ao parsear atividades: {e}")
    
    return activities

def get_all_formatted_activities() -> List[FormattedActivity]:
    """
    Retorna todas as atividades formatadas
    v3.8.0: RESILIÊNCIA - Converte list para dict se necessário
    """
    activities_index = load_activities_index()  # Já faz a conversão se for list
    formatted = []
    
    # v3.8.0: VALIDAÇÃO ADICIONAL
    if not isinstance(activities_index, dict):
        logger.error(f"❌ activities_index não é dict: {type(activities_index)}")
        return []
    
    for activity_id, activity_data in activities_index.items():
        try:
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
                bike_cadence=activity_data.get('bike_cadence'),  # v3.8.0
                raw=activity_data.get('raw', {})
            )
            formatted.append(formatted_act)
        except Exception as e:
            logger.error(f"Erro ao formatar atividade {activity_id}: {e}")
    
    # Ordena por data (mais recente primeiro)
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
# GEMINI API (v3.8.0: LATENCY TRACKING)
# ==========================================
async def call_gemini_with_retry(prompt: str, user_id: int) -> str:
    """
    Chama Gemini com retry inteligente
    v3.8.0: Adiciona tracking de latência para health check
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
                
                # Se última tentativa foi timeout, usa delay maior
                if was_timeout:
                    delay = delay * 2
                    logger.info(f"Timeout anterior. Delay aumentado para {delay}s")
                
                logger.info(f"Retry {attempt}/{MAX_RETRIES} após {delay}s...")
                await asyncio.sleep(delay)
            
            # v3.8.0: Medir latência
            start_time = time.time()
            
            response = await asyncio.wait_for(
                asyncio.to_thread(model.generate_content, prompt),
                timeout=GEMINI_TIMEOUT_SECONDS
            )
            
            # v3.8.0: Registar latência
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
    if isinstance(last_error, GeminiTimeoutError):
        raise last_error
    else:
        raise Exception(f"Falha após {MAX_RETRIES} tentativas: {last_error}")

# ==========================================
# CONTEXT MANAGEMENT
# ==========================================
def get_context_path(user_id: int) -> str:
    """Retorna caminho do arquivo de contexto do usuário"""
    ensure_data_dir()
    return os.path.join(DATA_DIR, f'context_{user_id}.json')

def load_context_from_disk(user_id: int) -> Optional[Dict]:
    """Carrega contexto do disco"""
    path = get_context_path(user_id)
    
    if not os.path.exists(path):
        return None
    
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Verifica se contexto expirou
        last_activity = data.get('last_activity', 0)
        if time.time() - last_activity > CONTEXT_TIMEOUT_MINUTES * 60:
            os.remove(path)
            return None
        
        return data
    except Exception as e:
        logger.error(f"Erro ao carregar contexto: {e}")
        return None

def save_context_to_disk(user_id: int, context_data: Dict):
    """Salva contexto no disco"""
    path = get_context_path(user_id)
    
    try:
        context_data['last_activity'] = time.time()
        
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(context_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Erro ao salvar contexto: {e}")

def clear_context_disk(user_id: int) -> bool:
    """Remove contexto do disco"""
    path = get_context_path(user_id)
    
    if os.path.exists(path):
        try:
            os.remove(path)
            return True
        except Exception as e:
            logger.error(f"Erro ao remover contexto: {e}")
            return False
    
    return False

def add_to_context_history(user_id: int, analysis_type: str, prompt: str, response: str):
    """Adiciona entrada ao histórico de contexto"""
    context_data = load_context_from_disk(user_id) or {
        'user_id': user_id,
        'history': [],
        'last_activity': time.time()
    }
    
    entry = {
        'timestamp': datetime.now().isoformat(),
        'analysis_type': analysis_type,
        'prompt': prompt[:500],  # Limita tamanho
        'response': response[:1000]
    }
    
    context_data['history'].append(entry)
    
    # Limita tamanho do histórico
    if len(context_data['history']) > MAX_CONTEXT_HISTORY:
        context_data['history'] = context_data['history'][-MAX_CONTEXT_HISTORY:]
    
    save_context_to_disk(user_id, context_data)

def get_context_for_followup(user_id: int) -> str:
    """Obtém contexto formatado para follow-up"""
    context_data = load_context_from_disk(user_id)
    
    if not context_data or not context_data.get('history'):
        return ""
    
    history = context_data['history']
    
    context_lines = ["### CONTEXTO DAS ANÁLISES ANTERIORES:"]
    
    for i, entry in enumerate(history, 1):
        context_lines.append(f"\n**Análise {i} ({entry['analysis_type']}):**")
        context_lines.append(entry['response'][:500])
    
    return "\n".join(context_lines)

def get_context_stats() -> Dict:
    """Retorna estatísticas de contexto"""
    try:
        ensure_data_dir()
        
        total_users = 0
        by_type = defaultdict(int)
        
        for file in os.listdir(DATA_DIR):
            if not file.startswith('context_'):
                continue
            
            path = os.path.join(DATA_DIR, file)
            
            try:
                with open(path, 'r') as f:
                    data = json.load(f)
                
                total_users += 1
                
                for entry in data.get('history', []):
                    analysis_type = entry.get('analysis_type', 'unknown')
                    by_type[analysis_type] += 1
            except:
                continue
        
        return {
            'total_users': total_users,
            'by_type': dict(by_type)
        }
    except Exception as e:
        return {'error': str(e)}

# ==========================================
# TELEGRAM COMMAND HANDLERS
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler /start"""
    await update.message.reply_text(
        f"🏋️ FitnessJournal Bot v{BOT_VERSION}\n\n"
        f"{BOT_VERSION_DESC}\n\n"
        "Comandos principais:\n"
        "/status - Avalia readiness\n"
        "/analyze - Aderência ao plano\n"
        "/analyze_activity - Analisa atividade\n"
        "/activities - Lista atividades\n"
        "/help - Ajuda completa"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler /status - Avalia readiness do atleta
    v3.8.0: BIOMETRIA OBRIGATÓRIA
    """
    user_id = update.effective_user.id
    
    try:
        await update.message.reply_text("🔍 Avaliando readiness...")
        
        # v3.8.0: BIOMETRIA OBRIGATÓRIA
        history = get_recent_biometrics(7)
        baseline = calculate_biometric_baseline(history)
        bio_context = format_biometric_context(history, baseline)
        
        activities = get_all_formatted_activities()
        recent = activities[:MAX_ACTIVITIES_DISPLAY]
        
        prompt = f"""
{bio_context}

### ATIVIDADES RECENTES (Últimas {len(recent)}):

"""
        
        for act in recent:
            prompt += f"- {act.to_brief_summary()}\n"
        
        prompt += """

### TAREFA:
Avalia o readiness do atleta baseado EXCLUSIVAMENTE nos dados biométricos e histórico de atividades.
Se HRV/RHR indicarem fadiga, PRIORIZA os dados objetivos sobre qualquer sensação subjetiva.
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
        logger.error(f"Erro em /status: {e}\n{traceback.format_exc()}")
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
        
        # v3.8.0: BIOMETRIA OBRIGATÓRIA
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
    Handler /analyze_activity - Mostra botões para escolher atividade
    v3.8.0: CORRIGIDO - Primeiro pergunta sobre carga, depois envia para Gemini
    """
    try:
        activities = get_all_formatted_activities()
        
        if not activities:
            await update.message.reply_text("📭 Sem atividades.")
            return
        
        recent = activities[:5]
        
        keyboard = []
        for act in recent:
            button_text = act.to_brief_summary()
            callback_data = f"analyze_act_{act.raw.get('id', 'unknown')}"
            keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "🏃 Escolhe atividade:",
            reply_markup=reply_markup
        )
        
    except Exception as e:
        logger.error(f"Erro em /analyze_activity: {e}")
        await update.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def analyze_activity_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Callback quando user escolhe atividade
    v3.8.0: FLUXO CORRETO - Pergunta sobre carga ANTES de enviar para Gemini
    """
    query = update.callback_query
    await query.answer()
    
    try:
        activity_id = query.data.split('_')[-1]
        
        activities = get_all_formatted_activities()
        activity = next((a for a in activities if a.raw.get('id') == activity_id), None)
        
        if not activity:
            await query.message.reply_text("❌ Atividade não encontrada.")
            return
        
        # v3.8.0: Se for ciclismo, pergunta sobre carga PRIMEIRO
        is_cycling = any(x in activity.sport for x in ['Cicl', 'MTB', 'Spin', 'Bike'])
        
        if is_cycling:
            # Mostra botões de Carga/Passageiro
            keyboard = [
                [
                    InlineKeyboardButton("🚴 Carga", callback_data=f"cargo_yes_{activity_id}"),
                    InlineKeyboardButton("🚴‍♂️ Passageiro", callback_data=f"cargo_no_{activity_id}")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.message.reply_text(
                f"🚴 {activity.sport}\n\n"
                "Levaste carga ou foste passageiro?",
                reply_markup=reply_markup
            )
        else:
            # Não é ciclismo, analisa diretamente
            await perform_activity_analysis(query.message, activity, user_id=query.from_user.id)
            
    except Exception as e:
        logger.error(f"Erro em analyze_activity_callback: {e}")
        await query.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def cargo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    v3.8.0: Callback para resposta de carga/passageiro
    SÓ AGORA envia para o Gemini
    """
    query = update.callback_query
    await query.answer()
    
    try:
        # Parse: cargo_(yes|no)_ACTIVITY_ID
        parts = query.data.split('_')
        has_cargo = parts[1] == 'yes'
        activity_id = parts[2]
        
        activities = get_all_formatted_activities()
        activity = next((a for a in activities if a.raw.get('id') == activity_id), None)
        
        if not activity:
            await query.message.reply_text("❌ Atividade não encontrada.")
            return
        
        # Adiciona info de carga ao contexto
        cargo_text = "CARGA" if has_cargo else "PASSAGEIRO"
        
        # AGORA sim, envia para análise
        await perform_activity_analysis(
            query.message, 
            activity, 
            user_id=query.from_user.id,
            extra_context=f"\n⚠️ IMPORTANTE: Esta saída de ciclismo foi como {cargo_text}.\n"
        )
        
    except Exception as e:
        logger.error(f"Erro em cargo_callback: {e}")
        await query.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def perform_activity_analysis(message, activity: FormattedActivity, user_id: int, extra_context: str = ""):
    """
    v3.8.0: Função auxiliar que faz a análise real
    BIOMETRIA OBRIGATÓRIA
    """
    try:
        await message.reply_text("🔍 Analisando...")
        
        # v3.8.0: BIOMETRIA OBRIGATÓRIA
        history = get_recent_biometrics(7)
        baseline = calculate_biometric_baseline(history)
        bio_context = format_biometric_context(history, baseline)
        
        prompt = f"""
{bio_context}

{extra_context}

### ATIVIDADE:

{activity.to_detailed_text()}

### TAREFA:
Análise DETALHADA desta atividade.
Avalia intensidade, volume, técnica e recuperação necessária.
PRIORIZA dados biométricos.
"""
        
        response_text = await call_gemini_with_retry(prompt, user_id)
        
        # Envia resposta
        if len(response_text) <= TELEGRAM_SAFE_MESSAGE_LENGTH:
            await message.reply_text(response_text)
        else:
            chunks = [response_text[i:i+TELEGRAM_SAFE_MESSAGE_LENGTH] 
                     for i in range(0, len(response_text), TELEGRAM_SAFE_MESSAGE_LENGTH)]
            for chunk in chunks:
                await message.reply_text(chunk)
        
        # Salva contexto
        add_to_context_history(user_id, 'analyze_activity', prompt, response_text)
        
    except GeminiTimeoutError:
        await message.reply_text(
            f"⏱️ Timeout após {GEMINI_TIMEOUT_SECONDS}s.\n"
            "Tenta novamente."
        )
    except Exception as e:
        logger.error(f"Erro em perform_activity_analysis: {e}\n{traceback.format_exc()}")
        await message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def bike_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback para tipo de ciclismo (mantido para compatibilidade)"""
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("⚠️ Use /analyze_activity para análise.")

async def cycling_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback para tipo de ciclismo (mantido para compatibilidade)"""
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("⚠️ Use /analyze_activity para análise.")

async def import_historical(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler /import - Importa histórico"""
    try:
        if create_import_request():
            await update.message.reply_text(
                "✅ Pedido de importação criado.\n\n"
                "O sistema vai importar atividades na próxima sincronização.\n"
                "Aguarda alguns minutos."
            )
        else:
            await update.message.reply_text("❌ Erro ao criar pedido.")
    except Exception as e:
        logger.error(f"Erro em /import: {e}")
        await update.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def sync_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler /sync - Sincroniza dados"""
    keyboard = [
        [
            InlineKeyboardButton("✅ Confirmar", callback_data="sync_confirmed"),
            InlineKeyboardButton("❌ Cancelar", callback_data="sync_cancel")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🔄 Sincronizar dados do Garmin?\n\n"
        "Isto vai:\n"
        "• Reorganizar atividades\n"
        "• Remover duplicados\n"
        "• Atualizar biometria",
        reply_markup=reply_markup
    )

async def sync_confirmed_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback de confirmação de sync"""
    query = update.callback_query
    await query.answer()
    
    try:
        await query.message.reply_text("🔄 Sincronizando...")
        
        # Cria pedido de sync
        if not create_sync_request():
            await query.message.reply_text("❌ Erro ao criar pedido de sync.")
            return
        
        # Reorganiza atividades
        duplicates, total, messages = reorganize_activities()
        
        msg = "✅ Sincronização completa:\n\n"
        msg += "\n".join(messages)
        
        await query.message.reply_text(msg)
        
    except Exception as e:
        logger.error(f"Erro em sync: {e}")
        await query.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def cleanup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler /cleanup - Limpeza de dados"""
    try:
        await update.message.reply_text("🧹 Limpando...")
        
        cleaned, messages = cleanup_old_flags()
        
        msg = f"🧹 LIMPEZA:\n\n"
        msg += "\n".join(messages)
        msg += f"\n\nTotal: {cleaned} flags removidos"
        
        await update.message.reply_text(msg)
        
    except Exception as e:
        logger.error(f"Erro em /cleanup: {e}")
        await update.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler /history - Lista análises anteriores"""
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
    """Handler /clear_context - Limpa contexto"""
    user_id = update.effective_user.id
    
    if clear_context_disk(user_id):
        await update.message.reply_text("🗑️ Contexto limpo.")
    else:
        await update.message.reply_text("ℹ️ Sem contexto.")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler /stats - Estatísticas"""
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
    
    # Circuit breaker e cache
    msg += f"\n🔌 Circuit Breaker:\n"
    msg += f"  Estado: {circuit_breaker.state}\n"
    msg += f"  Falhas: {circuit_breaker.failure_count}/{CIRCUIT_BREAKER_THRESHOLD}\n"
    
    msg += f"\n💾 Cache:\n"
    msg += f"  Entradas: {len(response_cache.cache)}/{RESPONSE_CACHE_SIZE}\n"
    
    # v3.8.0: Health info
    avg_latency = health_state.get_avg_gemini_latency()
    if avg_latency:
        msg += f"\n⚡ Gemini:\n"
        msg += f"  Latência média: {avg_latency}s\n"
    
    await update.message.reply_text(msg)

async def debug_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler /debug - Informações de debug
    v3.8.0: RESILIENTE - Usa nova lógica de leitura
    """
    try:
        data = load_garmin_data()
        
        # v3.8.0: Usa função resiliente
        all_activities = get_all_formatted_activities()
        
        msg = f"""🔧 DEBUG v{BOT_VERSION}:

📊 Dados Garmin: {'Sim' if data else 'Não'}
🏃 Atividades: {len(all_activities)}
"""
        
        if data:
            history = parse_garmin_history(data)
            valid = [h for h in history if h.is_valid()]
            msg += f"✅ Dias válidos: {len(valid)}"
        
        msg += f"\n\n🔌 Circuit Breaker: {circuit_breaker.state}"
        msg += f"\n💾 Cache: {len(response_cache.cache)} entradas"
        msg += f"\n⏱️ Rate limiter: {len(rate_limiter.requests)} users tracked"
        
        # v3.8.0: Integridade do ficheiro
        is_valid, status = check_activities_integrity()
        status_icon = "✅" if is_valid else "⚠️"
        msg += f"\n\n{status_icon} activities.json: {status}"
        
        await update.message.reply_text(msg)
        
    except Exception as e:
        logger.error(f"Erro em /debug: {e}\n{traceback.format_exc()}")
        await update.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    v3.8.0: Handler /health - Health check endpoint
    Verifica:
    - Espaço em disco
    - Integridade do activities.json
    - Latência do Gemini
    """
    try:
        await update.message.reply_text("🏥 A verificar saúde do sistema...")
        
        # Espaço em disco
        has_space, free_mb = check_disk_space()
        disk_status = "✅" if has_space else "🔴"
        disk_text = f"{disk_status} Espaço: {free_mb:.1f}MB livre"
        
        # Integridade do ficheiro
        is_valid, integrity_msg = check_activities_integrity()
        integrity_status = "✅" if is_valid else "⚠️"
        integrity_text = f"{integrity_status} activities.json: {integrity_msg}"
        
        # Latência Gemini
        avg_latency = health_state.get_avg_gemini_latency()
        if avg_latency:
            latency_status = "✅" if avg_latency < 30 else "⚠️" if avg_latency < 45 else "🔴"
            latency_text = f"{latency_status} Gemini: {avg_latency}s (avg últimas {len(health_state.gemini_latencies)} calls)"
        else:
            latency_text = "ℹ️ Gemini: Sem dados de latência"
        
        # Circuit breaker
        cb_status = "✅" if circuit_breaker.state == 'closed' else "⚠️" if circuit_breaker.state == 'half-open' else "🔴"
        cb_text = f"{cb_status} Circuit Breaker: {circuit_breaker.state}"
        
        msg = f"""🏥 HEALTH CHECK v{BOT_VERSION}:

{disk_text}
{integrity_text}
{latency_text}
{cb_text}

💾 Cache: {len(response_cache.cache)}/{RESPONSE_CACHE_SIZE}
⏱️ Timeout: {GEMINI_TIMEOUT_SECONDS}s
"""
        
        await update.message.reply_text(msg)
        
    except Exception as e:
        logger.error(f"Erro em /health: {e}\n{traceback.format_exc()}")
        await update.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler /help - Ajuda completa
    v3.8.0: CORRIGIDO - Inclui todos os comandos
    """
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
        "🆕 v3.8.0:\n"
        "• Blindagem de dados (corrupção list→dict)\n"
        "• Health check endpoint (/health)\n"
        "• Cadência de ciclismo (rpm)\n"
        "• Comandos /sync, /import, /debug recuperados\n"
        "• Timeout aumentado (60s)\n"
        "• Biometria obrigatória em todas análises\n"
        "• Atomic write para activities.json"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler para mensagens de texto livre (follow-up questions)"""
    user_id = update.effective_user.id
    message_text = update.message.text
    
    try:
        # Verifica se há contexto ativo
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
        
        # v3.8.0: BIOMETRIA OBRIGATÓRIA
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
        "Comandos disponíveis:\n"
        "/start - Iniciar bot\n"
        "/status - Avalia readiness\n"
        "/analyze - Analisa aderência ao plano\n"
        "/analyze_activity - Analisa atividade individual\n"
        "/activities - Lista atividades\n"
        "/sync - Sincroniza dados\n"
        "/import - Importa histórico\n"
        "/cleanup - Limpeza de dados\n"
        "/history - Análises anteriores\n"
        "/clear_context - Limpa contexto\n"
        "/stats - Estatísticas\n"
        "/debug - Informações de debug\n"
        "/health - Health check do sistema\n"
        "/help - Ajuda completa"
    )

# ==========================================
# MAIN (v3.8.0)
# ==========================================
def main():
    """Entry point v3.8.0"""
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
    
    # v3.8.0: Verificação de integridade no boot
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
    
    # Command Handlers (v3.8.0: TODOS ATIVOS)
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
    app.add_handler(CommandHandler("health", health_command))  # v3.8.0: NOVO
    app.add_handler(CommandHandler("help", help_command))
    
    # Callback Query Handlers
    app.add_handler(CallbackQueryHandler(sync_confirmed_callback, pattern=r'^sync_confirmed$'))
    app.add_handler(CallbackQueryHandler(bike_callback, pattern=r'^bike_(yes|no)$'))
    app.add_handler(CallbackQueryHandler(analyze_activity_callback, pattern=r'^analyze_act_\d+$'))
    app.add_handler(CallbackQueryHandler(cargo_callback, pattern=r'^cargo_(yes|no)_\d+$'))
    app.add_handler(CallbackQueryHandler(cycling_type_callback, pattern=r'^cyctype_\w+_\d+$'))
    
    # Message Handler (texto livre)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Handler para comandos não reconhecidos (DEVE VIR POR ÚLTIMO)
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))
    
    logger.info("✅ Bot v3.8.0 iniciado com:")
    logger.info(f"  - Timeout Gemini: {GEMINI_TIMEOUT_SECONDS}s (aumentado)")
    logger.info(f"  - Retry delays: {RETRY_DELAYS}")
    logger.info(f"  - Circuit breaker: {CIRCUIT_BREAKER_THRESHOLD} falhas threshold")
    logger.info(f"  - Cache: {RESPONSE_CACHE_SIZE} entradas, TTL {CACHE_TTL_SECONDS}s")
    logger.info(f"  - Rate limit: {RATE_LIMIT_MAX_REQUESTS} req/{RATE_LIMIT_WINDOW}s")
    logger.info(f"  - Biometric context: OBRIGATÓRIO")
    logger.info(f"  - Data resilience: ENABLED (list→dict auto-fix)")
    logger.info(f"  - Atomic write: ENABLED")
    logger.info(f"  - Health check: /health ENABLED")
    logger.info(f"  - Bike cadence: ENABLED (rpm)")
    logger.info(f"  - Altitude extraction: ENABLED")
    logger.info(f"  - Run cadence: ENABLED (spm)")
    
    print(f"🤖 Bot v{BOT_VERSION} ativo")
    
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == '__main__':
    main()
