# CHANGELOG — FitnessJournal Bot

## v3.13.0 — 2026-03-03

### Resumo
Reengenharia da sincronização (fim dos timeouts), inteligência de contexto para dados stale, segurança de escrita com backup automático, HRV trend com setas de direção, e alinhamento de constantes entre `main.py` e `fetcher.py`.

---

### 1. Reengenharia da Sincronização

**Problema resolvido:** `/sync` e `/import` bloqueavam o bot com um loop `while` de polling até 60 segundos. Qualquer falha de rede ou demora do fetcher causava timeout e resposta de erro ao utilizador.

**Solução implementada:**

- **`main.py` — `sync_confirmed_callback` e `import_historical`:** Removido o `asyncio.create_task(send_sync_feedback(...))` e a função `wait_for_sync_completion`. O bot agora responde imediatamente: *"✅ Pedido enviado. O processamento corre em background. Vou avisar-te assim que os dados chegarem."*

- **`main.py` — `job_check_flags` (JobQueue):** Nova tarefa de background registada via `app.job_queue.run_repeating(job_check_flags, interval=30)`. Verifica a cada 30 segundos se as flags em `/data` desapareceram (sinal de conclusão pelo fetcher). Quando uma flag desaparece: aguarda 2 segundos de settle, lê os dados processados e envia notificação ao utilizador com a contagem de itens.

- **`main.py` — `pending_flags`:** Dicionário em `context.bot_data` que associa cada flag ao `user_id` que a criou. Persiste entre chamadas do JobQueue sem estado global adicional.

- **`fetcher.py` — loop principal:** Substituído `time.sleep(3600)` por um loop com `time.sleep(10)` que verifica flags a cada 10 segundos. O sync automático continua a cada hora, controlado por `last_full_sync` (timestamp). Log de progresso apenas de 5 em 5 minutos para não poluir os logs.

- **`fetcher.py` — `check_and_process_flags`:** Remove a flag **sempre** em `finally` (mesmo em caso de erro), garantindo que o JobQueue nunca fica à espera de uma flag que nunca desaparece.

**Ficheiros alterados:** `main.py`, `fetcher.py`

---

### 2. Inteligência de Contexto — Flag de Dados Stale

**Problema resolvido:** Quando a biometria de hoje ainda não estava sincronizada e o sistema usava o fallback do dia anterior, a IA não sabia disso e prescrevia como se os dados fossem actuais.

**Solução implementada:**

- **`get_today_biometrics`:** Alterada para retornar `Tuple[Optional[BiometricDay], bool]` em vez de `Optional[BiometricDay]`. O segundo valor é `is_stale=True` quando usa fallback do dia anterior.

- **`status` handler:** Guarda `is_stale` em `context.user_data['biometrics_is_stale']` imediatamente após obter a biometria.

- **`process_status_with_feeling`:** Lê `is_stale` de `context.user_data` e injeta no prompt, se verdadeiro:
  ```
  ⚠️ ATENÇÃO: Plano baseado nos dados de ontem. A biometria de hoje ainda não foi
  sincronizada. Indica este facto na análise e nas recomendações.
  ```

- **Todos os callers de `get_today_biometrics`** atualizados para desempacotar o tuple: `parse_garmin_history`, `get_recent_biometrics`, `health_command`.

**Ficheiros alterados:** `main.py`

---

### 3. Cálculo de Carga 150kg em Ciclismo com Passageiro

**Problema resolvido:** O prompt de análise mencionava "peso total: 150kg" mas não instruía explicitamente a IA sobre como usar esse valor nos cálculos.

**Solução implementada:**

- **`perform_activity_analysis`:** Adicionada `cargo_instruction` quando `has_cargo=True`:
  ```
  IMPORTANTE: O utilizador levou um passageiro. Usa 150kg como massa total
  (bike + condutor + carga) para calcular o custo metabólico em subidas
  (W/kg e fadiga acumulada). Ignora comparações com recordes de velocidade pessoal.
  ```

