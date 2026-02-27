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
# CONFIGURATION
# ==========================================
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
logger.info("Gemini API configurada")

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
DATA_DIR = '/data'
EQUIPAMENTOS_GIM = [
    "Elástico", "Máquina Remo", "Haltere 25kg max", 
    "Barra olímpica 45kg max", "Kettlebell 12kg", 
    "Bicicleta Spinning", "Banco musculação/Supino"
]

# Constants
MAX_FEELING_LENGTH = 500
MAX_ACTIVITIES_DISPLAY = 10
MAX_ACTIVITIES_STORED = 100
CACHE_TTL_SECONDS = 60
FLAG_TIMEOUT_SECONDS = 300

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
            return False, "Dados biométricos de hoje inválidos ou faltando"
        
        if not self.history:
            return False, "Histórico biométrico vazio"
        
        if self.bike is None:
            return False, "Decisão de ciclismo não registada"
        
        return True, ""
    
    def to_dict(self) -> Dict:
        """Converte para dict para armazenar em context.user_data"""
        data = asdict(self)
        # Converter BiometricDay objects para dicts
        data['today'] = asdict(self.today)
        data['history'] = [asdict(h) for h in self.history]
        data['formatted_activities'] = [asdict(a) for a in self.formatted_activities]
        return data
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'UserSessionState':
        """Reconstrói UserSessionState de dict"""
        # Reconstruir BiometricDay objects
        data['today'] = BiometricDay(**data['today'])
        data['history'] = [BiometricDay(**h) for h in data['history']]
        data['formatted_activities'] = [FormattedActivity(**a) for a in data['formatted_activities']]
        return cls(**data)

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
    # Prioridade 1: Campo 'sport' (manual)
    if 'sport' in activity and activity['sport']:
        sport = activity['sport']
    # Prioridade 2: activityName (Garmin)
    elif 'activityName' in activity and activity['activityName']:
        sport = activity['activityName']
    # Prioridade 3: activityType (Garmin)
    elif 'activityType' in activity:
        act_type = activity['activityType']
        if isinstance(act_type, dict):
            sport = act_type.get('typeKey') or act_type.get('typeId') or 'Desconhecido'
        else:
            sport = str(act_type) if act_type else 'Desconhecido'
    # Prioridade 4: sportType (Garmin)
    elif 'sportType' in activity:
        sport_obj = activity['sportType']
        if isinstance(sport_obj, dict):
            sport = sport_obj.get('sportTypeKey') or sport_obj.get('sportTypeId') or 'Desconhecido'
        else:
            sport = str(sport_obj) if sport_obj else 'Desconhecido'
    else:
        sport = 'Desconhecido'
    
    # Limpar nome
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
    
    # Se < 500, assumir que já está em minutos
    # Se >= 500, assumir que está em segundos
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
    
    # Se < 100, assumir que já está em km
    # Se >= 100, assumir que está em metros
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
    
    # Se não tem data, não é uma atividade válida
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
    
    # Inferir sport se necessário
    formatted.sport = infer_missing_sport(formatted)
    
    # Arredondar duração
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
        os.replace(temp_path, path)  # Atomic on POSIX
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
        # Criar backup do arquivo corrompido
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
    
    # Validar que cada item é um dict
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
        if formatted:  # Apenas atividades com data válida
            formatted_activities.append(formatted)
    
    # Ordenar por data (mais recente primeiro)
    formatted_activities.sort(key=lambda x: x.date, reverse=True)
    
    return formatted_activities

