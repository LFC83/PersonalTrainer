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
# CONFIGURATION & CONSTANTS (v3.10.0)
# ==========================================
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
logger.info("Gemini API configurada")

# Bot Configuration
BOT_VERSION = "3.10.0"
BOT_VERSION_DESC = "CRITICAL FIX: Consolidated JSON as List + Cycling Type Selector + Sync Feedback Fixed"
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
GEMINI_TIMEOUT_SECONDS = 45

# Context Management
CONTEXT_TIMEOUT_MINUTES = 15
MAX_CONTEXT_HISTORY = 3

# Disk Space
MIN_DISK_SPACE_MB = 10

# Cycling Types (v3.10.0)
CYCLING_TYPES = ["MTB", "Estrada", "Spinning", "Cidade"]

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
# SYSTEM PROMPT (v3.10.0)
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

### CRITÉRIOS BIOMÉTRICOS RIGOROSOS (v3.10.0 - PRIORIDADE ABSOLUTA):
**REGRA FUNDAMENTAL:** Quando os dados biométricos (HRV baixa/RHR elevada) indicarem fadiga, mas o feedback subjetivo do utilizador for positivo (ex: "sinto-me bem"), DEVES DAR PRIORIDADE ABSOLUTA AOS DADOS BIOLÓGICOS e alertar para o risco de fadiga mascarada.

1. HRV menor que 95% da média = APENAS RECUPERAÇÃO ATIVA.
2. RHR maior que mais 2% da média = FADIGA DETETADA (Reduzir volume em 50%).
3. PONTUAÇÃO DE SONO menor que 75 mais Sensação Negativa = ZERO INTENSIDADE.
4. **FADIGA MASCARADA (v3.10.0):** Se HRV está baixa OU RHR está elevada, mas o atleta reporta sentir-se bem:
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
# DATA MODELS (v3.10.0)
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
        """Resumo breve para UI"""
        parts = []
        if self.date:
            parts.append(self.date)
        parts.append(self.sport)
        if self.duration_min:
            parts.append(f"{self.duration_min:.0f}min")
        if self.distance_km:
            parts.append(f"{self.distance_km:.1f}km")
        return " | ".join(parts)
    
    def to_detailed_summary(self) -> str:
        """Resumo detalhado para análise"""
        lines = [f"📅 {self.date or 'N/A'} | {self.sport}"]
        if self.duration_min:
            lines.append(f"⏱️ Duração: {self.duration_min:.0f}min")
        if self.distance_km:
            speed = (self.distance_km / self.duration_min * 60) if self.duration_min > 0 else 0
            lines.append(f"📏 Distância: {self.distance_km:.1f}km (Vel: {speed:.1f}km/h)")
        if self.avg_hr:
            lines.append(f"💓 FC Média: {self.avg_hr}bpm")
        if self.calories:
            lines.append(f"🔥 Calorias: {self.calories}kcal")
        if self.elevation_gain:
            lines.append(f"⛰️ Ganho Alt: {self.elevation_gain:.0f}m")
        if self.avg_cadence:
            lines.append(f"🔄 Cadência: {self.avg_cadence}rpm")
        if self.intensity:
            lines.append(f"💪 Intensidade: {self.intensity}")
        if self.load:
            lines.append(f"📊 Carga: {self.load:.1f}")
        return "\n".join(lines)

# ==========================================
# RELIABILITY INFRASTRUCTURE (v3.6+)
# ==========================================
class CircuitBreaker:
    """Circuit breaker para prevenir cascata de falhas"""
    def __init__(self):
        self.failure_count = 0
        self.last_failure_time = None
        self.state = 'closed'
    
    def record_success(self):
        """Registra sucesso"""
        self.failure_count = 0
        self.state = 'closed'
    
    def record_failure(self):
        """Registra falha"""
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.failure_count >= CIRCUIT_BREAKER_THRESHOLD:
            self.state = 'open'
            logger.warning(f"Circuit breaker OPEN após {self.failure_count} falhas")
    
    def can_proceed(self) -> bool:
        """Verifica se pode prosseguir"""
        if self.state == 'closed':
            return True
        
        # Tenta recuperar após timeout
        if time.time() - self.last_failure_time > CIRCUIT_BREAKER_TIMEOUT:
            logger.info("Circuit breaker tentando recuperar...")
            self.state = 'half-open'
            self.failure_count = 0
            return True
        
        return False

class RateLimiter:
    """Rate limiter por user"""
    def __init__(self):
        self.requests = defaultdict(list)
    
    def check_limit(self, user_id: int) -> bool:
        """Verifica se user excedeu rate limit"""
        now = time.time()
        
        # Remove requisições antigas
        self.requests[user_id] = [
            t for t in self.requests[user_id] 
            if now - t < RATE_LIMIT_WINDOW
        ]
        
        # Verifica limite
        if len(self.requests[user_id]) >= RATE_LIMIT_MAX_REQUESTS:
            return False
        
        # Registra nova requisição
        self.requests[user_id].append(now)
        return True

class ResponseCache:
    """Cache simples com TTL"""
    def __init__(self):
        self.cache = {}
        self.max_size = RESPONSE_CACHE_SIZE
    
    def _make_key(self, prompt: str, user_id: int) -> str:
        """Gera chave de cache"""
        content = f"{user_id}:{prompt}"
        return hashlib.md5(content.encode()).hexdigest()
    
    def get(self, prompt: str, user_id: int) -> Optional[Tuple[str, float]]:
        """Obtém do cache se válido"""
        key = self._make_key(prompt, user_id)
        
        if key not in self.cache:
            return None
        
        response, timestamp = self.cache[key]
        
        # Verifica TTL
        if time.time() - timestamp > CACHE_TTL_SECONDS:
            del self.cache[key]
            return None
        
        return response, timestamp
    
    def set(self, prompt: str, user_id: int, response: str):
        """Salva no cache"""
        # Limpa cache se cheio
        if len(self.cache) >= self.max_size:
            oldest_key = min(self.cache.keys(), key=lambda k: self.cache[k][1])
            del self.cache[oldest_key]
        
        key = self._make_key(prompt, user_id)
        self.cache[key] = (response, time.time())

class HealthCheckState:
    """Estado para health checks"""
    def __init__(self):
        self.gemini_latencies = []
        self.last_gemini_call = None
        self.last_success = None
        self.last_error = None
    
    def record_gemini_latency(self, latency: float):
        """Registra latência do Gemini"""
        self.gemini_latencies.append(latency)
        if len(self.gemini_latencies) > GEMINI_LATENCY_HISTORY_SIZE:
            self.gemini_latencies.pop(0)
        self.last_gemini_call = time.time()
    
    def get_avg_latency(self) -> Optional[float]:
        """Retorna latência média"""
        if not self.gemini_latencies:
            return None
        return mean(self.gemini_latencies)