**Ficheiros alterados:** `main.py`

---

### 4. Segurança de Dados — Backup e Recuperação Automática

**Problema resolvido:** Uma falha de escrita em `activities.json` podia corromper ou apagar o ficheiro sem possibilidade de recuperação.

**Solução implementada:**

- **`save_activities_index`:** Antes de qualquer escrita, copia o ficheiro actual para `activities.json.bak`. Após a escrita do `.tmp`, valida que o ficheiro temporário não está vazio (`os.path.getsize > 0`) antes de o promover via `os.replace`. Em caso de falha, tenta recuperar automaticamente o `.bak` e loga o resultado. O fluxo atomic write do `.tmp` da v3.12 é mantido intacto.

**Ficheiros alterados:** `main.py`

---

### 5. Visual HRV — Setas de Direção na Tendência de 5 Dias

**Problema resolvido:** A tendência HRV era uma sequência de números sem contexto visual de direção.

**Solução implementada:**

- **`_hrv_trend_with_arrows` (nova função):** Gera string com setas `↑` (subida >1), `↓` (descida >1) ou `=` (estável ±1) entre cada par de valores consecutivos.
  - Exemplo: `65 -> 68 (↑) -> 62 (↓) -> 63 (=)`
  - Limiar de ±1 evita falsos positivos por variação de arredondamento.

- **`status` handler:** Substitui a geração inline de `hrv_trend` por chamada a `_hrv_trend_with_arrows(valid_5)`.

**Ficheiros alterados:** `main.py`

---

### 6. Alinhamento de Constantes e Flags

**Problema resolvido:** `main.py` e `fetcher.py` construíam os caminhos das flags de forma independente, com risco de divergência.

**Solução implementada:**

- Constantes `SYNC_FLAG`, `IMPORT_FLAG`, `FLAG_EXT`, `DATA_DIR` definidas identicamente em ambos os ficheiros com comentário `# MUST match`.
- Função `_flag_path(flag_name)` centraliza a construção do path em ambos os ficheiros.
- Formato das flags alterado de texto simples para **JSON** (`{user_id, created_at, type, days?}`), lido por `read_flag_payload()` em ambos os ficheiros.
- `create_sync_request()` / `create_import_request()` substituídas por `create_sync_flag(user_id)` / `create_import_flag(user_id, days)` com assinatura explícita.

**Ficheiros alterados:** `main.py`, `fetcher.py`

---

### 7. Cleanup de Flags Penduradas

**Problema resolvido:** O `cleanup_old_flags` na v3.12 removia flags com mais de 5 minutos (`FLAG_TIMEOUT_SECONDS=300`), o que podia eliminar flags de importações históricas legítimas (30 dias de dados podem demorar mais de 5 minutos).

**Solução implementada:**

- `FLAG_STALE_SECONDS = 86400` (24 horas) substitui `FLAG_TIMEOUT_SECONDS` como critério de remoção no `cleanup_old_flags`.
- `cleanup_old_flags` é chamado em dois momentos: **no arranque do bot** e **no comando `/sync`** (antes de mostrar o botão de confirmação).
- `/debug` exibe as flags atualmente pendentes em `pending_flags`.

**Ficheiros alterados:** `main.py`

---

### Compatibilidade e Regressões

- Toda a lógica de headers técnicos (`to_technical_header`) e separação de contextos IA (`/status` vs `/analyze_activity`) da v3.12 mantida intacta.
- `handle_message` actualizado para passar `context` a `process_status_with_feeling` (necessário para `is_stale` via `context.user_data`).
- `historical_import.py` não requer alterações: é invocado pelo `fetcher.py` que já trata as flags.
- **Dependência:** `python-telegram-bot` com suporte a `JobQueue` (versão ≥ 20.x com `job-queue` extra instalado: `pip install "python-telegram-bot[job-queue]"`).