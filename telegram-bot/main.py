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
# CONFIGURATION & CONSTANTS (v3.12.0)
# ==========================================
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
logger.info("Gemini API configurada")

# Bot Configuration
BOT_VERSION = "3.12.0"
BOT_VERSION_DESC = "Separação estrita de contextos IA + Cabeçalho técnico /analyze_activity + /status invertido + Fallback biométrico + Logs JSON"
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
DATA_DIR = '/data'

# Equipment — lista exclusiva para sugestões de treino
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

# Cycling Types
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
# SYSTEM PROMPT (v3.12.0)
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

### CRITÉRIOS BIOMÉTRICOS RIGOROSOS (PRIORIDADE ABSOLUTA):
**REGRA FUNDAMENTAL:** Quando os dados biométricos (HRV baixa/RHR elevada) indicarem fadiga, mas o feedback subjetivo do utilizador for positivo (ex: "sinto-me bem"), DEVES DAR PRIORIDADE ABSOLUTA AOS DADOS BIOLÓGICOS e alertar para o risco de fadiga mascarada.

1. HRV menor que 95% da média = APENAS RECUPERAÇÃO ATIVA.
2. RHR maior que mais 2% da média = FADIGA DETETADA (Reduzir volume em 50%).
3. PONTUAÇÃO DE SONO menor que 75 mais Sensação Negativa = ZERO INTENSIDADE.
4. **FADIGA MASCARADA:** Se HRV está baixa OU RHR está elevada, mas o atleta reporta sentir-se bem:
   - Explica a discrepância entre sensação subjetiva e realidade fisiológica
   - Alerta para o perigo de ignorar os sinais biométricos
   - Prescreve treino baseado nos dados objetivos (HRV/RHR), NÃO no sentimento

### EQUIPAMENTO DISPONÍVEL (EXCLUSIVO):
Elástico, Máquina Remo, Haltere 25kg max, Barra olímpica 45kg max, Kettlebell 12kg, Bicicleta Spinning, Banco musculação/Supino.
NUNCA sugeres equipamento fora desta lista (sem Prensa, sem máquinas comerciais).

### FORMATO DE RESPOSTA OBRIGATÓRIO PARA /status (PLANO DO DIA):
| Tipo Treino | Descrição | Séries/Duração | Intensidade | Observações |
| :--- | :--- | :--- | :--- | :--- |

**CÁLCULO DE CARGA:** [Cálculo baseado em biometria]
**PROTOCOLO APLICADO:** [Nome do protocolo]
**ANÁLISE:** [Avaliação do estado atual]
**RECOMENDAÇÕES:** [Instruções de recuperação e nutrição]

### FORMATO DE RESPOSTA OBRIGATÓRIO PARA /analyze_activity (ANÁLISE TÉCNICA):
Foca EXCLUSIVAMENTE na atividade realizada:
- Eficiência de Cadência (real vs óptima para o tipo de esforço)
- Análise de FC (zonas, deriva cardíaca, desacoplamento)
- Impacto Altimétrico (W/kg estimados, fadiga acumulada)
- Impacto Biométrico pós-sessão (HRV prevista, recuperação estimada)
PROIBIDO incluir tabela de treino futuro ou sugestões de musculação/core neste contexto.

### REGRAS DE FORMATAÇÃO:
- Usa aritmética simples: "X dividido por Y igual a Z"
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
# DATA MODELS
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

    def to_technical_header(self) -> str:
        """
        v3.12.0: Cabeçalho técnico para /analyze_activity (f-string, dados reais do objeto).
        Impresso ANTES da chamada ao Gemini.
        """
        lines = []
        date_str = self.date or "N/A"
        lines.append(f"📅 {date_str} - {self.sport}")

        dur = f"{self.duration_min:.0f}min" if self.duration_min else "N/A"
        dist = f"{self.distance_km:.1f}km" if self.distance_km else "N/A"
        lines.append(f"⏱️ Duração: {dur} | 📏 Dist: {dist}")

        hr_str = f"{self.avg_hr}bpm" if self.avg_hr else "N/A"
        cal_str = f"{self.calories}" if self.calories else "N/A"
        lines.append(f"💓 FC Média: {hr_str} | 🔥 Cal: {cal_str}")

        elev_str = f"{self.elevation_gain:.0f}m" if self.elevation_gain else "N/A"
        cad_val = self.avg_cadence or self.bike_cadence
        cad_str = f"{cad_val} RPM" if (cad_val and cad_val > 0) else None
        if cad_str:
            lines.append(f"🏔️ Altimetria: {elev_str} | ⚙️ Cadência: {cad_str}")
        else:
            lines.append(f"🏔️ Altimetria: {elev_str}")

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
        self.failure_count = 0
        self.state = 'closed'

    def record_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= CIRCUIT_BREAKER_THRESHOLD:
            self.state = 'open'
            logger.warning(f"Circuit breaker OPEN após {self.failure_count} falhas")

    def can_proceed(self) -> bool:
        if self.state == 'closed':
            return True
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
        now = time.time()
        self.requests[user_id] = [t for t in self.requests[user_id] if now - t < RATE_LIMIT_WINDOW]
        if len(self.requests[user_id]) >= RATE_LIMIT_MAX_REQUESTS:
            return False
        self.requests[user_id].append(now)
        return True

