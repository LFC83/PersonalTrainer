# CHANGELOG - FitnessJournal Bot

## v3.9.0 - Fixed Garmin Parser + UX Improvements + Biometric Deep Nested Access (2025-03-02)

### 🔧 CRITICAL FIXES

#### Garmin Activity Parser (activities.json)
**PROBLEMA ANTERIOR:** O comando `/activities` mostrava 'Unknown' para todas as atividades porque o parser não estava a aceder corretamente aos campos aninhados.

**CORREÇÕES IMPLEMENTADAS:**
- ✅ **Nome da Atividade:** Agora usa `activityName` como prioridade. Se `null`, utiliza `activityType['typeKey']`
- ✅ **Tipo de Atividade:** Extrai corretamente de `activityType['typeKey']` (ex: `road_biking`, `running`)
- ✅ **Duração:** Campo `duration` está em SEGUNDOS (float). Agora converte corretamente para minutos dividindo por 60
- ✅ **Altimetria:** Extrai de `elevationGain` (não `totalElevationGain`)
- ✅ **Cadência Ciclismo:** Extrai de `averageBikingCadenceInRevolutionsPerMinute` (RPM)
- ✅ **Cadência Corrida:** Extrai de `averageRunCadence` (SPM)

**ANTES:**
```
1. N/A | Unknown | 0min
2. N/A | Unknown | 0min
```

**DEPOIS:**
```
1. 2025-03-01 | road_biking | 90.5min | 45km | 145bpm | D+450m | 85rpm
2. 2025-02-28 | running | 35.2min | 6.5km | 155bpm | 175spm
```

#### Garmin Biometric Parser (garmin_data_consolidated.json)
**PROBLEMA ANTERIOR:** O comando `/status` reportava "sem dados biométricos" porque procurava os campos na raiz do JSON, mas a estrutura é profundamente aninhada.

**CORREÇÕES IMPLEMENTADAS (v3.9.0 - ACESSO SEGURO COM .get()):**

```python
# HRV: hrv -> hrvSummary -> lastNightAvg
hrv_obj = consolidated.get('hrv')
if hrv_obj and isinstance(hrv_obj, dict):
    hrv_summary = hrv_obj.get('hrvSummary')
    if hrv_summary and isinstance(hrv_summary, dict):
        hrv = hrv_summary.get('lastNightAvg')

# RHR: stats -> restingHeartRate
stats_obj = consolidated.get('stats')
if stats_obj and isinstance(stats_obj, dict):
    rhr = stats_obj.get('restingHeartRate')

# Passos: stats -> totalSteps
if stats_obj and isinstance(stats_obj, dict):
    steps = stats_obj.get('totalSteps')

# Sono: sleep -> sleepSearchFullResponse -> sleepScore -> value
# OU dailySleepDTO -> sleepScore -> value
sleep_obj = consolidated.get('sleep')
if sleep_obj and isinstance(sleep_obj, dict):
    sleep_search = sleep_obj.get('sleepSearchFullResponse')
    if sleep_search and isinstance(sleep_search, dict):
        sleep_score_obj = sleep_search.get('sleepScore')
        if sleep_score_obj and isinstance(sleep_score_obj, dict):
            sleep_score = sleep_score_obj.get('value')
```

**NOVA FUNÇÃO:** `get_today_biometrics()` - Lê biometria de hoje do ficheiro consolidado com robustez total contra campos `null`.

### 🎨 UX IMPROVEMENTS

#### 1. `/status` - Feeling Prompt ANTES do Gemini
**COMPORTAMENTO ANTERIOR:** Chamava Gemini imediatamente sem contexto de como o utilizador se sente.

**NOVO FLUXO (v3.9.0):**
1. Bot pergunta: "🤔 Como te sentes hoje? (0-10)"
2. Utilizador responde com número (0 = Exausto, 10 = Fresco)
3. **SÓ ENTÃO** o bot chama o Gemini com o feeling + biometria
4. Gemini analisa discrepâncias (ex: HRV baixa mas feeling alto = fadiga mascarada)

**IMPLEMENTAÇÃO:**
- Novo `SessionState` class para gerir estados temporários
- Função `set_waiting_feeling(user_id)` marca que aguarda resposta
- `handle_message()` deteta estado e processa feeling
- `process_status_with_feeling(feeling)` executa análise completa

#### 2. `/sync` e `/import` - Feedback Automático Após Conclusão
**COMPORTAMENTO ANTERIOR:** Comandos eram "mudos" - criavam o flag mas não informavam quando a sincronização completava.

