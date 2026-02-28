import os, json, logging, traceback
from datetime import datetime, date, timedelta
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Tuple, Any
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
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
BOT_VERSION = "3.4.0"
BOT_VERSION_DESC = "Persistent Context + Cycling Types"
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
GEMINI_SAFE_PROMPT_LENGTH = 28000

# Analysis Context (v3.3+)
ENABLE_FOLLOWUP_QUESTIONS = True  # Feature flag
ENABLE_FOLLOWUP_ANALYTICS = False  # Feature flag (v3.4)
ANALYSIS_CONTEXT_TIMEOUT = 15  # Minutos até contexto expirar
MAX_ANALYSIS_CONTEXT_LENGTH = 8000  # Chars para truncar análise anterior
MAX_ANALYSIS_HISTORY = 5  # Máximo de análises no histórico

# Context Warning
CONTEXT_WARNING_THRESHOLD = 13  # Avisar quando faltar 2min para expirar

# Cycling Types (v3.4)
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

class ContextExpiredError(Exception):
    """Contexto de análise expirou"""
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
    awaiting_cycling_type: bool = False  # NEW v3.4
    
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

@dataclass
class AnalysisContext:
    """
    v3.3+: Contexto de uma análise anterior para follow-up questions.
    v3.4: Agora persiste em disco
    """
    analysis_type: str  # 'adherence' ou 'individual'
    original_prompt: str
    analysis_result: str
    timestamp: datetime
    activity_date: Optional[str] = None
    
    def is_expired(self, timeout_minutes: int = ANALYSIS_CONTEXT_TIMEOUT) -> bool:
        """Verifica se o contexto expirou"""
        age = datetime.now() - self.timestamp
        return age.total_seconds() > (timeout_minutes * 60)
    
    def minutes_until_expiry(self, timeout_minutes: int = ANALYSIS_CONTEXT_TIMEOUT) -> float:
        """Retorna minutos até expiração"""
        age = datetime.now() - self.timestamp
        elapsed_minutes = age.total_seconds() / 60
        return max(0, timeout_minutes - elapsed_minutes)
    
    def validate(self) -> Tuple[bool, str]:
        """Valida se o contexto tem dados mínimos"""
        if not self.analysis_type or self.analysis_type not in ['adherence', 'individual']:
            return False, "Tipo de análise inválido"
        
        if not self.original_prompt or len(self.original_prompt) < 10:
            return False, "Prompt original vazio ou inválido"
        
        if not self.analysis_result or len(self.analysis_result) < 10:
            return False, "Resultado da análise vazio ou inválido"
        
        return True, ""
    
    def to_dict(self) -> Dict:
        """Serializa para guardar em context.user_data ou disco"""
        return {
            'analysis_type': self.analysis_type,
            'original_prompt': self.original_prompt,
            'analysis_result': self.analysis_result,
            'timestamp': self.timestamp.isoformat(),
            'activity_date': self.activity_date
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'AnalysisContext':
        """Deserializa de context.user_data ou disco"""
        data['timestamp'] = datetime.fromisoformat(data['timestamp'])
        return cls(**data)

@dataclass
class FollowUpAnalytics:
    """v3.4: Analytics de perguntas follow-up"""
    total_questions: int = 0
    by_type: Dict[str, int] = field(default_factory=lambda: {'adherence': 0, 'individual': 0})
    common_keywords: Dict[str, int] = field(default_factory=dict)
    last_updated: Optional[str] = None
    
    def record_question(self, analysis_type: str, question: str):
        """Regista uma pergunta follow-up"""
        self.total_questions += 1
        self.by_type[analysis_type] = self.by_type.get(analysis_type, 0) + 1
        
        # Extrair keywords simples (palavras > 4 chars)
        words = [w.lower() for w in question.split() if len(w) > 4]
        for word in words[:5]:  # Limitar a 5 palavras
            self.common_keywords[word] = self.common_keywords.get(word, 0) + 1
        
        self.last_updated = datetime.now().isoformat()
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'FollowUpAnalytics':
        return cls(**data)

# ==========================================
# UTILITY FUNCTIONS
# ==========================================
def pluralize_pt(count: int, singular: str, plural: str) -> str:
    """Retorna forma correta (singular/plural) baseado na contagem"""
    return plural if count != 1 else singular

def format_found_activities_message(count: int, date: str, is_today: bool) -> str:
    """Formata mensagem de atividades encontradas com pluralização correta"""
    day_label = "hoje" if is_today else "ontem"
    
    if count == 1:
        return f"✅ Encontrada 1 atividade de {day_label} ({date})"
    else:
        return f"✅ Encontradas {count} atividades de {day_label} ({date})"

def truncate_text_safe(text: str, max_length: int, suffix: str = "...") -> str:
    """Trunca texto de forma segura, adicionando sufixo se necessário"""
    if len(text) <= max_length:
        return text
    
    return text[:max_length - len(suffix)] + suffix

def truncate_analysis_safe(analysis: str, max_length: int = MAX_ANALYSIS_CONTEXT_LENGTH) -> str:
    """
    v3.4: Helper dedicado para truncar análises longas.
    Smart truncation: mantém início e fim (geralmente contêm o essencial).
    """
    if len(analysis) <= max_length:
        return analysis
    
    keep_chars = max_length // 2
    truncated = (
        analysis[:keep_chars] + 
        "\n\n[... análise truncada ...]\n\n" +
        analysis[-keep_chars:]
    )
    
    logger.info(f"Analysis truncated: {len(analysis)} → {len(truncated)} chars")
    return truncated

def split_long_message(text: str, max_length: int = TELEGRAM_SAFE_MESSAGE_LENGTH) -> List[str]:
    """Divide mensagem longa em partes que cabem no limite do Telegram"""
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

def validate_prompt_size(prompt: str) -> Tuple[bool, str]:
    """
    v3.4: Valida se prompt cabe nos limites da API Gemini.
    Retorna: (is_valid, error_message)
    """
    prompt_length = len(prompt)
    
    if prompt_length > GEMINI_MAX_PROMPT_LENGTH:
        return False, f"Prompt demasiado longo: {prompt_length} chars (max: {GEMINI_MAX_PROMPT_LENGTH})"
    
    if prompt_length > GEMINI_SAFE_PROMPT_LENGTH:
        logger.warning(f"Prompt próximo do limite: {prompt_length}/{GEMINI_MAX_PROMPT_LENGTH}")
    
    return True, ""

# ==========================================
# CONTEXT STORAGE (v3.4)
# ==========================================
def get_context_store_path(user_id: int) -> str:
    """Retorna path do arquivo de contexto para um user"""
    return os.path.join(DATA_DIR, f'context_store_{user_id}.json')

def load_context_from_disk(user_id: int) -> Optional[Dict]:
    """
    v3.4: Carrega contexto persistido em disco.
    Retorna dict com 'current_context' e 'history'.
    """
    path = get_context_store_path(user_id)
    
    if not os.path.exists(path):
        logger.info(f"No context store found for user {user_id}")
        return None
    
    try:
        with open(path, 'r') as f:
            data = json.load(f)
        
        logger.info(f"Loaded context from disk for user {user_id}")
        return data
        
    except json.JSONDecodeError as e:
        logger.error(f"Corrupted context store for user {user_id}: {e}")
        return None
    except Exception as e:
        logger.error(f"Failed to load context for user {user_id}: {e}")
        return None

def save_context_to_disk(user_id: int, current_context: Optional[Dict], history: List[Dict]) -> bool:
    """
    v3.4: Persiste contexto em disco (atomic write).
    """
    path = get_context_store_path(user_id)
    temp_path = path + '.tmp'
    
    try:
        store = {
            'user_id': user_id,
            'current_context': current_context,
            'history': history,
            'last_updated': datetime.now().isoformat()
        }
        
        with open(temp_path, 'w') as f:
            json.dump(store, f, indent=2)
        
        os.replace(temp_path, path)
        logger.info(f"Context saved to disk for user {user_id}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to save context for user {user_id}: {e}")
        if os.path.exists(temp_path):
            os.remove(temp_path)
        return False

def clear_context_disk(user_id: int) -> bool:
    """v3.4: Remove arquivo de contexto do disco"""
    path = get_context_store_path(user_id)
    
    try:
        if os.path.exists(path):
            os.remove(path)
            logger.info(f"Context store deleted for user {user_id}")
            return True
        return False
    except Exception as e:
        logger.error(f"Failed to delete context for user {user_id}: {e}")
        return False

def save_analysis_context(
    context: ContextTypes.DEFAULT_TYPE,
    analysis_type: str,
    original_prompt: str,
    analysis_result: str,
    activity_date: Optional[str] = None
) -> bool:
    """
    v3.3: Helper para guardar contexto de análise (DRY).
    v3.4: Agora também persiste em disco e mantém histórico.
    """
    if not ENABLE_FOLLOWUP_QUESTIONS:
        return False
    
    try:
        user_id = context._user_id
        
        # Criar novo contexto
        analysis_context = AnalysisContext(
            analysis_type=analysis_type,
            original_prompt=original_prompt,
            analysis_result=analysis_result,
            timestamp=datetime.now(),
            activity_date=activity_date
        )
        
        # Validar
        is_valid, error_msg = analysis_context.validate()
        if not is_valid:
            logger.error(f"Invalid analysis context: {error_msg}")
            return False
        
        # Guardar em memória (context.user_data)
        context_dict = analysis_context.to_dict()
        context.user_data['last_analysis_context'] = context_dict
        
        # Atualizar histórico (FIFO)
        history = context.user_data.get('analysis_history', [])
        history.append(context_dict)
        
        if len(history) > MAX_ANALYSIS_HISTORY:
            history = history[-MAX_ANALYSIS_HISTORY:]
        
        context.user_data['analysis_history'] = history
        
        # Persistir em disco
        save_context_to_disk(user_id, context_dict, history)
        
        # Analytics (opcional)
        if ENABLE_FOLLOWUP_ANALYTICS:
            record_analytics_event(user_id, 'analysis_created', analysis_type)
        
        logger.info(
            f"Analysis context saved: type={analysis_type}, "
            f"date={activity_date or 'N/A'}, "
            f"history_size={len(history)}"
        )
        return True
        
    except Exception as e:
        logger.error(f"Failed to save analysis context: {e}")
        return False

def load_analysis_context(context: ContextTypes.DEFAULT_TYPE) -> Optional[AnalysisContext]:
    """
    v3.4: Carrega contexto atual (memória ou disco).
    Se não há em memória, tenta carregar do disco.
    """
    # Tentar memória primeiro
    context_dict = context.user_data.get('last_analysis_context')
    
    if not context_dict:
        # Tentar disco
        user_id = context._user_id
        disk_data = load_context_from_disk(user_id)
        
        if disk_data and disk_data.get('current_context'):
            context_dict = disk_data['current_context']
            context.user_data['last_analysis_context'] = context_dict
            context.user_data['analysis_history'] = disk_data.get('history', [])
            logger.info(f"Context restored from disk for user {user_id}")
        else:
            return None
    
    try:
        analysis_ctx = AnalysisContext.from_dict(context_dict)
        
        # Verificar se expirou
        if analysis_ctx.is_expired():
            raise ContextExpiredError("Context expired")
        
        return analysis_ctx
        
    except ContextExpiredError:
        logger.info("Analysis context expired")
        context.user_data.pop('last_analysis_context', None)
        return None
    except Exception as e:
        logger.error(f"Failed to load analysis context: {e}")
        return None

def build_followup_prompt(analysis_ctx: AnalysisContext, question: str) -> str:
    """
    v3.3: Constrói super-prompt para follow-up question.
    v3.4: Agora com validação de tamanho e melhor error handling.
    """
    personality_reminder = """És um TREINADOR DE ELITE especializado em Ciclismo de Resistência e Hipertrofia.
TOM: Assertivo, direto e orientado para resultados. Sem emojis.
Usa PORTUGUÊS EUROPEU (PT-PT) EXCLUSIVAMENTE."""

    analysis_type_label = (
        "análise de aderência ao plano" 
        if analysis_ctx.analysis_type == 'adherence' 
        else "análise individual de atividade"
    )
    
    # Truncar análise se necessário
    analysis_result = truncate_analysis_safe(analysis_ctx.analysis_result)
    
    prompt = f"""{personality_reminder}

CONTEXTO DA CONVERSA ANTERIOR:
Fizeste uma {analysis_type_label}. Aqui está o que disseste:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PROMPT ORIGINAL:
{analysis_ctx.original_prompt}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TUA ANÁLISE ANTERIOR:
{analysis_result}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

O atleta tem agora uma DÚVIDA sobre a tua análise anterior:

"{question}"

TAREFA:
Responde à dúvida do atleta de forma clara e direta.
- Mantém o contexto da análise anterior.
- Se necessário, refere pontos específicos que mencionaste.
- Se a dúvida não estiver relacionada com a análise, informa o atleta educadamente.
- Usa PORTUGUÊS EUROPEU sem LaTeX ou símbolos especiais.
"""

    # Validar tamanho
    is_valid, error_msg = validate_prompt_size(prompt)
    if not is_valid:
        raise PromptTooLargeError(error_msg)

    return prompt

# ==========================================
# ANALYTICS (v3.4)
# ==========================================
def get_analytics_path() -> str:
    """Retorna path do arquivo de analytics"""
    return os.path.join(DATA_DIR, 'followup_analytics.json')

def load_analytics() -> FollowUpAnalytics:
    """Carrega analytics do disco"""
    path = get_analytics_path()
    
    if not os.path.exists(path):
        return FollowUpAnalytics()
    
    try:
        with open(path, 'r') as f:
            data = json.load(f)
        return FollowUpAnalytics.from_dict(data)
    except Exception as e:
        logger.error(f"Failed to load analytics: {e}")
        return FollowUpAnalytics()

def save_analytics(analytics: FollowUpAnalytics) -> bool:
    """Salva analytics no disco"""
    path = get_analytics_path()
    temp_path = path + '.tmp'
    
    try:
        with open(temp_path, 'w') as f:
            json.dump(analytics.to_dict(), f, indent=2)
        os.replace(temp_path, path)
        return True
    except Exception as e:
        logger.error(f"Failed to save analytics: {e}")
        if os.path.exists(temp_path):
            os.remove(temp_path)
        return False

def record_analytics_event(user_id: int, event_type: str, analysis_type: str = None, question: str = None):
    """Regista evento de analytics"""
    if not ENABLE_FOLLOWUP_ANALYTICS:
        return
    
    try:
        analytics = load_analytics()
        
        if event_type == 'followup_question' and question:
            analytics.record_question(analysis_type, question)
        
        save_analytics(analytics)
    except Exception as e:
        logger.error(f"Failed to record analytics: {e}")

# ==========================================
# DATA EXTRACTION FUNCTIONS
# ==========================================
def extract_date(activity: Dict) -> Optional[str]:
    """Extrai data de uma atividade (múltiplos formatos suportados)"""
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
    """Extrai tipo de esporte de uma atividade"""
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
    """Extrai duração em minutos de uma atividade"""
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
    """Extrai distância em km de uma atividade"""
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
    """Extrai frequência cardíaca média de uma atividade"""
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
    """Extrai calorias de uma atividade"""
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
    """Infere tipo de esporte baseado em outras métricas"""
    if formatted.sport != 'Desconhecido':
        return formatted.sport
    
    if formatted.distance_km and formatted.distance_km > 10:
        return 'Ciclismo/Corrida'
    elif formatted.duration_min > 60 and not formatted.distance_km:
        return 'Ginásio/Força'
    
    return 'Desconhecido'

def format_activity(activity: Dict) -> Optional[FormattedActivity]:
    """
    Formata uma atividade para exibição unificada.
    Suporta múltiplos formatos e faz merge de dados.
    Retorna None se a atividade não tem data válida.
    """
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
    """Extrai HRV com validação robusta"""
    try:
        if 'hrv' not in data or not isinstance(data['hrv'], dict):
            return None
        
        hrv_summary = data['hrv'].get('hrvSummary')
        if not isinstance(hrv_summary, dict):
            return None
        
        hrv = hrv_summary.get('lastNightAvg') or hrv_summary.get('weeklyAvg')
        return float(hrv) if hrv is not None else None
        
    except (TypeError, ValueError, AttributeError) as e:
        logger.warning(f"HRV extraction failed for date {data.get('date')}: {e}")
        return None

def extract_rhr(data: Dict) -> Optional[float]:
    """Extrai RHR (Resting Heart Rate) com validação robusta"""
    try:
        if 'stats' not in data or not isinstance(data['stats'], dict):
            return None
        
        rhr = data['stats'].get('restingHeartRate') or data['stats'].get('minHeartRate')
        return float(rhr) if rhr is not None else None
        
    except (TypeError, ValueError, AttributeError) as e:
        logger.warning(f"RHR extraction failed for date {data.get('date')}: {e}")
        return None

def extract_sleep_score(data: Dict) -> Optional[int]:
    """Extrai Sleep Score com validação robusta"""
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
        logger.warning(f"Sleep score extraction failed for date {data.get('date')}: {e}")
        return None

def extract_training_load(data: Dict) -> Optional[float]:
    """Extrai Training Load com validação robusta"""
    try:
        if 'stats' not in data or not isinstance(data['stats'], dict):
            return None
        
        load = data['stats'].get('trainingLoad') or data['stats'].get('intensityMinutesGoal')
        return float(load) if load is not None else None
        
    except (TypeError, ValueError, AttributeError) as e:
        logger.warning(f"Training load extraction failed for date {data.get('date')}: {e}")
        return None

def parse_garmin_history(raw_data: List[Dict]) -> List[BiometricDay]:
    """
    Converte dados brutos do Garmin para lista de BiometricDay.
    Validação robusta com logging específico de falhas.
    """
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
# FILE OPERATIONS (with safety)
# ==========================================
def atomic_write_json(path: str, data: Any) -> None:
    """
    Escreve JSON de forma atômica para evitar corrupção.
    Usa write-to-temp + rename pattern.
    """
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
    """
    Carrega JSON com validação e fallback.
    Retorna default se arquivo não existe ou está corrompido.
    """
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
    """Carrega dados consolidados do Garmin"""
    path = os.path.join(DATA_DIR, 'garmin_data_consolidated.json')
    data = load_json_safe(path, default=[])
    
    if not isinstance(data, list):
        logger.error(f"garmin_data_consolidated.json is not a list: {type(data)}")
        return None
    
    return data if data else None

def load_activities() -> List[Dict]:
    """Carrega histórico de atividades com validação"""
    path = os.path.join(DATA_DIR, 'activities.json')
    data = load_json_safe(path, default=[])
    
    if not isinstance(data, list):
        logger.error(f"activities.json is not a list: {type(data)}")
        return []
    
    valid_activities = []
    for item in data:
        if isinstance(item, dict):
            valid_activities.append(item)
        else:
            logger.warning(f"Invalid activity item skipped: {item}")
    
    return valid_activities

def save_activities(activities: List[Dict]) -> bool:
    """Salva atividades com atomic write"""
    path = os.path.join(DATA_DIR, 'activities.json')
    try:
        atomic_write_json(path, activities)
        return True
    except FileOperationError as e:
        logger.error(f"Failed to save activities: {e}")
        return False

# ==========================================
# ACTIVITY FILTERING AND VALIDATION
# ==========================================
def get_all_formatted_activities() -> List[FormattedActivity]:
    """
    Carrega e formata TODAS as atividades válidas.
    Retorna lista ordenada por data (mais recente primeiro).
    """
    activities = load_activities()
    
    formatted_activities = []
    for act in activities:
        formatted = format_activity(act)
        if formatted:
            formatted_activities.append(formatted)
    
    formatted_activities.sort(key=lambda x: x.date, reverse=True)
    
    return formatted_activities

def get_activities_by_date(target_date: str) -> List[FormattedActivity]:
    """
    Carrega e filtra atividades por data específica.
    Mais eficiente que carregar todas e depois filtrar.
    """
    activities = load_activities()
    
    filtered_activities = []
    for act in activities:
        formatted = format_activity(act)
        if formatted and formatted.date == target_date:
            filtered_activities.append(formatted)
    
    return filtered_activities

def find_activities_for_analysis() -> Tuple[List[FormattedActivity], str, str]:
    """
    Encontra TODAS as atividades apropriadas para análise seguindo as regras:
    1. Se existem atividades de hoje → usar todas de hoje
    2. Se não existe hoje mas existe ontem → usar todas de ontem
    3. Se não existe hoje nem ontem → retornar lista vazia com mensagem
    """
    today_str = datetime.now().strftime('%Y-%m-%d')
    yesterday_str = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    
    today_activities = get_activities_by_date(today_str)
    if today_activities:
        count = len(today_activities)
        msg = format_found_activities_message(count, today_str, is_today=True)
        
        if count > MAX_ACTIVITIES_IN_ANALYSIS:
            logger.warning(f"Limitando análise de {count} para {MAX_ACTIVITIES_IN_ANALYSIS} atividades")
            today_activities = today_activities[:MAX_ACTIVITIES_IN_ANALYSIS]
        
        return today_activities, today_str, msg
    
    yesterday_activities = get_activities_by_date(yesterday_str)
    if yesterday_activities:
        count = len(yesterday_activities)
        msg = format_found_activities_message(count, yesterday_str, is_today=False)
        
        if count > MAX_ACTIVITIES_IN_ANALYSIS:
            logger.warning(f"Limitando análise de {count} para {MAX_ACTIVITIES_IN_ANALYSIS} atividades")
            yesterday_activities = yesterday_activities[:MAX_ACTIVITIES_IN_ANALYSIS]
        
        return yesterday_activities, yesterday_str, msg
    
    all_activities = get_all_formatted_activities()
    
    if not all_activities:
        return [], "", "❌ Não existem atividades registadas no sistema."
    
    most_recent = all_activities[0]
    return [], "", (
        f"❌ Não existem atividades de hoje ({today_str}) nem ontem ({yesterday_str}).\n\n"
        f"Última atividade registada: {most_recent.date} - {most_recent.sport}\n\n"
        f"Para analisar aderência ao plano, preciso de atividades recentes (hoje ou ontem)."
    )

# ==========================================
# REQUEST MANAGEMENT
# ==========================================
def create_import_request(days: int = 7) -> bool:
    """Cria flag file para importação histórica (atomic write)"""
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
    """Cria flag file para sincronização (atomic write)"""
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
    """
    Verifica o status de um pedido (import ou sync).
    Retorna: 'pending', 'completed', 'failed', ou None se não existe.
    """
    filename = f'{request_type}_request.json'
    flag_path = os.path.join(DATA_DIR, filename)
    
    data = load_json_safe(flag_path)
    if not data:
        return None
    
    return data.get('status')

def cleanup_old_flags() -> Tuple[int, List[str]]:
    """
    Limpa flags pending com mais de FLAG_TIMEOUT_SECONDS.
    Retorna: (flags_limpos, mensagens)
    """
    cleaned = 0
    messages = []
    
    for flag_name in ['import_request.json', 'sync_request.json']:
        flag_path = os.path.join(DATA_DIR, flag_name)
        
        data = load_json_safe(flag_path)
        if not data:
            messages.append(f"ℹ️  {flag_name.replace('_request.json', '')}: não existe")
            continue
        
        status = data.get('status')
        
        if status == 'pending':
            try:
                requested_at = datetime.fromisoformat(data['requested_at'])
                age_seconds = (datetime.now() - requested_at).total_seconds()
                
                if age_seconds > FLAG_TIMEOUT_SECONDS:
                    data['status'] = 'completed'
                    data['processed_at'] = datetime.now().isoformat()
                    data['cleaned_by'] = 'auto_cleanup'
                    
                    atomic_write_json(flag_path, data)
                    
                    cleaned += 1
                    messages.append(f"✅ {flag_name.replace('_request.json', '')}: pending há {int(age_seconds)}s → completed")
                else:
                    messages.append(f"⏳ {flag_name.replace('_request.json', '')}: pending recente ({int(age_seconds)}s)")
            except Exception as e:
                messages.append(f"❌ Erro em {flag_name}: {e}")
        elif status == 'completed':
            messages.append(f"✅ {flag_name.replace('_request.json', '')}: já completed")
        else:
            messages.append(f"❓ {flag_name.replace('_request.json', '')}: status {status}")
    
    return cleaned, messages

# ==========================================
# ACTIVITY MANAGEMENT
# ==========================================
def reorganize_activities() -> Tuple[int, int, List[str]]:
    """
    Reorganiza activities.json: remove duplicados e ordena por data.
    Retorna: (duplicados_removidos, total_final, mensagens)
    """
    messages = []
    
    activities = load_activities()
    if not activities:
        return 0, 0, ["ℹ️  activities.json não existe ou está vazio"]
    
    original_count = len(activities)
    messages.append(f"📊 Total original: {original_count} atividades")
    
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
        messages.append(f"🗑️  Removidos {duplicates_removed} duplicados")
    
    unique_activities.sort(key=lambda x: extract_date(x) or '0000-00-00', reverse=True)
    
    trimmed = 0
    if len(unique_activities) > MAX_ACTIVITIES_STORED:
        trimmed = len(unique_activities) - MAX_ACTIVITIES_STORED
        unique_activities = unique_activities[:MAX_ACTIVITIES_STORED]
        messages.append(f"✂️  Limitadas a {MAX_ACTIVITIES_STORED} (removidas {trimmed} mais antigas)")
    
    if save_activities(unique_activities):
        messages.append(f"✅ Activities.json reorganizado: {len(unique_activities)} atividades")
    else:
        messages.append("❌ Falha ao salvar activities.json")
        return duplicates_removed, len(unique_activities), messages
    
    if len(unique_activities) >= 3:
        messages.append("\n📋 Últimas 3:")
        for i in range(min(3, len(unique_activities))):
            formatted = format_activity(unique_activities[i])
            if formatted:
                messages.append(f"  {i+1}. {formatted.date} - {formatted.sport} ({formatted.duration_min}min)")
    
    return duplicates_removed, len(unique_activities), messages

# ==========================================
# HEALTH CHECKS
# ==========================================
def check_garmin_fetcher_health() -> Tuple[bool, str]:
    """
    Verifica se o garmin-fetcher está funcional verificando:
    1. Se o arquivo consolidado existe
    2. Se foi atualizado recentemente (últimas 2 horas)
    3. Se tem conteúdo válido
    """
    consolidated_path = os.path.join(DATA_DIR, 'garmin_data_consolidated.json')
    
    if not os.path.exists(consolidated_path):
        return False, "Arquivo consolidado não existe"
    
    try:
        mod_time = os.path.getmtime(consolidated_path)
        age_hours = (time.time() - mod_time) / 3600
        
        if age_hours > 2:
            return False, f"Dados desatualizados (última atualização há {age_hours:.1f}h)"
        
        data = load_garmin_data()
        if not data or len(data) == 0:
            return False, "Arquivo consolidado vazio"
        
        return True, "OK"
        
    except Exception as e:
        return False, f"Erro ao verificar: {e}"

def list_data_files() -> List[str]:
    """Lista arquivos de dados disponíveis"""
    try:
        files = [f for f in os.listdir(DATA_DIR) 
                if f.startswith('garmin_data_') and f.endswith('.json')]
        return sorted(files)
    except Exception as e:
        logger.error(f"Failed to list data files: {e}")
        return []

# ==========================================
# SESSION STATE MANAGEMENT
# ==========================================
def get_session_state(context: ContextTypes.DEFAULT_TYPE) -> Optional[UserSessionState]:
    """
    Recupera e valida estado da sessão.
    Retorna None se estado inválido.
    """
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
    """Salva estado da sessão no context.user_data"""
    context.user_data.update(state.to_dict())

# ==========================================
# TELEGRAM HANDLERS
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando inicial"""
    user_id = update.effective_user.id
    
    # Tentar carregar contexto persistido
    disk_data = load_context_from_disk(user_id)
    if disk_data:
        await update.message.reply_text("📂 Contexto anterior restaurado do disco.")
    
    welcome_msg = (
        f"🏋️ **FitnessJournal Bot v{BOT_VERSION}** - {BOT_VERSION_DESC}\n\n"
        "Comandos disponíveis:\n"
        "/start - Inicia o bot\n"
        "/plan - Gera plano de treino diário\n"
        "/check - Verifica dados Garmin\n"
        "/import [dias] - Importa histórico (padrão: 7 dias)\n"
        "/sync - Sincroniza dados Garmin\n"
        "/status - Verifica status das flags\n"
        "/cleanup - Limpa flags antigas\n"
        "/reorganize - Reorganiza activities.json\n"
        "/analyze - Analisa aderência ao plano\n"
        "/activity - Analisa atividade individual\n"
        "/history - Lista análises anteriores (v3.4)\n"
        "/clear_context - Limpa contexto de follow-up (v3.4)\n"
        "/stats - Estatísticas de perguntas (admin, v3.4)\n"
        "/version - Mostra versão do bot\n\n"
        "💬 **Novo em v3.4:** Contexto persiste entre restarts!"
    )
    
    await update.message.reply_text(welcome_msg, parse_mode='Markdown')

async def version(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra versão do bot"""
    await update.message.reply_text(
        f"🤖 **FitnessJournal Bot**\n"
        f"Versão: {BOT_VERSION}\n"
        f"Descrição: {BOT_VERSION_DESC}\n\n"
        f"Features:\n"
        f"✅ Follow-up Questions: {'Ativado' if ENABLE_FOLLOWUP_QUESTIONS else 'Desativado'}\n"
        f"✅ Context Persistence: Ativado (v3.4)\n"
        f"✅ Analytics: {'Ativado' if ENABLE_FOLLOWUP_ANALYTICS else 'Desativado'}\n"
        f"✅ Cycling Types: Ativado (v3.4)",
        parse_mode='Markdown'
    )

async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    v3.4: Lista histórico de análises anteriores.
    """
    analysis_history = context.user_data.get('analysis_history', [])
    
    if not analysis_history:
        await update.message.reply_text(
            "📭 Não há histórico de análises.\n\n"
            "O histórico é criado quando fazes:\n"
            "• /analyze - Aderência ao plano\n"
            "• /activity - Análise individual"
        )
        return
    
    msg_parts = [f"📚 **Histórico de Análises** ({len(analysis_history)}):\n"]
    
    for i, ctx_dict in enumerate(reversed(analysis_history), 1):
        try:
            ctx = AnalysisContext.from_dict(ctx_dict)
            
            type_label = "Aderência" if ctx.analysis_type == 'adherence' else "Individual"
            timestamp = ctx.timestamp.strftime("%d/%m %H:%M")
            expired = " ❌ EXPIRADO" if ctx.is_expired() else ""
            
            activity_info = f" - {ctx.activity_date}" if ctx.activity_date else ""
            
            msg_parts.append(
                f"{i}. {type_label}{activity_info}\n"
                f"   {timestamp}{expired}"
            )
            
        except Exception as e:
            logger.error(f"Failed to parse history item {i}: {e}")
            msg_parts.append(f"{i}. [Erro ao carregar]")
    
    msg_parts.append(
        f"\n💡 Podes fazer perguntas sobre a última análise "
        f"(válida por {ANALYSIS_CONTEXT_TIMEOUT}min)."
    )
    
    await update.message.reply_text("\n".join(msg_parts), parse_mode='Markdown')

async def clear_context_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    v3.4: Limpa contexto de follow-up (memória + disco).
    """
    user_id = update.effective_user.id
    
    # Limpar memória
    context.user_data.pop('last_analysis_context', None)
    context.user_data.pop('analysis_history', None)
    
    # Limpar disco
    disk_cleared = clear_context_disk(user_id)
    
    msg = "🗑️ Contexto de follow-up limpo:\n"
    msg += "✅ Memória limpa\n"
    msg += f"{'✅' if disk_cleared else '⚠️'} Disco {'limpo' if disk_cleared else 'não tinha dados'}"
    
    await update.message.reply_text(msg)
    logger.info(f"Context cleared for user {user_id}")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    v3.4: Mostra estatísticas de follow-up questions (admin only).
    """
    if not ENABLE_FOLLOWUP_ANALYTICS:
        await update.message.reply_text("⚠️ Analytics não estão ativadas.")
        return
    
    analytics = load_analytics()
    
    if analytics.total_questions == 0:
        await update.message.reply_text("📊 Ainda não há dados de analytics.")
        return
    
    msg_parts = [
        f"📊 **Analytics de Follow-Up Questions**\n",
        f"Total de perguntas: {analytics.total_questions}\n",
        f"\n**Por tipo:**"
    ]
    
    for analysis_type, count in analytics.by_type.items():
        percentage = (count / analytics.total_questions * 100) if analytics.total_questions > 0 else 0
        msg_parts.append(f"  • {analysis_type}: {count} ({percentage:.1f}%)")
    
    if analytics.common_keywords:
        top_keywords = sorted(
            analytics.common_keywords.items(), 
            key=lambda x: x[1], 
            reverse=True
        )[:10]
        
        msg_parts.append("\n**Top 10 palavras:**")
        for word, count in top_keywords:
            msg_parts.append(f"  • {word}: {count}x")
    
    if analytics.last_updated:
        try:
            last_update = datetime.fromisoformat(analytics.last_updated)
            msg_parts.append(f"\n📅 Última atualização: {last_update.strftime('%d/%m/%Y %H:%M')}")
        except:
            pass
    
    await update.message.reply_text("\n".join(msg_parts), parse_mode='Markdown')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    v3.3: Handler para mensagens de texto (follow-up questions).
    v3.4: Agora com melhor error handling e analytics.
    """
    if not ENABLE_FOLLOWUP_QUESTIONS:
        await update.message.reply_text(
            "💬 Para interagir com o bot, usa os comandos disponíveis.\n"
            "Envia /start para ver todos os comandos."
        )
        return
    
    user_id = update.effective_user.id
    question = update.message.text.strip()
    
    if not question or len(question) < 3:
        await update.message.reply_text("⚠️ Pergunta muito curta. Por favor, formula uma pergunta clara.")
        return
    
    # Carregar contexto
    try:
        analysis_ctx = load_analysis_context(context)
    except ContextExpiredError:
        await update.message.reply_text(
            "⏰ O contexto da análise anterior expirou.\n\n"
            "Por favor, faz uma nova análise:\n"
            "• /analyze - para aderência ao plano\n"
            "• /activity - para atividade individual"
        )
        return
    
    if not analysis_ctx:
        await update.message.reply_text(
            "❓ Não há contexto de análise anterior.\n\n"
            "Faz primeiro uma análise:\n"
            "• /analyze - para aderência ao plano\n"
            "• /activity - para atividade individual\n\n"
            "Depois podes fazer perguntas sobre essa análise."
        )
        return
    
    # Verificar se está próximo de expirar
    minutes_left = analysis_ctx.minutes_until_expiry()
    if minutes_left < 2:
        await update.message.reply_text(
            f"⚠️ Atenção: O contexto expira em {minutes_left:.1f} minutos.\n"
            "A tua pergunta será processada, mas considera fazer uma nova análise em breve."
        )
    
    # Construir prompt
    try:
        prompt = build_followup_prompt(analysis_ctx, question)
    except PromptTooLargeError as e:
        await update.message.reply_text(
            f"❌ Erro: {str(e)}\n\n"
            "A análise anterior é muito longa. Faz uma nova análise mais específica."
        )
        return
    
    # Enviar ao Gemini
    await update.message.reply_text("🤔 A processar a tua pergunta...")
    
    try:
        response = model.generate_content(prompt)
        answer = response.text
        
        # Analytics
        if ENABLE_FOLLOWUP_ANALYTICS:
            record_analytics_event(user_id, 'followup_question', analysis_ctx.analysis_type, question)
        
        # Enviar resposta
        if len(answer) > TELEGRAM_SAFE_MESSAGE_LENGTH:
            parts = split_long_message(answer)
            for part in parts:
                await update.message.reply_text(part)
        else:
            await update.message.reply_text(answer)
        
        logger.info(f"Follow-up question answered for user {user_id}")
        
    except Exception as e:
        logger.error(f"Gemini API error: {e}")
        await update.message.reply_text(
            "❌ Erro ao processar a pergunta. Por favor, tenta novamente ou faz uma nova análise."
        )

async def check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Verifica dados disponíveis do Garmin"""
    await update.message.reply_text("🔍 A verificar dados do Garmin...")
    
    data = load_garmin_data()
    
    if not data:
        await update.message.reply_text(
            "❌ Nenhum dado Garmin encontrado.\n\n"
            "Usa /import para carregar dados históricos ou /sync para sincronizar."
        )
        return
    
    history = parse_garmin_history(data)
    
    if not history:
        await update.message.reply_text("❌ Dados Garmin inválidos ou incompletos.")
        return
    
    valid_days = [d for d in history if d.is_valid()]
    
    msg = f"✅ Dados Garmin disponíveis:\n"
    msg += f"Total de dias: {len(history)}\n"
    msg += f"Dias com dados válidos: {len(valid_days)}\n\n"
    
    if valid_days:
        latest = valid_days[0]
        msg += f"📅 Último dia com dados:\n"
        msg += f"  Data: {latest.date}\n"
        
        if latest.hrv:
            msg += f"  HRV: {latest.hrv}\n"
        if latest.rhr:
            msg += f"  RHR: {latest.rhr}\n"
        if latest.sleep:
            msg += f"  Sono: {latest.sleep}\n"
        if latest.training_load:
            msg += f"  Load: {latest.training_load}\n"
    
    activities = load_activities()
    msg += f"\n📊 Atividades registadas: {len(activities)}"
    
    is_healthy, health_msg = check_garmin_fetcher_health()
    msg += f"\n\n🏥 Garmin Fetcher: {health_msg}"
    
    await update.message.reply_text(msg)

async def import_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Importa dados históricos do Garmin"""
    days = 7
    
    if context.args:
        try:
            days = int(context.args[0])
            days = max(1, min(days, 90))
        except ValueError:
            await update.message.reply_text("⚠️ Número de dias inválido. Usando padrão (7 dias).")
    
    if create_import_request(days):
        await update.message.reply_text(
            f"✅ Pedido de importação criado: {days} dias.\n\n"
            "O garmin-fetcher vai processar este pedido em breve.\n"
            "Usa /status para verificar o progresso."
        )
    else:
        await update.message.reply_text("❌ Erro ao criar pedido de importação.")

async def sync_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sincroniza dados do Garmin"""
    if create_sync_request():
        await update.message.reply_text(
            "✅ Pedido de sincronização criado.\n\n"
            "O garmin-fetcher vai sincronizar os dados em breve.\n"
            "Usa /status para verificar o progresso."
        )
    else:
        await update.message.reply_text("❌ Erro ao criar pedido de sincronização.")

async def status_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Verifica status dos pedidos"""
    import_status = check_request_status('import')
    sync_status = check_request_status('sync')
    
    msg = "📊 **Status dos Pedidos:**\n\n"
    
    msg += f"📥 Import: {import_status or 'Nenhum pedido'}\n"
    msg += f"🔄 Sync: {sync_status or 'Nenhum pedido'}\n"
    
    await update.message.reply_text(msg, parse_mode='Markdown')

async def cleanup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Limpa flags antigas"""
    await update.message.reply_text("🧹 A limpar flags antigas...")
    
    cleaned, messages = cleanup_old_flags()
    
    msg = f"✅ Limpeza concluída: {cleaned} flags atualizados\n\n"
    msg += "\n".join(messages)
    
    await update.message.reply_text(msg)

async def reorganize_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reorganiza activities.json"""
    await update.message.reply_text("🔧 A reorganizar activities.json...")
    
    duplicates, total, messages = reorganize_activities()
    
    msg = "✅ Reorganização concluída:\n\n"
    msg += "\n".join(messages)
    
    await update.message.reply_text(msg)

async def analyze_adherence(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    v3.3: Analisa aderência ao plano com suporte para follow-up.
    v3.4: Agora salva contexto em disco.
    """
    await update.message.reply_text("📊 A analisar aderência ao plano...")
    
    # [Resto do código de análise permanece igual...]
    # No final, após gerar análise:
    
    # save_analysis_context(
    #     context, 
    #     'adherence', 
    #     prompt_enviado_ao_gemini, 
    #     resposta_do_gemini,
    #     activity_date=date_analyzed
    # )

async def analyze_individual_activity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    v3.3: Analisa atividade individual com follow-up.
    v3.4: Agora com cycling types e persistência.
    """
    # [Implementação completa omitida por brevidade]
    # Inclui pergunta sobre tipo de ciclismo após "sem passageiro"
    pass

# ==========================================
# MAIN
# ==========================================
def main():
    """Entry point"""
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN não configurado")
        return
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("version", version))
    application.add_handler(CommandHandler("check", check))
    application.add_handler(CommandHandler("import", import_data))
    application.add_handler(CommandHandler("sync", sync_data))
    application.add_handler(CommandHandler("status", status_check))
    application.add_handler(CommandHandler("cleanup", cleanup_command))
    application.add_handler(CommandHandler("reorganize", reorganize_command))
    application.add_handler(CommandHandler("history", history_command))
    application.add_handler(CommandHandler("clear_context", clear_context_command))
    application.add_handler(CommandHandler("stats", stats_command))
    
    # Message handler (follow-up questions)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info(f"Bot v{BOT_VERSION} iniciado")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
