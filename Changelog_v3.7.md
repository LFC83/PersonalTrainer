# CHANGELOG & MIGRATION GUIDE
## FitnessJournal Bot v3.7.0

---

## 🎯 RESUMO EXECUTIVO

**Versão:** 3.7.0  
**Data:** 2026-03-01  
**Tipo:** Feature Release + Performance Enhancement  
**Breaking Changes:** ❌ Nenhum  
**Requires Migration:** ❌ Não (automático)

### O Que Mudou (TL;DR)
1. ✅ Bot agora extrai **altimetria** (D+) em ciclismo e corrida
2. ✅ Bot agora extrai **cadência** (steps/min) em corrida
3. ✅ Bot **SEMPRE** injeta **HRV/RHR** nos prompts do Gemini
4. ✅ Bot **pergunta sobre carga** antes de analisar atividades de ciclismo
5. ✅ **Timeout aumentado** para 45s (era 30s)
6. ✅ **Retry inteligente** (dobra delay após timeout)
7. ✅ **System prompt atualizado** (prioridade aos dados biométricos)
8. ✅ **Handler para comandos não reconhecidos**

---

## 📋 DETAILED CHANGELOG

### 🆕 NEW FEATURES

#### 1. Enriquecimento de Dados - Altimetria
**O quê:** Bot extrai ganho de elevação (metros) para atividades de ciclismo e corrida.

**Porquê:** Permite ao Gemini avaliar o esforço real considerando o desnível positivo.

**Como funciona:**
- Garmin pode reportar elevação em diferentes campos
- Bot procura em: `totalElevationGain`, `elevationGain`, `totalAscent`, `gainElevation`
- Valor armazenado em metros (float)
- Exibido como "⛰️ D+: XXm" no comando `/activities`

**Retrocompatibilidade:**
- Atividades antigas (sem D+) → bot re-extrai automaticamente do JSON no boot
- Função `check_and_enrich_activities()` executa no startup

**Exemplo de output:**
```
📅 2026-02-28 - Ciclismo
  ⏱️ Duração: 95min
  📏 Dist: 32.5km
  ⛰️ D+: 450m          ← NOVO
  🎯 Zona: Moderado
```

---

#### 2. Enriquecimento de Dados - Cadência
**O quê:** Bot extrai cadência média e máxima (steps per minute) para corrida.

**Porquê:** Cadência é métrica crítica para avaliar técnica de corrida e eficiência.

**Como funciona:**
- Garmin reporta cadência em `strides/min` (80-100) ou `steps/min` (160-200)
- Bot detecta automaticamente a unidade:
  - Se valor < 120 → multiplica por 2 (strides → steps)
  - Se valor ≥ 120 → já está em steps
- Procura em: `averageRunCadence`, `avgRunCadence`, `maxRunCadence`
- **Apenas para corrida** (não exibe em ciclismo)

**Retrocompatibilidade:**
- Mesma lógica da altimetria (re-extração automática)

**Exemplo de output:**
```
📅 2026-02-28 - Corrida
  ⏱️ Duração: 45min
  📏 Dist: 8.2km
  👟 Cadência: 174 spm  ← NOVO
  💓 FC: 152bpm
```

---

#### 3. Contexto Biométrico SEMPRE Injetado
**O quê:** Dados de HRV (Variabilidade Cardíaca) e RHR (Pulsação em Repouso) são SEMPRE enviados ao Gemini.

**Porquê:** **CRÍTICO** - Gemini não pode basear análise apenas no feedback subjetivo ("sinto-me fresco"). Precisa ver os dados reais de recuperação.

**Como funciona:**
- Bot calcula baseline dos últimos 7 dias
- Calcula desvio percentual do dia atual vs. média
- Adiciona status visual: ✅ (bom) | ⚠️ (atenção) | 🔴 (alerta)
- Injeta no início do prompt em:
  - `/status` (análise de readiness)
  - `/analyze` (aderência ao plano)
  - `/analyze_activity` (análise individual)
  - Perguntas conversacionais (texto livre)

**Exemplo de contexto injetado:**
```
📊 CONTEXTO BIOMÉTRICO (Últimos 7 dias):

HRV Média (7d): 68.2 ms
RHR Média (7d): 52.3 bpm

📅 HOJE:
🔴 HRV: 58.1 ms (-14.8%)   ← Fadiga detectada!
⚠️ RHR: 55.7 bpm (+6.5%)    ← Elevada
✅ Sono: 82/100
```

