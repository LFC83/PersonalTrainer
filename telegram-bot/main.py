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
# CONFIGURATION & CONSTANTS
# ==========================================
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
logger.info("Gemini API configurada")

# Bot Configuration
BOT_VERSION = "3.7.0"
BOT_VERSION_DESC = "Biometric Context + Altitude + Cadence + Intelligent Retry + System Prompt Update"
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
GEMINI_TIMEOUT_SECONDS = 45  # v3.7.0: Aumentado de 30 para 45 segundos

# Context Management
CONTEXT_TIMEOUT_MINUTES = 15
MAX_CONTEXT_HISTORY = 3

# Disk Space
MIN_DISK_SPACE_MB = 10

# Cycling Types
CYCLING_TYPES = ["Spinning", "MTB", "Commute", "Estrada"]

# v3.6: Retry & Circuit Breaker
MAX_RETRIES = 3
RETRY_DELAYS = [2, 5, 10]  # v3.7.0: Delays mais inteligentes (2s, 5s, 10s)
CIRCUIT_BREAKER_THRESHOLD = 5  # consecutive failures
CIRCUIT_BREAKER_TIMEOUT = 60  # seconds

# v3.6: Rate Limiting
RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_MAX_REQUESTS = 10  # requests per window per user

# v3.6: Cache
RESPONSE_CACHE_SIZE = 100  # max cached responses

# ==========================================
# SYSTEM PROMPT (v3.7.0 ATUALIZADO)
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
# DATA MODELS (v3.7.0 EXPANDIDO)
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
    """Atividade formatada para display (v3.7.0: + altimetria + cadência)"""
    date: Optional[str]
    sport: str
    duration_min: float
    distance_km: Optional[float] = None
    avg_hr: Optional[int] = None
    calories: Optional[int] = None
    intensity: Optional[str] = None
    load: Optional[float] = None
    elevation_gain: Optional[float] = None  # v3.7.0: Ganho de elevação (metros)
    avg_cadence: Optional[int] = None       # v3.7.0: Cadência média (spm para corrida)
    max_cadence: Optional[int] = None       # v3.7.0: Cadência máxima (spm)
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
        """Retorna resumo detalhado multi-linha (v3.7.0: + altimetria + cadência)"""
        lines = [f"📅 {self.date} - {self.sport}"]
        lines.append(f"  ⏱️ Duração: {self.duration_min}min")
        
        if self.distance_km:
            lines.append(f"  📏 Dist: {self.distance_km}km")
        
        # v3.7.0: Altimetria
        if self.elevation_gain:
            lines.append(f"  ⛰️ D+: {self.elevation_gain}m")
        
        if self.intensity:
            lines.append(f"  🎯 Zona: {self.intensity}")
        
        if self.load:
            lines.append(f"  💪 Load: {self.load}")
        
        # v3.7.0: Cadência (apenas para corrida)
        if self.avg_cadence and "Corr" in self.sport:
            lines.append(f"  👟 Cadência: {self.avg_cadence} spm")
        
        if self.avg_hr or self.calories:
            detail = "  "
            if self.avg_hr:
                detail += f"💓 FC: {self.avg_hr}bpm"
            if self.calories:
                detail += f" 🔥 {self.calories}kcal"
            lines.append(detail)
        
        return "\n".join(lines)

@dataclass
class ContextEntry:
    """Entrada de contexto conversacional"""
    timestamp: str
    analysis_type: str
    prompt: str
    response: str
    user_input: Optional[str] = None

# ==========================================
# v3.6: CIRCUIT BREAKER
# ==========================================
class CircuitBreaker:
    """Circuit breaker para proteger contra falhas consecutivas"""
    def __init__(self, threshold: int, timeout: int):
        self.threshold = threshold
        self.timeout = timeout
        self.failure_count = 0
        self.last_failure_time = None
        self.state = "closed"  # closed, open, half-open
    
    def call_failed(self):
        """Registra falha"""
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.failure_count >= self.threshold:
            self.state = "open"
            logger.warning(f"Circuit breaker OPEN após {self.failure_count} falhas")
    
    def call_succeeded(self):
        """Registra sucesso"""
        self.failure_count = 0
        self.state = "closed"
        logger.info("Circuit breaker CLOSED após sucesso")
    
    def can_attempt(self) -> bool:
        """Verifica se pode tentar chamada"""
        if self.state == "closed":
            return True
        
        if self.state == "open":
            # Verifica se timeout passou
            if time.time() - self.last_failure_time > self.timeout:
                self.state = "half-open"
                logger.info("Circuit breaker HALF-OPEN (tentando recuperação)")
                return True
            return False
        
        # half-open: permite tentativa
        return True

circuit_breaker = CircuitBreaker(CIRCUIT_BREAKER_THRESHOLD, CIRCUIT_BREAKER_TIMEOUT)

