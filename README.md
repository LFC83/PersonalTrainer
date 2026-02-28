# 🏋️ FitnessJournal-HRV Bot (v2.7)

[PT] Este projeto é um ecossistema inteligente que utiliza a API do **Google Gemini** para atuar como um treinador de elite. Ele lê os teus dados biométricos do **Garmin Connect** (HRV, RHR, Sono e Carga) e gera planos de treino personalizados em **Português Europeu**, ajustados ao teu material e estado de recuperação.

[EN] This project is an intelligent ecosystem that uses the **Google Gemini API** to act as an elite coach. It reads your **Garmin Connect** biometric data (HRV, RHR, Sleep, and Load) and generates personalized workout plans in **European Portuguese**, tailored to your equipment and recovery state.

---

## 🇵🇹 Guia em Português

### 🚀 Funcionalidades
* **Análise de Readiness:** Avalia se estás apto para treinar com base na variabilidade da frequência cardíaca (HRV) e batimento em repouso (RHR).
* **Planos Adaptativos:** Gera treinos de ginásio ou ciclismo considerando o teu equipamento disponível.
* **Análise de Aderência:** Compara o plano sugerido com o que realmente executaste nas últimas sessões.
* **Análise de Carga (Cargo Bike):** Cálculo específico de esforço para quem transporta carga ou passageiros (ex: 150kg total).

### 🛠️ Configuração e Personalização

#### 1. Obter as Chaves (Tokens)
* **Google Gemini API:** Acede ao [Google AI Studio](https://aistudio.google.com/), cria uma chave em "Get API key" e guarda-a.
* **Telegram Bot:** Fala com o [@BotFather](https://t.me/botfather) no Telegram, usa `/newbot` e guarda o **API TOKEN** fornecido.

#### 2. Personalização do Código (`main.py`)
* **3.1 Equipamento de Ginásio:** Localiza a variável `EQUIPAMENTOS_GIM` na linha 81 e altera a lista para o material que tens disponível (ex: "Halteres 10kg", "Elásticos").
* **3.2 O Prompt do Sistema:** A variável `SYSTEM_PROMPT` (linhas 23-66) define a personalidade do treinador e as regras de cálculo. Podes ajustar o tom ou o foco nesta secção.

#### 3. Estrutura de Pastas e Docker
* **4. Organização:** O bot precisa de comunicar com os ficheiros JSON na pasta `/data`. A estrutura deve ser:
    ```text
    /projeto
    ├── main.py
    ├── Dockerfile
    ├── requirements.txt
    ├── docker-compose.yml
    └── /data (Onde os JSONs do Garmin são guardados)
    ```

---

## 🇬🇧 English Guide

### 🚀 Features
* **Readiness Analysis:** Evaluates training readiness based on HRV and RHR.
* **Adaptive Plans:** Generates workouts considering your specific gym equipment.
* **Adherence Analysis:** Compares the suggested coach plan with actual Garmin activities.
* **Cargo Bike Analysis:** Specialized effort calculation for heavy loads (e.g., 150kg total).

### 🛠️ Setup and Customization

#### 1. Obtain Tokens
* **Google Gemini API:** Visit [Google AI Studio](https://aistudio.google.com/), create a key under "Get API key" and save it.
* **Telegram Bot:** Message [@BotFather](https://t.me/botfather) on Telegram, use `/newbot` and save the provided **API TOKEN**.

#### 2. Code Customization (`main.py`)
* **3.1 Gym Equipment:** Locate the `EQUIPAMENTOS_GIM` variable (line 81) and edit the list to match your gear.
* **3.2 System Prompt:** The `SYSTEM_PROMPT` variable (lines 23-66) defines the coach's personality and rules.

---

## 📦 Configuração Técnica / Technical Setup

### requirements.txt
```text
python-telegram-bot==20.8
google-generativeai==0.3.2


Dockerfile

FROM python:3.10-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
RUN mkdir -p /data
CMD ["python", "main.py"]

Docker-compose

version: '3.8'
services:
  fitness-bot:
    build: .
    container_name: fitness-journal-bot
    volumes:
      - ./data:/data
    environment:
      - TELEGRAM_TOKEN=YOUR_TELEGRAM_TOKEN
      - GEMINI_API_KEY=YOUR_GEMINI_KEY
      - GARMIN_EMAIL=your_email@garmin.com
      - GARMIN_PASSWORD=your_password
    restart: always
    
    
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