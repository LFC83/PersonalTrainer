🏋️ FitnessJournal-HRV Bot (v3.5)

[PT] Este projeto é um ecossistema inteligente que utiliza a API do Google Gemini (2.0 Flash) para atuar como um treinador de elite. Ele funde os teus dados biométricos do Garmin Connect (HRV, RHR, Sono e Carga) com o teu feedback subjetivo para gerar planos de treino e análises de performance em Português Europeu.

[EN] An intelligent ecosystem using Google Gemini 2.0 Flash to act as an elite performance coach. It merges Garmin Connect biometrics with user feedback to generate personalized workout plans and activity analysis in European Portuguese.

## 🇵🇹 Guia em Português

### 🚀 Novas Funcionalidades (v3.0 a v3.5)
* **💬 Perguntas de Seguimento (Follow-Up):** Agora podes responder às análises do bot para tirar dúvidas. O bot mantém o contexto da conversa por 15 minutos.
* **💾 Persistência em Disco:** O contexto das tuas conversas e análises agora sobrevive a restarts do servidor ou do contentor Docker via ficheiros JSON persistentes.
* **🚴 Ciclismo Avançado:** Lógica de análise expandida para diferenciar entre **Spinning, MTB, Commute e Estrada**, incluindo cálculos específicos para carga/passageiros.
* **📊 Analytics de Utilização:** Usa o comando `/stats` para visualizar tendências de perguntas e métricas de interação.
* **📜 Histórico de Análises:** Acesso rápido às últimas 5 análises realizadas através do comando `/history`.

### 🛠️ Configuração e Personalização