# ==========================================
# v3.6: RESPONSE CACHE
# ==========================================
class ResponseCache:
    """Cache de respostas para evitar chamadas duplicadas"""
    def __init__(self, max_size: int, ttl: int):
        self.cache: Dict[str, Tuple[str, float]] = {}
        self.max_size = max_size
        self.ttl = ttl
    
    def get(self, key: str) -> Optional[str]:
        """Busca resposta no cache"""
        if key not in self.cache:
            return None
        
        response, timestamp = self.cache[key]
        
        # Verifica TTL
        if time.time() - timestamp > self.ttl:
            del self.cache[key]
            return None
        
        logger.info(f"Cache HIT: {key[:50]}...")
        return response
    
    def set(self, key: str, value: str):
        """Armazena resposta no cache"""
        # Limita tamanho do cache
        if len(self.cache) >= self.max_size:
            # Remove entrada mais antiga
            oldest_key = min(self.cache.keys(), key=lambda k: self.cache[k][1])
            del self.cache[oldest_key]
        
        self.cache[key] = (value, time.time())
        logger.info(f"Cache SET: {key[:50]}...")
    
    def make_key(self, prompt: str) -> str:
        """Cria chave de cache a partir do prompt"""
        return hashlib.md5(prompt.encode()).hexdigest()

response_cache = ResponseCache(RESPONSE_CACHE_SIZE, CACHE_TTL_SECONDS)

# ==========================================
# v3.6: RATE LIMITER
# ==========================================
class RateLimiter:
    """Rate limiter por usuário"""
    def __init__(self, window: int, max_requests: int):
        self.window = window
        self.max_requests = max_requests
        self.requests: Dict[int, List[float]] = defaultdict(list)
    
    def can_proceed(self, user_id: int) -> bool:
        """Verifica se usuário pode fazer request"""
        now = time.time()
        
        # Remove requests antigos
        self.requests[user_id] = [
            ts for ts in self.requests[user_id]
            if now - ts < self.window
        ]
        
        # Verifica limite
        if len(self.requests[user_id]) >= self.max_requests:
            logger.warning(f"Rate limit excedido para user {user_id}")
            return False
        
        # Registra novo request
        self.requests[user_id].append(now)
        return True

rate_limiter = RateLimiter(RATE_LIMIT_WINDOW, RATE_LIMIT_MAX_REQUESTS)

# ==========================================
# FILE OPERATIONS
# ==========================================
def ensure_data_dir():
    """Garante que o diretório de dados existe"""
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
    """Carrega índice de atividades"""
    ensure_data_dir()
    try:
        path = os.path.join(DATA_DIR, 'activities.json')
        if not os.path.exists(path):
            return {}
        
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Erro ao carregar activities.json: {e}")
        return {}