class SessionState:
    """
    v3.9.0: Gerencia estado de sessão do usuário
    Para tracking de flows multi-step (ex: /status aguardando feeling)
    """
    def __init__(self):
        self.states = {}
    
    def set_user_state(self, user_id: int, state: str):
        """Define estado do usuário"""
        self.states[user_id] = {
            'state': state,
            'timestamp': time.time()
        }
        logger.debug(f"User {user_id} state: {state}")
    
    def get_user_state(self, user_id: int) -> Optional[str]:
        """Obtém estado do usuário"""
        if user_id not in self.states:
            return None
        
        data = self.states[user_id]
        
        # Expira após timeout
        if time.time() - data['timestamp'] > CONTEXT_TIMEOUT_MINUTES * 60:
            del self.states[user_id]
            return None
        
        return data['state']
    
    def clear_user_state(self, user_id: int):
        """Limpa estado do usuário"""
        if user_id in self.states:
            del self.states[user_id]
            logger.debug(f"User {user_id} state cleared")

# Global instances
circuit_breaker = CircuitBreaker()
rate_limiter = RateLimiter()
response_cache = ResponseCache()
health_state = HealthCheckState()
session_state = SessionState()

# ==========================================
# FILESYSTEM OPERATIONS (v3.10.0 - CRITICAL FIX)
# ==========================================
def ensure_data_dir():
    """Garante que DATA_DIR existe"""
    os.makedirs(DATA_DIR, exist_ok=True)

def load_json_safe(filepath: str, default_value=None):
    """
    v3.10.0: NOVO - Carrega JSON com robustez e logging de tipo
    """
    try:
        if not os.path.exists(filepath):
            logger.debug(f"Ficheiro não existe: {filepath}")
            return default_value
        
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Log do tipo carregado para debug
        data_type = type(data).__name__
        logger.debug(f"✅ {filepath} carregado como {data_type}")
        
        return data
    except json.JSONDecodeError as e:
        logger.error(f"❌ JSON inválido em {filepath}: {e}")
        return default_value
    except Exception as e:
        logger.error(f"❌ Erro ao carregar {filepath}: {e}")
        return default_value

def load_garmin_data() -> Optional[Dict]:
    """Carrega dados do Garmin (garmin_dump.json)"""
    ensure_data_dir()
    path = os.path.join(DATA_DIR, 'garmin_dump.json')
    return load_json_safe(path, None)

def load_garmin_consolidated():
    """
    v3.10.0: CRITICAL FIX - Carrega dados consolidados
    IMPORTANTE: Este ficheiro pode ser LISTA ou DICT!
    """
    ensure_data_dir()
    path = os.path.join(DATA_DIR, 'garmin_data_consolidated.json')
    data = load_json_safe(path, None)
    
    if data is not None:
        if isinstance(data, list):
            logger.debug(f"✅ Consolidated é LISTA com {len(data)} itens")
        elif isinstance(data, dict):
            logger.debug(f"✅ Consolidated é DICT com {len(data)} chaves")
        else:
            logger.warning(f"⚠️ Consolidated tem tipo inesperado: {type(data)}")
    
    return data

def load_activities_index() -> Dict:
    """
    Carrega índice de atividades
    v3.8.0: BLINDAGEM - Converte list para dict se necessário
    """
    ensure_data_dir()
    path = os.path.join(DATA_DIR, 'activities.json')
    data = load_json_safe(path, {})
    
    # v3.8.0: BLINDAGEM CRÍTICA - Se for lista, converte para dict
    if isinstance(data, list):
        logger.warning(f"⚠️ activities.json é LISTA ({len(data)} itens) - CONVERTENDO para DICT")
        converted = {}
        for i, item in enumerate(data):
            if isinstance(item, dict) and 'activityId' in item:
                converted[str(item['activityId'])] = item
            else:
                logger.warning(f"  Item {i} inválido, ignorando")
        logger.info(f"✅ Convertidos {len(converted)} atividades para dict")
        
        # Salva versão corrigida
        try:
            save_activities_index(converted)
            logger.info("✅ activities.json corrigido e salvo")
        except Exception as e:
            logger.error(f"❌ Erro ao salvar correção: {e}")
        
        return converted
    
    if not isinstance(data, dict):
        logger.error(f"❌ activities.json tem tipo inválido: {type(data)}")
        return {}
    
    return data

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
        # Cleanup do temp se existir
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise FileOperationError(f"Erro ao salvar: {e}")

def check_disk_space() -> Tuple[bool, str]:
    """Verifica espaço em disco disponível"""
    try:
        stat = os.statvfs(DATA_DIR)
        free_mb = (stat.f_bavail * stat.f_frsize) / (1024 * 1024)
        
        if free_mb < MIN_DISK_SPACE_MB:
            return False, f"Espaço baixo: {free_mb:.1f}MB"
        
        return True, f"Espaço OK: {free_mb:.1f}MB"
    except Exception as e:
        logger.error(f"Erro ao verificar disco: {e}")
        return True, "Não verificado"

# ==========================================
# SYNC/IMPORT FLAGS (v3.8.0)
# ==========================================
def create_sync_request() -> bool:
    """Cria flag de sync"""
    ensure_data_dir()
    try:
        flag_path = os.path.join(DATA_DIR, 'sync_request.flag')
        with open(flag_path, 'w') as f:
            f.write(str(int(time.time())))
        logger.info("✅ sync_request.flag criado")
        return True
    except Exception as e:
        logger.error(f"Erro ao criar sync flag: {e}")
        return False

def create_import_request(days: int = 30) -> bool:
    """Cria flag de import"""
    ensure_data_dir()
    try:
        flag_path = os.path.join(DATA_DIR, 'import_request.flag')
        with open(flag_path, 'w') as f:
            f.write(f"{int(time.time())}|{days}")
        logger.info(f"✅ import_request.flag criado (days={days})")
        return True
    except Exception as e:
        logger.error(f"Erro ao criar import flag: {e}")
        return False

def check_flag_exists(flag_name: str) -> bool:
    """Verifica se flag existe"""
    flag_path = os.path.join(DATA_DIR, f'{flag_name}.flag')
    return os.path.exists(flag_path)

