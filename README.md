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
```markdown
## 2. 📦 Configuração Técnica / Technical Setup

### Docker Compose (Recomendado)
```yaml
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

---
    
❓ FAQ (Perguntas Frequentes)

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
    
    
    # Changelog

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