def save_activities_index(activities: Dict):
    """Salva índice de atividades"""
    ensure_data_dir()
    try:
        path = os.path.join(DATA_DIR, 'activities.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(activities, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Erro ao salvar activities.json: {e}")
        raise FileOperationError(f"Falha ao salvar atividades: {e}")

def check_disk_space() -> bool:
    """Verifica se há espaço em disco suficiente"""
    try:
        stat = os.statvfs(DATA_DIR)
        free_mb = (stat.f_bavail * stat.f_frsize) / (1024 * 1024)
        return free_mb >= MIN_DISK_SPACE_MB
    except Exception as e:
        logger.error(f"Erro ao verificar espaço em disco: {e}")
        return True  # Assume OK se não conseguir verificar

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
# DATA EXTRACTION (v3.7.0: + ALTIMETRIA + CADÊNCIA)
# ==========================================
def extract_elevation_from_raw(raw_data: Dict, sport: str) -> Optional[float]:
    """
    Extrai ganho de elevação do JSON raw da Garmin
    v3.7.0: Nova função para extrair altimetria
    """
    try:
        # Garmin pode usar diferentes campos dependendo do tipo de atividade
        elevation_fields = [
            'totalElevationGain',
            'elevationGain', 
            'totalAscent',
            'gainElevation'
        ]
        
        for field in elevation_fields:
            if field in raw_data and raw_data[field] is not None:
                value = raw_data[field]
                # Converter para metros se necessário
                if isinstance(value, (int, float)):
                    return round(float(value), 1)
        
        return None
    except Exception as e:
        logger.debug(f"Erro ao extrair elevação: {e}")
        return None

def extract_cadence_from_raw(raw_data: Dict, sport: str) -> Tuple[Optional[int], Optional[int]]:
    """
    Extrai cadência média e máxima do JSON raw da Garmin
    v3.7.0: Nova função para extrair cadência (especialmente para corrida)
    """
    try:
        avg_cadence = None
        max_cadence = None
        
        # Para corrida, Garmin reporta em strides per minute (divide por 2 para obter steps per minute)
        # ou já em steps per minute dependendo do modelo
        if "Corr" in sport or "Run" in sport:
            cadence_fields_avg = ['averageRunCadence', 'avgRunCadence', 'averageCadence']
            cadence_fields_max = ['maxRunCadence', 'maxCadence']
            
            for field in cadence_fields_avg:
                if field in raw_data and raw_data[field] is not None:
                    value = raw_data[field]
                    # Se valor está entre 80-100, provavelmente é strides/min (multiply by 2)
                    # Se está entre 160-200, já é steps/min
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
        logger.debug(f"Erro ao extrair cadência: {e}")
        return None, None

def needs_data_enrichment(activity: Dict) -> bool:
    """
    Verifica se atividade precisa de enriquecimento de dados
    v3.7.0: Nova função para detectar dados em falta
    """
    sport = activity.get('sport', '')
    
    # Verifica se é ciclismo ou corrida (atividades que devem ter altimetria)
    is_cycling = any(x in sport for x in ['Cicl', 'MTB', 'Spin', 'Bike'])
    is_running = any(x in sport for x in ['Corr', 'Run'])
    
    missing_elevation = (is_cycling or is_running) and activity.get('elevation_gain') is None
    missing_cadence = is_running and activity.get('avg_cadence') is None
    
    return missing_elevation or missing_cadence

def enrich_activity_from_garmin(activity_id: str, garmin_data: Dict) -> Dict:
    """
    Re-extrai dados de uma atividade específica do JSON da Garmin
    v3.7.0: Nova função para retrocompatibilidade
    """
    try:
        # Procura atividade no garmin_dump.json
        activities_list = garmin_data.get('activities', [])
        
        for raw_act in activities_list:
            if str(raw_act.get('activityId')) == str(activity_id):
                sport = raw_act.get('activityType', {}).get('typeKey', 'Unknown')
                
                # Extrai novos dados
                elevation = extract_elevation_from_raw(raw_act, sport)
                avg_cad, max_cad = extract_cadence_from_raw(raw_act, sport)
                
                enriched = {
                    'elevation_gain': elevation,
                    'avg_cadence': avg_cad,
                    'max_cadence': max_cad
                }
                
                logger.info(f"Atividade {activity_id} enriquecida: elevation={elevation}m, cadence={avg_cad}spm")
                return enriched
        
        logger.warning(f"Atividade {activity_id} não encontrada no garmin_dump.json")
        return {}
    except Exception as e:
        logger.error(f"Erro ao enriquecer atividade {activity_id}: {e}")
        return {}

def check_and_enrich_activities():
    """
    Verifica todas as atividades e enriquece as que precisam
    v3.7.0: Nova função de manutenção automática
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
    """
    Calcula baseline biométrico
    v3.7.0: Função crítica para contexto biométrico
    """
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
    v3.7.0: CRÍTICO - Injecção de biometria no prompt
    """
    if not history or not baseline:
        return "⚠️ DADOS BIOMÉTRICOS INSUFICIENTES"
    
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
    v3.7.0: Expandido para extrair altimetria e cadência
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
            
            # v3.7.0: Extrair altimetria
            elevation_gain = extract_elevation_from_raw(act, sport)
            
            # v3.7.0: Extrair cadência
            avg_cadence, max_cadence = extract_cadence_from_raw(act, sport)
            
            activity = {
                'id': str(activity_id),
                'date': date_str,
                'sport': sport,
                'duration_min': duration_min,
                'distance_km': distance_km,
                'avg_hr': avg_hr,
                'calories': calories,
                'elevation_gain': elevation_gain,  # v3.7.0
                'avg_cadence': avg_cadence,        # v3.7.0
                'max_cadence': max_cadence,        # v3.7.0
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
    v3.7.0: Inclui novos campos (altimetria + cadência)
    """
    activities_index = load_activities_index()
    formatted = []
    
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
                elevation_gain=activity_data.get('elevation_gain'),  # v3.7.0
                avg_cadence=activity_data.get('avg_cadence'),        # v3.7.0
                max_cadence=activity_data.get('max_cadence'),        # v3.7.0
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
# GEMINI API (v3.7.0: RETRY INTELIGENTE)
# ==========================================
async def call_gemini_with_retry(prompt: str, user_id: int) -> str:
    """
    Chama Gemini com retry inteligente
    v3.7.0: Delays maiores após timeout, backoff exponencial
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
                
                # v3.7.0: Se última tentativa foi timeout, usa delay maior
                if was_timeout:
                    delay = delay * 2  # Dobra o delay após timeout
                    logger.info(f"Timeout detectado, delay aumentado para {delay}s")
                
                logger.info(f"Retry {attempt}/{MAX_RETRIES} após {delay}s")
                await asyncio.sleep(delay)
            
            logger.info(f"Chamando Gemini (tentativa {attempt + 1}/{MAX_RETRIES})...")
            
            # v3.7.0: Timeout aumentado para 45s
            response = await asyncio.wait_for(
                asyncio.to_thread(model.generate_content, prompt),
                timeout=GEMINI_TIMEOUT_SECONDS
            )
            
            if not response or not response.text:
                raise ValueError("Resposta vazia do Gemini")
            
            result = response.text.strip()
            
            # Sucesso: atualiza circuit breaker e cache
            circuit_breaker.call_succeeded()
            response_cache.set(cache_key, result)
            
            logger.info(f"✅ Gemini respondeu ({len(result)} chars)")
            return result
            
        except asyncio.TimeoutError:
            was_timeout = True  # v3.7.0: Flag para ajustar próximo delay
            last_error = GeminiTimeoutError(f"Timeout após {GEMINI_TIMEOUT_SECONDS}s")
            logger.warning(f"Timeout na tentativa {attempt + 1}")
            
        except Exception as e:
            was_timeout = False
            last_error = e
            logger.error(f"Erro na tentativa {attempt + 1}: {e}")
    
    # Todas as tentativas falharam
    circuit_breaker.call_failed()
    
    if isinstance(last_error, GeminiTimeoutError):
        raise last_error
    else:
        raise Exception(f"Gemini falhou após {MAX_RETRIES} tentativas: {last_error}")

def truncate_prompt_if_needed(prompt: str, max_length: int = GEMINI_MAX_PROMPT_LENGTH) -> str:
    """Trunca prompt se exceder limite"""
    if len(prompt) <= max_length:
        return prompt
    
    logger.warning(f"Prompt muito grande ({len(prompt)} chars), truncando...")
    
    # Mantém início e fim, remove meio
    keep_start = int(max_length * 0.4)
    keep_end = int(max_length * 0.4)
    
    truncated = (
        prompt[:keep_start] + 
        f"\n\n[... {len(prompt) - max_length} caracteres removidos ...]\n\n" + 
        prompt[-keep_end:]
    )
    
    return truncated

# ==========================================
# CONTEXT MANAGEMENT
# ==========================================
def get_context_path(user_id: int) -> str:
    """Retorna caminho do arquivo de contexto do usuário"""
    ensure_data_dir()
    contexts_dir = os.path.join(DATA_DIR, 'contexts')
    os.makedirs(contexts_dir, exist_ok=True)
    return os.path.join(contexts_dir, f"{user_id}.json")

def load_context_from_disk(user_id: int) -> Optional[Dict]:
    """Carrega contexto do disco"""
    try:
        path = get_context_path(user_id)
        if not os.path.exists(path):
            return None
        
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Erro ao carregar contexto de {user_id}: {e}")
        return None

def save_context_to_disk(user_id: int, context_data: Dict):
    """Salva contexto no disco"""
    try:
        path = get_context_path(user_id)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(context_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Erro ao salvar contexto de {user_id}: {e}")

def clear_context_disk(user_id: int) -> bool:
    """Remove arquivo de contexto"""
    try:
        path = get_context_path(user_id)
        if os.path.exists(path):
            os.remove(path)
            return True
        return False
    except Exception as e:
        logger.error(f"Erro ao limpar contexto de {user_id}: {e}")
        return False

def add_to_context(user_id: int, analysis_type: str, prompt: str, response: str, user_input: Optional[str] = None):
    """Adiciona entrada ao contexto"""
    context_data = load_context_from_disk(user_id) or {'history': [], 'last_updated': None}
    
    entry = ContextEntry(
        timestamp=datetime.now().isoformat(),
        analysis_type=analysis_type,
        prompt=prompt,
        response=response,
        user_input=user_input
    )
    
    context_data['history'].append(asdict(entry))
    context_data['last_updated'] = datetime.now().isoformat()
    
    # Limita histórico
    if len(context_data['history']) > MAX_CONTEXT_HISTORY:
        context_data['history'] = context_data['history'][-MAX_CONTEXT_HISTORY:]
    
    save_context_to_disk(user_id, context_data)

def get_context_stats() -> Dict:
    """Retorna estatísticas dos contextos"""
    try:
        contexts_dir = os.path.join(DATA_DIR, 'contexts')
        if not os.path.exists(contexts_dir):
            return {'total_users': 0, 'by_type': {}}
        
        total_users = 0
        analysis_counts = defaultdict(int)
        
        for file in os.listdir(contexts_dir):
            if not file.endswith('.json'):
                continue
            
            total_users += 1
            
            try:
                with open(os.path.join(contexts_dir, file), 'r') as f:
                    data = json.load(f)
                    for entry in data.get('history', []):
                        analysis_type = entry.get('analysis_type', 'unknown')
                        analysis_counts[analysis_type] += 1
            except:
                pass
        
        return {
            'total_users': total_users,
            'by_type': dict(analysis_counts)
        }
    except Exception as e:
        return {'error': str(e)}

def build_conversational_prompt(user_id: int, user_message: str) -> str:
    """Constrói prompt conversacional com contexto"""
    context_data = load_context_from_disk(user_id)
    
    if not context_data or not context_data.get('history'):
        return f"Pergunta sem contexto prévio:\n\n{user_message}"
    
    # Verifica timeout
    last_updated = context_data.get('last_updated')
    if last_updated:
        try:
            last_time = datetime.fromisoformat(last_updated)
            if datetime.now() - last_time > timedelta(minutes=CONTEXT_TIMEOUT_MINUTES):
                return f"Contexto expirado. Pergunta:\n\n{user_message}"
        except:
            pass
    
    # Constrói prompt com histórico
    prompt_parts = ["Histórico da conversa (responde com base neste contexto):\n"]
    
    for entry in context_data['history'][-MAX_CONTEXT_HISTORY:]:
        analysis_type = entry.get('analysis_type', '')
        timestamp = entry.get('timestamp', '')
        user_input = entry.get('user_input', '')
        
        prompt_parts.append(f"\n--- {analysis_type} ({timestamp[:16]}) ---")
        if user_input:
            prompt_parts.append(f"Input do usuário: {user_input}")
    
    prompt_parts.append(f"\n\n--- NOVA PERGUNTA ---")
    prompt_parts.append(user_message)
    
    return "\n".join(prompt_parts)

# ==========================================
# TELEGRAM HANDLERS
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler /start"""
    await update.message.reply_text(
        f"🏋️ FitnessJournal v{BOT_VERSION}\n\n"
        "Comandos:\n"
        "/status - Avalia readiness\n"
        "/analyze - Analisa aderência ao plano\n"
        "/analyze_activity - Analisa atividade individual\n"
        "/activities - Lista atividades\n"
        "/history - Análises anteriores\n"
        "/help - Ajuda completa"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler /status
    v3.7.0: INJETA BIOMETRIA NO PROMPT
    """
    user_id = update.effective_user.id
    
    msg = await update.message.reply_text("🔍 A analisar...")
    
    try:
        # Obtém dados biométricos
        history = get_recent_biometrics(7)
        if not history:
            await msg.edit_text("❌ Sem dados biométricos. Faz /sync primeiro.")
            return
        
        # v3.7.0: Calcula baseline e formata contexto
        baseline = calculate_biometric_baseline(history)
        biometric_context = format_biometric_context(history, baseline)
        
        # Obtém atividades recentes
        all_activities = get_all_formatted_activities()
        recent = all_activities[:MAX_ACTIVITIES_IN_ANALYSIS]
        
        # Constrói prompt COM BIOMETRIA
        prompt = f"""Analisa o estado de readiness do atleta.

{biometric_context}

ATIVIDADES RECENTES ({len(recent)}):
"""
        
        for act in recent:
            prompt += f"\n{act.to_brief_summary()}"
        
        prompt += "\n\nDá recomendação de treino para hoje baseada em:"
        prompt += "\n1. Estado biométrico (HRV/RHR) - PRIORIDADE MÁXIMA"
        prompt += "\n2. Carga de treino recente"
        prompt += "\n3. Padrão de recuperação"
        
        # Chama Gemini com retry inteligente
        response = await call_gemini_with_retry(prompt, user_id)
        
        # Salva contexto
        add_to_context(user_id, 'status', prompt, response)
        
        # Envia resposta (pode ser dividida se muito grande)
        await send_long_message(msg, response)
        
    except RateLimitExceeded as e:
        await msg.edit_text(f"⏳ {str(e)}")
    except CircuitBreakerOpen:
        await msg.edit_text("⚠️ Serviço temporariamente indisponível. Tenta em 1 minuto.")
    except GeminiTimeoutError:
        await msg.edit_text("⏱️ Timeout ao analisar. Tenta novamente.")
    except Exception as e:
        logger.error(f"Erro em /status: {e}\n{traceback.format_exc()}")
        await msg.edit_text("❌ Erro na análise.")

async def send_long_message(message_obj, text: str):
    """Envia mensagem longa dividindo se necessário"""
    if len(text) <= TELEGRAM_SAFE_MESSAGE_LENGTH:
        try:
            await message_obj.edit_text(text)
        except BadRequest:
            # Se falhar, tenta sem edit
            await message_obj.reply_text(text)
        return
    
    # Divide em partes
    parts = []
    current_part = ""
    
    for line in text.split('\n'):
        if len(current_part) + len(line) + 1 > TELEGRAM_SAFE_MESSAGE_LENGTH:
            parts.append(current_part)
            current_part = line
        else:
            current_part += '\n' + line if current_part else line
    
    if current_part:
        parts.append(current_part)
    
    # Envia primeira parte como edit
    try:
        await message_obj.edit_text(parts[0])
    except BadRequest:
        await message_obj.reply_text(parts[0])
    
    # Envia resto como mensagens novas
    for part in parts[1:]:
        await message_obj.reply_text(part)

async def activities_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lista atividades recentes"""
    all_activities = get_all_formatted_activities()
    
    if not all_activities:
        await update.message.reply_text("📭 Sem atividades. Faz /sync primeiro.")
        return
    
    # v3.7.0: Mostra novos campos (altimetria + cadência)
    recent = all_activities[:MAX_ACTIVITIES_DISPLAY]
    
    msg = f"🏃 ATIVIDADES RECENTES ({len(recent)}/{len(all_activities)}):\n\n"
    
    for i, act in enumerate(recent, 1):
        msg += f"{i}. {act.to_detailed_summary()}\n\n"
    
    msg += f"💡 Usa /analyze_activity para análise detalhada."
    
    await update.message.reply_text(msg)

async def analyze_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler /analyze - Aderência ao plano
    v3.7.0: INJETA BIOMETRIA NO PROMPT
    """
    user_id = update.effective_user.id
    
    msg = await update.message.reply_text("🔍 A analisar aderência...")
    
    try:
        # Obtém biometria
        history = get_recent_biometrics(7)
        baseline = calculate_biometric_baseline(history)
        biometric_context = format_biometric_context(history, baseline)
        
        # Obtém atividades
        all_activities = get_all_formatted_activities()
        if len(all_activities) < 3:
            await msg.edit_text("❌ Preciso de pelo menos 3 atividades para analisar aderência.")
            return
        
        recent = all_activities[:MAX_ACTIVITIES_IN_ANALYSIS]
        
        # Constrói prompt COM BIOMETRIA
        prompt = f"""Analisa a aderência do atleta ao plano de treino.

{biometric_context}

ATIVIDADES RECENTES ({len(recent)}):
"""
        
        for act in recent:
            prompt += f"\n📅 {act.date}:\n{act.to_brief_summary()}\n"
        
        prompt += "\n\nAvalia:"
        prompt += "\n1. Consistência e progressão de carga"
        prompt += "\n2. Equilíbrio entre volume e recuperação (baseado em HRV/RHR)"
        prompt += "\n3. Recomendações para próxima semana"
        
        # Chama Gemini
        response = await call_gemini_with_retry(prompt, user_id)
        
        # Salva contexto
        add_to_context(user_id, 'analyze_plan', prompt, response)
        
        await send_long_message(msg, response)
        
    except RateLimitExceeded as e:
        await msg.edit_text(f"⏳ {str(e)}")
    except CircuitBreakerOpen:
        await msg.edit_text("⚠️ Serviço temporariamente indisponível.")
    except GeminiTimeoutError:
        await msg.edit_text("⏱️ Timeout. Tenta novamente.")
    except Exception as e:
        logger.error(f"Erro em /analyze: {e}\n{traceback.format_exc()}")
        await msg.edit_text("❌ Erro na análise.")

async def analyze_activity_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler /analyze_activity
    v3.7.0: Agora pergunta sobre carga ANTES de enviar para Gemini
    """
    all_activities = get_all_formatted_activities()
    
    if not all_activities:
        await update.message.reply_text("📭 Sem atividades. Faz /sync primeiro.")
        return
    
    # Mostra últimas 5 atividades com botões
    recent = all_activities[:5]
    
    keyboard = []
    for i, act in enumerate(recent):
        button_text = f"{act.date} - {act.sport} ({act.duration_min}min)"
        callback_data = f"analyze_act_{i}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🔍 Escolhe atividade para analisar:",
        reply_markup=reply_markup
    )

async def analyze_activity_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Callback quando user escolhe atividade
    v3.7.0: Pergunta sobre carga/passageiro ANTES de analisar
    """
    query = update.callback_query
    await query.answer()
    
    # Extrai índice
    activity_index = int(query.data.split('_')[-1])
    
    all_activities = get_all_formatted_activities()
    if activity_index >= len(all_activities):
        await query.edit_message_text("❌ Atividade não encontrada.")
        return
    
    activity = all_activities[activity_index]
    
    # v3.7.0: Verifica se é ciclismo → pergunta sobre carga
    is_cycling = any(x in activity.sport for x in ['Cicl', 'MTB', 'Spin', 'Bike', 'Estrada'])
    
    if is_cycling:
        # Pergunta sobre passageiro/carga
        keyboard = [
            [InlineKeyboardButton("✅ Sim (com carga)", callback_data=f"cargo_yes_{activity_index}")],
            [InlineKeyboardButton("❌ Não (sozinho)", callback_data=f"cargo_no_{activity_index}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"🚴 {activity.sport} - {activity.date}\n\n"
            f"Esta volta teve passageiro ou carga extra?",
            reply_markup=reply_markup
        )
    else:
        # Não é ciclismo, vai direto para análise
        await perform_activity_analysis(query, activity, user_id=query.from_user.id, has_cargo=False)

async def cargo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Callback quando user responde sobre carga
    v3.7.0: Nova função crítica - recebe resposta e prossegue para análise
    """
    query = update.callback_query
    await query.answer()
    
    # Parse callback data: cargo_(yes|no)_INDEX
    parts = query.data.split('_')
    has_cargo = parts[1] == 'yes'
    activity_index = int(parts[2])
    
    all_activities = get_all_formatted_activities()
    if activity_index >= len(all_activities):
        await query.edit_message_text("❌ Atividade não encontrada.")
        return
    
    activity = all_activities[activity_index]
    
    # Agora sim, vai para análise COM informação de carga
    await perform_activity_analysis(query, activity, user_id=query.from_user.id, has_cargo=has_cargo)

async def perform_activity_analysis(query, activity: FormattedActivity, user_id: int, has_cargo: bool):
    """
    Executa análise da atividade com Gemini
    v3.7.0: Recebe informação de carga + INJETA BIOMETRIA
    """
    proc_msg = await query.edit_message_text("🔍 A analisar atividade...")
    
    try:
        # v3.7.0: Obtém biometria do dia da atividade
        history = get_recent_biometrics(7)
        baseline = calculate_biometric_baseline(history)
        
        # Encontra biometria do dia específico
        activity_date = activity.date
        day_biometrics = next((d for d in history if d.date == activity_date), None)
        
        biometric_info = ""
        if day_biometrics and day_biometrics.is_valid():
            biometric_info = f"\n📊 BIOMETRIA DO DIA ({activity_date}):\n"
            if day_biometrics.hrv:
                biometric_info += f"HRV: {day_biometrics.hrv} ms"
                if baseline.get('hrv_mean'):
                    deviation = ((day_biometrics.hrv - baseline['hrv_mean']) / baseline['hrv_mean']) * 100
                    biometric_info += f" ({deviation:+.1f}%)\n"
            if day_biometrics.rhr:
                biometric_info += f"RHR: {day_biometrics.rhr} bpm"
                if baseline.get('rhr_mean'):
                    deviation = ((day_biometrics.rhr - baseline['rhr_mean']) / baseline['rhr_mean']) * 100
                    biometric_info += f" ({deviation:+.1f}%)\n"
        
        # Constrói prompt detalhado
        prompt = f"""Analisa esta atividade em detalhe:

{biometric_info}

📅 DATA: {activity.date}
🏃 TIPO: {activity.sport}
⏱️ DURAÇÃO: {activity.duration_min} min
"""
        
        if activity.distance_km:
            prompt += f"📏 DISTÂNCIA: {activity.distance_km} km\n"
        
        # v3.7.0: Altimetria
        if activity.elevation_gain:
            prompt += f"⛰️ DESNÍVEL POSITIVO: {activity.elevation_gain} m\n"
        
        if activity.avg_hr:
            prompt += f"💓 FC MÉDIA: {activity.avg_hr} bpm\n"
        
        # v3.7.0: Cadência
        if activity.avg_cadence:
            prompt += f"👟 CADÊNCIA MÉDIA: {activity.avg_cadence} spm\n"
        if activity.max_cadence:
            prompt += f"👟 CADÊNCIA MÁXIMA: {activity.max_cadence} spm\n"
        
        if activity.calories:
            prompt += f"🔥 CALORIAS: {activity.calories} kcal\n"
        
        if activity.load:
            prompt += f"💪 TRAINING LOAD: {activity.load}\n"
        
        # v3.7.0: Informação de carga (crítico para ciclismo)
        if has_cargo:
            prompt += f"\n⚠️ CONDIÇÃO: Volta com passageiro/carga extra\n"
        
        prompt += "\n\nAvalia:"
        prompt += "\n1. Qualidade da execução (pace, FC, cadência)"
        prompt += "\n2. Nível de esforço vs. biometria do dia"
        prompt += "\n3. Pontos fortes e áreas de melhoria"
        
        if has_cargo:
            prompt += "\n4. Impacto da carga extra no desempenho"
        
        # Chama Gemini
        response = await call_gemini_with_retry(prompt, user_id)
        
        # Salva contexto
        add_to_context(user_id, 'activity_analysis', prompt, response, user_input=f"Activity {activity.date}")
        
        # Envia resposta
        await send_long_message(proc_msg, response)
        
        logger.info(f"Activity analysis completed for user {user_id}")
        
    except CircuitBreakerOpen:
        try:
            await proc_msg.edit_text(
                "⚠️ Serviço temporariamente indisponível.\n"
                "Por favor, tenta novamente em 1 minuto."
            )
        except:
            pass
            
    except GeminiTimeoutError:
        try:
            await proc_msg.edit_text("⏱️ Timeout ao analisar atividade.")
        except:
            pass
            
    except Exception as e:
        logger.error(f"Activity analysis error: {e}")
        try:
            await proc_msg.edit_text("❌ Erro na análise da atividade.")
        except:
            pass

async def bike_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback para confirmação de bike"""
    query = update.callback_query
    await query.answer()
    
    # Implementação placeholder
    await query.edit_message_text("✅ Confirmado.")

async def cycling_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback para tipo de ciclismo"""
    query = update.callback_query
    await query.answer()
    
    # Implementação placeholder
    await query.edit_message_text("✅ Tipo registado.")

async def sync_confirmed_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback para confirmação de sync"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text("🔄 A criar pedido de sync...")
    
    if create_sync_request():
        await query.message.reply_text("✅ Pedido criado. Aguarda 1min.")
    else:
        await query.message.reply_text("❌ Erro ao criar pedido.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler para mensagens de texto livre (perguntas conversacionais)
    v3.7.0: INJETA BIOMETRIA no contexto conversacional
    """
    user_id = update.effective_user.id
    user_message = update.message.text.strip()
    
    if not user_message:
        return
    
    msg = await update.message.reply_text("🤔 A pensar...")
    
    try:
        # v3.7.0: Adiciona biometria ao contexto conversacional
        history = get_recent_biometrics(7)
        baseline = calculate_biometric_baseline(history)
        biometric_context = format_biometric_context(history, baseline)
        
        # Constrói prompt conversacional COM biometria
        conversational_prompt = build_conversational_prompt(user_id, user_message)
        
        # Injeta biometria no início do prompt
        full_prompt = f"{biometric_context}\n\n{conversational_prompt}"
        
        # Chama Gemini
        response = await call_gemini_with_retry(full_prompt, user_id)
        
        # NÃO salva contexto aqui (apenas perguntas ad-hoc)
        
        await send_long_message(msg, response)
        
    except RateLimitExceeded as e:
        await msg.edit_text(f"⏳ {str(e)}")
    except CircuitBreakerOpen:
        await msg.edit_text("⚠️ Serviço temporariamente indisponível.")
    except GeminiTimeoutError:
        await msg.edit_text("⏱️ Timeout. Tenta novamente.")
    except Exception as e:
        logger.error(f"Erro em handle_message: {e}\n{traceback.format_exc()}")
        await msg.edit_text("❌ Erro ao processar pergunta.")

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
    
    # v3.6: Adicionar stats de circuit breaker e cache
    msg += f"\n🔌 Circuit Breaker:\n"
    msg += f"  Estado: {circuit_breaker.state}\n"
    msg += f"  Falhas: {circuit_breaker.failure_count}/{CIRCUIT_BREAKER_THRESHOLD}\n"
    
    msg += f"\n💾 Cache:\n"
    msg += f"  Entradas: {len(response_cache.cache)}/{RESPONSE_CACHE_SIZE}\n"
    
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
    
    # v3.6: Info adicional
    msg += f"\n\n🔌 Circuit Breaker: {circuit_breaker.state}"
    msg += f"\n💾 Cache: {len(response_cache.cache)} entradas"
    msg += f"\n⏱️ Rate limiter: {len(rate_limiter.requests)} users tracked"
    
    await update.message.reply_text(msg)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help"""
    await update.message.reply_text(
        f"🏋️ FitnessJournal v{BOT_VERSION}\n\n"
        "Comandos principais:\n"
        "/status - Readiness (com biometria)\n"
        "/analyze - Aderência ao plano\n"
        "/analyze_activity - Análise individual\n"
        "/activities - Lista atividades\n"
        "/history - Análises anteriores\n"
        "/help - Ajuda\n\n"
        "🆕 v3.7.0:\n"
        "• Contexto biométrico completo (HRV/RHR)\n"
        "• Altimetria (D+) em ciclismo/corrida\n"
        "• Cadência em corrida (spm)\n"
        "• Pergunta sobre carga no ciclismo\n"
        "• Timeout aumentado (45s)\n"
        "• Retry inteligente com backoff"
    )

# v3.7.0: HANDLER PARA COMANDO NÃO RECONHECIDO
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
        "/help - Ajuda completa"
    )

# ==========================================
# MAIN (v3.7.0)
# ==========================================
def main():
    """Entry point v3.7.0"""
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
    
    # v3.7.0: Enriquecimento automático de atividades
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
    app.add_handler(CommandHandler("help", help_command))
    
    # Callback Query Handlers
    app.add_handler(CallbackQueryHandler(sync_confirmed_callback, pattern=r'^sync_confirmed$'))
    app.add_handler(CallbackQueryHandler(bike_callback, pattern=r'^bike_(yes|no)$'))
    app.add_handler(CallbackQueryHandler(analyze_activity_callback, pattern=r'^analyze_act_\d+$'))
    app.add_handler(CallbackQueryHandler(cargo_callback, pattern=r'^cargo_(yes|no)_\d+$'))  # v3.7.0: CRÍTICO
    app.add_handler(CallbackQueryHandler(cycling_type_callback, pattern=r'^cyctype_\w+_\d+$'))
    
    # Message Handler (texto livre)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # v3.7.0: Handler para comandos não reconhecidos (DEVE VIR POR ÚLTIMO)
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))
    
    logger.info("✅ Bot v3.7.0 iniciado com:")
    logger.info(f"  - Timeout Gemini: {GEMINI_TIMEOUT_SECONDS}s (aumentado)")
    logger.info(f"  - Retry delays: {RETRY_DELAYS} (inteligente)")
    logger.info(f"  - Circuit breaker: {CIRCUIT_BREAKER_THRESHOLD} falhas threshold")
    logger.info(f"  - Cache: {RESPONSE_CACHE_SIZE} entradas, TTL {CACHE_TTL_SECONDS}s")
    logger.info(f"  - Rate limit: {RATE_LIMIT_MAX_REQUESTS} req/{RATE_LIMIT_WINDOW}s")
    logger.info(f"  - Biometric context: ENABLED")
    logger.info(f"  - Altitude extraction: ENABLED")
    logger.info(f"  - Cadence extraction: ENABLED")
    logger.info(f"  - Cargo question: ENABLED")
    
    print(f"🤖 Bot v{BOT_VERSION} ativo")
    
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == '__main__':
    main()