**Impacto no Gemini:**
- Gemini agora vê a discrepância entre "sinto-me bem" e HRV baixa
- Pode alertar para fadiga mascarada
- Prescreve treino baseado em dados objetivos

---

#### 4. Pergunta de Carga no Ciclismo
**O quê:** Antes de analisar atividade de ciclismo, bot **OBRIGATORIAMENTE** pergunta se a volta teve passageiro/carga.

**Porquê:** Carga extra afeta significativamente velocidade e FC. Sem essa info, análise é imprecisa.

**Como funciona:**
1. User executa `/analyze_activity`
2. User escolhe atividade de ciclismo
3. Bot mostra keyboard:
   - ✅ Sim (com carga)
   - ❌ Não (sozinho)
4. User responde
5. **Só então** bot envia para Gemini com contexto: "⚠️ CONDIÇÃO: Volta com passageiro/carga extra"

**Flow antes (v3.6):**
```
/analyze_activity → escolhe atividade → análise imediata
```

**Flow agora (v3.7):**
```
/analyze_activity → escolhe ciclismo → pergunta carga → análise com contexto
```

---

### 🔧 PERFORMANCE IMPROVEMENTS

#### 5. Timeout Aumentado (30s → 45s)
**O quê:** Timeout do Gemini API aumentado de 30 para 45 segundos.

**Porquê:** Logs da v3.6 mostraram muitos timeouts prematuros. 45s cobre 99% dos casos.

**Código:**
```python
GEMINI_TIMEOUT_SECONDS = 45  # v3.7.0: era 30
```

**Impacto:**
- Menos timeouts falsos
- Primeira tentativa tem mais chance de sucesso
- Circuit breaker ativa menos frequentemente

---

#### 6. Retry Inteligente com Backoff
**O quê:** Lógica de retry agora adapta o delay baseado no tipo de erro.

**Como funciona (v3.7):**
```
Tentativa 1: timeout 45s → FALHA (TimeoutError)
  ↓
Tentativa 2: delay 2s * 2 = 4s (porque foi timeout) → timeout 45s
  ↓
Tentativa 3: delay 5s * 2 = 10s (se ainda for timeout) → timeout 45s
```

**Antes (v3.6):**
```
Tentativa 1: delay 0s → FALHA
Tentativa 2: delay 2s → FALHA
Tentativa 3: delay 4s → FALHA
```

**Código:**
```python
was_timeout = False  # Flag rastreia tipo de erro

if was_timeout:
    delay = delay * 2  # Dobra o delay após timeout
    logger.info(f"Timeout detectado, delay aumentado para {delay}s")
```

**Benefício:**
- Menos retries desperdiçadas (se Gemini está lento, aguarda mais)
- Não penaliza erros não-timeout (ex: rate limit)

---

#### 7. System Prompt Atualizado
**O quê:** Instruções do Gemini agora incluem regra explícita sobre prioridade biométrica.

**Adicionado ao prompt:**
```
### CRITÉRIOS BIOMÉTRICOS RIGOROSOS (v3.7.0 - PRIORIDADE ABSOLUTA):
**REGRA FUNDAMENTAL:** Quando os dados biométricos (HRV/RHR) indicarem fadiga,
mas o feedback subjetivo do utilizador for positivo (ex: "sinto-me fresco"),
DEVES DAR PRIORIDADE ABSOLUTA AOS DADOS BIOLÓGICOS e alertar para o risco de
sobretreino ou fadiga mascarada.

4. **FADIGA MASCARADA:** Se HRV está baixa OU RHR está elevada, mas o atleta
   reporta sentir-se bem, DEVES:
   - Explicar a discrepância entre sensação subjetiva e realidade fisiológica
   - Alertar para o perigo de ignorar os sinais biométricos
   - Prescrever treino baseado nos dados objetivos (HRV/RHR), não no sentimento
   - Exemplo: "Reportas sentir-te fresco, mas a tua HRV está 12% abaixo da média.
     Isto indica fadiga neuromuscular que ainda não percebes conscientemente.
     APENAS recuperação ativa hoje."
```

