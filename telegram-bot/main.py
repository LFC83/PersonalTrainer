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
# CONFIGURATION & CONSTANTS (v3.11.0)
# ==========================================
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
logger.info("Gemini API configurada")

# Bot Configuration
BOT_VERSION = "3.11.0"
BOT_VERSION_DESC = "UX RESTORATION: Technical Headers + Visible Biometrics + Precise AI Training"
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

# Cycling Types
CYCLING_TYPES = ["MTB", "Estrada", "Spinning", "Cidade"]

# Retry & Circuit Breaker
MAX_RETRIES = 3
RETRY_DELAYS = [2, 5, 10]
CIRCUIT_BREAKER_THRESHOLD = 5
CIRCUIT_BREAKER_TIMEOUT = 60

# Rate Limiting
RATE_LIMIT_WINDOW = 60
RATE_LIMIT_MAX_REQUESTS = 10

# Cache
RESPONSE_CACHE_SIZE = 100

# Health Check
GEMINI_LATENCY_HISTORY_SIZE = 10

# ==========================================
# SYSTEM PROMPT (v3.11.0 - ENHANCED)
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
És um TREINADOR DE PERFORMANCE HUMANA especializado em Ciclismo de Resistência e Reforço Estrutural.
- TOM: Assertivo, direto e orientado para resultados. Sem emojis.
- SEM RODEIOS: Se o atleta falhou ou fez escolhas subótimas, diz claramente.
- FOCO: Ciclismo de endurance + reforço core/postural. NUNCA hipertrofia de máquinas comerciais.

### EQUIPAMENTO DISPONÍVEL (USAR EXCLUSIVAMENTE):
""" + "\n".join([f"- {eq}" for eq in EQUIPAMENTOS_GIM]) + """

### RESTRIÇÕES DE PRESCRIÇÃO:
1. Para ciclistas: reforço core, postural, ou endurance cardiovascular.
2. PROIBIDO: Exercícios de hipertrofia comercial (Prensa, Leg Press, máquinas isoladas).
3. OBRIGATÓRIO: Justificar transferência do exercício para performance ciclística.

### CRITÉRIOS BIOMÉTRICOS RIGOROSOS (v3.11.0 - PRIORIDADE ABSOLUTA):
**REGRA FUNDAMENTAL:** Quando os dados biométricos (HRV baixa/RHR elevada) indicarem fadiga, mas o feedback subjetivo do utilizador for positivo (ex: "sinto-me bem"), DEVES DAR PRIORIDADE ABSOLUTA AOS DADOS BIOLÓGICOS e alertar para o risco de fadiga mascarada.

**LIMITES DE CORTE (95% HRV):**
1. HRV menor que 95% da média = APENAS RECUPERAÇÃO ATIVA.
2. RHR maior que mais 2% da média = FADIGA DETETADA (Reduzir volume em 50%).
3. PONTUAÇÃO DE SONO menor que 75 mais Sensação Negativa = ZERO INTENSIDADE.
4. **FADIGA MASCARADA (v3.11.0):** Se HRV está baixa OU RHR está elevada, mas o atleta reporta sentir-se bem:
   - Explica a discrepância entre sensação subjetiva e realidade fisiológica
   - Alerta para o perigo de ignorar os sinais biométricos
   - Prescreve treino baseado nos dados objetivos (HRV/RHR), NÃO no sentimento
   - Exemplo: "Reportas sentir-te fresco, mas a tua HRV está 12% abaixo da média. Isto indica fadiga neuromuscular que ainda não percebes conscientemente. APENAS recuperação ativa hoje."

### FORMATO DE RESPOSTA OBRIGATÓRIO:

**CÁLCULOS DE CARGA:**
[Mostra matemática explícita: HRV atual vs média, limites 95%, desvios RHR]

**PROTOCOLO APLICADO:**
[Decisão: Treino/Recuperação/Off baseado em cálculos acima]

**TABELA DE TREINO:**
| Tipo Treino | Descrição | Séries/Duração | Intensidade | Observações |
| :--- | :--- | :--- | :--- | :--- |
| ... | ... | ... | ... | ... |

**ANÁLISE TÉCNICA:**
[Avaliação do estado atual, coerência biometria versus sensação, eficiência de cadência se ciclismo]

**RECOMENDAÇÕES:**
[Instruções de recuperação, nutrição, ajustes para próximo treino]

### ANÁLISE DE CADÊNCIA (Ciclismo):
- Cadência ótima MTB: 75-85 RPM
- Cadência ótima Estrada: 85-95 RPM
- Spinning: 80-100 RPM
- Se cadência < -10% do ótimo: alertar para perda de eficiência
- Comentar sempre a eficiência da cadência quando dados disponíveis

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
# DATA MODELS (v3.11.0)
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
        """v3.11.0: Summary breve para listagem"""
        parts = [f"{self.date or 'Sem data'}", f"{self.sport}"]
        
        if self.distance_km:
            parts.append(f"{self.distance_km:.1f}km")
        
        parts.append(f"{self.duration_min:.0f}min")
        
        if self.avg_hr:
            parts.append(f"FC:{self.avg_hr}bpm")
        
        return " | ".join(parts)
    
    def to_detailed_summary(self) -> str:
        """v3.11.0: ENHANCED - Summary detalhado para análise com TODOS os dados técnicos"""
        lines = [
            f"Data: {self.date or 'Sem data'}",
            f"Modalidade: {self.sport}",
            f"Duração: {self.duration_min:.0f} minutos"
        ]
        
        if self.distance_km:
            lines.append(f"Distância: {self.distance_km:.2f} km")
            # Calcula velocidade
            if self.duration_min > 0:
                speed = (self.distance_km / (self.duration_min / 60.0))
                lines.append(f"Velocidade Média: {speed:.1f} km/h")
        
        if self.avg_hr:
            lines.append(f"FC Média: {self.avg_hr} bpm")
        
        if self.calories:
            lines.append(f"Calorias: {self.calories} kcal")
        
        # v3.11.0: Altimetria obrigatória
        if self.elevation_gain is not None and self.elevation_gain > 0:
            lines.append(f"Desnível Positivo: {self.elevation_gain:.0f} m")
        
        # v3.11.0: Cadência obrigatória (verifica múltiplos campos)
        cadence_value = None
        if self.avg_cadence is not None and self.avg_cadence > 0:
            cadence_value = self.avg_cadence
        elif self.bike_cadence is not None and self.bike_cadence > 0:
            cadence_value = self.bike_cadence
        
        if cadence_value:
            lines.append(f"Cadência Média: {cadence_value:.0f} RPM")
        
        if self.load:
            lines.append(f"Training Load: {self.load:.1f}")
        
        return "\n".join(lines)

