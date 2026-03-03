import os
import json
import time
from datetime import date, timedelta
from garminconnect import Garmin, GarminConnectAuthenticationError

# Credenciais via variáveis de ambiente
GARMIN_EMAIL = os.getenv('GARMIN_EMAIL')
GARMIN_PASSWORD = os.getenv('GARMIN_PASSWORD')
DATA_DIR = '/data'

# Flag names — MUST match main.py exactly
SYNC_FLAG = 'sync_request'
IMPORT_FLAG = 'import_request'
FLAG_EXT = '.flag'

# Timing
SYNC_CHECK_INTERVAL_SECONDS = 10
FULL_SYNC_INTERVAL_SECONDS = 3600  # 1 hora

def _flag_path(flag_name: str) -> str:
    """Retorna o caminho absoluto de uma flag. Alinhado com main.py."""
    return os.path.join(DATA_DIR, f'{flag_name}{FLAG_EXT}')

def read_flag_payload(flag_name: str) -> dict:
    path = _flag_path(flag_name)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, 'r') as f:
            content = f.read().strip()
            # Tenta ler como JSON
            try:
                data = json.loads(content)
                if isinstance(data, dict):
                    return data
                # Se for apenas um número (versão antiga), converte para o novo formato
                return {"user_id": int(data), "created_at": time.time()}
            except json.JSONDecodeError:
                # Se for texto puro (ID antigo)
                if content.isdigit():
                    return {"user_id": int(content), "created_at": time.time()}
                return {}
    except Exception as e:
        print(f"⚠️ Erro ao ler payload: {e}")
        return {}

def remove_flag(flag_name: str):
    """Remove uma flag de forma segura."""
    path = _flag_path(flag_name)
    try:
        if os.path.exists(path):
            os.remove(path)
            print(f"✅ Flag removida: {flag_name}{FLAG_EXT}")
    except Exception as e:
        print(f"⚠️  Erro ao remover flag {flag_name}: {e}")

def get_client() -> Garmin:
    if not GARMIN_EMAIL or not GARMIN_PASSWORD:
        raise ValueError("❌ GARMIN_EMAIL e GARMIN_PASSWORD devem estar definidos")
    client = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    client.login()
    print("✅ Autenticado no Garmin Connect")
    return client

def fetch_hrv_data(client: Garmin, target_date: date) -> dict:
    date_str = target_date.isoformat()
    try:
        hrv_data = client.get_hrv_data(date_str)
        print(f"✅ HRV data obtido para {date_str}")
        return hrv_data
    except Exception as e:
        print(f"⚠️  Erro ao obter HRV para {date_str}: {e}")
        return None

def fetch_sleep_data(client: Garmin, target_date: date) -> dict:
    date_str = target_date.isoformat()
    try:
        sleep_data = client.get_sleep_data(date_str)
        print(f"✅ Sleep data obtido para {date_str}")
        return sleep_data
    except Exception as e:
        print(f"⚠️  Erro ao obter Sleep para {date_str}: {e}")
        return None

def fetch_daily_stats(client: Garmin, target_date: date) -> dict:
    date_str = target_date.isoformat()
    try:
        stats = client.get_stats(date_str)
        print(f"✅ Stats obtidos para {date_str}")
        return stats
    except Exception as e:
        print(f"⚠️  Erro ao obter Stats para {date_str}: {e}")
        return None

def fetch_activities(client: Garmin, days: int = 7) -> list:
    try:
        limit = days * 3
        print(f"🏃 A buscar atividades (limit={limit})...")
        activities = client.get_activities(0, limit)

        if not activities:
            print("⚠️  Nenhuma atividade encontrada")
            return []

        cutoff_date = date.today() - timedelta(days=days)
        filtered = []

        for act in activities:
            start_time = act.get('startTimeLocal') or act.get('startTimeGMT') or act.get('beginTimestamp')
            if not start_time:
                continue
            act_date_str = start_time.split('T')[0] if 'T' in start_time else start_time[:10]
            try:
                act_date = date.fromisoformat(act_date_str)
            except Exception:
                continue
            if act_date >= cutoff_date:
                filtered.append(act)

        print(f"✅ {len(filtered)} atividades encontradas nos últimos {days} dias")
        return filtered

    except Exception as e:
        print(f"❌ Erro ao buscar atividades: {e}")
        import traceback
        traceback.print_exc()
        return []