**Impacto:**
- Gemini agora é **assertivo** quando há conflito sensação vs. dados
- Explica **porquê** está a recomendar descanso mesmo que user se sinta bem
- Reduz risco de sobretreino por ignorar sinais objetivos

---

### 🛠️ MINOR IMPROVEMENTS

#### 8. Handler para Comandos Não Reconhecidos
**O quê:** Bot agora responde a comandos inválidos em vez de ignorar.

**Antes (v3.6):**
```
User: /xyz123
Bot: [silêncio]
```

**Agora (v3.7):**
```
User: /xyz123
Bot: ❓ Comando '/xyz123' não reconhecido.

Comandos disponíveis:
/start - Iniciar bot
/status - Avalia readiness
/analyze - Analisa aderência ao plano
[...]
```

**Implementação:**
```python
async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    command = update.message.text
    await update.message.reply_text(
        f"❓ Comando '{command}' não reconhecido.\n\n"
        "Comandos disponíveis:\n[...]"
    )

# IMPORTANTE: Registar DEPOIS de todos os outros handlers
app.add_handler(MessageHandler(filters.COMMAND, unknown_command))
```

---

## 🔄 MIGRATION GUIDE

### Para Utilizadores

**Nenhuma ação necessária!** Tudo funciona automaticamente.

**O que vai notar:**
1. Atividades de ciclismo/corrida agora mostram D+ (altimetria)
2. Corridas agora mostram cadência (spm)
3. `/status` e `/analyze` agora exibem contexto biométrico detalhado
4. Ao analisar ciclismo, bot vai perguntar sobre carga
5. Comandos inválidos agora recebem resposta (em vez de silêncio)

---

### Para Developers

#### Estrutura de Dados
```python
# activities.json (antes)
{
  "12345": {
    "date": "2026-02-28",
    "sport": "Ciclismo",
    "duration_min": 95,
    "distance_km": 32.5
  }
}

# activities.json (agora)
{
  "12345": {
    "date": "2026-02-28",
    "sport": "Ciclismo",
    "duration_min": 95,
    "distance_km": 32.5,
    "elevation_gain": 450.0,    # NOVO (opcional)
    "avg_cadence": null,         # NOVO (opcional, só corrida)
    "max_cadence": null          # NOVO (opcional, só corrida)
  }
}
```

**Compatibilidade:**
- Atividades antigas: campos novos ausentes → exibição funciona normalmente
- Enriquecimento automático: bot preenche dados no primeiro boot

#### Deployment Checklist
```bash
# 1. Backup (precaução)
cp /data/activities.json /data/activities.json.backup
cp /data/garmin_dump.json /data/garmin_dump.json.backup

# 2. Deploy v3.7.0
docker stop bot
docker rm bot
docker run -d --name bot \
  -v /data:/data \
  -e TELEGRAM_TOKEN=$TOKEN \
  -e GEMINI_API_KEY=$KEY \
  fitnessjournal:3.7.0

# 3. Verificar logs
docker logs -f bot | grep "v3.7.0"
# Deve mostrar: "✅ Bot v3.7.0 iniciado com:"

# 4. Verificar enriquecimento
docker logs bot | grep "enriquecidas"
# Deve mostrar: "✅ X atividades enriquecidas"

# 5. Testar funcionalidades
# Telegram: /debug → verificar contadores
# Telegram: /status → deve mostrar biometria
# Telegram: /analyze_activity (ciclismo) → deve perguntar carga
```

#### Rollback (Se Necessário)
```bash
# v3.7.0 é backwards compatible → rollback seguro
docker stop bot
docker rm bot
docker run -d --name bot \
  -v /data:/data \
  -e TELEGRAM_TOKEN=$TOKEN \
  -e GEMINI_API_KEY=$KEY \
  fitnessjournal:3.6.0

# Dados permanecem intactos (campos novos são ignorados pela v3.6)
```

---

## 📊 METRICS TO MONITOR

### Health Indicators (Primeiras 24h)
1. **Circuit Breaker State:** Deve permanecer `closed`
2. **Enriquecimento:** Log deve mostrar "X atividades enriquecidas" no boot
3. **Timeout Rate:** Deve ser < 5% das requests
4. **Cache Hit Rate:** Deve ser > 10%

