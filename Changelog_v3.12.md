# CHANGELOG — FitnessJournal Bot

---

## v3.12.0 — 2026-03-02

### Sumário
Refinamento de interface de utilizador e precisão de IA. A lógica estrutural da v3.10 permanece intacta. Alterações circunscritas a: prompts, fluxo de /status, cabeçalho de /analyze_activity, parsing biométrico e logging de JSON.

---

### 1. Separação Estrita de Contextos IA

**`/analyze_activity` — Análise Técnica Pura**
- **Removido:** Tabela de treino futuro e sugestões de musculação/core do contexto de análise.
- **Adicionado:** Prompt reformulado para focar exclusivamente em: eficiência de cadência, análise de FC por zonas, impacto altimétrico (W/kg estimados) e impacto biométrico pós-sessão (previsão HRV, recuperação estimada).
- A instrução `PROIBIDO incluir tabela de treino futuro` foi adicionada tanto no `SYSTEM_PROMPT` como no prompt inline de `perform_activity_analysis`.

**`/status` — Único Local de Prescrição de Treino**
- **Adicionado:** Prompt reformulado com secção `EQUIPAMENTO DISPONÍVEL (EXCLUSIVO)` injetada no prompt da IA a partir da lista `EQUIPAMENTOS_GIM`.
- **Adicionado:** Instrução explícita: se o atleta é ciclista, sugerir apenas reforço core/postural ou endurance, nunca máquinas comerciais (ex: Prensa).
- **Adicionado:** Formato obrigatório de resposta para `/status` inclui: Cálculo de Carga, Protocolo Aplicado, Tabela de Treino.
- O `SYSTEM_PROMPT` documenta claramente os dois contextos separados com formatos de resposta distintos.

---

### 2. Fluxo de Interatividade & UI

**`/analyze_activity` — Cabeçalho Técnico**
- **Adicionado:** Método `FormattedActivity.to_technical_header()` que gera cabeçalho via f-string com dados reais do objeto:
  - `📅 [Data] - [Nome da Atividade]`
  - `⏱️ Duração: [X]min | 📏 Dist: [X]km`
  - `💓 FC Média: [X]bpm | 🔥 Cal: [X]`
  - `🏔️ Altimetria: [D+]m | ⚙️ Cadência: [RPM]` (apenas se > 0)
- Este cabeçalho é impresso com `query.edit_message_text()` **antes** da chamada ao Gemini.
- A questão sobre Tipo de ciclismo (MTB/Estrada/Spinning/Cidade) mantém-se inalterada da v3.10.
- Carga/Passageiro: quando `has_cargo=True`, injeta `peso total estimado: 150kg` no prompt.

**`/status` — Fluxo Invertido com Dashboard Biométrico Visível**
- **Antes (v3.10):** `/status` pedia o feeling imediatamente.
- **Agora (v3.12):** Fluxo invertido:
  1. `⏳ A extrair biometria...`
  2. Mostra dashboard `📊 HOJE:` com RHR, HRV (% vs média) e Sono.
  3. Mostra `📈 TENDÊNCIA 5 DIAS:` com séries HRV e RHR geradas por Python.
  4. Mostra `🏃 ÚLTIMAS:` com as 3 atividades mais recentes.
  5. **Só depois** envia `💭 Como te sentes hoje (0-10)?`
- A lógica de `process_status_with_feeling` e `handle_message` permanece inalterada.

**Personalidade / Tom**
- Mensagens de estado atualizadas: `🔍 A avaliar prontidão biológica...` (em vez de `A analisar readiness...`), `🔍 A avaliar métricas da sessão...`, `🔍 A avaliar aderência ao plano...`.

---

### 3. Robustez de Dados & Mapeamento

**`get_today_biometrics` — Fallback Biométrico**
- **Adicionado:** Se não existirem dados para hoje, procura automaticamente o dia anterior (`date.today() - timedelta(days=1)`).
- Se fallback for usado: regista `logger.warning` e marca `bio_day.date` com sufixo `" (fallback)"` para transparência na UI.
- O dashboard do `/status` exibe aviso `⚠️ Dados de ontem (hoje sem registo)` quando fallback está ativo.

**`_extract_biometric_from_day` — Função Extraída**
- Lógica de parsing de campos aninhados (`hrv.hrvSummary.lastNightAvg`, `stats.restingHeartRate`, etc.) centralizada numa função privada reutilizável.
- Elimina duplicação entre `get_today_biometrics` e `parse_garmin_history`.

**`parse_activity_from_garmin` — Campos Técnicos Robustos**
- **`elevation_gain`:** Tenta `elevationGain` primeiro, fallback para `totalElevationGain`.
- **`avg_cadence`:** Tenta `averageBikingCadenceInRevPerMinute`, depois `averageRunningCadenceInStepsPerMinute`, depois `averageCadence`.
- Estes valores são injetados explicitamente no prompt de `perform_activity_analysis`.

---

### 4. Observabilidade & Logs

**Logs de JSON**
- `load_json_safe`: quando o ficheiro é `list` ou `dict`, emite `logger.info(f"Ficheiro {path} lido com {len(data)} itens")`.
- Cobre todas as leituras de `garmin_data_consolidated.json`, `activities.json`, `garmin_dump.json` e ficheiros de contexto.

---

### 5. Refactoring & Qualidade (Guidelines aplicadas)

| Critério (Guidelines) | Ação |
|---|---|
| Thin vertical slices | Cada feature implementada de forma isolada; zero alterações em código não relacionado |
| Simpler structure | `_extract_biometric_from_day` elimina duplicação; `send_long_message` consolida chunking |
| Explicit names | `to_technical_header`, `_extract_biometric_from_day`, `send_long_message` — nomes diretos |
| Leave code easier to read | Comentários de versão atualizados; secções bem delimitadas |
| No over-engineering | Sem novas classes; sem meta-programming; sem abstrações desnecessárias |

---

### 6. Sem Alterações (Estabilidade v3.10 preservada)

As seguintes componentes **não foram alteradas**:
- `CircuitBreaker`, `RateLimiter`, `ResponseCache`, `HealthCheckState`, `SessionState`
- `load_garmin_consolidated`, `load_activities_index`, `save_activities_index`
- `check_activities_integrity`, `reorganize_activities`, `check_and_enrich_activities`
- `call_gemini_with_timeout`, `call_gemini_with_retry`
- `sync_command`, `sync_confirmed_callback`, `cargo_callback`, `cycling_type_callback`
- `cleanup_command`, `history_command`, `clear_context_command`, `stats_command`, `debug_command`
- Todos os handlers de `/import`, `/activities`, `/analyze`
- Estrutura de registo de handlers em `main()`

---

### 7. Notas de Migração

- Sem alterações de schema de dados — `activities.json` e `garmin_data_consolidated.json` são compatíveis.
- Sem novas dependências de biblioteca.
- Variáveis de ambiente necessárias: `TELEGRAM_TOKEN`, `GEMINI_API_KEY` (inalteradas).
- Model name atualizado para `gemini-2.5-flash-preview-05-20` (verificar disponibilidade na conta; reverter para `gemini-2.0-flash` se necessário).

---

## v3.10.0 (referência anterior)

CRITICAL FIX: Consolidated JSON como lista suportada; tipo de ciclismo perguntado (MTB/Estrada/Spinning/Cidade); feedback de sync corrigido; evolução HRV em /status; `load_json_safe` com logging de tipo; `wait_for_sync_completion` aceita Query/Update.