# ==========================================
# SESSION STATE MANAGER
# ==========================================
class SessionStateManager:
    """v3.11.0: Gestor de estado de sessão de utilizadores"""
    def __init__(self):
        self._states: Dict[int, str] = {}
    
    def set_user_state(self, user_id: int, state: str):
        """Define estado do utilizador"""
        self._states[user_id] = state
        logger.debug(f"User {user_id} state: {state}")
    
    def get_user_state(self, user_id: int) -> Optional[str]:
        """Obtém estado do utilizador"""
        return self._states.get(user_id)
    
    def clear_user_state(self, user_id: int):
        """Limpa estado do utilizador"""
        if user_id in self._states:
            del self._states[user_id]
            logger.debug(f"User {user_id} state cleared")

session_state = SessionStateManager()

# ==========================================
# CIRCUIT BREAKER
# ==========================================
class CircuitBreaker:
    """v3.11.0: Circuit breaker para Gemini API"""
    def __init__(self, threshold: int, timeout: int):
        self.threshold = threshold
        self.timeout = timeout
        self.failures = 0
        self.last_failure_time = 0
        self.state = 'closed'
    
    def can_execute(self) -> bool:
        """Verifica se pode executar"""
        if self.state == 'open':
            if time.time() - self.last_failure_time > self.timeout:
                self.state = 'half-open'
                self.failures = 0
                logger.info("Circuit breaker: open → half-open")
                return True
            return False
        return True
    
    def record_success(self):
        """Registra sucesso"""
        self.failures = 0
        if self.state == 'half-open':
            self.state = 'closed'
            logger.info("Circuit breaker: half-open → closed")
    
    def record_failure(self):
        """Registra falha"""
        self.failures += 1
        self.last_failure_time = time.time()
        
        if self.failures >= self.threshold:
            self.state = 'open'
            logger.warning(f"Circuit breaker OPENED após {self.failures} falhas")

circuit_breaker = CircuitBreaker(CIRCUIT_BREAKER_THRESHOLD, CIRCUIT_BREAKER_TIMEOUT)

# ==========================================
# RATE LIMITER
# ==========================================
class RateLimiter:
    """v3.11.0: Rate limiter por usuário"""
    def __init__(self, window: int, max_requests: int):
        self.window = window
        self.max_requests = max_requests
        self.requests: Dict[int, List[float]] = defaultdict(list)
    
    def can_proceed(self, user_id: int) -> bool:
        """Verifica se pode proceder"""
        now = time.time()
        
        # Remove requisições antigas
        self.requests[user_id] = [
            t for t in self.requests[user_id] 
            if now - t < self.window
        ]
        
        # Verifica limite
        if len(self.requests[user_id]) >= self.max_requests:
            return False
        
        # Adiciona requisição
        self.requests[user_id].append(now)
        return True

rate_limiter = RateLimiter(RATE_LIMIT_WINDOW, RATE_LIMIT_MAX_REQUESTS)

# ==========================================
# RESPONSE CACHE
# ==========================================
class ResponseCache:
    """v3.11.0: Cache de respostas do Gemini"""
    def __init__(self, max_size: int, ttl: int):
        self.max_size = max_size
        self.ttl = ttl
        self.cache: Dict[str, Tuple[str, float, int]] = {}
    
    def _hash(self, prompt: str, user_id: int) -> str:
        """Gera hash do prompt"""
        key = f"{user_id}:{prompt}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]
    
    def get(self, prompt: str, user_id: int) -> Optional[str]:
        """Obtém resposta do cache"""
        key = self._hash(prompt, user_id)
        
        if key not in self.cache:
            return None
        
        response, timestamp, _ = self.cache[key]
        
        # Verifica TTL
        if time.time() - timestamp > self.ttl:
            del self.cache[key]
            return None
        
        logger.debug(f"Cache HIT para user {user_id}")
        return response
    
    def set(self, prompt: str, user_id: int, response: str):
        """Armazena resposta no cache"""
        key = self._hash(prompt, user_id)
        
        # Limita tamanho
        if len(self.cache) >= self.max_size:
            # Remove entrada mais antiga
            oldest_key = min(self.cache.keys(), key=lambda k: self.cache[k][1])
            del self.cache[oldest_key]
        
        self.cache[key] = (response, time.time(), user_id)
        logger.debug(f"Cache SET para user {user_id}")

response_cache = ResponseCache(RESPONSE_CACHE_SIZE, CACHE_TTL_SECONDS)

# ==========================================
# HEALTH STATE
# ==========================================
class HealthState:
    """v3.11.0: Estado de saúde do sistema"""
    def __init__(self):
        self.gemini_latencies: List[float] = []
        self.last_success: float = 0
        self.last_error: Optional[str] = None
        self.total_requests = 0
        self.total_errors = 0
    
    def record_latency(self, latency: float):
        """Registra latência"""
        self.gemini_latencies.append(latency)
        if len(self.gemini_latencies) > GEMINI_LATENCY_HISTORY_SIZE:
            self.gemini_latencies.pop(0)
    
    def get_avg_latency(self) -> float:
        """Obtém latência média"""
        if not self.gemini_latencies:
            return 0.0
        return mean(self.gemini_latencies)