def cleanup_old_flags() -> Tuple[int, List[str]]:
    """
    Remove flags antigas
    v3.8.0: RESILIENTE - Não falha se flags não existirem
    """
    ensure_data_dir()
    cleaned = 0
    messages = []
    
    try:
        for filename in os.listdir(DATA_DIR):
            if filename.endswith('.flag'):
                flag_path = os.path.join(DATA_DIR, filename)
                
                try:
                    # Verifica idade
                    mtime = os.path.getmtime(flag_path)
                    age = time.time() - mtime
                    
                    if age > FLAG_TIMEOUT_SECONDS:
                        os.remove(flag_path)
                        cleaned += 1
                        messages.append(f"✅ Removido: {filename} ({age/60:.0f}min)")
                except Exception as e:
                    messages.append(f"⚠️ Erro em {filename}: {str(e)[:50]}")
        
        if cleaned == 0:
            messages.append("Sem flags antigas")
        
    except FileNotFoundError:
        messages.append("Diretório não existe")
    except Exception as e:
        logger.error(f"Erro no cleanup: {e}")
        messages.append(f"❌ Erro: {str(e)[:50]}")
    
    return cleaned, messages

async def wait_for_sync_completion(query_or_update, timeout_seconds: int = 60) -> bool:
    """
    v3.10.0: FIXED - Aguarda conclusão do sync/import
    Aceita CallbackQuery ou Update
    """
    start_time = time.time()
    
    # Determina o tipo e extrai message
    if hasattr(query_or_update, 'message'):
        # É CallbackQuery
        message = query_or_update.message
    else:
        # É Update
        message = query_or_update.message
    
    while time.time() - start_time < timeout_seconds:
        # Verifica se ainda há flags
        has_sync = check_flag_exists('sync_request')
        has_import = check_flag_exists('import_request')
        
        if not has_sync and not has_import:
            logger.info("✅ Sync/Import completou")
            return True
        
        # Aguarda 2s antes de verificar novamente
        await asyncio.sleep(2)
    
    logger.warning(f"⏱️ Timeout aguardando sync ({timeout_seconds}s)")
    return False

# ==========================================
# GARMIN DATA PARSING (v3.10.0 - CRITICAL FIX)
# ==========================================
def get_today_biometrics() -> Optional[BiometricDay]:
    """
    v3.10.0: CRITICAL FIX - Obtém biometria de hoje
    CORRIGIDO: Consolidated pode ser LISTA ou DICT
    """
    try:
        consolidated = load_garmin_consolidated()
        if not consolidated:
            logger.debug("Sem dados consolidados disponíveis")
            return None
        
        today_str = date.today().isoformat()
        
        # v3.10.0: CRITICAL FIX - Determina tipo e processa adequadamente
        day_data = {}
        
        if isinstance(consolidated, list):
            # Consolidated é LISTA - procura o item de hoje
            logger.debug(f"Procurando {today_str} em lista com {len(consolidated)} itens")
            day_data = next((item for item in consolidated if item.get('date') == today_str), {})
            
            if not day_data:
                logger.debug(f"Dia {today_str} não encontrado na lista")
                return None
        
        elif isinstance(consolidated, dict):
            # Consolidated é DICT - usa diretamente
            logger.debug("Consolidated é dict, usando diretamente")
            day_data = consolidated
        
        else:
            logger.error(f"Consolidated tem tipo inesperado: {type(consolidated)}")
            return None
        
        # v3.10.0: Extração com acesso seguro a campos aninhados
        # HRV: hrv -> hrvSummary -> lastNightAvg
        hrv = None
        hrv_obj = day_data.get('hrv')
        if hrv_obj and isinstance(hrv_obj, dict):
            hrv_summary = hrv_obj.get('hrvSummary')
            if hrv_summary and isinstance(hrv_summary, dict):
                hrv = hrv_summary.get('lastNightAvg')
        
        # RHR: stats -> restingHeartRate
        rhr = None
        stats_obj = day_data.get('stats')
        if stats_obj and isinstance(stats_obj, dict):
            rhr = stats_obj.get('restingHeartRate')
        
        # Passos: stats -> totalSteps
        steps = None
        if stats_obj and isinstance(stats_obj, dict):
            steps = stats_obj.get('totalSteps')
        
        # Sono: sleep -> sleepSearchFullResponse -> sleepScore -> value
        # OU dailySleepDTO -> sleepScore -> value
        sleep_score = None
        sleep_obj = day_data.get('sleep')
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
            steps=steps,
            training_load=None
        )
        
        logger.debug(f"Biometria extraída: HRV={hrv}, RHR={rhr}, Sleep={sleep_score}")
        
        return bio_day
        
    except Exception as e:
        logger.error(f"Erro em get_today_biometrics: {e}\n{traceback.format_exc()}")
        return None

def parse_garmin_history(data: Dict) -> List[BiometricDay]:
    """
    v3.10.0: CORRIGIDO - Parse dados históricos do Garmin
    Tenta primeiro o consolidado (lista), depois o dump
    """
    history = []
    
    try:
        # Tenta carregar do consolidado primeiro
        consolidated = load_garmin_consolidated()
        
        if consolidated:
            if isinstance(consolidated, list):
                # v3.10.0: Processa lista completa
                logger.debug(f"Processando lista consolidada com {len(consolidated)} dias")
                
                for day_data in consolidated:
                    day_date = day_data.get('date')
                    if not day_date:
                        continue
                    
                    # Extrai campos aninhados
                    hrv = None
                    hrv_obj = day_data.get('hrv')
                    if hrv_obj and isinstance(hrv_obj, dict):
                        hrv_summary = hrv_obj.get('hrvSummary')
                        if hrv_summary and isinstance(hrv_summary, dict):
                            hrv = hrv_summary.get('lastNightAvg')
                    
                    rhr = None
                    stats_obj = day_data.get('stats')
                    if stats_obj and isinstance(stats_obj, dict):
                        rhr = stats_obj.get('restingHeartRate')
                    
                    steps = None
                    if stats_obj and isinstance(stats_obj, dict):
                        steps = stats_obj.get('totalSteps')
                    
                    sleep_score = None
                    sleep_obj = day_data.get('sleep')
                    if sleep_obj and isinstance(sleep_obj, dict):
                        sleep_search = sleep_obj.get('sleepSearchFullResponse')
                        if sleep_search and isinstance(sleep_search, dict):
                            sleep_score_obj = sleep_search.get('sleepScore')
                            if sleep_score_obj and isinstance(sleep_score_obj, dict):
                                sleep_score = sleep_score_obj.get('value')
                        
                        if sleep_score is None:
                            daily_sleep = sleep_obj.get('dailySleepDTO')
                            if daily_sleep and isinstance(daily_sleep, dict):
                                sleep_score_obj = daily_sleep.get('sleepScore')
                                if sleep_score_obj and isinstance(sleep_score_obj, dict):
                                    sleep_score = sleep_score_obj.get('value')
                    
                    bio_day = BiometricDay(
                        date=day_date,
                        hrv=hrv,
                        rhr=rhr,
                        sleep=sleep_score,
                        steps=steps,
                        training_load=None
                    )
                    
                    if not bio_day.is_empty():
                        history.append(bio_day)
            
            elif isinstance(consolidated, dict):
                # Se for dict, adiciona só hoje
                today_bio = get_today_biometrics()
                if today_bio and not today_bio.is_empty():
                    history.append(today_bio)
        
        # Adiciona dados do dump histórico (se existirem)
        daily_data = data.get('dailySummaries', [])
        
        for day in daily_data:
            calendar_date = day.get('calendarDate')
            if not calendar_date:
                continue
            
            # Evita duplicar se já temos este dia
            if any(h.date == calendar_date for h in history):
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
                history.append(bio_day)
        
        # Ordena por data (mais recente primeiro)
        history.sort(key=lambda x: x.date, reverse=True)
        
        logger.debug(f"Histórico parseado: {len(history)} dias com dados")
        
    except Exception as e:
        logger.error(f"Erro ao parsear histórico: {e}\n{traceback.format_exc()}")
    
    return history