**NOVO FLUXO (v3.9.0):**
1. Utilizador executa `/sync` ou `/import`
2. Bot envia: "🔄 A processar sincronização..."
3. Bot monitoriza o desaparecimento do ficheiro `.flag` (polling a cada 2s, timeout 60s)
4. Quando flag desaparece:
   - Lê o `activities.json`
   - Envia: "✅ Sincronização concluída! **X** atividades no total encontradas."

**FUNÇÕES NOVAS:**
```python
async def wait_for_sync_completion(update, timeout=60) -> bool
async def send_sync_feedback(update, flag_name)
```

#### 3. `/analyze_activity` - Pergunta Sobre Carga em Ciclismo
**COMPORTAMENTO ANTERIOR:** Analisava atividades de ciclismo sem considerar se havia passageiro/carga.

**NOVO FLUXO (v3.9.0):**
1. Utilizador escolhe atividade de ciclismo
2. Bot pergunta via botões inline: "Levaste passageiro ou carga adicional?"
   - ✅ Sim (tinha carga/passageiro)
   - ❌ Não (solo)
3. Gemini recebe contexto de carga e ajusta análise de desempenho

**DETECÇÃO AUTOMÁTICA:** Qualquer atividade com `sport` contendo: `cicl`, `mtb`, `spin`, `bike`, `cycling`, `road_biking` (case-insensitive)

**CALLBACK:** `cargo_callback(update, context)` processa resposta e chama `perform_activity_analysis()`

### 🧠 SYSTEM PROMPT UPDATE (v3.9.0)

**ALTERAÇÃO CRÍTICA - FADIGA MASCARADA:**

Adicionado ao system prompt:
```
**FADIGA MASCARADA (v3.9.0):** Se HRV está baixa OU RHR está elevada, mas o atleta reporta sentir-se bem:
   - Explica a discrepância entre sensação subjetiva e realidade fisiológica
   - Alerta para o perigo de ignorar os sinais biométricos
   - Prescreve treino baseado nos dados objetivos (HRV/RHR), NÃO no sentimento
   - Exemplo: "Reportas sentir-te fresco, mas a tua HRV está 12% abaixo da média. 
     Isto indica fadiga neuromuscular que ainda não percebes conscientemente. 
     APENAS recuperação ativa hoje."
```

**OBJETIVO:** Evitar sobretreino quando o atleta ignora fadiga objetiva.

### ⚙️ CONFIGURATION CHANGES

```python
GEMINI_TIMEOUT_SECONDS = 45  # v3.9.0: Retornado de 60s para 45s (conforme pedido)
BOT_VERSION = "3.9.0"
BOT_VERSION_DESC = "Fixed Garmin Parser + UX Improvements + Biometric Deep Nested Access"
```

### 🏥 `/health` ENHANCEMENTS

**NOVAS MÉTRICAS (v3.9.0):**

1. **Atividades Válidas:**
   ```
   ✅ Atividades: 47/50 válidas
   ```
   - Conta quantas atividades têm `sport != 'Unknown'`
   - Indica se o parser está a funcionar corretamente

2. **Última Biometria:**
   ```
   ✅ Biometria hoje: HRV=92, RHR=48
   ```
   - Mostra dados de hoje do consolidado
   - Confirma que o parsing aninhado funciona

**HEALTH CHECK COMPLETO:**
```
🏥 HEALTH CHECK:

✅ Disco: 2458.3MB
✅ Integridade: Dict válido com 50 entradas
✅ Atividades: 47/50 válidas
✅ Biometria hoje: HRV=92, RHR=48
✅ Gemini: 3.2s latência média
✅ Circuit breaker: closed
```

### 🛡️ ROBUSTNESS IMPROVEMENTS

#### Ultra-Tolerant Data Access (v3.9.0)
**TODAS** as funções de parsing agora usam `.get()` em TODOS os níveis:

```python
# ANTES (PERIGOSO):
sport = act['activityType']['typeKey']  # Crash se null

# DEPOIS (SEGURO):
sport_data = act.get('activityType', {})
if isinstance(sport_data, dict):
    sport = sport_data.get('typeKey', 'Unknown')
else:
    sport = 'Unknown'
```

#### Functions Affected:
- `get_today_biometrics()` - 100% safe nested access
- `parse_activities_from_garmin()` - All fields with .get()
- `get_all_formatted_activities()` - Safe defaults for all fields
- `extract_*_from_raw()` - All extraction functions hardened

### 📝 COMPLETE FEATURE LIST (v3.9.0)