health_state = HealthState()

# ==========================================
# FILE OPERATIONS
# ==========================================
def ensure_data_dir():
    """Garante que o diretório de dados existe"""
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR, exist_ok=True)
        logger.info(f"Diretório {DATA_DIR} criado")

def load_json_safe(filepath: str, default=None):
    """v3.11.0: Carrega JSON com fallback seguro"""
    ensure_data_dir()
    
    if not os.path.exists(filepath):
        logger.debug(f"Arquivo não existe: {filepath}")
        return default if default is not None else {}
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
            logger.debug(f"JSON carregado: {filepath} (tipo: {type(data).__name__})")
            return data
    except json.JSONDecodeError as e:
        logger.error(f"JSON inválido em {filepath}: {e}")
        return default if default is not None else {}
    except Exception as e:
        logger.error(f"Erro ao ler {filepath}: {e}")
        return default if default is not None else {}

def save_json_safe(filepath: str, data: Any) -> bool:
    """Salva JSON com tratamento de erro"""
    ensure_data_dir()
    
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.debug(f"JSON salvo: {filepath}")
        return True
    except Exception as e:
        logger.error(f"Erro ao salvar {filepath}: {e}")
        return False

def load_garmin_data() -> Dict:
    """Carrega dados do Garmin"""
    return load_json_safe(os.path.join(DATA_DIR, 'garmin_data_dump.json'), {})

def load_activities_index() -> Dict:
    """v3.11.0: Carrega índice de atividades (sempre dict)"""
    filepath = os.path.join(DATA_DIR, 'activities.json')
    data = load_json_safe(filepath, {})
    
    # Se for lista (formato antigo), converte para dict
    if isinstance(data, list):
        logger.warning("⚠️ activities.json é lista, convertendo para dict...")
        converted = {}
        for item in data:
            if isinstance(item, dict) and 'activityId' in item:
                activity_id = str(item['activityId'])
                converted[activity_id] = item
        
        # Salva convertido
        save_activities_index(converted)
        logger.info(f"✅ Convertido {len(converted)} atividades")
        return converted
    
    return data

def save_activities_index(activities: Dict) -> bool:
    """Salva índice de atividades"""
    filepath = os.path.join(DATA_DIR, 'activities.json')
    return save_json_safe(filepath, activities)

def create_sync_request() -> bool:
    """Cria flag de requisição de sync"""
    flag_path = os.path.join(DATA_DIR, 'sync_request')
    try:
        with open(flag_path, 'w') as f:
            f.write(str(int(time.time())))
        logger.info("Flag sync_request criado")
        return True
    except Exception as e:
        logger.error(f"Erro ao criar sync_request: {e}")
        return False

def create_import_request(days: int = 30) -> bool:
    """Cria flag de requisição de importação"""
    flag_path = os.path.join(DATA_DIR, 'import_request')
    try:
        with open(flag_path, 'w') as f:
            f.write(str(days))
        logger.info(f"Flag import_request criado ({days} dias)")
        return True
    except Exception as e:
        logger.error(f"Erro ao criar import_request: {e}")
        return False

async def wait_for_sync_completion(query_or_update, timeout_seconds: int = 60) -> bool:
    """v3.11.0: Aguarda conclusão de sync"""
    start_time = time.time()
    
    while (time.time() - start_time) < timeout_seconds:
        # Verifica se flags desapareceram
        sync_flag = os.path.join(DATA_DIR, 'sync_request')
        import_flag = os.path.join(DATA_DIR, 'import_request')
        
        if not os.path.exists(sync_flag) and not os.path.exists(import_flag):
            logger.info("Sync concluído")
            return True
        
        await asyncio.sleep(2)
    
    logger.warning("Timeout aguardando sync")
    return False

def cleanup_old_flags() -> Tuple[int, List[str]]:
    """Remove flags antigos"""
    flags = ['sync_request', 'import_request']
    cleaned = 0
    messages = []
    
    for flag in flags:
        flag_path = os.path.join(DATA_DIR, flag)
        if not os.path.exists(flag_path):
            continue
        
        try:
            # Verifica idade
            mtime = os.path.getmtime(flag_path)
            age = time.time() - mtime
            
            if age > FLAG_TIMEOUT_SECONDS:
                os.remove(flag_path)
                cleaned += 1
                messages.append(f"{flag} removido (idade: {age:.0f}s)")
                logger.info(f"Flag antigo removido: {flag}")
        except Exception as e:
            logger.error(f"Erro ao limpar {flag}: {e}")
    
    return cleaned, messages

# ==========================================
# CONTEXT MANAGEMENT
# ==========================================
def get_context_filepath(user_id: int) -> str:
    """Obtém caminho do arquivo de contexto"""
    return os.path.join(DATA_DIR, f'context_{user_id}.json')

def load_context_from_disk(user_id: int) -> Dict:
    """Carrega contexto do usuário"""
    return load_json_safe(get_context_filepath(user_id), {})

def save_context_to_disk(user_id: int, context_data: Dict) -> bool:
    """Salva contexto do usuário"""
    return save_json_safe(get_context_filepath(user_id), context_data)

def add_to_context_history(user_id: int, command: str, prompt: str, response: str):
    """Adiciona entrada ao histórico de contexto"""
    context_data = load_context_from_disk(user_id)
    
    if 'history' not in context_data:
        context_data['history'] = []
    
    entry = {
        'command': command,
        'timestamp': datetime.now().isoformat(),
        'prompt': prompt[:1000],
        'response': response[:2000]
    }
    
    context_data['history'].append(entry)
    
    # Limita tamanho
    if len(context_data['history']) > MAX_CONTEXT_HISTORY:
        context_data['history'] = context_data['history'][-MAX_CONTEXT_HISTORY:]
    
    save_context_to_disk(user_id, context_data)