#### 1. Obter as Chaves (Tokens)
* **Google Gemini API:** Obtém a tua chave no [Google AI Studio](https://aistudio.google.com/). O bot está otimizado para o modelo `gemini-2.0-flash-exp`.
* **Telegram Bot:** Cria o teu bot com o [@BotFather](https://t.me/botfather) e guarda o **API TOKEN** fornecido.

#### 2. Personalização do Código (`main.py`)
* **Equipamento:** Atualiza a lista `EQUIPAMENTOS_GIM` para refletir o material que tens em casa ou no teu ginásio.
* **Prompt do Sistema:** O `SYSTEM_PROMPT` define o "Protocolo de Verdade". Ele obriga o bot a ser rigoroso e a nunca ignorar dados de HRV baixo.

---

## 🇬🇧 English Guide

### 🚀 New Features (v3.0 to v3.5)
* **💬 Follow-Up Questions:** You can now reply directly to the bot's analysis to ask questions. The bot maintains conversation context for 15 minutes.
* **💾 Disk Persistence:** Your conversation and analysis context now survives server restarts or Docker container reboots via persistent JSON files.
* **🚴 Advanced Cycling:** Expanded analysis logic to differentiate between **Spinning, MTB, Commute, and Road cycling**, including specific load/passenger calculations.
* **📊 Usage Analytics:** Use the `/stats` command to visualize question trends and interaction metrics.
* **📜 Analysis History:** Quick access to the last 5 performed analyses through the `/history` command.

### 🛠️ Setup and Customization

#### 1. Obtain Tokens
* **Google Gemini API:** Get your key at [Google AI Studio](https://aistudio.google.com/). The bot is optimized for the `gemini-2.0-flash-exp` model.
* **Telegram Bot:** Create your bot with [@BotFather](https://t.me/botfather) and save the provided **API TOKEN**.

#### 2. Code Customization (`main.py`)
* **Equipment:** Update the `EQUIPAMENTOS_GIM` list to reflect the gear you have at home or in your gym.
* **System Prompt:** The `SYSTEM_PROMPT` defines the "Truth Protocol". It forces the bot to be rigorous and never ignore low HRV data.

#### 3. Estrutura de Dados (Docker)
O bot utiliza a pasta `/data` para persistência. Certifica-te de que o volume está corretamente montado:

```text
/data
├── activities.json       # Histórico de atividades Garmin
├── health_data.json      # Métricas biométricas (HRV, Sono, etc)
├── context_user_id.json  # Persistência de conversas (Novo v3.4)
└── analytics.json        # Métricas de uso (Novo v3.4)
```
---

### 📦 Configuração Técnica / Technical Setup
### Docker Compose (Recomendado)

```text
version: '3.8'

services:
  fitness-bot:
    build: .
    container_name: fitness-journal-bot
    volumes:
      - ./data:/data
    environment:
      - TELEGRAM_TOKEN=your_token
      - GEMINI_API_KEY=your_key
      - GARMIN_EMAIL=your_email
      - GARMIN_PASSWORD=your_password
      - GEMINI_MAX_PROMPT_LENGTH=8000 # Proteção contra overflow
    restart: always
```
---

### 3. Tabela de Comandos e FAQ
```markdown
### 🎮 Comandos Principais (Changelog v3.5)

| Comando | Descrição | Versão |
| :--- | :--- | :--- |
| `/start` | Inicia o bot e restaura contexto do disco | v3.4 |
| `/analyze` | Analisa todas as atividades de hoje/ontem | v3.2 |
| `/activity` | Menu interativo para analisar atividade específica | v3.0 |
| `/history` | Lista as últimas 5 análises guardadas | v3.4 |
| `/clear_context` | Limpa a memória de curto prazo (Follow-up) | v3.4 |
| `/stats` | Analytics de perguntas e interações | v3.4 |
| `/cleanup` | Limpa flags pendentes e organiza JSONs | v2.5 |
```

    
### ❓ FAQ (Perguntas Frequentes)

PT: O bot diz que não tem dados de hoje.

    Usa o comando /sync para forçar uma sincronização. O bot cria um pedido que será processado pelo fetcher em ~60s.

EN: The bot says today's data is empty.

    Use the /sync command to force a synchronization. The bot creates a request that will be processed in ~60s.

PT: Posso analisar um treino antigo?

    Sim, usa /analyze_activity e seleciona uma das últimas 5 atividades para uma análise profunda.

EN: Can I analyze an old workout?

    Yes, use /analyze_activity and select one of the last 5 activities for a deep analysis.

PT: Como limpo pedidos pendentes?

    Usa o comando /cleanup para limpar flags antigas e reorganizar o histórico de atividades.

EN: How do I clear pending requests?

    Use the /cleanup command to clear old flags and reorganize activity history.
    

---

# Changelog

## 🚀 Versão Atual: 3.10.0 (2026-03-02)

Esta versão foca-se na **estabilidade estrutural** e na correção de erros críticos de parsing de dados do Garmin Connect, além de melhorar o feedback visual do utilizador.

### 🔧 Correções Críticas e Estabilidade
- **Data Resilience (List vs Dict):** Resolvido o erro `AttributeError: 'list' object has no attribute 'get'` no ficheiro consolidado. O bot agora deteta e processa automaticamente tanto dicionários como listas de biometria.
- **Seletor de Ciclismo:** Implementada a distinção entre **MTB, Estrada, Spinning e Cidade**. O bot agora pergunta o tipo de ciclismo antes de iniciar a análise para garantir recomendações precisas.
- **Sync Feedback:** Corrigido o sistema de monitorização de ficheiros `.flag`. O bot agora notifica o utilizador assim que o processamento em background termina.

### ✨ Novas Funcionalidades e UX
- **Evolução HRV:** O comando `/status` agora apresenta a tendência visual dos últimos 5 dias (ex: `📈 HRV: 65 -> 68 -> 62...`).
- **Deep Logging:** Melhoria nos logs de sistema para identificar o tipo de dados carregados em cada ficheiro JSON, facilitando o debug.
- **Atomic Writes:** Garantia de integridade dos ficheiros ao gravar dados, evitando corrupção em caso de interrupção.

## 🚀 Versão Atual: 3.9.0 (2025-03-02)

Esta versão foca-se na correção crítica da extração de dados do Garmin Connect e na melhoria da experiência do utilizador (UX).

### 🔧 Correções Críticas e Melhorias
- **Novo Garmin Parser:** Resolvido o erro que mostrava atividades como "Unknown". Agora o bot acede corretamente a campos aninhados (`activityName`, `typeKey`, etc).
- **Cálculo de Duração:** Corrigida a conversão de segundos para minutos no ecrã de atividades.
- **Deep Nested Biometrics:** O bot agora "escava" os ficheiros JSON para encontrar o HRV (`lastNightAvg`), RHR e Pontuação de Sono em sub-estruturas complexas.
- **Fluxo de "Feeling":** Antes de cada análise de prontidão (`/status`), o bot pergunta ativamente como o utilizador se sente (0-10).
- **Feedback de Sincronização:** Adicionado sistema de monitorização de ficheiros `.flag` para notificar o utilizador assim que o `fetcher` termina o seu trabalho.
- **Telemetria Avançada:** Suporte total para Cadência de Ciclismo (RPM), Cadência de Corrida (SPM) e Altimetria (D+).

### 🛠️ Comandos Atualizados
- `/status`: Agora inclui a fase de recolha de feedback subjetivo.
- `/health`: Reporta a integridade da base de dados e sucesso na leitura de biometria.
- `/activities`: Lista detalhada com data, tipo, duração, batimento médio, altimetria e cadência.

### CHANGELOG v3.8.0

🎯 Objetivo da Versão
Correção crítica de corrupção de dados + novas funcionalidades de telemetria e health check.

🔴 CORREÇÕES CRÍTICAS (Prioridade Máxima)
1. Blindagem do Sistema de Dados
Problema: Bot crashava com AttributeError: 'list' object has no attribute 'items' quando activities.json estava corrompido como lista.
Solução Implementada:

✅ Resiliência de Leitura (load_activities_index):

Detecta se activities.json é uma list
Converte automaticamente para dict usando IDs das atividades
Salva imediatamente no formato correto
Log detalhado do processo de conversão


✅ Consistência de Escrita (save_activities_index):

Validação pré-escrita: Verifica que dados são dict antes de salvar
Atomic Write: Escreve para .tmp primeiro, depois faz os.replace()
Levanta FileOperationError se tentar salvar tipo inválido
Cleanup automático de ficheiros temporários em caso de erro


✅ Validação Extra (get_all_formatted_activities):

Verifica tipo do activities_index após load
Retorna lista vazia se tipo for inválido
Log de erro detalhado

### 🆕 NOVAS FUNCIONALIDADES v3.8.0

3. Telemetria de Ciclismo
Feature: Extração e display de cadência de ciclismo (rpm).
Implementação:

✅ Nova função: extract_bike_cadence_from_raw()

Procura averageBikingCadenceInRevolutionsPerMinute
Fallback para outros campos de cadência
Só ativa para atividades de ciclismo


✅ Campo bike_cadence adicionado a:

FormattedActivity dataclass
Parsing do Garmin (parse_activities_from_garmin)
Enriquecimento automático (enrich_activity_from_garmin)


✅ Display no resumo: "(120min, 45km, 150bpm, 85rpm)"

## [3.7.0] - 2026-03-01

## 🎯 Resumo da Versão

**Versão:** 3.7.0  
**Data:** 2026-03-01  
**Tipo:** Feature Release + Performance Enhancement  

### O Que Mudou (TL;DR)
1. ✅ Bot agora extrai **altimetria** (D+) em ciclismo e corrida
2. ✅ Bot agora extrai **cadência** (steps/min) em corrida
3. ✅ Bot **SEMPRE** injeta **HRV/RHR** nos prompts do Gemini
4. ✅ Bot **pergunta sobre carga** antes de analisar atividades de ciclismo
5. ✅ **Timeout aumentado** para 45s (era 30s)
6. ✅ **Retry inteligente** (dobra delay após timeout)
7. ✅ **System prompt atualizado** (prioridade aos dados biométricos)
8. ✅ **Handler para comandos não reconhecidos**


## [3.6.0] - 2026-03-01

### 🎯 Resumo da Versão
Versão de estabilização crítica que resolve os problemas de inicialização da v3.5.1 e introduz camadas de resiliência "Enterprise-Grade" para a API Gemini e gestão de ficheiros.

### 🚀 Added
- **Análise Individual via Comando**: Novo comando `/analyze_activity` que permite selecionar uma atividade específica para análise profunda.
- **Resiliência de API**: Implementação de *Exponential Backoff* (tentativas automáticas) para falhas temporárias do Gemini.
- **Proteção Anti-Spam**: Rate limiting por utilizador para evitar sobrecarga da API e custos desnecessários.
- **Validação de Integridade**: Verificação automática de JSONs corrompidos com sistema de auto-reparação.

### 🐛 Fixed
- **[CRITICAL]** Corrigido o erro de "Handler Mismatch" que impedia o bot de iniciar.
- **Mapeamento de Botões**: Reativados e corrigidos os callbacks de Ciclismo (MTB/Estrada) e Carga/Passageiro.
- **Memory Leaks**: Otimização do fecho de ficheiros e gestão de memória em processos longos.

### 🔧 Changed
- **Arquitetura de Handlers**: Separação clara entre comandos de texto e interações de botões (Inline Keyboards).
- **Logging**: Implementação de logs estruturados para facilitar o diagnóstico de erros em produção.

### [3.5.1] - 2026-03-01 (Code Review Release)

🐛 Critical Fixes

    Gemini Timeout: Adicionado timeout de 30s em todas as chamadas (GEMINI_TIMEOUT_SECONDS).

    Response Validation: Criada função validate_gemini_response() para validar respostas vazias ou demasiado curtas.

    Disk Space Check: Implementada a função has_disk_space() para verificar armazenamento antes de escrever contextos.

    Race Condition: Melhorado o tratamento de erros em save_context_to_disk() com limpeza automática de ficheiros temporários.

🔧 Novas Funções (Backend)

    validate_gemini_response(response): Valida a integridade da resposta da IA.

    call_gemini_with_timeout(prompt, timeout=30): Execução protegida contra bloqueios.

    has_disk_space(path, min_mb=10): Prevenção de corrupção de ficheiros por falta de espaço.

📝 Code Quality Improvements

    Async Timeout: Implementação de asyncio.wait_for() e asyncio.to_thread() para chamadas não bloqueantes.

    Validação Explícita: Respostas do Gemini validadas em 3 pontos críticos: handle_followup_question, handle_feeling e analyze_command.

    Logging Detalhado: Distinção clara entre erros de timeout e erros de lógica da API.

    Constantes: Definição de MIN_DISK_SPACE_MB e GEMINI_TIMEOUT_SECONDS no topo do ficheiro para fácil ajuste.

🚫 Novas Excepções Personalizadas

    GeminiTimeoutError: Tratamento específico para lentidão da API.

    DiskSpaceError: Proteção contra falhas de escrita no servidor.

🎯 User Experience

    Mensagens Específicas: O bot informa agora: "⏱️ O Gemini demorou muito a responder" em vez de ficar mudo.

    Feedback de Erro: Mensagem clara: "❌ Resposta inválida do Gemini" quando a IA falha na geração.

    Fallback Gracioso: Garantia de que o bot nunca crasha, fornecendo sempre uma resposta ao utilizador.

📊 Technical Debt Paid

    ✅ Lógica de Validação Extraída: Aplicação do princípio DRY (Don't Repeat Yourself).

    ✅ Separação de Responsabilidades: Timeout vs Validação vs Armazenamento.

    ✅ Programação Defensiva: Validação antes da escrita e após a leitura.

    ✅ Degradação Graciosa: Erros específicos convertidos em mensagens acionáveis.

### [3.5.0] - 2026-03-01
🎯 Objetivo

Evolução da v2.2 (estável) para v3.5 com gestão de contexto persistente e perguntas de seguimento.
✅ Implementado
1. Dispatcher Inteligente (handle_message)

    Lógica condicional: Se contexto existe (últimos 15min) → follow-up; caso contrário → feeling.

    Routing transparente: O utilizador não precisa de mudar o comportamento habitual.

    Validação de timeout: Os contextos expiram automaticamente após 15 minutos de inatividade.

2. Persistência de Contexto

    Ficheiros por utilizador: Armazenamento em user_context_{user_id}.json na pasta /data.

    Histórico FIFO: Mantém as últimas 3 análises (MAX_CONTEXT_HISTORY).

    Escrita Atómica: Garante a integridade dos dados mesmo em caso de crash durante a escrita.

3. Tipos de Ciclismo Expandidos

    Fluxo completo: Pergunta sobre passageiro → Se NÃO → Pergunta Tipo (Spinning/MTB/Commute/Estrada).

    Novo callback: cycling_type_callback para processar a seleção do utilizador.

    Contexto na análise: O tipo de ciclismo é incluído no prompt enviado ao Gemini.

4. Markdown Resiliente

    Função send_safe_message: Tenta enviar com Markdown; se ocorrer BadRequest, faz fallback automático para texto simples.

    Estabilidade: O bot continua funcional mesmo que o Gemini gere caracteres de formatação inválidos.

5. Novos Comandos

    /history: Lista as últimas 3 análises com timestamps.

    /clear_context: Remove o contexto atual e limpa o ficheiro em disco.

    /stats: Analytics agregados (total de utilizadores e breakdown por tipo).

🔧 Melhorias Técnicas

    Tratamento de Erros:

        Falhas na API Gemini: Retorna "Serviço temporariamente indisponível" de forma amigável.

        Erros de I/O: Logging detalhado e salvaguarda de ficheiros.

    Qualidade de Código:

        Base sólida: Mantido 100% do código funcional da v2.2.

        Incremental: Funcionalidades adicionadas sem necessidade de reescritas totais.

    Performance:

        Smart Context: Verifica primeiro a memória (RAM) antes de consultar o disco.

        Truncagem Segura: Limita prompts e respostas a 5k/10k caracteres antes de guardar.

        Polling: Uso de drop_pending_updates para evitar processar mensagens acumuladas após reinícios.

📝 Matriz de Compatibilidade
Funcionalidade	Estado	Descrição
Readiness	✅	/status completo com métricas HRV/RHR
Análise	✅	/analyze (aderência) e /analyze_activity (individual)
Dados	✅	/import e /sync (sincronização Garmin)
Manutenção	✅	/cleanup e /reorganize de ficheiros JSON
Seguimento	🆕	handle_message com IA contextual
Ciclismo	🆕	Seleção de sub-tipos (MTB, Estrada, etc.)
Resiliência	🆕	send_safe_message com fallback de Markdown
🐛 Correcções (Fixes)

    Críticos:

        Resolvida a f-string truncada da v3.4 ao restaurar a base da v2.2.

        Mapeamento de todos os handlers e callbacks no main() para evitar funções órfãs.

    Preventivos:

        Limpeza automática de contextos expirados para evitar fugas de memória.

        Limitação do histórico em disco para poupar espaço e processamento.

📊 Verificação de Deployment

    [x] Todos os comandos mapeados no main()

    [x] Callbacks de botões registados corretamente

    [x] Error handling em todas as funções async

    [x] Escritas atómicas configuradas para persistência

    [x] drop_pending_updates ativo no arranque

## [3.4.0] - 2026-02-28

### 🚀 Added
- **Persistência de Contexto em Disco**: Contexto de análises agora sobrevive a restarts do bot via `context_store_{user_id}.json`
- **Histórico de Análises**: Comando `/history` lista últimas 5 análises (FIFO) com timestamps e tipo
- **Comando `/clear_context`**: Permite limpar explicitamente contexto de follow-up (memória + disco)
- **Tipos de Ciclismo**: Após selecionar "sem passageiro", bot pergunta tipo específico: Spinning/MTB/Commute/Estrada
- **Analytics de Follow-Up** (opcional): Contadores de perguntas por tipo e keywords mais comuns via `/stats`
- **Validação de Tamanho de Prompt**: Guard contra overflow da API Gemini (GEMINI_MAX_PROMPT_LENGTH)
- **Aviso de Expiração**: Bot avisa utilizador quando contexto está a 2min de expirar

### 🐛 Fixed
- **[CRITICAL]** Corrigida f-string não terminada em `start()` que causava SyntaxError na linha 1132
- **Truncation Seguro**: Análises longas agora truncadas de forma inteligente (mantém início e fim)
- **Context Expiry**: Melhor handling de contextos expirados com mensagens claras ao utilizador

### 🔧 Changed
- `AnalysisContext` agora serializa/deserializa de disco automaticamente
- `save_analysis_context()` integra escrita em disco e atualização de histórico
- Helper `truncate_analysis_safe()` extraído para reutilização (DRY)
- Fluxo de análise de ciclismo expandido para 3 níveis: atividade → passageiro → tipo
- Gemini model atualizado para `gemini-2.0-flash-exp`

### 📚 Technical Debt Paid
- Consolidada validação de contexto em `AnalysisContext.validate()`
- Separadas responsabilidades: storage vs. prompt building
- Adicionado `validate_prompt_size()` para prevenir erros de API

---

## [3.3.0] - 2026-02-27

### 🚀 Added
- **Follow-Up Questions**: Utilizadores podem fazer perguntas sobre análises anteriores
- `AnalysisContext` dataclass para guardar contexto de análises
- `build_followup_prompt()` para construir super-prompts com contexto completo
- `save_analysis_context()` helper para guardar contexto (DRY)
- Feature flag `ENABLE_FOLLOWUP_QUESTIONS` para controlar funcionalidade
- Timeout configurável de 15 minutos para contextos (`ANALYSIS_CONTEXT_TIMEOUT`)

### 🔧 Changed
- Handler de mensagens agora processa follow-up questions quando contexto existe
- Análises agora guardam prompt original e resposta completa
- Smart truncation de análises longas (8000 chars) mantendo início e fim

### 📚 Documentation
- Documentados limites e thresholds no topo do ficheiro
- Comentários inline sobre contexto e expiração

---

## [3.2.0] - 2026-02-25

### 🚀 Added
- **Análise Multi-Atividade**: `/analyze` agora processa TODAS as atividades de hoje/ontem
- Lógica robusta: hoje → todas de hoje; se não, ontem → todas de ontem
- Mensagens com pluralização correta PT-PT ("1 atividade" vs "2 atividades")
- `format_found_activities_message()` para mensagens consistentes

### 🐛 Fixed
- Análise agora considera todas as atividades do dia, não apenas a primeira
- Melhor handling de dias sem atividades

### 🔧 Changed
- `find_activities_for_analysis()` retorna lista completa + data + mensagem
- Limite de `MAX_ACTIVITIES_IN_ANALYSIS = 5` para evitar prompts muito longos

---

## [3.1.0] - 2026-02-23

### 🚀 Added
- **Formatação Unificada de Atividades**: `FormattedActivity` dataclass
- `to_brief_summary()` e `to_detailed_summary()` para displays consistentes
- Extração robusta de dados com múltiplos fallbacks
- Suporte para múltiplos formatos de dados Garmin

### 🔧 Changed
- Consolidados extractors: `extract_date()`, `extract_sport()`, `extract_duration()`, etc.
- `format_activity()` faz merge inteligente de dados de múltiplas fontes
- Melhor inferência de esportes baseada em métricas

### 📚 Refactoring
- Separadas responsabilidades: extração vs. formatação vs. display
- Código mais testável e manutenível

---

## [3.0.0] - 2026-02-20

### 🚀 Breaking Changes
- Reescrita completa do sistema de análise de atividades
- Novo fluxo: listar → selecionar → decidir passageiro → analisar
- `UserSessionState` com histórico de atividades formatadas

### 🚀 Added
- Análise individual de atividades via `/activity`
- Keyboard inline para seleção de atividades (até 10)
- Pergunta sobre passageiro para atividades de ciclismo
- Caching de atividades formatadas na sessão

### 🐛 Fixed
- Gestão robusta de estado de sessão
- Validação de índices de atividades
- Handling de sessões expiradas

---

## [2.5.0] - 2026-02-18

### 🚀 Added
- `/reorganize`: Comando para limpar duplicados e ordenar activities.json
- Atomic write pattern para todas as operações de ficheiros
- `load_json_safe()` com fallback e backup de ficheiros corrompidos

### 🐛 Fixed
- Race conditions em escrita de ficheiros
- Corrupção de dados por writes parciais

### 🔧 Changed
- Limite de `MAX_ACTIVITIES_STORED = 100` com FIFO automático

---

## [2.0.0] - 2026-02-15

### 🚀 Major Features
- Sistema de pedidos async: `/import` e `/sync`
- Flag files para comunicação com garmin-fetcher
- `/status` para verificar progresso de pedidos
- `/cleanup` para limpar flags antigas (timeout 5min)

### 🔧 Changed
- Arquitetura desacoplada: bot não faz fetch direto
- Garmin-fetcher como serviço independente

---

## [1.0.0] - 2026-02-10

### 🚀 Initial Release
- Geração de planos de treino baseados em HRV/RHR
- Integração com dados Garmin (HRV, RHR, Sono, Training Load)
- Sistema de prontidão biométrica
- Protocolo de Verdade com prompt rigoroso
- Suporte para PT-PT exclusivo
- Análise de desvios biométricos (d_hrv, d_rhr)
- Recomendações para ciclismo e ginásio
- Equipamento disponível configurável

### 📚 Documentation
- README.md com instruções de instalação
- Docker Compose setup
- Variáveis de ambiente documentadas