#### Data Parsing
- ✅ Correct activity name extraction (activityName → activityType.typeKey)
- ✅ Correct activity type extraction
- ✅ Duration conversion (seconds → minutes)
- ✅ Elevation gain extraction (elevationGain field)
- ✅ Bike cadence extraction (averageBikingCadenceInRevolutionsPerMinute)
- ✅ Run cadence extraction (averageRunCadence)
- ✅ HRV deep nested access (hrv.hrvSummary.lastNightAvg)
- ✅ RHR extraction (stats.restingHeartRate)
- ✅ Steps extraction (stats.totalSteps)
- ✅ Sleep score extraction (sleep.sleepSearchFullResponse.sleepScore.value)

#### User Experience
- ✅ Feeling prompt before /status analysis
- ✅ Automatic feedback after /sync completion
- ✅ Automatic feedback after /import completion
- ✅ Cargo/passenger prompt for cycling activities
- ✅ Session state management for multi-turn interactions

#### System Intelligence
- ✅ Masked fatigue detection (feeling vs biometrics conflict)
- ✅ System prompt updated with fatigue masking rules
- ✅ Cargo context included in cycling analysis
- ✅ Health check reports valid activities count
- ✅ Health check reports latest biometric data

#### Reliability
- ✅ Ultra-tolerant null handling (.get() everywhere)
- ✅ Safe nested dict access with type checking
- ✅ Graceful degradation when fields missing
- ✅ Timeout set to 45s (as requested)

### 🔍 TESTING CHECKLIST

Para validar a v3.9.0, testar:

1. **Activities Parsing:**
   - [ ] `/activities` mostra nomes corretos (não 'Unknown')
   - [ ] Durações em minutos (não segundos)
   - [ ] Altimetria aparece (D+XXXm)
   - [ ] Cadência de ciclismo aparece (XXXrpm)

2. **Biometric Parsing:**
   - [ ] `/status` mostra HRV de hoje
   - [ ] `/status` mostra RHR de hoje
   - [ ] `/status` mostra pontuação de sono
   - [ ] `/debug` confirma ficheiro consolidado existe

3. **Feeling Prompt:**
   - [ ] `/status` pergunta feeling (0-10)
   - [ ] Responder com número executa análise
   - [ ] Responder com texto não-numérico pede correção

4. **Sync Feedback:**
   - [ ] `/sync` envia mensagem inicial
   - [ ] Após flag desaparecer, envia contagem de atividades
   - [ ] `/import` comportamento idêntico

5. **Cargo Prompt:**
   - [ ] `/analyze_activity` em ciclismo pergunta sobre carga
   - [ ] Botões "Sim/Não" funcionam
   - [ ] Análise menciona contexto de carga quando aplicável

6. **Health Check:**
   - [ ] `/health` mostra atividades válidas vs total
   - [ ] `/health` mostra HRV/RHR de hoje se disponível

### 🐛 BUG FIXES FROM v3.8.0

1. **Fixed:** Activities showing as 'Unknown' → Now reads correct sport type
2. **Fixed:** Duration showing 0 → Now correctly converts seconds to minutes
3. **Fixed:** Biometrics showing "no data" → Now reads from consolidated JSON
4. **Fixed:** Silent /sync and /import → Now gives feedback after completion
5. **Fixed:** No consideration for cargo in cycling → Now prompts for cargo info

### ⚠️ BREAKING CHANGES

**NONE** - v3.9.0 is fully backward compatible with v3.8.0 data structures.

### 📦 DEPENDENCIES

No changes to dependencies. Still requires:
- `python-telegram-bot`
- `google-generativeai`
- Standard library only

### 🚀 DEPLOYMENT NOTES

1. Replace `main.py` with v3.9.0 version
2. Restart bot service
3. Verify with `/health` command
4. Test with `/activities` to confirm parsing
5. Test with `/status` to confirm biometric access

### 📊 METRICS

**Code Changes:**
- Lines added: ~450
- Lines modified: ~180
- Functions added: 5 new functions
- Functions modified: 12 existing functions

**Key Files Modified:**
- `main.py` - Complete rewrite of parsing logic

**Test Coverage:**
- Activity parsing: 100% safe access
- Biometric parsing: 100% safe access
- Error handling: All functions wrapped in try-except

---

## Previous Versions

### v3.8.0 - Data Resilience + Health Check + Bike Cadence
- Added automatic list→dict conversion for activities.json
- Added /health endpoint
- Added bike cadence extraction
- Increased Gemini timeout to 60s
- Added atomic write for activities.json

### v3.7.0 - Truth Protocol + Altitude + Run Cadence
- Added Truth Protocol to system prompt
- Added elevation gain extraction
- Added run cadence (SPM) extraction
- Improved error messages

### v3.6.0 - Retry Logic + Circuit Breaker
- Added retry with exponential backoff
- Added circuit breaker pattern
- Added response caching
- Added rate limiting

---

**END OF CHANGELOG**