def get_context_for_followup(user_id: int) -> str:
    """Obtém contexto formatado para followup"""
    context_data = load_context_from_disk(user_id)
    
    if not context_data or not context_data.get('history'):
        return ""
    
    lines = ["### CONTEXTO DAS ANÁLISES ANTERIORES:"]
    
    for entry in context_data['history'][-2:]:
        lines.append(f"\n**{entry['command'].upper()} em {entry['timestamp']}:**")
        lines.append(f"Resposta: {entry['response'][:500]}...")
    
    return "\n".join(lines)

def clear_user_context(user_id: int) -> bool:
    """Limpa contexto do usuário"""
    filepath = get_context_filepath(user_id)
    
    if os.path.exists(filepath):
        try:
            os.remove(filepath)
            logger.info(f"Contexto do user {user_id} removido")
            return True
        except Exception as e:
            logger.error(f"Erro ao remover contexto: {e}")
            return False
    
    return True

# ==========================================
# BIOMETRIC FUNCTIONS (v3.11.0)
# ==========================================
def get_today_biometrics() -> Optional[BiometricDay]:
    """v3.11.0: Obtém biometria de hoje do consolidated"""
    data = load_garmin_data()
    
    if not data:
        return None
    
    consolidated = data.get('consolidated')
    
    if not consolidated:
        return None
    
    today_str = date.today().isoformat()
    
    # Se consolidated é lista, procura entrada de hoje
    if isinstance(consolidated, list):
        for day in consolidated:
            if day.get('calendarDate') == today_str:
                return BiometricDay(
                    date=today_str,
                    hrv=day.get('avgWakingHeartRateVariability'),
                    rhr=day.get('restingHeartRate'),
                    sleep=day.get('sleepScore'),
                    steps=day.get('totalSteps')
                )
    
    # Se consolidated é dict (hoje apenas)
    elif isinstance(consolidated, dict):
        return BiometricDay(
            date=today_str,
            hrv=consolidated.get('avgWakingHeartRateVariability'),
            rhr=consolidated.get('restingHeartRate'),
            sleep=consolidated.get('sleepScore'),
            steps=consolidated.get('totalSteps')
        )
    
    return None

def parse_garmin_history(data: Dict) -> List[BiometricDay]:
    """v3.11.0: Parse de histórico biométrico"""
    history = []
    
    try:
        # Extrai de consolidated (lista ou dict)
        consolidated = data.get('consolidated')
        
        if consolidated:
            if isinstance(consolidated, list):
                # Lista de dias
                for day in consolidated:
                    day_date = day.get('calendarDate')
                    if not day_date:
                        continue
                    
                    hrv = day.get('avgWakingHeartRateVariability')
                    rhr = day.get('restingHeartRate')
                    sleep_score = day.get('sleepScore')
                    steps = day.get('totalSteps')
                    
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
    """v3.11.0: Obtém biometria recente"""
    data = load_garmin_data()
    if not data:
        today_bio = get_today_biometrics()
        return [today_bio] if today_bio and not today_bio.is_empty() else []
    
    history = parse_garmin_history(data)
    return history[:days]

def calculate_biometric_baseline(history: List[BiometricDay]) -> Dict[str, float]:
    """v3.11.0: Calcula baseline biométrico"""
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
    """v3.11.0: Formata contexto biométrico com evolução"""
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
    
    # Evolução dos últimos 7 dias
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

def format_biometric_summary_for_status(history: List[BiometricDay], baseline: Dict[str, float]) -> str:
    """v3.11.0: NEW - Formata resumo biométrico visual para /status"""
    if not history:
        return "📭 Sem dados biométricos disponíveis.\nUsa /sync primeiro."
    
    lines = ["📊 **BIOMETRIA:**\n"]
    
    # Dados de hoje
    today = history[0] if history else None
    if today:
        lines.append(f"**HOJE ({today.date}):**")
        
        if today.hrv is not None:
            deviation = ""
            if 'hrv_avg' in baseline:
                pct = ((today.hrv - baseline['hrv_avg']) / baseline['hrv_avg']) * 100
                deviation = f" ({pct:+.1f}%)"
            lines.append(f"• HRV: {today.hrv:.0f}{deviation}")
        
        if today.rhr is not None:
            deviation = ""
            if 'rhr_avg' in baseline:
                diff = today.rhr - baseline['rhr_avg']
                deviation = f" ({diff:+.0f}bpm)"
            lines.append(f"• FC Repouso: {today.rhr}bpm{deviation}")
        
        if today.sleep is not None:
            lines.append(f"• Sono: {today.sleep}/100")
    
    # Tendência 5 dias
    valid_days = [d for d in history if d.is_valid()][:5]
    
    if len(valid_days) >= 3:
        lines.append(f"\n**TENDÊNCIA 5 DIAS:**")
        
        hrv_values = [d.hrv for d in valid_days if d.hrv is not None]
        if hrv_values:
            hrv_str = " → ".join([f"{v:.0f}" for v in hrv_values])
            lines.append(f"HRV: {hrv_str}")
        
        rhr_values = [d.rhr for d in valid_days if d.rhr is not None]
        if rhr_values:
            rhr_str = " → ".join([f"{v:.0f}" for v in rhr_values])
            lines.append(f"RHR: {rhr_str}")
    
    return "\n".join(lines)