def get_recent_biometrics(days: int = 7) -> List[BiometricDay]:
    """
    v3.10.0: CORRIGIDO - Obtém biometria recente
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
    
    hrv_values = [d.hrv for d in valid_days if d.hrv is not None]
    rhr_values = [d.rhr for d in valid_days if d.rhr is not None]
    
    baseline = {}
    
    if hrv_values:
        baseline['hrv_avg'] = mean(hrv_values)
        baseline['hrv_min'] = min(hrv_values)
        baseline['hrv_max'] = max(hrv_values)
    
    if rhr_values:
        baseline['rhr_avg'] = mean(rhr_values)
        baseline['rhr_min'] = min(rhr_values)
        baseline['rhr_max'] = max(rhr_values)
    
    return baseline

def format_biometric_context(history: List[BiometricDay], baseline: Dict[str, float]) -> str:
    """
    v3.10.0: MELHORADO - Formata contexto biométrico com evolução
    """
    if not history:
        return "### BIOMETRIA:\nSem dados disponíveis"
    
    lines = ["### BIOMETRIA:"]
    
    # Baseline
    if baseline:
        lines.append("\n**BASELINE (7 dias):**")
        if 'hrv_avg' in baseline:
            lines.append(f"HRV: {baseline['hrv_avg']:.1f} (min: {baseline['hrv_min']:.1f}, max: {baseline['hrv_max']:.1f})")
        if 'rhr_avg' in baseline:
            lines.append(f"RHR: {baseline['rhr_avg']:.0f}bpm (min: {baseline['rhr_min']:.0f}, max: {baseline['rhr_max']:.0f})")
    
    # v3.10.0: EVOLUÇÃO dos últimos 7 dias
    valid_days = [d for d in history if d.is_valid()][:7]
    
    if valid_days:
        lines.append("\n**EVOLUÇÃO (mais recente → mais antigo):**")
        
        # HRV evolution
        hrv_values = [d.hrv for d in valid_days if d.hrv is not None]
        if hrv_values:
            hrv_str = " -> ".join([f"{v:.0f}" for v in hrv_values])
            lines.append(f"HRV: {hrv_str}")
        
        # RHR evolution
        rhr_values = [d.rhr for d in valid_days if d.rhr is not None]
        if rhr_values:
            rhr_str = " -> ".join([f"{v:.0f}" for v in rhr_values])
            lines.append(f"RHR: {rhr_str}")
    
    # Dados de hoje (detalhado)
    today = history[0] if history else None
    if today:
        lines.append(f"\n**HOJE ({today.date}):**")
        if today.hrv is not None:
            deviation = ""
            if 'hrv_avg' in baseline:
                pct = ((today.hrv - baseline['hrv_avg']) / baseline['hrv_avg']) * 100
                deviation = f" ({pct:+.1f}% vs média)"
            lines.append(f"HRV: {today.hrv:.1f}{deviation}")
        
        if today.rhr is not None:
            deviation = ""
            if 'rhr_avg' in baseline:
                diff = today.rhr - baseline['rhr_avg']
                deviation = f" ({diff:+.0f}bpm vs média)"
            lines.append(f"RHR: {today.rhr}{deviation}")
        
        if today.sleep is not None:
            lines.append(f"Sono: {today.sleep}/100")
        
        if today.steps is not None:
            lines.append(f"Passos: {today.steps:,}")
    
    return "\n".join(lines)

def parse_activity_from_garmin(activity_raw: Dict) -> Optional[FormattedActivity]:
    """
    v3.8.0: CORRIGIDO - Parse de atividade do Garmin com acesso seguro
    """
    try:
        # Extração segura de campos
        activity_id = activity_raw.get('activityId')
        if not activity_id:
            return None
        
        # Data
        start_time_local = activity_raw.get('startTimeLocal')
        activity_date = None
        if start_time_local:
            try:
                dt = datetime.fromisoformat(start_time_local.replace('Z', '+00:00'))
                activity_date = dt.strftime('%Y-%m-%d')
            except:
                pass
        
        # Tipo de atividade (acesso seguro a campo aninhado)
        activity_type = activity_raw.get('activityType', {})
        sport = 'Desconhecido'
        
        if isinstance(activity_type, dict):
            sport = activity_type.get('typeKey', 'unknown')
        elif isinstance(activity_type, str):
            sport = activity_type
        
        # Duração (segundos para minutos)
        duration_sec = activity_raw.get('duration')
        duration_min = (duration_sec / 60.0) if duration_sec else 0
        
        # Distância (metros para km)
        distance_m = activity_raw.get('distance')
        distance_km = (distance_m / 1000.0) if distance_m else None
        
        # Métricas
        avg_hr = activity_raw.get('averageHR')
        calories = activity_raw.get('calories')
        elevation_gain = activity_raw.get('elevationGain')
        
        # Cadência
        avg_cadence = activity_raw.get('averageBikingCadenceInRevPerMinute')
        max_cadence = activity_raw.get('maxBikingCadenceInRevPerMinute')
        
        # Cria FormattedActivity
        formatted = FormattedActivity(
            date=activity_date,
            sport=sport,
            duration_min=duration_min,
            distance_km=distance_km,
            avg_hr=avg_hr,
            calories=calories,
            elevation_gain=elevation_gain,
            avg_cadence=avg_cadence,
            max_cadence=max_cadence,
            raw=activity_raw
        )
        
        return formatted
        
    except Exception as e:
        logger.error(f"Erro ao parsear atividade: {e}")
        return None

def get_all_formatted_activities() -> List[FormattedActivity]:
    """Obtém todas as atividades formatadas"""
    activities_index = load_activities_index()
    
    if not activities_index:
        return []
    
    formatted = []
    
    for activity_id, activity_data in activities_index.items():
        parsed = parse_activity_from_garmin(activity_data)
        if parsed:
            formatted.append(parsed)
    
    # Ordena por data (mais recente primeiro)
    formatted.sort(key=lambda x: x.date or '0000-00-00', reverse=True)
    
    return formatted

# ==========================================
# DATA INTEGRITY (v3.8.0)
# ==========================================
def check_activities_integrity() -> Tuple[bool, str]:
    """
    v3.8.0: Verifica integridade do activities.json
    """
    activities = load_activities_index()
    
    if not isinstance(activities, dict):
        return False, f"Tipo inválido: {type(activities)}"
    
    if not activities:
        return True, "Vazio mas válido"
    
    # Verifica estrutura
    invalid_count = 0
    for activity_id, data in activities.items():
        if not isinstance(data, dict):
            invalid_count += 1
            continue
        
        if 'activityId' not in data:
            invalid_count += 1
    
    if invalid_count > 0:
        return False, f"{invalid_count}/{len(activities)} entradas inválidas"
    
    return True, f"{len(activities)} atividades OK"

def reorganize_activities() -> Tuple[int, int, List[str]]:
    """
    v3.8.0: Reorganiza activities.json
    Remove duplicados e limita tamanho
    """
    activities = load_activities_index()
    messages = []
    
    original_count = len(activities)
    
    # Remove duplicados (mantém o mais recente)
    seen_ids = set()
    cleaned = {}
    duplicates = 0
    
    for activity_id, data in activities.items():
        if activity_id in seen_ids:
            duplicates += 1
            continue
        
        seen_ids.add(activity_id)
        cleaned[activity_id] = data
    
    # Limita tamanho
    if len(cleaned) > MAX_ACTIVITIES_STORED:
        # Ordena por data
        sorted_items = sorted(
            cleaned.items(),
            key=lambda x: x[1].get('startTimeLocal', ''),
            reverse=True
        )
        
        cleaned = dict(sorted_items[:MAX_ACTIVITIES_STORED])
        messages.append(f"Limitado a {MAX_ACTIVITIES_STORED} mais recentes")
    
    # Salva se houve mudanças
    if len(cleaned) != original_count:
        save_activities_index(cleaned)
        messages.append(f"Reorganizado: {original_count} → {len(cleaned)}")
    else:
        messages.append("Sem mudanças necessárias")
    
    if duplicates > 0:
        messages.append(f"Removidos {duplicates} duplicados")
    
    return duplicates, len(cleaned), messages

def check_and_enrich_activities():
    """
    v3.8.0: Verifica e enriquece atividades se necessário
    """
    activities = load_activities_index()
    
    if not activities:
        logger.debug("Sem atividades para enriquecer")
        return
    
    enriched_count = 0
    
    for activity_id, data in activities.items():
        # Verifica se precisa enriquecer
        needs_enrichment = False
        
        # Exemplo: adicionar campo de versão se não existir
        if '_enriched' not in data:
            data['_enriched'] = True
            data['_enriched_at'] = datetime.now().isoformat()
            needs_enrichment = True
        
        if needs_enrichment:
            enriched_count += 1
    
    if enriched_count > 0:
        save_activities_index(activities)
        logger.info(f"✅ {enriched_count} atividades enriquecidas")

# ==========================================
# CONTEXT MANAGEMENT (v3.7.0)
# ==========================================
def get_user_context_path(user_id: int) -> str:
    """Retorna path do contexto do usuário"""
    ensure_data_dir()
    return os.path.join(DATA_DIR, f'context_{user_id}.json')

def load_context_from_disk(user_id: int) -> Dict:
    """Carrega contexto do disco"""
    path = get_user_context_path(user_id)
    return load_json_safe(path, {'history': [], 'last_update': None})

def save_context_to_disk(user_id: int, context_data: Dict):
    """Salva contexto no disco"""
    path = get_user_context_path(user_id)
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(context_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Erro ao salvar contexto: {e}")

def add_to_context_history(user_id: int, command: str, prompt: str, response: str):
    """Adiciona entrada ao histórico de contexto"""
    context_data = load_context_from_disk(user_id)
    
    # Cria entrada
    entry = {
        'command': command,
        'timestamp': time.time(),
        'prompt': prompt[:1000],  # Limita tamanho
        'response': response[:2000],
        'response_preview': response[:100]
    }
    
    # Adiciona ao histórico
    history = context_data.get('history', [])
    history.insert(0, entry)
    
    # Limita tamanho do histórico
    if len(history) > MAX_CONTEXT_HISTORY:
        history = history[:MAX_CONTEXT_HISTORY]
    
    # Salva
    context_data['history'] = history
    context_data['last_update'] = time.time()
    save_context_to_disk(user_id, context_data)

def get_context_for_followup(user_id: int) -> str:
    """Obtém contexto formatado para followup"""
    context_data = load_context_from_disk(user_id)
    
    if not context_data or not context_data.get('history'):
        return "Sem contexto prévio."
    
    history = context_data['history']
    
    lines = ["### CONTEXTO DAS ANÁLISES ANTERIORES:"]
    
    for i, entry in enumerate(history, 1):
        timestamp = datetime.fromtimestamp(entry['timestamp']).strftime('%d/%m %H:%M')
        lines.append(f"\n{i}. {entry['command']} ({timestamp}):")
        lines.append(f"Resposta: {entry['response'][:500]}...")
    
    return "\n".join(lines)

def clear_user_context(user_id: int):
    """Limpa contexto do usuário"""
    path = get_user_context_path(user_id)
    try:
        if os.path.exists(path):
            os.remove(path)
            logger.info(f"Contexto do user {user_id} removido")
    except Exception as e:
        logger.error(f"Erro ao limpar contexto: {e}")

# ==========================================
# GEMINI API (v3.7.0+ com retry & circuit breaker)
# ==========================================
async def call_gemini_with_timeout(prompt: str, timeout_seconds: int) -> str:
    """
    Chama Gemini com timeout
    v3.7.0: Usando asyncio para timeout real
    """
    start_time = time.time()
    
    try:
        # Cria task assíncrona
        response = await asyncio.wait_for(
            asyncio.to_thread(model.generate_content, prompt),
            timeout=timeout_seconds
        )
        
        latency = time.time() - start_time
        health_state.record_gemini_latency(latency)
        logger.info(f"✅ Gemini respondeu em {latency:.2f}s")
        
        return response.text
        
    except asyncio.TimeoutError:
        logger.error(f"❌ Gemini timeout após {timeout_seconds}s")
        raise GeminiTimeoutError(f"Timeout após {timeout_seconds}s")
    except Exception as e:
        logger.error(f"❌ Erro no Gemini: {e}")
        raise

async def call_gemini_with_retry(prompt: str, user_id: int) -> str:
    """
    v3.7.0: Chama Gemini com retry, circuit breaker e cache
    """
    # Circuit breaker check
    if not circuit_breaker.can_proceed():
        logger.warning("Circuit breaker OPEN")
        raise CircuitBreakerOpen("Serviço temporariamente indisponível")
    
    # Rate limit check
    if not rate_limiter.check_limit(user_id):
        logger.warning(f"Rate limit exceeded para user {user_id}")
        raise RateLimitExceeded("Rate limit excedido")
    
    # Cache check
    cached = response_cache.get(prompt, user_id)
    if cached:
        response, timestamp = cached
        age = time.time() - timestamp
        logger.info(f"✅ Cache hit (age: {age:.0f}s)")
        return response
    
    # Valida tamanho do prompt
    if len(prompt) > GEMINI_MAX_PROMPT_LENGTH:
        logger.error(f"Prompt muito grande: {len(prompt)} chars")
        raise PromptTooLargeError(f"Prompt excede {GEMINI_MAX_PROMPT_LENGTH} chars")
    
    # Retry loop
    last_error = None
    
    for attempt in range(MAX_RETRIES):
        try:
            logger.info(f"Tentativa {attempt + 1}/{MAX_RETRIES}")
            
            response = await call_gemini_with_timeout(prompt, GEMINI_TIMEOUT_SECONDS)
            
            # Sucesso
            circuit_breaker.record_success()
            health_state.last_success = time.time()
            
            # Salva no cache
            response_cache.set(prompt, user_id, response)
            
            return response
            
        except GeminiTimeoutError as e:
            last_error = e
            circuit_breaker.record_failure()
            health_state.last_error = str(e)
            
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_DELAYS[attempt]
                logger.warning(f"Retry em {delay}s...")
                await asyncio.sleep(delay)
            else:
                logger.error("Todas as tentativas falharam")
                raise
        
        except Exception as e:
            last_error = e
            circuit_breaker.record_failure()
            health_state.last_error = str(e)
            logger.error(f"Erro na tentativa {attempt + 1}: {e}")
            
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_DELAYS[attempt]
                await asyncio.sleep(delay)
            else:
                raise
    
    # Fallback (não deve chegar aqui)
    if last_error:
        raise last_error
    raise Exception("Erro desconhecido no retry loop")

# ==========================================
# TELEGRAM HANDLERS (v3.10.0)
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
    v3.10.0: Handler /status
    Pergunta como o utilizador se sente ANTES de chamar Gemini
    """
    user_id = update.effective_user.id
    
    try:
        # Pergunta feeling primeiro
        session_state.set_user_state(user_id, 'waiting_feeling')
        
        await update.message.reply_text(
            "📊 Como te sentes hoje?\n\n"
            "Responde com um número de 0 a 10:\n"
            "0 = Exausto\n"
            "5 = Normal\n"
            "10 = Energizado"
        )
        
    except Exception as e:
        logger.error(f"Erro em /status: {e}")
        session_state.clear_user_state(user_id)
        await update.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def process_status_with_feeling(update: Update, feeling: int):
    """
    v3.10.0: Processa /status após receber o feeling
    """
    user_id = update.effective_user.id
    
    try:
        await update.message.reply_text("🔍 A analisar readiness...")
        
        # Biometria obrigatória
        history = get_recent_biometrics(7)
        baseline = calculate_biometric_baseline(history)
        bio_context = format_biometric_context(history, baseline)
        
        # Atividades recentes
        activities = get_all_formatted_activities()
        
        if not activities:
            await update.message.reply_text(
                "📭 Sem atividades.\n\n"
                "Usa /sync ou /import primeiro."
            )
            session_state.clear_user_state(user_id)
            return
        
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
        
        # Atividades
        activities = get_all_formatted_activities()
        
        if not activities:
            await update.message.reply_text(
                "📭 Sem atividades para analisar.\n\n"
                "Usa /sync ou /import."
            )
            return
        
        recent = activities[:MAX_ACTIVITIES_IN_ANALYSIS]
        
        prompt = f"""
{bio_context}

### ATIVIDADES RECENTES:

"""
        
        for act in recent:
            prompt += f"{act.to_detailed_summary()}\n\n"
        
        prompt += """
### TAREFA:
Analisa a aderência ao plano de treino semanal.
Verifica distribuição de volume, intensidade e recuperação.
Se HRV/RHR indicarem sobrecarga, ALERTA e prescreve ajustes.
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
        logger.error(f"Erro em /analyze: {e}")
        await update.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def analyze_activity_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    v3.10.0: Handler /analyze_activity
    Mostra lista de atividades para escolher
    """
    try:
        activities = get_all_formatted_activities()
        
        if not activities:
            await update.message.reply_text(
                "📭 Sem atividades.\n\n"
                "Usa /sync ou /import."
            )
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
    v3.10.0: CRITICAL FIX - Callback para análise de atividade
    Pergunta TIPO DE CICLISMO se o activityType for genérico
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
        
        # v3.10.0: CRITICAL FIX - Verifica se é ciclismo E se o tipo é genérico
        sport_lower = activity.sport.lower()
        is_cycling = any(x in sport_lower for x in ['cicl', 'mtb', 'spin', 'bike', 'cycling', 'road_biking'])
        
        if is_cycling:
            # Verifica se o tipo já é específico
            is_generic_type = sport_lower in ['cycling', 'other', 'bike', 'ciclismo']
            
            if is_generic_type:
                # v3.10.0: NOVO - Pergunta o tipo específico
                keyboard = [
                    [InlineKeyboardButton("🚵 MTB", callback_data=f"cycle_type_mtb_{index}")],
                    [InlineKeyboardButton("🚴 Estrada", callback_data=f"cycle_type_estrada_{index}")],
                    [InlineKeyboardButton("🏋️ Spinning", callback_data=f"cycle_type_spinning_{index}")],
                    [InlineKeyboardButton("🚲 Cidade", callback_data=f"cycle_type_cidade_{index}")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(
                    f"🚴 Atividade: {activity.to_brief_summary()}\n\n"
                    "Que tipo de ciclismo foi?",
                    reply_markup=reply_markup
                )
            else:
                # Tipo já específico, pergunta sobre carga
                await ask_about_cargo(query, activity, index)
        else:
            # Não é ciclismo, analisa diretamente
            await perform_activity_analysis(query, activity, has_cargo=False, cycling_type=None)
        
    except Exception as e:
        logger.error(f"Erro em analyze_activity_callback: {e}\n{traceback.format_exc()}")
        await query.edit_message_text(f"❌ Erro: {str(e)[:100]}")

async def cycling_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    v3.10.0: NOVO - Callback para tipo de ciclismo
    """
    query = update.callback_query
    await query.answer()
    
    try:
        parts = query.data.split('_')
        cycling_type = parts[2]  # mtb, estrada, spinning, cidade
        index = int(parts[3])
        
        activities = get_all_formatted_activities()
        if index >= len(activities):
            await query.edit_message_text("❌ Atividade não encontrada")
            return
        
        activity = activities[index]
        
        # Agora pergunta sobre carga/passageiro
        await ask_about_cargo(query, activity, index, cycling_type)
        
    except Exception as e:
        logger.error(f"Erro em cycling_type_callback: {e}\n{traceback.format_exc()}")
        await query.edit_message_text(f"❌ Erro: {str(e)[:100]}")

async def ask_about_cargo(query, activity: FormattedActivity, index: int, cycling_type: str = None):
    """
    v3.10.0: NOVO - Pergunta sobre carga/passageiro
    """
    keyboard = [
        [InlineKeyboardButton("Sim (tinha carga/passageiro)", callback_data=f"cargo_yes_{index}_{cycling_type or 'none'}")],
        [InlineKeyboardButton("Não (solo)", callback_data=f"cargo_no_{index}_{cycling_type or 'none'}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"🚴 Atividade: {activity.to_brief_summary()}\n\n"
        "Levaste passageiro ou carga adicional?",
        reply_markup=reply_markup
    )

async def cargo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    v3.10.0: CORRIGIDO - Callback para resposta sobre carga em ciclismo
    """
    query = update.callback_query
    await query.answer()
    
    try:
        parts = query.data.split('_')
        has_cargo = parts[1] == 'yes'
        index = int(parts[2])
        cycling_type = parts[3] if len(parts) > 3 and parts[3] != 'none' else None
        
        activities = get_all_formatted_activities()
        if index >= len(activities):
            await query.edit_message_text("❌ Atividade não encontrada")
            return
        
        activity = activities[index]
        
        await perform_activity_analysis(query, activity, has_cargo=has_cargo, cycling_type=cycling_type)
        
    except Exception as e:
        logger.error(f"Erro em cargo_callback: {e}\n{traceback.format_exc()}")
        await query.edit_message_text(f"❌ Erro: {str(e)[:100]}")

async def perform_activity_analysis(query, activity: FormattedActivity, has_cargo: bool, cycling_type: str = None):
    """
    v3.10.0: CORRIGIDO - Executa análise de atividade individual
    """
    user_id = query.from_user.id
    
    try:
        await query.edit_message_text("🔍 Analisando atividade...")
        
        # Biometria
        history = get_recent_biometrics(7)
        baseline = calculate_biometric_baseline(history)
        bio_context = format_biometric_context(history, baseline)
        
        # Contexto da atividade
        activity_context = f"""
### ATIVIDADE ANALISADA:

{activity.to_detailed_summary()}

Tipo de ciclismo: {cycling_type.upper() if cycling_type else 'N/A'}
Tinha carga/passageiro: {'SIM' if has_cargo else 'NÃO'}
"""
        
        prompt = f"""
{bio_context}

{activity_context}

### TAREFA:
Analisa esta atividade individual em detalhe.
Se é ciclismo com carga, considera o impacto na intensidade real.
Compara com a biometria de hoje e dias anteriores.
Prescreve ajustes se necessário.
"""
        
        response_text = await call_gemini_with_retry(prompt, user_id)
        
        # Envia resposta
        if len(response_text) <= TELEGRAM_SAFE_MESSAGE_LENGTH:
            await query.message.reply_text(response_text)
        else:
            chunks = [response_text[i:i+TELEGRAM_SAFE_MESSAGE_LENGTH] 
                     for i in range(0, len(response_text), TELEGRAM_SAFE_MESSAGE_LENGTH)]
            for chunk in chunks:
                await query.message.reply_text(chunk)
        
        # Salva contexto
        add_to_context_history(user_id, 'analyze_activity', prompt, response_text)
        
    except GeminiTimeoutError:
        await query.message.reply_text(
            f"⏱️ Timeout após {GEMINI_TIMEOUT_SECONDS}s.\n"
            "Tenta novamente."
        )
    except Exception as e:
        logger.error(f"Erro em perform_activity_analysis: {e}\n{traceback.format_exc()}")
        await query.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def import_historical(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    v3.10.0: CORRIGIDO - Handler /import
    Com feedback após conclusão
    """
    try:
        await update.message.reply_text("🔄 A processar importação histórica...")
        
        if not create_import_request(days=30):
            await update.message.reply_text("❌ Erro ao criar pedido de importação")
            return
        
        # v3.10.0: FIXED - Passa update diretamente
        asyncio.create_task(send_sync_feedback(update, 'import_request'))
        
    except Exception as e:
        logger.error(f"Erro em /import: {e}")
        await update.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def sync_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    v3.10.0: Handler /sync
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
    v3.10.0: CRITICAL FIX - Callback de confirmação de sync
    """
    query = update.callback_query
    await query.answer()
    
    try:
        await query.edit_message_text("🔄 A processar sincronização...")
        
        if not create_sync_request():
            await query.message.reply_text("❌ Erro ao criar pedido de sync")
            return
        
        # v3.10.0: CRITICAL FIX - Cria Update object para compatibilidade
        # Passa query diretamente que será convertido em wait_for_sync_completion
        asyncio.create_task(send_sync_feedback(query, 'sync_request'))
        
    except Exception as e:
        logger.error(f"Erro em sync_confirmed_callback: {e}")
        await query.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def send_sync_feedback(query_or_update, flag_name: str):
    """
    v3.10.0: CRITICAL FIX - Envia feedback após sincronização
    Aceita CallbackQuery ou Update
    """
    try:
        # Aguarda até 60s
        completed = await wait_for_sync_completion(query_or_update, timeout_seconds=60)
        
        # Determina como enviar mensagem
        if hasattr(query_or_update, 'message'):
            # É CallbackQuery
            message = query_or_update.message
        else:
            # É Update
            message = query_or_update.message
        
        if not completed:
            await message.reply_text(
                "⏱️ Sincronização ainda em progresso...\n"
                "Usa /activities para ver o estado."
            )
            return
        
        # Lê as atividades
        activities = get_all_formatted_activities()
        
        if activities:
            msg = (
                f"✅ Sincronização concluída!\n"
                f"📊 {len(activities)} atividades no total encontradas.\n\n"
                f"💡 Usa /status ou /analyze"
            )
        else:
            msg = "⚠️ Sincronização completou mas sem atividades encontradas."
        
        await message.reply_text(msg)
        
    except Exception as e:
        logger.error(f"Erro no feedback de sync: {e}\n{traceback.format_exc()}")

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
            sports = defaultdict(int)
            for act in activities:
                sports[act.sport] += 1
            
            msg += "Por desporto:\n"
            for sport, count in sorted(sports.items(), key=lambda x: x[1], reverse=True):
                msg += f"- {sport}: {count}\n"
        
        await update.message.reply_text(msg)
        
    except Exception as e:
        logger.error(f"Erro em /stats: {e}")
        await update.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def debug_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler /debug - Informações de debug"""
    try:
        activities = get_all_formatted_activities()
        history = get_recent_biometrics(7)
        
        disk_ok, disk_msg = check_disk_space()
        integrity_ok, integrity_msg = check_activities_integrity()
        
        msg = f"🔧 DEBUG v{BOT_VERSION}:\n\n"
        msg += f"Atividades: {len(activities)}\n"
        msg += f"Biometria: {len(history)} dias\n"
        msg += f"Disco: {disk_msg}\n"
        msg += f"Integridade: {integrity_msg}\n\n"
        
        msg += f"Circuit Breaker: {circuit_breaker.state}\n"
        msg += f"Falhas: {circuit_breaker.failure_count}\n\n"
        
        avg_latency = health_state.get_avg_latency()
        if avg_latency:
            msg += f"Gemini latência: {avg_latency:.2f}s\n"
        
        if health_state.last_success:
            last_success = datetime.fromtimestamp(health_state.last_success).strftime('%H:%M:%S')
            msg += f"Último sucesso: {last_success}\n"
        
        if health_state.last_error:
            msg += f"Último erro: {health_state.last_error[:50]}\n"
        
        await update.message.reply_text(msg)
        
    except Exception as e:
        logger.error(f"Erro em /debug: {e}")
        await update.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    v3.10.0: Handler /health - Health check do sistema
    """
    try:
        # Atividades
        activities = get_all_formatted_activities()
        valid_activities = [a for a in activities if a.date]
        
        # Biometria
        history = get_recent_biometrics(7)
        today_bio = get_today_biometrics()
        
        # Disco
        disk_ok, disk_msg = check_disk_space()
        
        # Status geral
        status_emoji = "✅" if disk_ok and activities else "⚠️"
        
        msg = f"{status_emoji} HEALTH CHECK v{BOT_VERSION}:\n\n"
        
        msg += "📊 DADOS:\n"
        msg += f"- Atividades: {len(activities)} ({len(valid_activities)} válidas)\n"
        msg += f"- Biometria: {len(history)} dias\n"
        
        if today_bio and not today_bio.is_empty():
            msg += f"- Última biometria: HOJE\n"
            if today_bio.hrv:
                msg += f"  HRV: {today_bio.hrv:.0f}\n"
            if today_bio.rhr:
                msg += f"  RHR: {today_bio.rhr}bpm\n"
        else:
            msg += f"- Última biometria: SEM DADOS\n"
        
        msg += f"\n💾 SISTEMA:\n"
        msg += f"- Disco: {disk_msg}\n"
        msg += f"- Circuit Breaker: {circuit_breaker.state}\n"
        
        avg_latency = health_state.get_avg_latency()
        if avg_latency:
            msg += f"- Gemini latência: {avg_latency:.2f}s\n"
        
        await update.message.reply_text(msg)
        
    except Exception as e:
        logger.error(f"Erro em /health: {e}")
        await update.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler /help"""
    await update.message.reply_text(
        f"🏋️ FitnessJournal v{BOT_VERSION}\n\n"
        "COMANDOS:\n"
        "/status - Readiness + prescrição treino\n"
        "/activities - Lista atividades recentes\n"
        "/analyze - Análise de aderência ao plano\n"
        "/analyze_activity - Análise de atividade individual\n"
        "/sync - Sincroniza dados do Garmin\n"
        "/import - Importa histórico (30 dias)\n"
        "/cleanup - Limpa dados antigos\n"
        "/history - Análises anteriores\n"
        "/clear_context - Limpa contexto\n"
        "/stats - Estatísticas\n"
        "/debug - Informações de debug\n"
        "/health - Health check do sistema\n"
        "/help - Esta ajuda\n\n"
        "🆕 v3.10.0:\n"
        "• CRITICAL FIX: Consolidated JSON como lista\n"
        "• Tipo de ciclismo perguntado (MTB/Estrada/Spinning/Cidade)\n"
        "• Feedback de sync corrigido\n"
        "• Evolução HRV mostrada em /status\n"
        "• load_json_safe com logging de tipo\n"
        "• wait_for_sync_completion aceita Query/Update"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    v3.10.0: Handler para mensagens de texto livre
    Verifica se está aguardando feeling para /status
    """
    user_id = update.effective_user.id
    message_text = update.message.text
    
    try:
        # Verifica se está aguardando feeling
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

# ==========================================
# MAIN (v3.10.0)
# ==========================================
def main():
    """Entry point v3.10.0"""
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
    app.add_handler(CallbackQueryHandler(cycling_type_callback, pattern=r'^cycle_type_(mtb|estrada|spinning|cidade)_\d+$'))
    app.add_handler(CallbackQueryHandler(cargo_callback, pattern=r'^cargo_(yes|no)_\d+'))
    
    # Message Handler (texto livre)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Handler para comandos não reconhecidos
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))
    
    logger.info("✅ Bot v3.10.0 iniciado com:")
    logger.info(f"  - CRITICAL FIX: Consolidated JSON como lista suportada")
    logger.info(f"  - CRITICAL FIX: Tipo de ciclismo perguntado")
    logger.info(f"  - CRITICAL FIX: Sync feedback corrigido")
    logger.info(f"  - load_json_safe com logging de tipo")
    logger.info(f"  - Evolução HRV em /status")
    logger.info(f"  - Timeout Gemini: {GEMINI_TIMEOUT_SECONDS}s")
    logger.info(f"  - Retry delays: {RETRY_DELAYS}")
    logger.info(f"  - Circuit breaker: {CIRCUIT_BREAKER_THRESHOLD} falhas threshold")
    logger.info(f"  - Cache: {RESPONSE_CACHE_SIZE} entradas, TTL {CACHE_TTL_SECONDS}s")
    logger.info(f"  - Rate limit: {RATE_LIMIT_MAX_REQUESTS} req/{RATE_LIMIT_WINDOW}s")
    
    print(f"🤖 Bot v{BOT_VERSION} ativo")
    
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == '__main__':
    main()