def find_activity_for_analysis() -> Tuple[Optional[FormattedActivity], str]:
    """
    Encontra a atividade apropriada para análise seguindo as regras:
    1. Se existe atividade de hoje → usar hoje
    2. Se não existe hoje mas existe ontem → usar ontem
    3. Se não existe hoje nem ontem → retornar None com mensagem
    
    Retorna: (atividade, mensagem_contexto)
    """
    today_str = datetime.now().strftime('%Y-%m-%d')
    yesterday_str = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    
    all_activities = get_all_formatted_activities()
    
    if not all_activities:
        return None, "❌ Não existem atividades registadas no sistema."
    
    # Procurar atividade de hoje
    today_activities = [act for act in all_activities if act.date == today_str]
    if today_activities:
        return today_activities[0], f"✅ Encontrada atividade de hoje ({today_str})"
    
    # Procurar atividade de ontem
    yesterday_activities = [act for act in all_activities if act.date == yesterday_str]
    if yesterday_activities:
        return yesterday_activities[0], f"ℹ️  Não há atividade de hoje. A usar atividade de ontem ({yesterday_str})"
    
    # Não encontrou hoje nem ontem
    most_recent = all_activities[0]
    return None, (
        f"❌ Não existem atividades de hoje ({today_str}) nem ontem ({yesterday_str}).\n\n"
        f"Última atividade registada: {most_recent.date} - {most_recent.sport}\n\n"
        f"Para analisar aderência ao plano, preciso de uma atividade recente (hoje ou ontem)."
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
    
    # Remover duplicados usando ID
    seen_ids = set()
    unique_activities = []
    
    for act in activities:
        act_id = act.get('activityId') or act.get('id')
        
        # Se não tem ID, usar combinação data+sport+duração
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
    
    # Ordenar por data
    unique_activities.sort(key=lambda x: extract_date(x) or '0000-00-00', reverse=True)
    
    # Limitar a MAX_ACTIVITIES_STORED
    trimmed = 0
    if len(unique_activities) > MAX_ACTIVITIES_STORED:
        trimmed = len(unique_activities) - MAX_ACTIVITIES_STORED
        unique_activities = unique_activities[:MAX_ACTIVITIES_STORED]
        messages.append(f"✂️  Limitadas a {MAX_ACTIVITIES_STORED} (removidas {trimmed} mais antigas)")
    
    # Salvar
    if save_activities(unique_activities):
        messages.append(f"✅ Activities.json reorganizado: {len(unique_activities)} atividades")
    else:
        messages.append("❌ Falha ao salvar activities.json")
        return duplicates_removed, len(unique_activities), messages
    
    # Mostrar últimas 3
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
        # Verificar última modificação
        mod_time = os.path.getmtime(consolidated_path)
        age_hours = (time.time() - mod_time) / 3600
        
        if age_hours > 2:
            return False, f"Dados desatualizados (última atualização há {age_hours:.1f}h)"
        
        # Verificar se tem conteúdo
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
        
        # Verificar se tem campos essenciais
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
        "🏋️ FitnessJournal-HRV Bot v3.1 (Refatorado + Bug Fixes)\n\n"
        "Bot conectado ao Garmin Connect.\n\n"
        "Comandos disponíveis:\n"
        "/status - Ver readiness\n"
        "/activities - Ver últimas atividades\n"
        "/analyze - Analisar aderência ao plano\n"
        "/analyze_activity - Análise detalhada individual\n"
        "/import - Importar últimos 7 dias\n"
        "/sync - Sincronizar hoje\n"
        "/cleanup - Limpar flags e reorganizar atividades\n"
        "/debug - Info de debug\n"
        "/help - Ajuda"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Status com validação robusta"""
    user_id = update.effective_user.id
    logger.info(f"Status command from user {user_id}")
    
    await update.message.reply_text("⏳ A extrair biometria do Garmin...")
    
    data = load_garmin_data()
    
    if not data:
        await update.message.reply_text(
            "❌ Nenhum dado disponível.\n\n"
            "Usa /import para importar dados históricos.\n"
            "Depois usa /debug para verificar o estado."
        )
        return
    
    history = parse_garmin_history(data)
    
    # FAILSAFE: Verificar se dados de hoje existem
    if history and history[0].is_empty():
        today = history[0]
        keyboard = [[InlineKeyboardButton("✅ SINCRONIZADO - Prosseguir", callback_data='sync_confirmed')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"⚠️ ATENÇÃO: Dados de hoje ({today.date}) estão vazios.\n\n"
            f"📱 INSTRUÇÕES:\n"
            f"1. Sincroniza o teu relógio Garmin com a app\n"
            f"2. Aguarda 2-3 minutos\n"
            f"3. Carrega no botão abaixo quando confirmares que sincronizou\n\n"
            f"💡 Se o problema persistir, os dados podem não estar disponíveis ainda.",
            reply_markup=reply_markup
        )
        
        # Guardar estado temporário
        context.user_data['awaiting_sync'] = True
        context.user_data['retry_date'] = today.date
        return
    
    valid = [h for h in history if h.is_valid()]
    
    if not valid:
        await update.message.reply_text(
            f"⚠️ Dados insuficientes para análise.\n\n"
            f"Total de dias: {len(history)}\n"
            f"Com HRV e RHR válidos: 0\n\n"
            f"Usa /import para buscar mais dados."
        )
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
    
    # BUG FIX: Usar get_all_formatted_activities para pegar TODAS as atividades
    all_formatted_activities = get_all_formatted_activities()
    recent_activities_formatted = all_formatted_activities[:3]
    recent_activities_raw = [f.raw for f in recent_activities_formatted]
    
    recent_load = sum([h.training_load or 0 for h in valid[:3]])

    msg = (f"📊 HOJE ({today.date}):\n"
           f"💓 RHR: {today.rhr} bpm ({d_rhr:+.1f}% vs média)\n"
           f"📈 HRV: {today.hrv} ms ({d_hrv:+.1f}% vs média)\n"
           f"😴 Sono: {today.sleep or 'N/A'}/100\n\n"
           f"📅 MÉDIAS 7 DIAS:\n"
           f"RHR médio: {m_rhr:.0f} bpm\n"
           f"HRV médio: {m_hrv:.0f} ms\n"
           f"Carga 3 dias: {recent_load:.0f} AU\n\n"
           f"🎯 READINESS: {readiness}")
    
    if recent_activities_formatted:
        msg += f"\n\n🏃 ÚLTIMAS ATIVIDADES:"
        for formatted in recent_activities_formatted:
            msg += f"\n• {formatted.to_brief_summary()}"

    # Criar e salvar estado da sessão
    state = UserSessionState(
        today=today,
        d_hrv=d_hrv,
        d_rhr=d_rhr,
        m_hrv=m_hrv,
        m_rhr=m_rhr,
        history=valid[:5],
        readiness=readiness,
        recent_activities=recent_activities_raw,
        recent_load=recent_load
    )
    save_session_state(context, state)
    
    kb = [[
        InlineKeyboardButton("✅ SIM - 20km+", callback_data='bike_yes'),
        InlineKeyboardButton("❌ NÃO", callback_data='bike_no')
    ]]
    
    await update.message.reply_text(msg)
    await update.message.reply_text("🚴 Vais pedalar hoje (20km+)?", reply_markup=InlineKeyboardMarkup(kb))

async def sync_confirmed_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback após confirmação de sincronização"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text("🔄 A criar pedido de sincronização...")
    
    success = create_sync_request()
    
    if not success:
        await query.message.reply_text(
            "❌ Falha ao criar pedido de sincronização.\n\n"
            "Verifica /debug para mais informações."
        )
        context.user_data.clear()
        return
    
    await query.message.reply_text(
        "⏳ Pedido de sincronização criado.\n"
        "O garmin-fetcher irá processar em breve (~30-60s).\n\n"
        "Usa /status novamente após 1 minuto."
    )
    context.user_data.clear()

async def bike_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback para decisão de ciclismo"""
    query = update.callback_query
    await query.answer()
    
    state = get_session_state(context)
    if not state:
        await query.message.reply_text("❌ Sessão expirada. Usa /status novamente.")
        return
    
    state.bike = (query.data == 'bike_yes')
    save_session_state(context, state)
    
    await query.edit_message_text(f"🚴 Ciclismo: {'SIM' if state.bike else 'NÃO'}")
    await query.message.reply_text("💭 Como te sentes hoje? (Descreve o teu estado)")

async def handle_feeling(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gera plano baseado em dados + feeling"""
    user_id = update.effective_user.id
    logger.info(f"handle_feeling from user {user_id}")
    
    state = get_session_state(context)
    if not state:
        await update.message.reply_text("❌ Primeiro usa /status para carregar os teus dados.")
        return
    
    # Validar estado
    is_valid, error_msg = state.validate()
    if not is_valid:
        await update.message.reply_text(f"❌ Estado inválido: {error_msg}\n\nUsa /status novamente.")
        return
    
    feeling = update.message.text.strip()
    
    # Validar tamanho do input
    if len(feeling) > MAX_FEELING_LENGTH:
        await update.message.reply_text(
            f"⚠️ Descrição muito longa ({len(feeling)} caracteres).\n"
            f"Por favor resume em até {MAX_FEELING_LENGTH} caracteres."
        )
        return
    
    # Sanitizar input
    feeling = feeling.replace('\x00', '')
    
    proc_msg = await update.message.reply_text("🤖 O Coach Gemini está a processar os dados...")
    
    try:
        # Montar contexto histórico
        h_str = "\n".join([
            f"• {h.date}: HRV {h.hrv}ms, RHR {h.rhr}bpm"
            for h in state.history if h.is_valid()
        ])
        
        # Montar contexto de atividades
        activities_str = ""
        if state.recent_activities:
            activities_list = []
            for act in state.recent_activities[:3]:
                try:
                    formatted = format_activity(act)
                    if formatted:
                        activities_list.append(formatted.to_brief_summary())
                except Exception as e:
                    logger.error(f"Failed to format activity: {e}")
                    continue
            
            if activities_list:
                activities_str = "\nAtividades recentes: " + ", ".join(activities_list)

        # PROMPT OTIMIZADO
        prompt = f"""DADOS DO ATLETA:
📈 HRV: {state.today.hrv}ms ({state.d_hrv:+.1f}% versus média)
💓 RHR: {state.today.rhr}bpm ({state.d_rhr:+.1f}% versus média)
😴 Sono: {state.today.sleep}/100
🎯 Readiness: {state.readiness}
🚴 Ciclismo hoje: {'SIM - 20km+ obrigatório' if state.bike else 'NÃO'}

Últimos 5 dias:
{h_str}{activities_str}

Equipamento disponível: {', '.join(EQUIPAMENTOS_GIM)}

💭 SENSAÇÃO DO ATLETA: "{feeling}"

TAREFA:
Gera o plano de treino para hoje em formato tabela markdown.
Usa PORTUGUÊS EUROPEU e cálculos em texto simples (sem LaTeX ou símbolos especiais)."""

        logger.info(f"Calling Gemini API (user {user_id}, prompt: {len(prompt)} chars)")
        
        start_time = time.time()
        response = model.generate_content(prompt)
        duration_ms = (time.time() - start_time) * 1000
        
        logger.info(f"Gemini response received (user {user_id}, response: {len(response.text)} chars, duration: {duration_ms:.0f}ms)")
        
        await proc_msg.delete()
        
        # Atualizar estado com plano gerado
        state.last_plan = response.text
        state.last_plan_date = state.today.date
        save_session_state(context, state)
        
        await update.message.reply_text(f"📋 PLANO DO DIA:\n\n{response.text}")
        logger.info(f"Plan sent successfully to user {user_id}")
        
    except Exception as e:
        logger.error(f"handle_feeling error for user {user_id}: {type(e).__name__}: {str(e)}")
        logger.error(traceback.format_exc())
        
        error_details = f"Erro: {type(e).__name__}"
        if hasattr(e, 'message'):
            error_details += f"\n{e.message}"
        
        try:
            await proc_msg.edit_text(
                f"❌ Falha na comunicação com o Gemini.\n\n"
                f"{error_details}\n\n"
                f"Verifica os logs do sistema."
            )
        except:
            await update.message.reply_text(f"❌ Erro: {error_details}")

async def activities_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra últimas atividades ordenadas por data"""
    # BUG FIX: Usar get_all_formatted_activities
    all_formatted = get_all_formatted_activities()
    
    if not all_formatted:
        await update.message.reply_text("❌ Nenhuma atividade registada.")
        return
    
    # Pegar últimas MAX_ACTIVITIES_DISPLAY
    recent = all_formatted[:MAX_ACTIVITIES_DISPLAY]
    
    msg = f"🏃 ÚLTIMAS {len(recent)} ATIVIDADES:\n\n"
    for formatted in recent:
        msg += formatted.to_detailed_summary() + "\n\n"
    
    await update.message.reply_text(msg)

async def analyze_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Analisa aderência ao plano com validação rigorosa:
    1. Procura atividade de hoje
    2. Se não existir, procura de ontem
    3. Se não existir nenhuma, informa o utilizador
    """
    user_id = update.effective_user.id
    logger.info(f"analyze_command from user {user_id}")
    
    await update.message.reply_text("🔍 A procurar atividade para análise...")
    
    # NOVA VALIDAÇÃO: Encontrar atividade apropriada
    activity_to_analyze, context_message = find_activity_for_analysis()
    
    if activity_to_analyze is None:
        # Não há atividade válida para analisar
        await update.message.reply_text(context_message)
        return
    
    # Informar qual atividade será analisada
    await update.message.reply_text(
        f"{context_message}\n\n"
        f"📋 Atividade a analisar:\n"
        f"{activity_to_analyze.to_detailed_summary()}"
    )
    
    # Preparar dados para análise
    activities_detail = f"DATA: {activity_to_analyze.date}\n"
    activities_detail += f"TIPO: {activity_to_analyze.sport}\n"
    activities_detail += f"DURAÇÃO: {activity_to_analyze.duration_min}min\n"
    if activity_to_analyze.distance_km:
        activities_detail += f"DISTÂNCIA: {activity_to_analyze.distance_km}km\n"
    if activity_to_analyze.avg_hr:
        activities_detail += f"FC MÉDIA: {activity_to_analyze.avg_hr}bpm\n"
    if activity_to_analyze.calories:
        activities_detail += f"CALORIAS: {activity_to_analyze.calories}\n"
    if activity_to_analyze.intensity:
        activities_detail += f"INTENSIDADE: {activity_to_analyze.intensity}\n"
    if activity_to_analyze.load:
        activities_detail += f"LOAD: {activity_to_analyze.load}\n"
    
    state = get_session_state(context)
    last_plan = state.last_plan if state else 'Nenhum plano anterior registado.'
    
    prompt = f"""ANÁLISE DE ADERÊNCIA AO PLANO

PLANO RECOMENDADO ANTERIORMENTE:
{last_plan}

ATIVIDADE EXECUTADA:
{activities_detail}

TAREFA:
Analisa a aderência do atleta ao plano proposto.
• A atividade executada seguiu as recomendações?
• Houve desvios de volume, intensidade ou tipo de treino?
• Qual o impacto desses desvios nos objetivos (hipertrofia mais endurance)?
• Que ajustes devem ser feitos no próximo plano?

Sê ASSERTIVO e DIRETO. Se houve falhas, diz claramente.
Usa PORTUGUÊS EUROPEU sem LaTeX ou símbolos especiais."""

    try:
        proc_msg = await update.message.reply_text("🤖 A analisar aderência ao plano...")
        response = model.generate_content(prompt)
        await proc_msg.delete()
        await update.message.reply_text(f"📊 ANÁLISE DE ADERÊNCIA:\n\n{response.text}")
        logger.info(f"Analyze completed for user {user_id}")
    except Exception as e:
        logger.error(f"Gemini error in analyze: {e}")
        await update.message.reply_text(
            "❌ Falha na comunicação com o Gemini.\n"
            "Tenta novamente."
        )

async def analyze_activity_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inicia análise individual de atividade"""
    # BUG FIX: Usar get_all_formatted_activities
    all_formatted = get_all_formatted_activities()
    
    if not all_formatted:
        await update.message.reply_text("❌ Nenhuma atividade para analisar.")
        return
    
    # Mostrar últimas 5 para escolha
    keyboard = []
    for i, act in enumerate(all_formatted[:5]):
        label = f"{act.date} - {act.sport} ({act.duration_min}min"
        if act.distance_km:
            label += f", {act.distance_km}km"
        label += ")"
        keyboard.append([InlineKeyboardButton(label, callback_data=f'analyze_act_{i}')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "📊 Seleciona a atividade para análise detalhada:",
        reply_markup=reply_markup
    )
    
    # Guardar atividades formatadas no estado
    state = get_session_state(context)
    if state:
        state.formatted_activities = all_formatted[:5]
        save_session_state(context, state)
    else:
        # Criar estado temporário apenas com formatted_activities
        context.user_data['formatted_activities'] = [asdict(a) for a in all_formatted[:5]]

async def analyze_activity_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback para análise individual de atividade"""
    query = update.callback_query
    await query.answer()
    
    # Extrair índice
    index = int(query.data.split('_')[-1])
    
    state = get_session_state(context)
    if state and state.formatted_activities:
        formatted_acts = state.formatted_activities
    elif 'formatted_activities' in context.user_data:
        formatted_acts = [FormattedActivity(**a) for a in context.user_data['formatted_activities']]
    else:
        await query.message.reply_text("❌ Sessão expirada. Usa /analyze_activity novamente.")
        return
    
    if index >= len(formatted_acts):
        await query.message.reply_text("❌ Atividade não encontrada.")
        return
    
    selected = formatted_acts[index]
    
    # Se for ciclismo, perguntar sobre carga
    if 'ciclismo' in selected.sport.lower() or 'cycling' in selected.sport.lower():
        keyboard = [
            [InlineKeyboardButton("🚴 Com passageiro (150kg total)", callback_data=f'cargo_yes_{index}')],
            [InlineKeyboardButton("🚴‍♂️ Sem passageiro (normal)", callback_data=f'cargo_no_{index}')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"🚴 Atividade selecionada:\n"
            f"{selected.date} - {selected.sport}\n"
            f"{selected.duration_min}min, {selected.distance_km}km\n\n"
            f"Esta atividade foi com bicicleta de carga?",
            reply_markup=reply_markup
        )
        
        # Guardar índice para próximo callback
        if state:
            state.selected_activity_index = index
            save_session_state(context, state)
        else:
            context.user_data['selected_activity_index'] = index
    else:
        # Não é ciclismo, analisar diretamente
        await perform_activity_analysis(query, context, index, cargo=False)

async def cargo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback para decisão de carga na bicicleta"""
    query = update.callback_query
    await query.answer()
    
    # Extrair se tem carga e índice
    parts = query.data.split('_')
    has_cargo = (parts[1] == 'yes')
    index = int(parts[2])
    
    await perform_activity_analysis(query, context, index, cargo=has_cargo)

async def perform_activity_analysis(query, context, index: int, cargo: bool = False):
    """Executa análise detalhada da atividade"""
    state = get_session_state(context)
    if state and state.formatted_activities:
        formatted_acts = state.formatted_activities
    elif 'formatted_activities' in context.user_data:
        formatted_acts = [FormattedActivity(**a) for a in context.user_data['formatted_activities']]
    else:
        await query.message.reply_text("❌ Sessão expirada.")
        return
    
    selected = formatted_acts[index]
    
    await query.edit_message_text(f"🔍 A analisar atividade de {selected.date}...")
    
    # Buscar histórico de biometria
    data = load_garmin_data()
    history = parse_garmin_history(data) if data else []
    
    # Encontrar biometria do dia da atividade
    activity_date = selected.date
    biometric_context = ""
    
    for h in history:
        if h.date == activity_date:
            biometric_context = f"\nBiometria do dia:\n"
            biometric_context += f"- HRV: {h.hrv}ms, RHR: {h.rhr}bpm, Sono: {h.sleep}/100"
            break
    
    # Pegar atividades adjacentes (contexto histórico)
    all_activities = get_all_formatted_activities()
    
    # Encontrar índice desta atividade na lista completa
    activity_index_in_full_list = None
    for i, act in enumerate(all_activities):
        if act.date == selected.date and act.sport == selected.sport:
            activity_index_in_full_list = i
            break
    
    historical_activities = []
    if activity_index_in_full_list is not None:
        # Pegar atividades adjacentes (±3 posições)
        start_idx = max(0, activity_index_in_full_list - 3)
        end_idx = min(len(all_activities), activity_index_in_full_list + 4)
        
        for i in range(start_idx, end_idx):
            if i != activity_index_in_full_list:
                act = all_activities[i]
                summary = f"- {act.date}: {act.to_brief_summary()}"
                historical_activities.append(summary)
    
    history_str = "\n".join(historical_activities) if historical_activities else "Sem atividades próximas registadas"
    
    # Contexto de carga se aplicável
    cargo_context = ""
    if cargo:
        cargo_context = "\n\n🚴 CONTEXTO DE CARGA:\n"
        cargo_context += "Esta atividade foi realizada com bicicleta de carga (cargo bike) transportando passageiro.\n"
        cargo_context += "Carga total: aproximadamente 150kg (bicicleta mais condutor mais passageiro)\n"
        cargo_context += "Este contexto adiciona resistência significativa, especialmente em subidas e acelerações."
    
    # Montar prompt para análise profunda
    prompt = f"""ANÁLISE INDIVIDUAL DE ATIVIDADE

ATIVIDADE ANALISADA:
Data: {selected.date}
Tipo: {selected.sport}
Duração: {selected.duration_min}min
Distância: {selected.distance_km}km
FC média: {selected.avg_hr}bpm
Calorias: {selected.calories}
Zona/Intensidade: {selected.intensity}
Load: {selected.load}{cargo_context}

{biometric_context}

CONTEXTO HISTÓRICO (atividades adjacentes):
{history_str}

TAREFA:
Faz uma análise PROFUNDA e INDIVIDUAL desta atividade, considerando:

1. PERFORMANCE ABSOLUTA:
   - Análise dos indicadores (FC, velocidade, carga)
   - Eficiência energética (calorias versus distância versus tempo)
   - Comparação com standards para o tipo de atividade

2. IMPACTO FISIOLÓGICO:
   - Carga de treino e stress metabólico
   - Recuperação necessária estimada
   - Zonas de treino e adaptações estimuladas

3. CONTEXTO HISTÓRICO:
   - Como esta atividade se enquadra no padrão recente
   - Se representa progressão, manutenção ou regressão
   - Coerência com atividades anteriores

4. IMPACTO NOS DIAS SEGUINTES:
   - Que tipo de treino é recomendado no dia seguinte
   - Janela de recuperação estimada
   - Sinais de alerta para overtraining

5. RECOMENDAÇÕES ESPECÍFICAS:
   - Ajustes para próximas sessões similares
   - Pontos fortes a manter
   - Áreas de melhoria identificadas

Usa PORTUGUÊS EUROPEU, sê ASSERTIVO e TÉCNICO.
Fornece números e métricas concretas sempre que possível."""

    try:
        response = model.generate_content(prompt)
        
        # Enviar resposta em partes se for muito longa (limite Telegram: 4096 chars)
        analysis_text = response.text
        
        if len(analysis_text) > 4000:
            parts = [analysis_text[i:i+4000] for i in range(0, len(analysis_text), 4000)]
            await query.message.reply_text(f"📊 ANÁLISE DETALHADA - Parte 1/{len(parts)}:\n\n{parts[0]}")
            for i, part in enumerate(parts[1:], 2):
                await query.message.reply_text(f"📊 ANÁLISE DETALHADA - Parte {i}/{len(parts)}:\n\n{part}")
        else:
            await query.message.reply_text(f"📊 ANÁLISE DETALHADA:\n\n{analysis_text}")
        
        logger.info(f"Individual analysis completed for activity {selected.date}")
        
    except Exception as e:
        logger.error(f"Error in individual analysis: {e}")
        await query.message.reply_text(f"❌ Erro ao gerar análise:\n{type(e).__name__}: {str(e)}")

async def import_historical(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Importa dados históricos dos últimos 7 dias"""
    
    # Verificar health do garmin-fetcher
    is_healthy, msg = check_garmin_fetcher_health()
    
    if not is_healthy:
        await update.message.reply_text(
            f"⚠️ AVISO: Garmin-fetcher pode não estar funcional.\n"
            f"Motivo: {msg}\n\n"
            f"A tentar criar pedido de importação mesmo assim..."
        )
    
    await update.message.reply_text(
        "📥 A criar pedido de importação histórica...\n"
        "⏳ O garmin-fetcher irá processar em breve."
    )
    
    success = create_import_request(7)
    
    if success:
        await update.message.reply_text(
            "✅ Pedido de importação criado.\n\n"
            "O garmin-fetcher irá processar nos próximos minutos.\n"
            "Isto pode demorar 2-5 minutos.\n\n"
            "Usa /debug para verificar o progresso."
        )
    else:
        await update.message.reply_text(
            "❌ Erro ao criar pedido de importação.\n\n"
            "Verifica /debug para mais informações."
        )

async def sync_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Força sincronização dos dados de hoje"""
    
    is_healthy, msg = check_garmin_fetcher_health()
    
    if not is_healthy:
        await update.message.reply_text(
            f"⚠️ Garmin-fetcher: {msg}\n\n"
            f"A tentar sincronizar mesmo assim..."
        )
    
    await update.message.reply_text("🔄 A criar pedido de sincronização...")
    
    success = create_sync_request()
    
    if success:
        await update.message.reply_text(
            "✅ Pedido criado.\n\n"
            "O garmin-fetcher irá processar em ~30-60 segundos.\n"
            "Usa /status para verificar."
        )
    else:
        await update.message.reply_text(
            "❌ Erro ao criar pedido.\n\n"
            "Verifica /debug."
        )

async def cleanup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Limpa flags antigos e reorganiza atividades"""
    await update.message.reply_text("🧹 A executar limpeza...")
    
    try:
        # Limpar flags
        cleaned_flags, flag_messages = cleanup_old_flags()
        
        # Reorganizar atividades
        duplicates, total, activity_messages = reorganize_activities()
        
        # Montar mensagem
        msg = "🧹 LIMPEZA COMPLETA\n\n"
        
        msg += "📋 FLAGS:\n"
        for m in flag_messages:
            msg += f"{m}\n"
        
        msg += f"\n🏃 ATIVIDADES:\n"
        for m in activity_messages:
            msg += f"{m}\n"
        
        msg += f"\n{'='*30}\n"
        msg += f"✅ Flags limpos: {cleaned_flags}\n"
        msg += f"✅ Duplicados removidos: {duplicates}\n"
        msg += f"✅ Total de atividades: {total}"
        
        await update.message.reply_text(msg)
        logger.info(f"Cleanup executed: {cleaned_flags} flags, {duplicates} duplicates")
        
    except Exception as e:
        logger.error(f"Cleanup error: {e}")
        await update.message.reply_text(f"❌ Erro ao executar limpeza:\n{type(e).__name__}: {str(e)}")

async def debug_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando de debug para verificar estado do sistema"""
    files = list_data_files()
    raw_data = load_garmin_data()
    
    # BUG FIX: Usar get_all_formatted_activities
    all_activities = get_all_formatted_activities()
    
    # Verificar health do garmin-fetcher
    is_healthy, health_msg = check_garmin_fetcher_health()
    
    msg = f"""🔧 DEBUG INFO:

📁 Arquivos em /data: {len(files)}
📊 Dados carregados: {'Sim' if raw_data else 'Não'}
📅 Total de dias: {len(raw_data) if raw_data else 0}
🏃 Atividades válidas: {len(all_activities)}
🐳 Garmin-fetcher: {'✅ HEALTHY' if is_healthy else '⚠️ UNHEALTHY'}
   {health_msg}

Arquivos recentes:
{chr(10).join(files[-5:] if files else ['Nenhum'])}
"""
    
    if raw_data:
        history = parse_garmin_history(raw_data)
        valid = [h for h in history if h.is_valid()]
        
        msg += f"\n✅ Dias com HRV/RHR válidos: {len(valid)}"
        
        if history:
            today = history[0]
            msg += f"\n\n📊 Último dia: {today.date}"
            msg += f"\nHRV: {today.hrv}"
            msg += f"\nRHR: {today.rhr}"
            msg += f"\nSono: {today.sleep}"
    
    # Verificar status de pedidos pendentes
    import_status = check_request_status('import')
    sync_status = check_request_status('sync')
    
    if import_status or sync_status:
        msg += f"\n\n🚩 PEDIDOS:"
        
        if import_status:
            status_emoji = "⏳" if import_status == "pending" else ("✅" if import_status == "completed" else "❌")
            msg += f"\n{status_emoji} Import: {import_status}"
            
            if import_status == "pending":
                flag_path = os.path.join(DATA_DIR, 'import_request.json')
                req = load_json_safe(flag_path)
                if req and 'requested_at' in req:
                    try:
                        requested_at = datetime.fromisoformat(req['requested_at'])
                        age = datetime.now() - requested_at
                        msg += f" (há {int(age.total_seconds())}s)"
                    except:
                        pass
        
        if sync_status:
            status_emoji = "⏳" if sync_status == "pending" else ("✅" if sync_status == "completed" else "❌")
            msg += f"\n{status_emoji} Sync: {sync_status}"
            
            if sync_status == "pending":
                flag_path = os.path.join(DATA_DIR, 'sync_request.json')
                req = load_json_safe(flag_path)
                if req and 'requested_at' in req:
                    try:
                        requested_at = datetime.fromisoformat(req['requested_at'])
                        age = datetime.now() - requested_at
                        msg += f" (há {int(age.total_seconds())}s)"
                    except:
                        pass
    
    # Verificar variáveis de ambiente
    has_garmin_email = bool(os.getenv('GARMIN_EMAIL'))
    has_garmin_pass = bool(os.getenv('GARMIN_PASSWORD'))
    has_gemini_key = bool(os.getenv('GEMINI_API_KEY'))
    
    msg += f"\n\n🔑 VARIÁVEIS:"
    msg += f"\nGARMIN_EMAIL: {'✅' if has_garmin_email else '❌'}"
    msg += f"\nGARMIN_PASSWORD: {'✅' if has_garmin_pass else '❌'}"
    msg += f"\nGEMINI_API_KEY: {'✅' if has_gemini_key else '❌'}"
    
    # Adicionar informação sobre última atividade
    if all_activities:
        last_act = all_activities[0]
        msg += f"\n\n🏃 Última atividade:"
        msg += f"\n{last_act.date} - {last_act.sport} ({last_act.duration_min}min)"
    
    await update.message.reply_text(msg)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menu de ajuda"""
    await update.message.reply_text(
        "🏋️ FitnessJournal-HRV Bot v3.1 (Refatorado + Bug Fixes)\n\n"
        "📋 COMANDOS:\n"
        "/status - Ver readiness e dados\n"
        "/activities - Ver últimas atividades\n"
        "/analyze - Analisar aderência ao plano (hoje ou ontem)\n"
        "/analyze_activity - Análise detalhada individual\n"
        "/import - Importar últimos 7 dias\n"
        "/sync - Forçar sync de hoje\n"
        "/cleanup - Limpar flags e reorganizar atividades\n"
        "/debug - Info de debug\n"
        "/help - Este menu\n\n"
        "🔄 FLUXO:\n"
        "1️⃣ /status - Vê dados HRV/RHR\n"
        "2️⃣ Confirma ciclismo\n"
        "3️⃣ Descreve sensação\n"
        "4️⃣ Recebe plano\n"
        "5️⃣ /analyze - Analisa aderência (hoje ou ontem)\n"
        "6️⃣ /analyze_activity - Análise profunda individual\n\n"
        "📡 DADOS: Garmin Connect\n"
        "🤖 IA: Google Gemini 2.0 Flash\n\n"
        "⚙️ MELHORIAS v3.1:\n"
        "• Bug fix: Todas atividades consideradas\n"
        "• Validação rigorosa para /analyze\n"
        "• Busca inteligente (hoje → ontem)\n"
        "• Mensagens de erro descritivas"
    )

# ==========================================
# STARTUP ROUTINE
# ==========================================
def auto_cleanup_on_startup():
    """Executa cleanup automático no arranque do bot"""
    try:
        cleaned_flags, messages = cleanup_old_flags()
        if cleaned_flags > 0:
            logger.info(f"Auto-cleanup: {cleaned_flags} flags limpos no arranque")
            for msg in messages:
                logger.info(f"  {msg}")
    except Exception as e:
        logger.error(f"Erro no auto-cleanup: {e}")

# ==========================================
# MAIN
# ==========================================
def main():
    """Inicia o bot"""
    logger.info("=" * 50)
    logger.info("FitnessJournal-HRV Bot v3.1 (Refatorado + Bug Fixes)")
    logger.info("=" * 50)
    logger.info(f"Data dir: {DATA_DIR}")
    
    # Verificar API keys
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.error("❌ GEMINI_API_KEY não configurada!")
    else:
        logger.info(f"✅ API Key presente: {api_key[:10]}...")
    
    garmin_email = os.environ.get("GARMIN_EMAIL")
    garmin_pass = os.environ.get("GARMIN_PASSWORD")
    
    if not garmin_email or not garmin_pass:
        logger.warning("⚠️ Credenciais Garmin não configuradas completamente")
    else:
        logger.info(f"✅ Garmin email: {garmin_email}")
    
    # Verificar arquivos no startup
    files = list_data_files()
    all_activities = get_all_formatted_activities()
    logger.info(f"📁 Arquivos encontrados: {len(files)}")
    logger.info(f"🏃 Atividades válidas: {len(all_activities)}")
    
    # Verificar health garmin-fetcher
    is_healthy, health_msg = check_garmin_fetcher_health()
    logger.info(f"🐳 Garmin-fetcher: {'HEALTHY' if is_healthy else 'UNHEALTHY'} - {health_msg}")
    
    # AUTO-CLEANUP no arranque
    logger.info("🧹 A executar auto-cleanup...")
    auto_cleanup_on_startup()
    
    # Inicializar bot
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Registrar handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("activities", activities_command))
    app.add_handler(CommandHandler("analyze", analyze_command))
    app.add_handler(CommandHandler("analyze_activity", analyze_activity_command))
    app.add_handler(CommandHandler("import", import_historical))
    app.add_handler(CommandHandler("sync", sync_command))
    app.add_handler(CommandHandler("cleanup", cleanup_command))
    app.add_handler(CommandHandler("debug", debug_command))
    app.add_handler(CommandHandler("help", help_command))
    
    # Callbacks
    app.add_handler(CallbackQueryHandler(sync_confirmed_callback, pattern=r'^sync_confirmed$'))
    app.add_handler(CallbackQueryHandler(analyze_activity_callback, pattern=r'^analyze_act_\d+$'))
    app.add_handler(CallbackQueryHandler(cargo_callback, pattern=r'^cargo_(yes|no)_\d+$'))
    app.add_handler(CallbackQueryHandler(bike_callback, pattern=r'^bike_(yes|no)$'))
    
    # Message handler para feeling
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_feeling))
    
    logger.info("✅ Bot iniciado e aguardando comandos")
    print("🤖 Bot ativo - Aguardando comandos...")
    app.run_polling()

if __name__ == '__main__':
    main()