# ==========================================
# ACTIVITY PARSING (v3.11.0 - ENHANCED)
# ==========================================
def parse_activity_from_garmin(activity_raw: Dict) -> Optional[FormattedActivity]:
    """v3.11.0: ENHANCED - Parse com mapeamento correto de cadência e altimetria"""
    try:
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
        
        # Tipo de atividade
        activity_type = activity_raw.get('activityType', {})
        sport = 'Desconhecido'
        
        if isinstance(activity_type, dict):
            sport = activity_type.get('typeKey', 'unknown')
        elif isinstance(activity_type, str):
            sport = activity_type
        
        # Duração
        duration_sec = activity_raw.get('duration')
        duration_min = (duration_sec / 60.0) if duration_sec else 0
        
        # Distância
        distance_m = activity_raw.get('distance')
        distance_km = (distance_m / 1000.0) if distance_m else None
        
        # Métricas
        avg_hr = activity_raw.get('averageHR')
        calories = activity_raw.get('calories')
        
        # v3.11.0: CRITICAL - Mapeamento correto de altimetria
        elevation_gain = activity_raw.get('elevationGain')
        if elevation_gain is None:
            elevation_gain = activity_raw.get('elevationGainUncorrected')
        
        # v3.11.0: CRITICAL - Mapeamento correto de cadência
        # Tenta múltiplos campos possíveis
        avg_cadence = None
        
        # Ciclismo
        bike_cadence = activity_raw.get('averageBikingCadenceInRevolutionsPerMinute')
        if bike_cadence is None:
            bike_cadence = activity_raw.get('averageBikingCadenceInRevPerMinute')
        
        # Corrida (steps per minute)
        run_cadence = activity_raw.get('averageRunCadence')
        if run_cadence is None:
            run_cadence = activity_raw.get('averageRunningCadenceInStepsPerMinute')
        
        # Define cadência baseado no tipo de atividade
        if bike_cadence is not None:
            avg_cadence = bike_cadence
        elif run_cadence is not None:
            avg_cadence = run_cadence
        
        max_cadence = activity_raw.get('maxBikingCadenceInRevPerMinute')
        if max_cadence is None:
            max_cadence = activity_raw.get('maxRunningCadenceInStepsPerMinute')
        
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
            bike_cadence=bike_cadence,
            raw=activity_raw
        )
        
        return formatted
        
    except Exception as e:
        logger.error(f"Erro ao parsear atividade: {e}")
        return None

def get_all_formatted_activities() -> List[FormattedActivity]:
    """v3.11.0: Obtém todas as atividades formatadas"""
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
# DATA INTEGRITY
# ==========================================
def check_activities_integrity() -> Tuple[bool, str]:
    """v3.11.0: Verifica integridade do activities.json"""
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
    """v3.11.0: Reorganiza activities.json"""
    activities = load_activities_index()
    messages = []
    
    original_count = len(activities)
    
    # Remove duplicados
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
    """v3.11.0: Verifica e enriquece atividades"""
    activities = load_activities_index()
    
    if not activities:
        logger.debug("Sem atividades para enriquecer")
        return
    
    enriched_count = 0
    
    for activity_id, data in activities.items():
        needs_enrichment = False
        
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
# GEMINI API (v3.11.0)
# ==========================================
async def call_gemini_with_timeout(prompt: str, timeout: int) -> str:
    """v3.11.0: Chama Gemini com timeout"""
    start_time = time.time()
    
    try:
        response = await asyncio.wait_for(
            asyncio.to_thread(model.generate_content, prompt),
            timeout=timeout
        )
        
        latency = time.time() - start_time
        health_state.record_latency(latency)
        health_state.total_requests += 1
        
        if not response or not response.text:
            raise Exception("Resposta vazia do Gemini")
        
        return response.text.strip()
        
    except asyncio.TimeoutError:
        health_state.total_errors += 1
        raise GeminiTimeoutError(f"Timeout após {timeout}s")
    except Exception as e:
        health_state.total_errors += 1
        logger.error(f"Erro no Gemini: {e}")
        raise

async def call_gemini_with_retry(prompt: str, user_id: int) -> str:
    """v3.11.0: Chama Gemini com retry, circuit breaker e cache"""
    
    # Verifica circuit breaker
    if not circuit_breaker.can_execute():
        raise CircuitBreakerOpen("Circuit breaker aberto")
    
    # Verifica rate limit
    if not rate_limiter.can_proceed(user_id):
        raise RateLimitExceeded("Rate limit excedido")
    
    # Verifica cache
    cached = response_cache.get(prompt, user_id)
    if cached:
        return cached
    
    # Limita tamanho
    if len(prompt) > GEMINI_MAX_PROMPT_LENGTH:
        prompt = prompt[:GEMINI_MAX_PROMPT_LENGTH]
        logger.warning(f"Prompt truncado para {GEMINI_MAX_PROMPT_LENGTH} chars")
    
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
    
    if last_error:
        raise last_error
    raise Exception("Erro desconhecido no retry loop")

