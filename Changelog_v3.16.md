# Changelog — FitnessJournal Bot v3.16.0

Data: 2026-03-14  
Baseline: v3.15.1

---

## Resumo Executivo

Quatro alterações cirúrgicas aplicadas sobre a v3.15.1, sem modificação à estrutura das classes `BiometricDay`, `FormattedActivity`, nem à lógica de UI de cards verticais do `SYSTEM_PROMPT`. Toda a lógica de headers técnicos e separação de contextos da v3.15 mantida intacta.

---

## Alterações

### 1. Adição de Corrida (`running`)

**Ficheiro:** `main.py`

| Localização | Alteração |
|---|---|
| `SYSTEM_PROMPT` | Especialidades do treinador actualizadas: `"Ciclismo (MTB/Estrada), Corrida de Estrada e Trail, e Hipertrofia"` |
| `analyze_activity_callback` | Detecção de `is_running` por keywords `['run', 'corrida', 'trail', 'running']` na string do desporto |
| `analyze_activity_callback` | Corrida salta o seletor de tipo ciclismo e vai directamente para `ask_about_cargo(..., cycling_type="corrida")` |
| `cycling_type_callback` regex | Pattern alargado: `(mtb\|estrada\|spinning\|cidade\|corrida)` |
| `perform_activity_analysis` | Nova `REGRA CORRIDA` injectada no prompt: análise de passada (170-180 spm), FC por zonas, impacto músculo-esquelético e trail; proíbe métricas de ciclismo |
| Botão seletor ciclismo | Adicionado `🦺 Corrida (Orox)` como quinta opção no seletor de tipo, para atividades de ciclismo que na verdade são deslocações com a Orox |

---

### 2. Fluxo de Carga Extra Orox no `/status`

**Ficheiro:** `main.py`

Antes: `feeling` → Gemini imediatamente.  
Depois: `feeling` → pergunta Orox → Gemini.

| Localização | Alteração |
|---|---|
| `process_status_with_feeling` | Reescrito: guarda `feeling` em `context.user_data['pending_feeling']`, define estado `waiting_orox`, apresenta dois botões inline: **"Sim, com carga"** (`orox_yes`) e **"Não"** (`orox_no`) |
| `orox_callback` *(nova função)* | Lê `pending_feeling`, determina `has_orox`, delega para `process_status_final` |
| `process_status_final` *(nova função)* | Executa a chamada ao Gemini com todos os dados (biometria, feeling, orox, histórico). Injecta no prompt: `[CONTEXTO ADICIONAL: O atleta terá uma carga extra de 20km em bicicleta de carga com passageiro após o treino principal]` quando `has_orox=True` |
| `handle_message` | Novo guard: se estado `waiting_orox`, responde `"⬆️ Usa os botões acima..."` e faz return sem processar texto |
| `main()` | Registo de `CallbackQueryHandler(orox_callback, pattern=r'^orox_(yes\|no)$')` |

**Invariante de segurança:** `session_state.clear_user_state` chamado em todos os caminhos de erro de `orox_callback` e `process_status_final`.

---

### 3. Correcção Bug HRV `/status`

**Ficheiro:** `main.py` — função `status`

**Antes:**
```
HRV: 65 (+3% vs média)
```
*(mostrava o valor de hoje repetido implicitamente no desvio percentual)*

**Depois:**
```
HRV: Tendência: 65 (Média 7d: 63) ms
```

O campo `baseline['hrv_avg']` já estava calculado no mesmo contexto; apenas a formatação da linha foi corrigida para expor ambos os valores em ms de forma explícita e legível.

---

### 4. Histórico de Atividades no Prompt Gemini (`/status`)

**Ficheiro:** `main.py`

| Localização | Alteração |
|---|---|
| `get_activity_history_for_prompt(n=3)` *(nova função)* | Lê `activities.json` via `get_all_formatted_activities()`, devolve as últimas N atividades formatadas como `"- DATA \| TIPO \| DURAÇÃO \| DISTÂNCIA \| FC \| CARGA/CAL"` |
| `process_status_final` | Injecta `get_activity_history_for_prompt(3)` sob o header `### HISTÓRICO DE ATIVIDADES RECENTES (contexto de fadiga acumulada):` |

Campos de esforço por prioridade: `training_load` → `calories` → `avg_hr` (o que estiver disponível).

---

### 5. Correcção Bug Pré-existente (Bonus)

**Ficheiro:** `main.py` — bloco após `reorganize_activities()`

Código ativo (`enriched_count = 0`, loop e save) estava fora de qualquer função, executado como código de módulo no import, resultado de uma função comentada incompletamente na v3.14. As linhas orfãs foram comentadas para eliminar efeitos laterais silenciosos no arranque.

---

## Compatibilidade

- Estrutura `BiometricDay`: **inalterada**
- Estrutura `FormattedActivity`: **inalterada**
- `SYSTEM_PROMPT` cards verticais e separação de contextos: **intactos**
- Headers técnicos v3.15: **intactos**
- Todos os handlers e comandos existentes: **inalterados**
- `activities.json` / `garmin_data_consolidated.json`: **formato inalterado**

---

## Testes Recomendados

1. `/status` → inserir feeling → confirmar que aparece pergunta Orox → responder "Sim, com carga" → confirmar `[CONTEXTO ADICIONAL]` no log ou na resposta IA
2. `/status` → inserir feeling → responder "Não" → confirmar chamada normal ao Gemini sem contexto Orox
3. `/analyze_activity` → selecionar atividade de corrida → confirmar que salta seletor ciclismo e vai directo para pergunta de carga
4. `/analyze_activity` → selecionar atividade ciclismo → confirmar novo botão "🦺 Corrida (Orox)"
5. `/status` → verificar linha HRV: deve mostrar `Tendência: X (Média 7d: Y) ms`
6. Arranque do bot: confirmar ausência de erros no log relacionados com `enriched_count`