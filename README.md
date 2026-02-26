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