def save_activities(activities: list):
    """Guarda atividades no ficheiro activities.json com proteção contra dados inválidos."""
    if not activities:
        return
        
    path = os.path.join(DATA_DIR, 'activities.json')
    existing_data = []
    
    if os.path.exists(path):
        try:
            with open(path, 'r') as f:
                content = json.load(f)
                # Garante que os dados existentes são uma lista
                existing_data = content if isinstance(content, list) else []
        except Exception:
            existing_data = []

    # Criar um set de IDs existentes para evitar duplicados
    # Adicionada proteção: verifica se 'a' é um dicionário antes de fazer .get()
    existing_ids = {
        str(a.get('activityId')) for a in existing_data 
        if isinstance(a, dict) and (a.get('activityId') or a.get('id'))
    }
    
    new_count = 0
    for act in activities:
        # PONTO CRÍTICO: Verifica se 'act' é um dicionário. Se for string, ignora.
        if not isinstance(act, dict):
            print(f"⚠️ Ignorando atividade inválida (formato str): {act}")
            continue
            
        act_id = str(act.get('activityId') or act.get('id'))
        if act_id and act_id not in existing_ids:
            existing_data.append(act)
            existing_ids.add(act_id)
            new_count += 1
            
    if new_count > 0:
        # Ordenar por data (opcional, mas recomendado)
        try:
            existing_data.sort(key=lambda x: x.get('startTimeLocal', ''), reverse=True)
        except:
            pass
            
        with open(path, 'w') as f:
            json.dump(existing_data, f, indent=2)
        print(f"✅ {new_count} novas atividades guardadas em activities.json")

