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
BOT_VERSION = "3.3"
BOT_VERSION_DESC = "Follow-Up Questions"
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

# Analysis Context (NEW in v3.3)
ENABLE_FOLLOWUP_QUESTIONS = True  # Feature flag
ANALYSIS_CONTEXT_TIMEOUT = 15  # Minutos até contexto expirar
MAX_ANALYSIS_CONTEXT_LENGTH = 8000  # Chars para truncar análise anterior se necessário

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

@dataclass
class AnalysisContext:
    """
    NEW in v3.3: Contexto de uma análise anterior para follow-up questions.
    
    Guarda o prompt original, a resposta do Gemini e metadata para permitir
    que o utilizador faça perguntas de seguimento com contexto completo.
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
        """Serializa para guardar em context.user_data"""
        return {
            'analysis_type': self.analysis_type,
            'original_prompt': self.original_prompt,
            'analysis_result': self.analysis_result,
            'timestamp': self.timestamp.isoformat(),
            'activity_date': self.activity_date
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'AnalysisContext':
        """Deserializa de context.user_data"""
        data['timestamp'] = datetime.fromisoformat(data['timestamp'])
        return cls(**data)

# ==========================================
# UTILITY FUNCTIONS
# ==========================================
def pluralize_pt(count: int, singular: str, plural: str) -> str:
    """
    Retorna forma correta (singular/plural) baseado na contagem.
    
    Exemplos:
        pluralize_pt(1, "atividade", "atividades") → "atividade"
        pluralize_pt(2, "atividade", "atividades") → "atividades"
    """
    return plural if count != 1 else singular

def format_found_activities_message(count: int, date: str, is_today: bool) -> str:
    """
    Formata mensagem de atividades encontradas com pluralização correta.
    
    Args:
        count: Número de atividades
        date: Data no formato YYYY-MM-DD
        is_today: Se True, usa "hoje", senão "ontem"
    """
    day_label = "hoje" if is_today else "ontem"
    
    if count == 1:
        return f"✅ Encontrada 1 atividade de {day_label} ({date})"
    else:
        return f"✅ Encontradas {count} atividades de {day_label} ({date})"

def truncate_text_safe(text: str, max_length: int, suffix: str = "...") -> str:
    """
    Trunca texto de forma segura, adicionando sufixo se necessário.
    
    Args:
        text: Texto a truncar
        max_length: Comprimento máximo
        suffix: Sufixo a adicionar quando truncado
    """
    if len(text) <= max_length:
        return text
    
    return text[:max_length - len(suffix)] + suffix

def split_long_message(text: str, max_length: int = TELEGRAM_SAFE_MESSAGE_LENGTH) -> List[str]:
    """
    Divide mensagem longa em partes que cabem no limite do Telegram.
    
    Args:
        text: Texto a dividir
        max_length: Tamanho máximo de cada parte
        
    Returns:
        Lista de strings, cada uma com comprimento <= max_length
    """
    if len(text) <= max_length:
        return [text]
    
    parts = []
    current_pos = 0
    
    while current_pos < len(text):
        end_pos = current_pos + max_length
        
        if end_pos >= len(text):
            parts.append(text[current_pos:])
            break
        
        # Tentar quebrar em newline mais próximo
        chunk = text[current_pos:end_pos]
        last_newline = chunk.rfind('\n')
        
        if last_newline > max_length * 0.5:
            parts.append(text[current_pos:current_pos + last_newline])
            current_pos += last_newline + 1
        else:
            parts.append(chunk)
            current_pos = end_pos
    
    return parts

def save_analysis_context(
    context: ContextTypes.DEFAULT_TYPE,
    analysis_type: str,
    original_prompt: str,
    analysis_result: str,
    activity_date: Optional[str] = None
) -> bool:
    """
    NEW in v3.3: Helper para guardar contexto de análise (DRY).
    
    Args:
        context: Telegram context
        analysis_type: 'adherence' ou 'individual'
        original_prompt: Prompt enviado ao Gemini
        analysis_result: Resposta do Gemini
        activity_date: Data da atividade (opcional)
        
    Returns:
        True se guardado com sucesso, False caso contrário
    """
    if not ENABLE_FOLLOWUP_QUESTIONS:
        return False
    
    try:
        analysis_context = AnalysisContext(
            analysis_type=analysis_type,
            original_prompt=original_prompt,
            analysis_result=analysis_result,
            timestamp=datetime.now(),
            activity_date=activity_date
        )
        
        # Validar antes de guardar
        is_valid, error_msg = analysis_context.validate()
        if not is_valid:
            logger.error(f"Invalid analysis context: {error_msg}")
            return False
        
        context.user_data['last_analysis_context'] = analysis_context.to_dict()
        
        logger.info(
            f"Analysis context saved: type={analysis_type}, "
            f"date={activity_date or 'N/A'}, "
            f"expires_in={ANALYSIS_CONTEXT_TIMEOUT}min"
        )
        return True
        
    except Exception as e:
        logger.error(f"Failed to save analysis context: {e}")
        return False

def build_followup_prompt(analysis_ctx: AnalysisContext, question: str) -> str:
    """
    NEW in v3.3: Constrói super-prompt para follow-up question.
    
    Inclui:
    1. Reforço da personalidade (do SYSTEM_PROMPT)
    2. Prompt original da análise
    3. Resposta que o Gemini deu
    4. Nova pergunta do utilizador
    
    Smart truncation: Se análise anterior for muito longa, trunca mas mantém
    os primeiros e últimos 2000 caracteres (geralmente contêm o essencial).
    
    Args:
        analysis_ctx: Contexto da análise anterior
        question: Pergunta do utilizador
        
    Returns:
        Prompt completo para o Gemini
    """
    
    # Resumo da personalidade (extraído do SYSTEM_PROMPT)
    personality_reminder = """És um TREINADOR DE ELITE especializado em Ciclismo de Resistência e Hipertrofia.
TOM: Assertivo, direto e orientado para resultados. Sem emojis.
Usa PORTUGUÊS EUROPEU (PT-PT) EXCLUSIVAMENTE."""

    # Label do tipo de análise
    analysis_type_label = (
        "análise de aderência ao plano" 
        if analysis_ctx.analysis_type == 'adherence' 
        else "análise individual de atividade"
    )
    
    # Smart truncation da análise anterior se necessário
    analysis_result = analysis_ctx.analysis_result
    if len(analysis_result) > MAX_ANALYSIS_CONTEXT_LENGTH:
        # Manter início e fim (geralmente têm o essencial)
        keep_chars = MAX_ANALYSIS_CONTEXT_LENGTH // 2
        analysis_result = (
            analysis_result[:keep_chars] + 
            "\n\n[... análise truncada ...]\n\n" +
            analysis_result[-keep_chars:]
        )
        logger.info(f"Analysis result truncated from {len(analysis_ctx.analysis_result)} to {len(analysis_result)} chars")
    
    # Construir super-prompt
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

    return prompt

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
    
    Args:
        target_date: Data no formato YYYY-MM-DD
        
    Returns:
        Lista de atividades formatadas da data especificada
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
    
    Retorna: (lista_atividades, data_analisada, mensagem_contexto)
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
    await update.message.reply_text(
        f"🏋️