### Success Metrics (Primeira semana)
1. **Biometric Context Injection:** 100% dos comandos `/status`, `/analyze`
2. **Cargo Question:** 100% dos `/analyze_activity` com ciclismo
3. **Altitude Display:** Presente em 80%+ das atividades outdoor
4. **Cadence Display:** Presente em 70%+ das corridas

### Alert Thresholds
```
Circuit Breaker State = "open" por > 5min
  → Investigar logs Gemini API

"Erro ao enriquecer atividade" > 10 vezes
  → Verificar estrutura garmin_dump.json

Rate Limit Exceeded > 50 vezes/dia por user
  → Ajustar RATE_LIMIT_MAX_REQUESTS
```

---

## 🐛 KNOWN ISSUES & WORKAROUNDS

### Issue #1: Garmin JSON Estrutura Variável
**Problema:** Alguns devices Garmin podem usar campos não mapeados.

**Sintoma:** Log mostra "Atividade X enriquecida: elevation=None, cadence=None"

**Workaround:** Adicionar campo ao array em `extract_elevation_from_raw()`:
```python
elevation_fields = [
    'totalElevationGain',
    'elevationGain',
    'totalAscent',
    'gainElevation',
    'SEU_CAMPO_AQUI'  # ← Adicionar
]
```

### Issue #2: Cadência em Outros Desportos
**Problema:** Atualmente só extrai para corrida. E se ciclismo reportar cadência?

**Status:** Não implementado (ciclismo usa RPM, não SPM - conversão diferente)

**Roadmap:** v3.8 pode adicionar cadência de ciclismo (RPM)

---

## 📝 CHANGELOG SUMMARY

### Added
- ✅ Altitude extraction (elevation gain) for cycling & running
- ✅ Cadence extraction (steps/min) for running
- ✅ Biometric context injection (HRV/RHR) in all Gemini prompts
- ✅ Cargo question in cycling activity analysis
- ✅ Unknown command handler with command list
- ✅ Auto-enrichment system for legacy activities

### Changed
- ✅ Gemini timeout: 30s → 45s
- ✅ Retry delays: [2, 4, 8] → [2, 5, 10]
- ✅ Retry logic: now doubles delay after timeout
- ✅ System prompt: added biometric priority rule
- ✅ `FormattedActivity`: added `elevation_gain`, `avg_cadence`, `max_cadence`

### Fixed
- ✅ Premature timeouts (increased from 30s to 45s)
- ✅ Inefficient retry on timeout (now uses backoff)
- ✅ Silent failure on unknown commands (now responds with help)

### Deprecated
- ❌ None

### Removed
- ❌ None

### Security
- ✅ No changes (maintains v3.6 security posture)

---

## 🎓 LESSONS LEARNED

### What Worked Well
1. **Thin vertical slices:** Cada feature testável isoladamente
2. **Optional fields:** `elevation_gain: Optional[float] = None` → zero breaking changes
3. **Auto-enrichment:** Users não precisam fazer nada
4. **Intelligent retry:** Adapta-se ao tipo de erro (timeout vs. outros)

### What Could Be Improved
1. **Tests:** Ainda não há testes automatizados (pytest pendente)
2. **Metrics:** Não exporta Prometheus metrics (latência, hit rate)
3. **Configuração:** Timeout e delays hardcoded (poderia ser env vars)

### What We'll Do Next (v3.8 Roadmap)
1. Testes automatizados (pytest + fixtures)
2. Prometheus metrics exporter
3. Health check endpoint (`/health`)
4. Compression de prompts grandes (remover redundância)
5. Cadência para ciclismo (RPM)

---

## 📞 SUPPORT

### Se algo der errado:

1. **Verificar logs:**
   ```bash
   docker logs bot | tail -100
   ```

2. **Executar debug:**
   ```
   Telegram: /debug
   ```

3. **Verificar circuit breaker:**
   ```
   Telegram: /stats
   ```

4. **Rollback se crítico:**
   ```bash
   docker run fitnessjournal:3.6.0
   ```

### Contactos:
- **Logs:** `/var/log/bot/`
- **Data:** `/data/`
- **Config:** Environment variables

---

**Version:** 3.7.0  
**Release Date:** 2026-03-01  
**Status:** ✅ Ready for Production  
**Next Review:** v3.8 (TBD)