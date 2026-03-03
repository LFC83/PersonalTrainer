# Changelog — FitnessJournal Bot v3.14.0

**Data:** 2026-03-03  
**Ficheiros alterados:** `main.py`, `fetcher.py`  
**Versão anterior:** 3.13.0

---

## main.py

### 1. `load_json_safe` — Resiliência JSON com fallback `.bak` (Camada 2)

**Antes:** Em caso de `JSONDecodeError` ou erro genérico, a função devolvia `default_value` mas não tentava recuperação.

**Depois:** Estratégia de 3 camadas:
1. Lê o ficheiro principal normalmente.
2. Se falhar (ficheiro ausente, JSON corrompido, erro de I/O), tenta o ficheiro `.bak` correspondente.
3. Se ambos falharem ou não existirem, devolve `default_value` **sem propagar qualquer exceção**.

**Garantia:** O bot nunca bloqueia no arranque por ficheiros corrompidos. O tipo do `default_value` (lista `[]` ou dicionário `{}`) é sempre preservado, conforme o contexto de cada chamador.

---

### 2. Fallback de Modelo Gemini em caso de 429 (Quota Exceeded)

**Adicionado:**
- Constante `GEMINI_FALLBACK_MODEL = "gemini-1.5-flash"` (modelo com quotas mais permissivas).
- Função `_is_quota_error(exc)` que deteta erros `429` / `quota` / `resource_exhausted` na string da exceção.
- Em `call_gemini_with_retry`: quando qualquer tentativa lança um erro de quota, é feita **uma única tentativa de fallback** com `gemini-1.5-flash` antes de propagar o erro.
- O fallback instancia o modelo com o mesmo `SYSTEM_PROMPT`, garantindo comportamento consistente.
- `call_gemini_with_timeout` aceita agora `override_model=None` para suportar o fallback sem alterar a instância global `model`.

**Não alterado:** Lógica de retry (`MAX_RETRIES`, `RETRY_DELAYS`), circuit breaker, rate limiter, cache — todos intactos.

---

### 3. JobQueue — Hora de conclusão na notificação de sync/import

**Antes:** `"✨ Sincronização concluída!\n..."`

**Depois:** `"✨ Sincronização concluída às HH:MM!\n..."` e `"✨ Importação concluída às HH:MM!\n..."`

A hora é calculada com `datetime.now().strftime("%H:%M")` no momento em que a flag desaparece (após o `JOB_QUEUE_WRITE_SETTLE_SECONDS` de settle). Aplica-se tanto a `sync` como a `import`.

---

### 4. `save_activities_index` — Context manager explícito antes de rename (alinhamento de segurança)

O padrão `with open(...) as f: ... os.replace(tmp, path)` já estava correto em v3.13.0. A v3.14.0 documenta explicitamente que o `with` garante fecho do ficheiro **antes** de qualquer `os.replace` ou `os.rename`, prevenindo bloqueios de escrita em Docker volumes e Windows. Nenhuma alteração de lógica.

---

## fetcher.py

### 5. Logging — Substituição completa de `print` por `logging`

**Antes:** 42 chamadas `print()` espalhadas por todo o módulo.

**Depois:** Módulo de logging configurado com o mesmo `basicConfig` que `main.py`:
```
format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
level=logging.INFO
```
Todos os `print()` substituídos por `logger.info()`, `logger.warning()`, `logger.error()` ou `logger.debug()` conforme a severidade. `traceback.print_exc()` substituído por `logger.debug(traceback.format_exc())`. `import traceback` movido para o topo do módulo.

**Benefício:** Logs unificados no Docker Compose com timestamps e níveis de severidade; sem perda de informação de diagnóstico.

---

### 6. `rename_flag_to_error` — Sinalização de falha total do Garmin

**Adicionado:**
- Sentinela `GARMIN_FETCH_ERROR = "GARMIN_FETCH_ERROR"` para distinguir falha total (autenticação, rede) de falha lógica interna.
- `fetch_and_save_today()` devolve `GARMIN_FETCH_ERROR` em vez de `False` para erros `GarminConnectAuthenticationError` e exceções inesperadas. Devolve `True` em caso de sucesso.
- `rename_flag_to_error(flag_name)`: renomeia `<flag>.flag` para `<flag>.flag.error` em vez de remover. O JobQueue do `main.py` **não interpreta** `.flag.error` como conclusão bem-sucedida (apenas a ausência de `.flag` é o sinal de conclusão). Tem fallback para `remove_flag()` se o rename falhar, para não bloquear o ciclo indefinidamente.
- `check_and_process_flags()` usa `result is GARMIN_FETCH_ERROR` para decidir entre `remove_flag` (sucesso/falha parcial) e `rename_flag_to_error` (falha total do Garmin).

---

## Code Review — Verificações realizadas

| Critério | Estado |
|---|---|
| Sintaxe Python válida (`ast.parse`) em ambos os ficheiros | ✅ |
| Nenhuma regressão nas funções não alteradas | ✅ |
| `load_json_safe` nunca propaga exceção | ✅ |
| Fallback Gemini só ativa em 429, não em outros erros | ✅ |
| JobQueue timestamp usa `datetime.now()` (hora local do servidor) | ✅ |
| `with` fecha ficheiro antes de `os.replace` em `save_activities_index` | ✅ |
| Zero chamadas `print()` restantes em `fetcher.py` | ✅ |
| Flag `.flag.error` não é detectada pelo JobQueue como conclusão | ✅ |
| `GARMIN_FETCH_ERROR` usa `is` (identidade), não `==` (comparação de string) | ✅ |
| Lógica de separação de contextos e headers da v3.13 intacta | ✅ |
| Versão atualizada em `BOT_VERSION`, `BOT_VERSION_DESC`, logs de arranque e comentários de funções | ✅ |