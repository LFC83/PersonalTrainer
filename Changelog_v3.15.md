# CHANGELOG — FitnessJournal Bot v3.15.0

**Data:** 2026-03-03  
**Base:** v3.14.0  
**Tipo de alteração:** Restauro de funcionalidade + enriquecimento de prompt IA

---

## Problema Identificado

A v3.13/v3.14 introduziu uma regressão silenciosa no fluxo de `/analyze_activity` para atividades de ciclismo: o seletor de tipo (MTB, Estrada, Spinning, Cidade) só era apresentado para atividades com `sport` genérico (`cycling`, `other`, `bike`, `ciclismo`). Atividades com tipo explícito do Garmin (ex: `road_biking`, `mountain_biking`) saltavam diretamente para a pergunta de carga, perdendo o contexto de tipo.

---

## Alterações em `main.py`

### 1. `analyze_activity_callback` — Seletor de tipo restaurado para TODAS as atividades de ciclismo

**Ficheiro:** `main.py`, função `analyze_activity_callback`  
**Linha aprox.:** 1578–1594

**Antes (v3.14):**
```python
if is_cycling:
    is_generic_type = sport_lower in ['cycling', 'other', 'bike', 'ciclismo']
    if is_generic_type:
        # mostrava teclado
    else:
        await ask_about_cargo(...)  # saltava o seletor
```

**Depois (v3.15):**
```python
if is_cycling:
    # v3.15.0: Sempre apresenta o seletor para QUALQUER atividade de ciclismo
    # mostrava teclado — sem is_generic_type guard
```

**Impacto:** Qualquer atividade detetada como ciclismo (`cicl`, `mtb`, `spin`, `bike`, `cycling`, `road_biking`) passa agora pelo seletor de tipo antes da pergunta de carga.

---

### 2. `perform_activity_analysis` — Regras de IA por tipo de ciclismo

**Ficheiro:** `main.py`, função `perform_activity_analysis`  
**Linha aprox.:** 1696–1730

Adicionado bloco `cycling_type_instruction` que injeta no prompt do Gemini regras específicas por tipo:

| Tipo | Regra injetada |
|:---|:---|
| **MTB** | Valoriza esforço cardiovascular/técnico em terreno irregular; potência efectiva +15-25%; cadência óptima 70-85 RPM; fadiga excêntrica nas descidas |
| **Estrada** | Foca aerodinâmica e cadência constante (85-95 RPM plano, 70-80 RPM subida); deriva cardíaca como KPI de eficiência aeróbia |
| **Spinning** | Contexto indoor; zonas de FC como único indicador; sem análise altimétrica real |
| **Cidade** | Stop-and-go urbano; eficiência metabólica reduzida; foco em volume e calorias; cadência não é métrica primária |

A instrução é acumulativa com `cargo_instruction` (v3.14): ambas coexistem no prompt sem conflito.

---

### 3. Versão e logs

- `BOT_VERSION`: `3.14.0` → `3.15.0`
- `BOT_VERSION_DESC`: actualizado com descrição da restauração
- `help_command`: notas de versão actualizadas para v3.15.0
- `main()` startup log: duas novas linhas a confirmar seletor e regras AI

---

## O que NÃO foi alterado (por design)

- `JobQueue` / `job_check_flags` — intacto
- `save_json_safe` com backup automático — intacto
- `load_json_safe` com recuperação em 3 camadas — intacto
- Indicadores de tendência HRV (setas ↑↓=) — intactos
- `cargo_callback` e `ask_about_cargo` — lógica inalterada
- `cycling_type_callback` — lógica inalterada (já estava correcta)
- Padrões de CallbackQueryHandler no `main()` — inalterados
- `SYSTEM_PROMPT`, modelos Gemini, circuit breaker, rate limiter — inalterados
- Separação de contextos `/status` vs `/analyze_activity` — inalterada

---

## Code Review — Checklist (Guidelines v3.14)

| Critério | Estado |
|:---|:---|
| Thin vertical slice — alteração mínima e cirúrgica | ✅ |
| Sem alterações a funções não relacionadas | ✅ |
| `is_generic_type` removido sem deixar código morto | ✅ |
| `cycling_type_instruction` não quebra prompts sem cycling_type (string vazia) | ✅ |
| Fluxo: cycling → tipo → cargo → análise verificado logicamente | ✅ |
| `cargo_callback` continua a receber `cycling_type` via `parts[3]` | ✅ |
| Sem introdução de secrets, dados não sanitizados, ou privilégios excessivos | ✅ |
| `python3 -m py_compile` sem erros | ✅ |
| Sem regressões nos handlers de CommandHandler ou CallbackQueryHandler | ✅ |

---

## Lesson Learned (para `lessons.md`)

**Modo de falha:** Guard condicional `is_generic_type` introduzido para optimizar um caso específico acabou por silenciosamente excluir tipos explícitos do Garmin (ex: `road_biking`) do seletor de tipo.  
**Sinal de detecção:** Atividades de estrada não apresentavam teclado de tipo de ciclismo.  
**Regra de prevenção:** Lógica de ramificação em callbacks de seleção deve ser validada contra o conjunto real de valores que o Garmin pode enviar, não apenas contra valores genéricos esperados.