class ResponseCache:
    """Cache simples com TTL"""
    def __init__(self):
        self.cache = {}
        self.max_size = RESPONSE_CACHE_SIZE

    def _make_key(self, prompt: str, user_id: int) -> str:
        content = f"{user_id}:{prompt}"
        return hashlib.md5(content.encode()).hexdigest()

    def get(self, prompt: str, user_id: int) -> Optional[Tuple[str, float]]:
        key = self._make_key(prompt, user_id)
        if key not in self.cache:
            return None
        response, timestamp = self.cache[key]
        if time.time() - timestamp > CACHE_TTL_SECONDS:
            del self.cache[key]
            return None
        return response, timestamp

    def set(self, prompt: str, user_id: int, response: str):
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
        self.gemini_latencies.append(latency)
        if len(self.gemini_latencies) > GEMINI_LATENCY_HISTORY_SIZE:
            self.gemini_latencies.pop(0)
        self.last_gemini_call = time.time()

    def get_avg_latency(self) -> Optional[float]:
        if not self.gemini_latencies:
            return None
        return mean(self.gemini_latencies)

class SessionState:
    """Gerencia estado de sessão do usuário"""
    def __init__(self):
        self.states = {}

    def set_user_state(self, user_id: int, state: str):
        self.states[user_id] = {'state': state, 'timestamp': time.time()}
        logger.debug(f"User {user_id} state: {state}")

    def get_user_state(self, user_id: int) -> Optional[str]:
        if user_id not in self.states:
            return None
        data = self.states[user_id]
        if time.time() - data['timestamp'] > CONTEXT_TIMEOUT_MINUTES * 60:
            del self.states[user_id]
            return None
        return data['state']

    def clear_user_state(self, user_id: int):
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
# FILESYSTEM OPERATIONS
# ==========================================
def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)