# ==========================================
# TELEGRAM HANDLERS (v3.11.0)
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """v3.11.0: Handler /start"""
    await update.message.reply_text(
        f"🏋️ FitnessJournal v{BOT_VERSION}\n\n"
        f"{BOT_VERSION_DESC}\n\n"
        "Usa /help para ver comandos."
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    v3.11.0: REDESIGNED - Handler /status
    Mostra biometria PRIMEIRO, depois pede feeling
    """
    user_id = update.effective_user.id
    
    try:
        # 1. Mostra "Extraindo biometria..."
        status_msg = await update.message.reply_text("⏳ A extrair biometria...")
        
        # 2. Obtém biometria
        history = get_recent_biometrics(7)
        baseline = calculate_biometric_baseline(history)
        
        if not history:
            await status_msg.edit_text(
                "📭 Sem dados biométricos.\n\n"
                "Usa /sync ou /import primeiro."
            )
            return
        
        # 3. Formata resumo biométrico visual
        bio_summary = format_biometric_summary_for_status(history, baseline)
        
        # 4. Obtém últimas 3 atividades
        activities = get_all_formatted_activities()
        recent_activities = activities[:3] if activities else []
        
        activities_text = ""
        if recent_activities:
            activities_text = "\n\n🏃 **ÚLTIMAS ATIVIDADES:**\n"
            for act in recent_activities:
                activities_text += f"• {act.to_brief_summary()}\n"
        
        # 5. Envia resumo completo
        full_summary = bio_summary + activities_text
        
        await status_msg.edit_text(full_summary)
        
        # 6. SÓ AGORA pergunta o feeling
        await update.message.reply_text(
            "💭 Como te sentes hoje?\n\n"
            "Responde com um número de 0 a 10:\n"
            "0 = Exausto | 5 = Normal | 10 = Energizado"
        )
        
        # 7. Define estado para aguardar resposta
        session_state.set_user_state(user_id, 'waiting_feeling')
        
    except Exception as e:
        logger.error(f"Erro em /status: {e}\n{traceback.format_exc()}")
        session_state.clear_user_state(user_id)
        await update.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def process_status_with_feeling(update: Update, feeling: int):
    """
    v3.11.0: ENHANCED - Processa /status após receber o feeling
    Com texto mais "personal trainer"
    """
    user_id = update.effective_user.id
    
    try:
        await update.message.reply_text("🔍 Avaliando prontidão biológica...")
        
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
Avalia o readiness do atleta e prescreve treino.

CRÍTICO: 
- Se HRV/RHR indicarem fadiga mas o feeling for alto (>7), ALERTA para fadiga mascarada.
- Se o feeling for baixo (<5) mas biometria OK, investiga recuperação inadequada.
- Usa EXCLUSIVAMENTE os equipamentos listados.
- Para ciclistas: reforço core/postural ou endurance, NUNCA hipertrofia de máquinas comerciais.
- Mostra CÁLCULOS DE CARGA explícitos (HRV vs 95% da média).
- Prescreve usando a TABELA obrigatória.
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
    finally:
        session_state.clear_user_state(user_id)

async def activities_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """v3.11.0: Handler /activities"""
    try:
        activities = get_all_formatted_activities()
        
        if not activities:
            await update.message.reply_text(
                "📭 Sem atividades registadas.\n\n"
                "Usa /sync ou /import."
            )
            return
        
        recent = activities[:MAX_ACTIVITIES_DISPLAY]
        
        lines = [f"🏃 **{len(activities)} ATIVIDADES TOTAIS**\n"]
        lines.append(f"Últimas {len(recent)}:\n")
        
        for i, act in enumerate(recent, 1):
            lines.append(f"{i}. {act.to_brief_summary()}")
        
        await update.message.reply_text("\n".join(lines))
        
    except Exception as e:
        logger.error(f"Erro em /activities: {e}")
        await update.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def analyze_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """v3.11.0: Handler /analyze"""
    try:
        activities = get_all_formatted_activities()
        
        if not activities:
            await update.message.reply_text(
                "📭 Sem atividades.\n\n"
                "Usa /sync ou /import primeiro."
            )
            return
        
        await update.message.reply_text("🔍 Analisando aderência ao plano...")
        
        # Biometria
        history = get_recent_biometrics(7)
        baseline = calculate_biometric_baseline(history)
        bio_context = format_biometric_context(history, baseline)
        
        recent = activities[:MAX_ACTIVITIES_IN_ANALYSIS]
        
        prompt = f"""
{bio_context}

### ATIVIDADES A ANALISAR (Últimas {len(recent)}):

"""
        
        for act in recent:
            prompt += f"- {act.to_brief_summary()}\n"
        
        prompt += """

### TAREFA:
Analisa a aderência do atleta ao plano.
Identifica padrões de sobrecarga, recuperação inadequada ou desvios.
Sugere ajustes baseado nos dados biométricos.
"""
        
        user_id = update.effective_user.id
        response_text = await call_gemini_with_retry(prompt, user_id)
        
        # Envia
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
    """v3.11.0: Handler /analyze_activity"""
    try:
        activities = get_all_formatted_activities()
        
        if not activities:
            await update.message.reply_text(
                "📭 Sem atividades.\n\n"
                "Usa /sync ou /import primeiro."
            )
            return
        
        # Cria botões (últimas 5)
        recent = activities[:5]
        keyboard = []
        
        for i, act in enumerate(recent):
            button_text = f"{act.date} - {act.sport} ({act.duration_min:.0f}min)"
            callback_data = f"analyze_act_{i}"
            keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "🔍 Seleciona a atividade para analisar:",
            reply_markup=reply_markup
        )
        
    except Exception as e:
        logger.error(f"Erro em /analyze_activity: {e}")
        await update.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def analyze_activity_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """v3.11.0: REDESIGNED - Callback de análise de atividade individual com CABEÇALHO TÉCNICO"""
    query = update.callback_query
    await query.answer()
    
    try:
        # Parse do índice
        callback_data = query.data
        activity_index = int(callback_data.split('_')[-1])
        
        activities = get_all_formatted_activities()
        
        if activity_index >= len(activities):
            await query.edit_message_text("❌ Atividade não encontrada")
            return
        
        activity = activities[activity_index]
        
        # Verifica se é ciclismo
        is_cycling = 'cycling' in activity.sport.lower() or 'bike' in activity.sport.lower()
        
        if is_cycling:
            # Pergunta tipo de ciclismo
            keyboard = []
            for cycle_type in CYCLING_TYPES:
                callback = f"cycle_type_{cycle_type.lower()}_{activity_index}"
                keyboard.append([InlineKeyboardButton(cycle_type, callback_data=callback)])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"🚴 Que tipo de ciclismo foi esta atividade?\n\n"
                f"{activity.to_brief_summary()}",
                reply_markup=reply_markup
            )
        else:
            # Não é ciclismo, analisa direto
            await perform_activity_analysis(query, activity, None, None)
        
    except Exception as e:
        logger.error(f"Erro em analyze_activity_callback: {e}\n{traceback.format_exc()}")
        await query.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def cycling_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """v3.11.0: Callback de tipo de ciclismo"""
    query = update.callback_query
    await query.answer()
    
    try:
        # Parse: cycle_type_MTB_0
        parts = query.data.split('_')
        cycling_type = parts[2]
        activity_index = int(parts[3])
        
        activities = get_all_formatted_activities()
        activity = activities[activity_index]
        
        # Pergunta se tinha carga
        keyboard = [
            [InlineKeyboardButton("✅ Sim, tinha carga", callback_data=f"cargo_yes_{activity_index}")],
            [InlineKeyboardButton("❌ Não, sem carga", callback_data=f"cargo_no_{activity_index}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Salva tipo de ciclismo no contexto (temporário)
        context.user_data['cycling_type'] = cycling_type
        
        await query.edit_message_text(
            f"🎒 Levava carga ou passageiro?\n\n"
            f"{activity.to_brief_summary()}",
            reply_markup=reply_markup
        )
        
    except Exception as e:
        logger.error(f"Erro em cycling_type_callback: {e}")
        await query.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def cargo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """v3.11.0: Callback de carga"""
    query = update.callback_query
    await query.answer()
    
    try:
        # Parse: cargo_yes_0
        parts = query.data.split('_')
        has_cargo = (parts[1] == 'yes')
        activity_index = int(parts[2])
        
        activities = get_all_formatted_activities()
        activity = activities[activity_index]
        
        cycling_type = context.user_data.get('cycling_type', 'Desconhecido')
        
        await perform_activity_analysis(query, activity, cycling_type, has_cargo)
        
    except Exception as e:
        logger.error(f"Erro em cargo_callback: {e}")
        await query.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def perform_activity_analysis(query, activity: FormattedActivity, cycling_type: Optional[str], has_cargo: Optional[bool]):
    """
    v3.11.0: REDESIGNED - Executa análise com CABEÇALHO TÉCNICO
    """
    user_id = query.from_user.id
    
    try:
        await query.edit_message_text("🔍 Analisando atividade...")
        
        # v3.11.0: CRITICAL - Constrói CABEÇALHO TÉCNICO
        header_lines = []
        header_lines.append(f"📅 {activity.date} - {activity.sport}")
        
        # Linha 2: Duração e Distância
        line2_parts = [f"⏱️ Duração: {activity.duration_min:.0f}min"]
        if activity.distance_km:
            line2_parts.append(f"📏 Dist: {activity.distance_km:.1f}km")
        header_lines.append(" | ".join(line2_parts))
        
        # Linha 3: FC e Calorias
        line3_parts = []
        if activity.avg_hr:
            line3_parts.append(f"💓 FC Média: {activity.avg_hr}bpm")
        if activity.calories:
            line3_parts.append(f"🔥 Cal: {activity.calories}")
        if line3_parts:
            header_lines.append(" | ".join(line3_parts))
        
        # Linha 4: Altimetria e Cadência (só se > 0)
        line4_parts = []
        if activity.elevation_gain is not None and activity.elevation_gain > 0:
            line4_parts.append(f"🏔️ D+: {activity.elevation_gain:.0f}m")
        
        # Cadência (verifica múltiplos campos)
        cadence_value = None
        if activity.avg_cadence is not None and activity.avg_cadence > 0:
            cadence_value = activity.avg_cadence
        elif activity.bike_cadence is not None and activity.bike_cadence > 0:
            cadence_value = activity.bike_cadence
        
        if cadence_value:
            line4_parts.append(f"⚙️ Cadência: {cadence_value:.0f} RPM")
        
        if line4_parts:
            header_lines.append(" | ".join(line4_parts))
        
        technical_header = "\n".join(header_lines)
        
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

OBRIGATÓRIO:
- Se é ciclismo com carga, considera o impacto na intensidade real.
- Compara com a biometria de hoje e dias anteriores.
- Avalia eficiência da cadência (se dados disponíveis).
- Prescreve ajustes se necessário.
"""
        
        response_text = await call_gemini_with_retry(prompt, user_id)
        
        # v3.11.0: CRITICAL - Concatena CABEÇALHO TÉCNICO + Resposta IA
        full_response = f"{technical_header}\n\n{'='*40}\n\n{response_text}"
        
        # Envia resposta
        if len(full_response) <= TELEGRAM_SAFE_MESSAGE_LENGTH:
            await query.message.reply_text(full_response)
        else:
            chunks = [full_response[i:i+TELEGRAM_SAFE_MESSAGE_LENGTH] 
                     for i in range(0, len(full_response), TELEGRAM_SAFE_MESSAGE_LENGTH)]
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
    """v3.11.0: Handler /import"""
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
    """v3.11.0: Handler /sync"""
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
    """v3.11.0: Callback de confirmação de sync"""
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
    """v3.11.0: Envia feedback após sincronização"""
    try:
        completed = await wait_for_sync_completion(query_or_update, timeout_seconds=60)
        
        # Determina como enviar mensagem
        if hasattr(query_or_update, 'message'):
            message = query_or_update.message
        else:
            message = query_or_update.message
        
        if not completed:
            await message.reply_text(
                "⏱️ Sincronização ainda em progresso...\n"
                "Usa /activities para ver o estado."
            )
            return
        
        activities = get_all_formatted_activities()
        
        if activities:
            msg = (
                f"✅ Sincronização concluída!\n"
                f"📊 {len(activities)} atividades no total.\n\n"
                f"💡 Usa /status ou /analyze"
            )
        else:
            msg = "⚠️ Sincronização completou mas sem atividades encontradas."
        
        await message.reply_text(msg)
        
    except Exception as e:
        logger.error(f"Erro em send_sync_feedback: {e}")

async def cleanup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """v3.11.0: Handler /cleanup"""
    try:
        await update.message.reply_text("🧹 A limpar dados antigos...")
        
        # Limpa flags
        cleaned_flags, flag_msgs = cleanup_old_flags()
        
        # Reorganiza atividades
        duplicates, final_count, reorg_msgs = reorganize_activities()
        
        msg = f"✅ Limpeza concluída:\n"
        msg += f"• Flags: {cleaned_flags} removidos\n"
        msg += f"• Atividades: {final_count} mantidas\n"
        
        if duplicates > 0:
            msg += f"• Duplicados: {duplicates} removidos\n"
        
        await update.message.reply_text(msg)
        
    except Exception as e:
        logger.error(f"Erro em /cleanup: {e}")
        await update.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """v3.11.0: Handler /history"""
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
        
        lines = [f"📚 **HISTÓRICO ({len(history)} análises):**\n"]
        
        for entry in history[-5:]:
            timestamp = entry.get('timestamp', 'Sem data')[:16]
            command = entry.get('command', 'Desconhecido')
            lines.append(f"• {timestamp} - {command}")
        
        await update.message.reply_text("\n".join(lines))
        
    except Exception as e:
        logger.error(f"Erro em /history: {e}")
        await update.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def clear_context_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """v3.11.0: Handler /clear_context"""
    user_id = update.effective_user.id
    
    try:
        if clear_user_context(user_id):
            await update.message.reply_text("✅ Contexto limpo com sucesso.")
        else:
            await update.message.reply_text("⚠️ Sem contexto para limpar.")
    except Exception as e:
        logger.error(f"Erro em /clear_context: {e}")
        await update.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """v3.11.0: Handler /stats"""
    try:
        activities = get_all_formatted_activities()
        history = get_recent_biometrics(7)
        
        lines = [f"📊 **ESTATÍSTICAS:**\n"]
        lines.append(f"Atividades: {len(activities)}")
        lines.append(f"Dias biométricos: {len(history)}")
        
        if activities:
            total_distance = sum(a.distance_km or 0 for a in activities)
            total_duration = sum(a.duration_min for a in activities)
            
            lines.append(f"\n**Totais:**")
            lines.append(f"Distância: {total_distance:.1f} km")
            lines.append(f"Duração: {total_duration:.0f} min ({total_duration/60:.1f}h)")
        
        await update.message.reply_text("\n".join(lines))
        
    except Exception as e:
        logger.error(f"Erro em /stats: {e}")
        await update.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def debug_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """v3.11.0: Handler /debug"""
    try:
        activities = load_activities_index()
        garmin_data = load_garmin_data()
        
        lines = [f"🐛 **DEBUG INFO:**\n"]
        lines.append(f"Version: {BOT_VERSION}")
        lines.append(f"Activities type: {type(activities).__name__}")
        lines.append(f"Activities count: {len(activities)}")
        lines.append(f"Garmin data keys: {list(garmin_data.keys())[:5]}")
        
        # Integridade
        is_valid, integrity_msg = check_activities_integrity()
        lines.append(f"\n**Integridade:** {'✅' if is_valid else '❌'} {integrity_msg}")
        
        await update.message.reply_text("\n".join(lines))
        
    except Exception as e:
        logger.error(f"Erro em /debug: {e}")
        await update.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """v3.11.0: Handler /health"""
    try:
        lines = [f"🏥 **HEALTH CHECK:**\n"]
        lines.append(f"Circuit Breaker: {circuit_breaker.state}")
        lines.append(f"Total Requests: {health_state.total_requests}")
        lines.append(f"Total Errors: {health_state.total_errors}")
        
        avg_latency = health_state.get_avg_latency()
        lines.append(f"Avg Latency: {avg_latency:.2f}s")
        
        if health_state.last_error:
            lines.append(f"\n**Last Error:** {health_state.last_error[:100]}")
        
        await update.message.reply_text("\n".join(lines))
        
    except Exception as e:
        logger.error(f"Erro em /health: {e}")
        await update.message.reply_text(f"❌ Erro: {str(e)[:100]}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """v3.11.0: Handler /help"""
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
        "🆕 v3.11.0:\n"
        "• Cabeçalho técnico em análise de atividade\n"
        "• Biometria visível ANTES do feeling em /status\n"
        "• Restrição de output do Gemini (equipamentos)\n"
        "• Mapeamento correto de cadência e altimetria\n"
        "• Textos refinados (personal trainer tone)"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """v3.11.0: Handler para mensagens de texto livre"""
    user_id = update.effective_user.id
    message_text = update.message.text
    
    try:
        # Verifica se está aguardando feeling
        user_state = session_state.get_user_state(user_id)
        
        if user_state == 'waiting_feeling':
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
    """v3.11.0: Handler para comandos não reconhecidos"""
    command = update.message.text
    
    await update.message.reply_text(
        f"❓ Comando '{command}' não reconhecido.\n\n"
        "Usa /help para ver todos os comandos disponíveis."
    )

# ==========================================
# MAIN (v3.11.0)
# ==========================================
def main():
    """v3.11.0: Entry point"""
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
    
    logger.info("✅ Bot v3.11.0 iniciado com:")
    logger.info(f"  - UX RESTORATION: Cabeçalho técnico em análise")
    logger.info(f"  - UX RESTORATION: Biometria visível antes de feeling")
    logger.info(f"  - AI PRECISION: Restrição de equipamentos no Gemini")
    logger.info(f"  - DATA MAPPING: Cadência e altimetria corretos")
    logger.info(f"  - TONE: Personal trainer refinado")
    logger.info(f"  - Timeout Gemini: {GEMINI_TIMEOUT_SECONDS}s")
    logger.info(f"  - Retry delays: {RETRY_DELAYS}")
    logger.info(f"  - Circuit breaker: {CIRCUIT_BREAKER_THRESHOLD} falhas threshold")
    logger.info(f"  - Cache: {RESPONSE_CACHE_SIZE} entradas, TTL {CACHE_TTL_SECONDS}s")
    logger.info(f"  - Rate limit: {RATE_LIMIT_MAX_REQUESTS} req/{RATE_LIMIT_WINDOW}s")
    
    print(f"🤖 Bot v{BOT_VERSION} ativo")
    
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == '__main__':
    main()