def save_data(target_date: date, hrv_data: dict, sleep_data: dict, stats_data: dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    date_str = target_date.isoformat()

    consolidated = {
        "date": date_str,
        "hrv": hrv_data,
        "sleep": sleep_data,
        "stats": stats_data,
        "fetched_at": time.time()
    }

    filename = os.path.join(DATA_DIR, f"garmin_data_{date_str}.json")
    with open(filename, 'w') as f:
        json.dump(consolidated, f, indent=2)

    print(f"💾 Dados guardados em {filename}")
    update_consolidated_file()

def update_consolidated_file():
    try:
        consolidated_path = os.path.join(DATA_DIR, "garmin_data_consolidated.json")
        all_data = []

        for filename in sorted(os.listdir(DATA_DIR)):
            if (filename.startswith("garmin_data_") and filename.endswith(".json")
                    and filename != "garmin_data_consolidated.json"):
                filepath = os.path.join(DATA_DIR, filename)
                try:
                    with open(filepath, 'r') as f:
                        data = json.load(f)
                        if isinstance(data, dict):
                            all_data.append(data)
                        elif isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
                            all_data.append(data[0])
                except Exception as e:
                    print(f"⚠️  Erro ao ler {filename}: {e}")

        all_data.sort(key=lambda x: x.get('date', ''), reverse=True)
        all_data = all_data[:30]

        with open(consolidated_path, 'w') as f:
            json.dump(all_data, f, indent=2)

        print(f"💾 Arquivo consolidado atualizado com {len(all_data)} dias")
    except Exception as e:
        print(f"⚠️  Erro ao atualizar arquivo consolidado: {e}")

def fetch_and_save_today():
    """Busca e guarda dados de hoje + atividades recentes."""
    try:
        client = get_client()
        today = date.today()

        print(f"\n📊 A buscar dados para {today.isoformat()}...")

        hrv_data = fetch_hrv_data(client, today)
        sleep_data = fetch_sleep_data(client, today)
        stats_data = fetch_daily_stats(client, today)

        save_data(today, hrv_data, sleep_data, stats_data)

        print("\n🏃 A buscar atividades recentes...")
        activities = fetch_activities(client, days=7)
        if activities:
            save_activities(activities)

        print("✅ Sincronização completa!")
        return True

    except GarminConnectAuthenticationError as e:
        print(f"❌ Erro de autenticação: {e}")
        print("   Verifique GARMIN_EMAIL e GARMIN_PASSWORD")
        return False
    except Exception as e:
        print(f"❌ Erro inesperado: {e}")
        import traceback
        traceback.print_exc()
        return False

def check_and_process_flags():
    """
    v3.13.0: Verifica e processa flags de sincronização/importação.
    Lê payload JSON para obter user_id e parâmetros.
    Remove a flag APÓS processamento (sinal para o JobQueue do main.py).
    """
    # --- Flag de sincronização ---
    sync_path = _flag_path(SYNC_FLAG)
    if os.path.exists(sync_path):
        payload = read_flag_payload(SYNC_FLAG)
        print(f"🚩 sync_request detectado (user_id={payload.get('user_id', 'desconhecido')})")
        try:
            success = fetch_and_save_today()
            print(f"{'✅' if success else '❌'} Sync {'concluído' if success else 'falhou'}")
        except Exception as e:
            print(f"❌ Erro durante sync: {e}")
        finally:
            # Sempre remove a flag — o JobQueue detecta a ausência como sinal de conclusão
            remove_flag(SYNC_FLAG)

    # --- Flag de importação ---
    import_path = _flag_path(IMPORT_FLAG)
    if os.path.exists(import_path):
        payload = read_flag_payload(IMPORT_FLAG)
        days = payload.get('days', 30)
        print(f"🚩 import_request detectado (user_id={payload.get('user_id', 'desconhecido')}, days={days})")
        try:
            from historical_import import import_historical_data
            success = import_historical_data(days)
            print(f"{'✅' if success else '❌'} Import {'concluído' if success else 'falhou'}")
        except Exception as e:
            print(f"❌ Erro durante import: {e}")
        finally:
            # Sempre remove a flag
            remove_flag(IMPORT_FLAG)

def main():
    """
    v3.13.0: Loop principal.
    - Verifica flags a cada SYNC_CHECK_INTERVAL_SECONDS (10s) — resposta quase imediata.
    - Executa fetch_and_save_today() a cada FULL_SYNC_INTERVAL_SECONDS (1h).
    """
    print("🚀 Garmin Fetcher v3.13.0 iniciado")
    print(f"   Email: {GARMIN_EMAIL}")
    print(f"   Data dir: {DATA_DIR}")
    print(f"   Check flags: cada {SYNC_CHECK_INTERVAL_SECONDS}s")
    print(f"   Sync automático: cada {FULL_SYNC_INTERVAL_SECONDS}s")

    last_full_sync = 0  # força sync imediato no arranque

    while True:
        try:
            # Verifica e processa flags (quase imediato)
            check_and_process_flags()

            # Sync automático a cada hora
            now = time.time()
            if now - last_full_sync >= FULL_SYNC_INTERVAL_SECONDS:
                print(f"\n⏰ Sync automático ({FULL_SYNC_INTERVAL_SECONDS}s decorridos)...")
                fetch_and_save_today()
                last_full_sync = time.time()
            else:
                remaining = int(FULL_SYNC_INTERVAL_SECONDS - (now - last_full_sync))
                # Log apenas de 5 em 5 minutos para não poluir
                if remaining % 300 < SYNC_CHECK_INTERVAL_SECONDS:
                    print(f"⏳ Próximo sync automático em {remaining}s...")

            time.sleep(SYNC_CHECK_INTERVAL_SECONDS)

        except KeyboardInterrupt:
            print("\n🛑 Fetcher interrompido pelo utilizador")
            break
        except Exception as e:
            print(f"❌ Erro no loop principal: {e}")
            import traceback
            traceback.print_exc()
            print("⏳ A tentar novamente em 30s...")
            time.sleep(30)

if __name__ == "__main__":
    main()