def load_json_safe(filepath: str, default_value=None):
    """
    Carrega JSON com robustez e logging de tipo.
    v3.12.0: logger.info em todas as leituras de JSON.
    """
    try:
        if not os.path.exists(filepath):
            logger.debug(f"Ficheiro não existe: {filepath}")
            return default_value

        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)

        data_type = type(data).__name__
        # v3.12.0: info-level log em leituras de JSON
        if isinstance(data, (list, dict)):
            logger.info(f"Ficheiro {filepath} lido com {len(data)} itens")
        else:
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
    Carrega dados consolidados.
    IMPORTANTE: Este ficheiro pode ser LISTA ou DICT.
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
    Carrega índice de atividades.
    BLINDAGEM: Converte list para dict se necessário.
    """
    ensure_data_dir()
    path = os.path.join(DATA_DIR, 'activities.json')
    data = load_json_safe(path, {})

    if isinstance(data, list):
        logger.warning(f"⚠️ activities.json é LISTA ({len(data)} itens) - CONVERTENDO para DICT")
        converted = {}
        for i, item in enumerate(data):
            if isinstance(item, dict) and 'activityId' in item:
                converted[str(item['activityId'])] = item
            else:
                logger.warning(f"  Item {i} inválido, ignorando")
        logger.info(f"✅ Convertidos {len(converted)} atividades para dict")
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
    """Salva índice de atividades com atomic write."""
    ensure_data_dir()

    if not isinstance(activities, dict):
        logger.error(f"❌ CRÍTICO: Tentativa de salvar activities como {type(activities)}")
        raise FileOperationError(f"Activities deve ser dict, não {type(activities)}")

    path = os.path.join(DATA_DIR, 'activities.json')
    temp_path = path + '.tmp'
    try:
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(activities, f, ensure_ascii=False, indent=2)
        os.replace(temp_path, path)
        logger.debug(f"✅ activities.json salvo com {len(activities)} entradas")
    except Exception as e:
        logger.error(f"Erro ao salvar activities.json: {e}")
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
# SYNC/IMPORT FLAGS
# ==========================================
def create_sync_request() -> bool:
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
    flag_path = os.path.join(DATA_DIR, f'{flag_name}.flag')
    return os.path.exists(flag_path)

def cleanup_old_flags() -> Tuple[int, List[str]]:
    """Remove flags antigas"""
    ensure_data_dir()
    cleaned = 0
    messages = []
    try:
        for filename in os.listdir(DATA_DIR):
            if filename.endswith('.flag'):
                flag_path = os.path.join(DATA_DIR, filename)
                try:
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
    """Aguarda conclusão do sync/import. Aceita CallbackQuery ou Update."""
    start_time = time.time()
    while time.time() - start_time < timeout_seconds:
        has_sync = check_flag_exists('sync_request')
        has_import = check_flag_exists('import_request')
        if not has_sync and not has_import:
            logger.info("✅ Sync/Import completou")
            return True
        await asyncio.sleep(2)
    logger.warning(f"⏱️ Timeout aguardando sync ({timeout_seconds}s)")
    return False

# ==========================================
# GARMIN DATA PARSING (v3.12.0 — Fallback Biométrico)
# ==========================================
def _extract_biometric_from_day(day_data: Dict) -> BiometricDay:
    """
    Extrai campos biométricos de um item do consolidated.
    Centraliza a lógica de parsing para reutilização.
    """
    day_date = day_data.get('date', '')

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

    return BiometricDay(
        date=day_date,
        hrv=hrv,
        rhr=rhr,
        sleep=sleep_score,
        steps=steps,
        training_load=None
    )

def get_today_biometrics() -> Optional[BiometricDay]:
    """
    v3.12.0: Obtém biometria de hoje.
    Fallback: se não houver dados de hoje, usa o dia anterior e avisa via log.
    """
    try:
        consolidated = load_garmin_consolidated()
        if not consolidated:
            logger.debug("Sem dados consolidados disponíveis")
            return None

        today_str = date.today().isoformat()
        yesterday_str = (date.today() - timedelta(days=1)).isoformat()

        if isinstance(consolidated, list):
            logger.debug(f"Procurando {today_str} em lista com {len(consolidated)} itens")

            day_data = next((item for item in consolidated if item.get('date') == today_str), None)
            fallback_used = False

            if day_data is None:
                # v3.12.0: Fallback para dia anterior
                day_data = next((item for item in consolidated if item.get('date') == yesterday_str), None)
                if day_data:
                    fallback_used = True
                    logger.warning(f"⚠️ Sem dados biométricos para hoje ({today_str}). A usar fallback: {yesterday_str}")
                else:
                    logger.debug(f"Sem dados para hoje nem para ontem no consolidado")
                    return None

            bio_day = _extract_biometric_from_day(day_data)
            if fallback_used:
                # Preserva a data real do fallback para transparência
                bio_day.date = yesterday_str + " (fallback)"

            logger.debug(f"Biometria extraída: HRV={bio_day.hrv}, RHR={bio_day.rhr}, Sleep={bio_day.sleep}")
            return bio_day

        elif isinstance(consolidated, dict):
            logger.debug("Consolidated é dict, usando diretamente")
            bio_day = _extract_biometric_from_day(consolidated)
            bio_day.date = today_str
            return bio_day

        else:
            logger.error(f"Consolidated tem tipo inesperado: {type(consolidated)}")
            return None

    except Exception as e:
        logger.error(f"Erro em get_today_biometrics: {e}\n{traceback.format_exc()}")
        return None

def parse_garmin_history(data: Dict) -> List[BiometricDay]:
    """Parse dados históricos do Garmin. Tenta primeiro o consolidado, depois o dump."""
    history = []

    try:
        consolidated = load_garmin_consolidated()

        if consolidated:
            if isinstance(consolidated, list):
                logger.debug(f"Processando lista consolidada com {len(consolidated)} dias")
                for day_data in consolidated:
                    if not day_data.get('date'):
                        continue
                    bio_day = _extract_biometric_from_day(day_data)
                    if not bio_day.is_empty():
                        history.append(bio_day)

            elif isinstance(consolidated, dict):
                today_bio = get_today_biometrics()
                if today_bio and not today_bio.is_empty():
                    history.append(today_bio)

        # Adiciona dados do dump histórico (se existirem)
        daily_data = data.get('dailySummaries', []) if data else []

        for day in daily_data:
            calendar_date = day.get('calendarDate')
            if not calendar_date:
                continue
            if any(h.date == calendar_date for h in history):
                continue

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

        history.sort(key=lambda x: x.date, reverse=True)
        logger.debug(f"Histórico parseado: {len(history)} dias com dados")

    except Exception as e:
        logger.error(f"Erro ao parsear histórico: {e}\n{traceback.format_exc()}")

    return history

def get_recent_biometrics(days: int = 7) -> List[BiometricDay]:
    """Obtém biometria recente."""
    data = load_garmin_data()
    if not data:
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
    """Formata contexto biométrico com evolução"""
    if not history:
        return "### BIOMETRIA:\nSem dados disponíveis"

    lines = ["### BIOMETRIA:"]

    if baseline:
        lines.append("\n**BASELINE (7 dias):**")
        if 'hrv_avg' in baseline:
            lines.append(f"HRV: {baseline['hrv_avg']:.1f} (min: {baseline['hrv_min']:.1f}, max: {baseline['hrv_max']:.1f})")
        if 'rhr_avg' in baseline:
            lines.append(f"RHR: {baseline['rhr_avg']:.0f}bpm (min: {baseline['rhr_min']:.0f}, max: {baseline['rhr_max']:.0f})")

    valid_days = [d for d in history if d.is_valid()][:7]
    if valid_days:
        lines.append("\n**EVOLUÇÃO (mais recente → mais antigo):**")
        hrv_values = [d.hrv for d in valid_days if d.hrv is not None]
        if hrv_values:
            hrv_str = " -> ".join([f"{v:.0f}" for v in hrv_values])
            lines.append(f"HRV: {hrv_str}")
        rhr_values = [d.rhr for d in valid_days if d.rhr is not None]
        if rhr_values:
            rhr_str = " -> ".join([f"{v:.0f}" for v in rhr_values])
            lines.append(f"RHR: {rhr_str}")

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
    Parse de atividade do Garmin com acesso seguro.
    v3.12.0: Garante extração de elevation_gain e average_cadence.
    """
    try:
        activity_id = activity_raw.get('activityId')
        if not activity_id:
            return None

        start_time_local = activity_raw.get('startTimeLocal')
        activity_date = None
        if start_time_local:
            try:
                dt = datetime.fromisoformat(start_time_local.replace('Z', '+00:00'))
                activity_date = dt.strftime('%Y-%m-%d')
            except Exception:
                pass

        activity_type = activity_raw.get('activityType', {})
        sport = 'Desconhecido'
        if isinstance(activity_type, dict):
            sport = activity_type.get('typeKey', 'unknown')
        elif isinstance(activity_type, str):
            sport = activity_type

        duration_sec = activity_raw.get('duration')
        duration_min = (duration_sec / 60.0) if duration_sec else 0

        distance_m = activity_raw.get('distance')
        distance_km = (distance_m / 1000.0) if distance_m else None

        avg_hr = activity_raw.get('averageHR')
        calories = activity_raw.get('calories')

        # v3.12.0: Extração explícita e robusta de elevation_gain
        elevation_gain = activity_raw.get('elevationGain')
        if elevation_gain is None:
            elevation_gain = activity_raw.get('totalElevationGain')

        # v3.12.0: Extração explícita e robusta de average_cadence
        avg_cadence = activity_raw.get('averageBikingCadenceInRevPerMinute')
        if avg_cadence is None:
            avg_cadence = activity_raw.get('averageRunningCadenceInStepsPerMinute')
        if avg_cadence is None:
            avg_cadence = activity_raw.get('averageCadence')

        max_cadence = activity_raw.get('maxBikingCadenceInRevPerMinute')

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

    formatted.sort(key=lambda x: x.date or '0000-00-00', reverse=True)
    return formatted

# ==========================================
# DATA INTEGRITY
# ==========================================
def check_activities_integrity() -> Tuple[bool, str]:
    activities = load_activities_index()
    if not isinstance(activities, dict):
        return False, f"Tipo inválido: {type(activities)}"
    if not activities:
        return True, "Vazio mas válido"
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
    """Reorganiza activities.json. Remove duplicados e limita tamanho."""
    activities = load_activities_index()
    messages = []
    original_count = len(activities)
    seen_ids = set()
    cleaned = {}
    duplicates = 0

    for activity_id, data in activities.items():
        if activity_id in seen_ids:
            duplicates += 1
            continue
        seen_ids.add(activity_id)
        cleaned[activity_id] = data

    if len(cleaned) > MAX_ACTIVITIES_STORED:
        sorted_items = sorted(
            cleaned.items(),
            key=lambda x: x[1].get('startTimeLocal', ''),
            reverse=True
        )
        cleaned = dict(sorted_items[:MAX_ACTIVITIES_STORED])
        messages.append(f"Limitado a {MAX_ACTIVITIES_STORED} mais recentes")

    if len(cleaned) != original_count:
        save_activities_index(cleaned)
        messages.append(f"Reorganizado: {original_count} → {len(cleaned)}")
    else:
        messages.append("Sem mudanças necessárias")

    if duplicates > 0:
        messages.append(f"Removidos {duplicates} duplicados")

    return duplicates, len(cleaned), messages

def check_and_enrich_activities():
    """Verifica e enriquece atividades se necessário"""
    activities = load_activities_index()
    if not activities:
        logger.debug("Sem atividades para enriquecer")
        return

    enriched_count = 0
    for activity_id, data in activities.items():
        if '_enriched' not in data:
            data['_enriched'] = True
            data['_enriched_at'] = datetime.now().isoformat()
            enriched_count += 1

    if enriched_count > 0:
        save_activities_index(activities)
        logger.info(f"✅ {enriched_count} atividades enriquecidas")

# ==========================================
# CONTEXT MANAGEMENT
# ==========================================
def get_user_context_path(user_id: int) -> str:
    ensure_data_dir()
    return os.path.join(DATA_DIR, f'context_{user_id}.json')

def load_context_from_disk(user_id: int) -> Dict:
    path = get_user_context_path(user_id)
    return load_json_safe(path, {'history': [], 'last_update': None})

def save_context_to_disk(user_id: int, context_data: Dict):
    path = get_user_context_path(user_id)
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(context_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Erro ao salvar contexto: {e}")

def add_to_context_history(user_id: int, command: str, prompt: str, response: str):
    context_data = load_context_from_disk(user_id)
    entry = {
        'command': command,
        'timestamp': time.time(),
        'prompt': prompt[:1000],
        'response': response[:2000],
        'response_preview': response[:100]
    }
    history = context_data.get('history', [])
    history.insert(0, entry)
    if len(history) > MAX_CONTEXT_HISTORY:
        history = history[:MAX_CONTEXT_HISTORY]
    context_data['history'] = history
    context_data['last_update'] = time.time()
    save_context_to_disk(user_id, context_data)

def get_context_for_followup(user_id: int) -> str:
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
    path = get_user_context_path(user_id)
    try:
        if os.path.exists(path):
            os.remove(path)
            logger.info(f"Contexto do user {user_id} removido")
    except Exception as e:
        logger.error(f"Erro ao limpar contexto: {e}")

# ==========================================
# GEMINI API
# ==========================================
async def call_gemini_with_timeout(prompt: str, timeout_seconds: int) -> str:
    start_time = time.time()
    try:
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
    """Chama Gemini com retry, circuit breaker e cache"""
    if not circuit_breaker.can_proceed():
        logger.warning("Circuit breaker OPEN")
        raise CircuitBreakerOpen("Serviço temporariamente indisponível")

    if not rate_limiter.check_limit(user_id):
        logger.warning(f"Rate limit exceeded para user {user_id}")
        raise RateLimitExceeded("Rate limit excedido")

    cached = response_cache.get(prompt, user_id)
    if cached:
        response, timestamp = cached
        age = time.time() - timestamp
        logger.info(f"✅ Cache hit (age: {age:.0f}s)")
        return response

    if len(prompt) > GEMINI_MAX_PROMPT_LENGTH:
        logger.error(f"Prompt muito grande: {len(prompt)} chars")
        raise PromptTooLargeError(f"Prompt excede {GEMINI_MAX_PROMPT_LENGTH} chars")

    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            logger.info(f"Tentativa {attempt + 1}/{MAX_RETRIES}")
            response = await call_gemini_with_timeout(prompt, GEMINI_TIMEOUT_SECONDS)
            circuit_breaker.record_success()
            health_state.last_success = time.time()
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

    if last_error:
        raise last_error
    raise Exception("Erro desconhecido no retry loop")

# ==========================================
# HELPERS: send long message
# ==========================================
async def send_long_message(target, text: str):
    """Envia mensagem, particionando se necessário."""
    if len(text) <= TELEGRAM_SAFE_MESSAGE_LENGTH:
        await target.reply_text(text)
    else:
        chunks = [text[i:i+TELEGRAM_SAFE_MESSAGE_LENGTH]
                  for i in range(0, len(text), TELEGRAM_SAFE_MESSAGE_LENGTH)]
        for chunk in chunks:
            await target.reply_text(chunk)

# ==========================================
# TELEGRAM HANDLERS
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"🏋️ FitnessJournal v{BOT_VERSION}\n\n"
        f"{BOT_VERSION_DESC}\n\n"
        "Usa /help para ver comandos."
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    v3.12.0: /status com fluxo invertido.
    1. Extrai biometria e mostra dashboard imediatamente.
    2. Só DEPOIS pede feeling ao utilizador.
    """
    user_id = update.effective_user.id

    try:
        # v3.12.0: Mensagem de estado PT com tom de Personal Trainer
        await update.message.reply_text("⏳ A extrair biometria...")

        # Carrega dados biométricos
        history = get_recent_biometrics(7)
        baseline = calculate_biometric_baseline(history)
        today_bio = get_today_biometrics()

        # v3.12.0: DASHBOARD BIOMÉTRICO VISÍVEL antes da pergunta
        bio_lines = ["📊 HOJE:"]
        if today_bio and not today_bio.is_empty():
            if today_bio.rhr:
                bio_lines.append(f"  RHR: {today_bio.rhr}bpm")
            if today_bio.hrv is not None and 'hrv_avg' in baseline:
                pct = ((today_bio.hrv - baseline['hrv_avg']) / baseline['hrv_avg']) * 100
                bio_lines.append(f"  HRV: {today_bio.hrv:.0f} ({pct:+.0f}% vs média)")
            elif today_bio.hrv is not None:
                bio_lines.append(f"  HRV: {today_bio.hrv:.0f}")
            if today_bio.sleep is not None:
                bio_lines.append(f"  Sono: {today_bio.sleep}/100")
            # Fallback warning
            if today_bio.date and 'fallback' in str(today_bio.date):
                bio_lines.append(f"  ⚠️ Dados de ontem (hoje sem registo)")
        else:
            bio_lines.append("  Sem dados biométricos disponíveis")

        # v3.12.0: TENDÊNCIA 5 DIAS
        valid_5 = [d for d in history if d.is_valid()][:5]
        if valid_5:
            bio_lines.append("\n📈 TENDÊNCIA 5 DIAS:")
            hrv_trend = " -> ".join([f"{d.hrv:.0f}" for d in valid_5 if d.hrv is not None])
            rhr_trend = " -> ".join([f"{d.rhr}" for d in valid_5 if d.rhr is not None])
            if hrv_trend:
                bio_lines.append(f"  HRV: {hrv_trend}")
            if rhr_trend:
                bio_lines.append(f"  RHR: {rhr_trend}")

        # v3.12.0: ÚLTIMAS 3 ATIVIDADES
        activities = get_all_formatted_activities()
        if activities:
            bio_lines.append("\n🏃 ÚLTIMAS:")
            for act in activities[:3]:
                bio_lines.append(f"  • {act.to_brief_summary()}")

        await update.message.reply_text("\n".join(bio_lines))

        # v3.12.0: SÓ AGORA pergunta o feeling
        session_state.set_user_state(user_id, 'waiting_feeling')
        await update.message.reply_text(
            "💭 Como te sentes hoje (0-10)?\n"
            "0 = Exausto | 5 = Normal | 10 = Energizado"
        )

    except Exception as e:
        logger.error(f"Erro em /status: {e}")
        session_state.clear_user_state(user_id)
        await update.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def process_status_with_feeling(update: Update, feeling: int):
    """
    v3.12.0: Processa /status após receber o feeling.
    Foca na prescrição de treino com cálculo de carga e equipamento restrito.
    """
    user_id = update.effective_user.id

    try:
        await update.message.reply_text("🔍 A avaliar prontidão biológica...")

        history = get_recent_biometrics(7)
        baseline = calculate_biometric_baseline(history)
        bio_context = format_biometric_context(history, baseline)

        activities = get_all_formatted_activities()

        if not activities:
            await update.message.reply_text(
                "📭 Sem atividades.\n\nUsa /sync ou /import primeiro."
            )
            session_state.clear_user_state(user_id)
            return

        recent = activities[:MAX_ACTIVITIES_DISPLAY]

        equipamentos_str = ", ".join(EQUIPAMENTOS_GIM)

        prompt = f"""
{bio_context}

### SENSAÇÃO SUBJETIVA DO ATLETA:
Feeling de hoje: {feeling}/10

### ATIVIDADES RECENTES (Últimas {len(recent)}):

"""
        for act in recent:
            prompt += f"- {act.to_brief_summary()}\n"

        prompt += f"""

### EQUIPAMENTO DISPONÍVEL (EXCLUSIVO — NÃO SUGERIRES OUTROS):
{equipamentos_str}

### TAREFA (/status — PLANO DO DIA):
Avalia o readiness do atleta e prescreve o treino de hoje.
OBRIGATÓRIO:
1. Cálculo de Carga baseado em biometria (mostra a matemática)
2. Protocolo Aplicado (nome e justificação)
3. Tabela de Treino usando APENAS o equipamento listado acima
4. Se o atleta é ciclista, sugere apenas reforço core/postural ou endurance — NUNCA máquinas de ginásio comercial
5. Se HRV/RHR indicarem fadiga mas o feeling for alto (>7), ALERTA para fadiga mascarada
Usa o formato de tabela obrigatório do sistema.
"""

        response_text = await call_gemini_with_retry(prompt, user_id)

        await send_long_message(update.message, response_text)
        add_to_context_history(user_id, 'status', prompt, response_text)
        session_state.clear_user_state(user_id)

    except GeminiTimeoutError:
        await update.message.reply_text(f"⏱️ Timeout após {GEMINI_TIMEOUT_SECONDS}s. Tenta novamente.")
    except CircuitBreakerOpen:
        await update.message.reply_text("⚠️ Serviço temporariamente indisponível. Aguarda 1 minuto.")
    except RateLimitExceeded:
        await update.message.reply_text("⚠️ Rate limit excedido. Aguarda 1 minuto.")
    except Exception as e:
        logger.error(f"Erro em process_status_with_feeling: {e}\n{traceback.format_exc()}")
        await update.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def activities_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler /activities"""
    try:
        all_activities = get_all_formatted_activities()

        if not all_activities:
            await update.message.reply_text("📭 Sem atividades.\n\nUsa /sync ou /import.")
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
    """Handler /analyze — Análise de aderência ao plano"""
    user_id = update.effective_user.id

    try:
        await update.message.reply_text("🔍 A avaliar aderência ao plano...")

        history = get_recent_biometrics(7)
        baseline = calculate_biometric_baseline(history)
        bio_context = format_biometric_context(history, baseline)
        activities = get_all_formatted_activities()

        if not activities:
            await update.message.reply_text("📭 Sem atividades para analisar.\n\nUsa /sync ou /import.")
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
        await send_long_message(update.message, response_text)
        add_to_context_history(user_id, 'analyze', prompt, response_text)

    except GeminiTimeoutError:
        await update.message.reply_text(f"⏱️ Timeout após {GEMINI_TIMEOUT_SECONDS}s. Tenta novamente.")
    except Exception as e:
        logger.error(f"Erro em /analyze: {e}")
        await update.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def analyze_activity_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    v3.12.0: Handler /analyze_activity
    Mostra lista de atividades para escolher.
    """
    try:
        activities = get_all_formatted_activities()

        if not activities:
            await update.message.reply_text("📭 Sem atividades.\n\nUsa /sync ou /import.")
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
    v3.12.0: Callback para análise de atividade.
    Pergunta TIPO DE CICLISMO se aplicável.
    """
    query = update.callback_query
    await query.answer()

    try:
        index = int(query.data.split('_')[-1])
        activities = get_all_formatted_activities()

        if index >= len(activities):
            await query.edit_message_text("❌ Atividade não encontrada")
            return

        activity = activities[index]
        sport_lower = activity.sport.lower()
        is_cycling = any(x in sport_lower for x in ['cicl', 'mtb', 'spin', 'bike', 'cycling', 'road_biking'])

        if is_cycling:
            is_generic_type = sport_lower in ['cycling', 'other', 'bike', 'ciclismo']
            if is_generic_type:
                keyboard = [
                    [InlineKeyboardButton("🚵 MTB", callback_data=f"cycle_type_mtb_{index}")],
                    [InlineKeyboardButton("🚴 Estrada", callback_data=f"cycle_type_estrada_{index}")],
                    [InlineKeyboardButton("🏋️ Spinning", callback_data=f"cycle_type_spinning_{index}")],
                    [InlineKeyboardButton("🚲 Cidade", callback_data=f"cycle_type_cidade_{index}")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_text(
                    f"🚴 Atividade: {activity.to_brief_summary()}\n\nQue tipo de ciclismo foi?",
                    reply_markup=reply_markup
                )
            else:
                await ask_about_cargo(query, activity, index)
        else:
            await perform_activity_analysis(query, activity, has_cargo=False, cycling_type=None)

    except Exception as e:
        logger.error(f"Erro em analyze_activity_callback: {e}\n{traceback.format_exc()}")
        await query.edit_message_text(f"❌ Erro: {str(e)[:100]}")

async def cycling_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback para tipo de ciclismo"""
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
        await ask_about_cargo(query, activity, index, cycling_type)

    except Exception as e:
        logger.error(f"Erro em cycling_type_callback: {e}\n{traceback.format_exc()}")
        await query.edit_message_text(f"❌ Erro: {str(e)[:100]}")

async def ask_about_cargo(query, activity: FormattedActivity, index: int, cycling_type: str = None):
    """Pergunta sobre carga/passageiro"""
    keyboard = [
        [InlineKeyboardButton("Sim (tinha carga/passageiro)", callback_data=f"cargo_yes_{index}_{cycling_type or 'none'}")],
        [InlineKeyboardButton("Não (solo)", callback_data=f"cargo_no_{index}_{cycling_type or 'none'}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        f"🚴 Atividade: {activity.to_brief_summary()}\n\nLevaste passageiro ou carga adicional?",
        reply_markup=reply_markup
    )

async def cargo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback para resposta sobre carga em ciclismo"""
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
    v3.12.0: Análise de atividade individual.
    Imprime cabeçalho técnico ANTES de chamar o Gemini.
    Prompt focado exclusivamente em análise técnica — SEM tabela de treino futuro.
    Se ciclismo com carga, usa 150kg de peso total no contexto.
    """
    user_id = query.from_user.id

    try:
        # v3.12.0: Cabeçalho técnico impresso ANTES da chamada ao Gemini
        header = activity.to_technical_header()
        await query.edit_message_text(f"🔬 A avaliar métricas da sessão...\n\n{header}")

        # Biometria de contexto
        history = get_recent_biometrics(7)
        baseline = calculate_biometric_baseline(history)
        bio_context = format_biometric_context(history, baseline)

        # Contexto de carga
        cargo_context = ""
        peso_total = 150 if has_cargo else None
        if cycling_type and has_cargo:
            cargo_context = f"\nTipo: {cycling_type.upper()} | Carga/Passageiro: SIM (peso total estimado: 150kg)"
        elif cycling_type:
            cargo_context = f"\nTipo: {cycling_type.upper()} | Solo (peso atleta)"
        elif has_cargo:
            cargo_context = f"\nCarga/Passageiro: SIM (peso total estimado: 150kg)"

        # v3.12.0: Dados técnicos explícitos para injeção no prompt
        elev_str = f"{activity.elevation_gain:.0f}m" if activity.elevation_gain else "sem dados"
        cad_val = activity.avg_cadence or activity.bike_cadence
        cad_str = f"{cad_val} RPM" if cad_val else "sem dados"
        hr_str = f"{activity.avg_hr}bpm" if activity.avg_hr else "sem dados"
        dist_str = f"{activity.distance_km:.1f}km" if activity.distance_km else "sem dados"
        dur_str = f"{activity.duration_min:.0f}min" if activity.duration_min else "sem dados"

        prompt = f"""
{bio_context}

### ATIVIDADE PARA ANÁLISE TÉCNICA:
Data: {activity.date or 'N/A'}
Desporto: {activity.sport}{cargo_context}
Duração: {dur_str}
Distância: {dist_str}
FC Média: {hr_str}
Calorias: {activity.calories or 'sem dados'}kcal
Ganho Altimétrico: {elev_str}
Cadência Média: {cad_str}
{f"Peso total estimado: {peso_total}kg" if peso_total else ""}

### TAREFA (/analyze_activity — ANÁLISE TÉCNICA EXCLUSIVA):
Analisa esta sessão realizada. FOCA EXCLUSIVAMENTE em:
1. Eficiência de Cadência: {cad_str} vs óptimo para este tipo de esforço (mostra cálculo)
2. Análise de FC por zonas: deriva cardíaca, desacoplamento aeróbio
3. Impacto Altimétrico: ganho de {elev_str} — estima W/kg e fadiga acumulada
4. Impacto Biométrico desta sessão: previsão de HRV amanhã, tempo de recuperação estimado
PROIBIDO incluir tabela de treino futuro, sugestões de musculação ou core nesta resposta.
Usa /status para prescrição de treino futuro.
"""

        response_text = await call_gemini_with_retry(prompt, user_id)
        await send_long_message(query.message, response_text)
        add_to_context_history(user_id, 'analyze_activity', prompt, response_text)

    except GeminiTimeoutError:
        await query.message.reply_text(f"⏱️ Timeout após {GEMINI_TIMEOUT_SECONDS}s. Tenta novamente.")
    except Exception as e:
        logger.error(f"Erro em perform_activity_analysis: {e}\n{traceback.format_exc()}")
        await query.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def import_historical(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler /import"""
    try:
        await update.message.reply_text("🔄 A processar importação histórica...")
        if not create_import_request(days=30):
            await update.message.reply_text("❌ Erro ao criar pedido de importação")
            return
        asyncio.create_task(send_sync_feedback(update, 'import_request'))
    except Exception as e:
        logger.error(f"Erro em /import: {e}")
        await update.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def sync_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler /sync"""
    keyboard = [
        [InlineKeyboardButton("✅ Sim, sincronizar", callback_data="sync_confirmed")],
        [InlineKeyboardButton("❌ Cancelar", callback_data="sync_cancel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🔄 Sincronizar dados do Garmin?\n\nIsto irá importar atividades e biometria recentes.",
        reply_markup=reply_markup
    )

async def sync_confirmed_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback de confirmação de sync"""
    query = update.callback_query
    await query.answer()

    try:
        await query.edit_message_text("🔄 A processar sincronização...")
        if not create_sync_request():
            await query.message.reply_text("❌ Erro ao criar pedido de sync")
            return
        asyncio.create_task(send_sync_feedback(query, 'sync_request'))
    except Exception as e:
        logger.error(f"Erro em sync_confirmed_callback: {e}")
        await query.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def send_sync_feedback(query_or_update, flag_name: str):
    """Envia feedback após sincronização. Aceita CallbackQuery ou Update."""
    try:
        completed = await wait_for_sync_completion(query_or_update, timeout_seconds=60)
        message = query_or_update.message

        if not completed:
            await message.reply_text("⏱️ Sincronização ainda em progresso...\nUsa /activities para ver o estado.")
            return

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
        msg = "🧹 LIMPEZA:\n\nFLAGS:\n" + "\n".join(messages) + "\n\nATIVIDADES:\n" + "\n".join(reorg_messages)
        await update.message.reply_text(msg)
    except Exception as e:
        logger.error(f"Erro em /cleanup: {e}")
        await update.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler /history"""
    user_id = update.effective_user.id
    try:
        context_data = load_context_from_disk(user_id)
        if not context_data or not context_data.get('history'):
            await update.message.reply_text("📭 Sem histórico de análises.\n\nUsa /status ou /analyze primeiro.")
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
    """Handler /stats"""
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
    """Handler /debug"""
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
    """Handler /health"""
    try:
        activities = get_all_formatted_activities()
        valid_activities = [a for a in activities if a.date]
        history = get_recent_biometrics(7)
        today_bio = get_today_biometrics()
        disk_ok, disk_msg = check_disk_space()
        status_emoji = "✅" if disk_ok and activities else "⚠️"
        msg = f"{status_emoji} HEALTH CHECK v{BOT_VERSION}:\n\n"
        msg += "📊 DADOS:\n"
        msg += f"- Atividades: {len(activities)} ({len(valid_activities)} válidas)\n"
        msg += f"- Biometria: {len(history)} dias\n"
        if today_bio and not today_bio.is_empty():
            msg += f"- Última biometria: {today_bio.date}\n"
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
        "/analyze_activity - Análise técnica de atividade individual\n"
        "/sync - Sincroniza dados do Garmin\n"
        "/import - Importa histórico (30 dias)\n"
        "/cleanup - Limpa dados antigos\n"
        "/history - Análises anteriores\n"
        "/clear_context - Limpa contexto\n"
        "/stats - Estatísticas\n"
        "/debug - Informações de debug\n"
        "/health - Health check do sistema\n"
        "/help - Esta ajuda\n\n"
        "🆕 v3.12.0:\n"
        "• /status: dashboard biométrico visível antes do feeling\n"
        "• /analyze_activity: cabeçalho técnico antes do Gemini\n"
        "• Separação estrita de contextos IA (análise vs prescrição)\n"
        "• Fallback biométrico para dia anterior com aviso\n"
        "• Logs JSON com contagem de itens (logger.info)\n"
        "• Extração robusta de elevation_gain e cadência\n"
        "• Equipamento restrito ao EQUIPAMENTOS_GIM"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler para mensagens de texto livre.
    Verifica se está aguardando feeling para /status.
    """
    user_id = update.effective_user.id
    message_text = update.message.text

    try:
        user_state = session_state.get_user_state(user_id)

        if user_state == 'waiting_feeling':
            try:
                feeling = int(message_text.strip())
                if 0 <= feeling <= 10:
                    await process_status_with_feeling(update, feeling)
                    return
                else:
                    await update.message.reply_text("❌ Por favor, responde com um número entre 0 e 10.")
                    return
            except ValueError:
                await update.message.reply_text("❌ Por favor, responde com um número entre 0 e 10.")
                return

        context_data = load_context_from_disk(user_id)

        if not context_data or not context_data.get('history'):
            await update.message.reply_text(
                "💡 Usa /status, /analyze ou /analyze_activity primeiro.\n"
                "Depois podes fazer perguntas sobre a análise."
            )
            return

        if len(message_text) > MAX_FEELING_LENGTH:
            await update.message.reply_text(
                f"❌ Mensagem demasiado longa.\nMáximo: {MAX_FEELING_LENGTH} caracteres."
            )
            return

        await update.message.reply_text("🤔 A processar pergunta...")

        context_text = get_context_for_followup(user_id)
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
        await send_long_message(update.message, response_text)
        add_to_context_history(user_id, 'followup', prompt, response_text)

    except GeminiTimeoutError:
        await update.message.reply_text(f"⏱️ Timeout após {GEMINI_TIMEOUT_SECONDS}s. Tenta novamente.")
    except Exception as e:
        logger.error(f"Erro em handle_message: {e}\n{traceback.format_exc()}")
        await update.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    command = update.message.text
    await update.message.reply_text(
        f"❓ Comando '{command}' não reconhecido.\n\nUsa /help para ver todos os comandos disponíveis."
    )

# ==========================================
# MAIN (v3.12.0)
# ==========================================
def main():
    """Entry point v3.12.0"""
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

    is_valid, integrity_msg = check_activities_integrity()
    if not is_valid:
        logger.warning(f"⚠️ INTEGRIDADE: {integrity_msg}")
        logger.info("🔧 Será corrigido automaticamente no próximo load...")
    else:
        logger.info(f"✅ INTEGRIDADE: {integrity_msg}")

    logger.info("🔍 Verificando atividades para enriquecimento...")
    check_and_enrich_activities()

    all_activities = get_all_formatted_activities()
    logger.info(f"🏃 Atividades: {len(all_activities)}")

    logger.info("🧹 Auto-cleanup...")
    cleaned, _ = cleanup_old_flags()
    if cleaned > 0:
        logger.info(f"✅ {cleaned} flags limpos")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

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

    app.add_handler(CallbackQueryHandler(sync_confirmed_callback, pattern=r'^sync_confirmed$'))
    app.add_handler(CallbackQueryHandler(analyze_activity_callback, pattern=r'^analyze_act_\d+$'))
    app.add_handler(CallbackQueryHandler(cycling_type_callback, pattern=r'^cycle_type_(mtb|estrada|spinning|cidade)_\d+$'))
    app.add_handler(CallbackQueryHandler(cargo_callback, pattern=r'^cargo_(yes|no)_\d+'))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))

    logger.info(f"✅ Bot v{BOT_VERSION} iniciado")
    logger.info(f"  - Separação estrita de contextos IA")
    logger.info(f"  - Cabeçalho técnico em /analyze_activity")
    logger.info(f"  - /status com dashboard biométrico antes do feeling")
    logger.info(f"  - Fallback biométrico dia anterior")
    logger.info(f"  - Logs JSON com contagem de itens")
    logger.info(f"  - Timeout Gemini: {GEMINI_TIMEOUT_SECONDS}s")
    logger.info(f"  - Retry delays: {RETRY_DELAYS}")
    logger.info(f"  - Circuit breaker: {CIRCUIT_BREAKER_THRESHOLD} falhas threshold")
    logger.info(f"  - Cache: {RESPONSE_CACHE_SIZE} entradas, TTL {CACHE_TTL_SECONDS}s")
    logger.info(f"  - Rate limit: {RATE_LIMIT_MAX_REQUESTS} req/{RATE_LIMIT_WINDOW}s")

    print(f"🤖 Bot v{BOT_VERSION} ativo")

    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == '__main__':
    main()
