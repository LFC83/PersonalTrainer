import os
import sys
import json
import time
from datetime import date, timedelta
from garminconnect import Garmin, GarminConnectAuthenticationError

# Credenciais via variáveis de ambiente
GARMIN_EMAIL = os.getenv('GARMIN_EMAIL')
GARMIN_PASSWORD = os.getenv('GARMIN_PASSWORD')
DATA_DIR = '/data'

def get_client() -> Garmin:
    """Autentica no Garmin Connect"""
    if not GARMIN_EMAIL or not GARMIN_PASSWORD:
        raise ValueError("❌ GARMIN_EMAIL e GARMIN_PASSWORD devem estar definidos")
    
    client = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    client.login()
    print("✅ Autenticado no Garmin Connect")
    return client

def fetch_hrv_data(client: Garmin, target_date: date) -> dict:
    """Busca dados de HRV para uma data específica"""
    date_str = target_date.isoformat()
    try:
        hrv_data = client.get_hrv_data(date_str)
        return hrv_data
    except Exception as e:
        print(f"⚠️  Erro ao obter HRV para {date_str}: {e}")
        return None

def fetch_sleep_data(client: Garmin, target_date: date) -> dict:
    """Busca dados de sono para uma data específica"""
    date_str = target_date.isoformat()
    try:
        sleep_data = client.get_sleep_data(date_str)
        return sleep_data
    except Exception as e:
        print(f"⚠️  Erro ao obter Sleep para {date_str}: {e}")
        return None

def fetch_daily_stats(client: Garmin, target_date: date) -> dict:
    """Busca estatísticas diárias"""
    date_str = target_date.isoformat()
    try:
        stats = client.get_stats(date_str)
        return stats
    except Exception as e:
        print(f"⚠️  Erro ao obter Stats para {date_str}: {e}")
        return None

def fetch_activities(client: Garmin, days: int = 7) -> list:
    """
    Busca atividades dos últimos N dias.
    """
    try:
        limit = days * 3  # Assumir ~3 atividades por dia
        
        print(f"🏃 A buscar atividades (últimos {days} dias, limit={limit})...")
        activities = client.get_activities(0, limit)
        
        if not activities:
            print("⚠️  Nenhuma atividade encontrada")
            return []
        
        # Filtrar por data
        cutoff_date = date.today() - timedelta(days=days)
        filtered = []
        
        for act in activities:
            start_time = act.get('startTimeLocal') or act.get('startTimeGMT') or act.get('beginTimestamp')
            if not start_time:
                continue
            
            if 'T' in start_time:
                act_date_str = start_time.split('T')[0]
            else:
                act_date_str = start_time[:10]
            
            try:
                act_date = date.fromisoformat(act_date_str)
            except:
                continue
            
            if act_date >= cutoff_date:
                filtered.append(act)
        
        print(f"✅ {len(filtered)} atividades encontradas")
        return filtered
        
    except Exception as e:
        print(f"❌ Erro ao buscar atividades: {e}")
        return []

def save_activities(activities: list):
    """
    Salva atividades em activities.json com merge inteligente.
    """
    try:
        activities_path = os.path.join(DATA_DIR, 'activities.json')
        
        # Carregar existentes
        existing = []
        if os.path.exists(activities_path):
            try:
                with open(activities_path, 'r') as f:
                    existing = json.load(f)
            except:
                existing = []
        
        # Índice de IDs
        existing_ids = set()
        for act in existing:
            act_id = act.get('activityId') or act.get('id')
            if act_id:
                existing_ids.add(act_id)
        
        # Adicionar novas
        added = 0
        for act in activities:
            act_id = act.get('activityId') or act.get('id')
            if act_id and act_id not in existing_ids:
                existing.append(act)
                existing_ids.add(act_id)
                added += 1
        
        # Ordenar por data (mais recente primeiro)
        def get_date(act):
            st = act.get('startTimeLocal') or act.get('startTimeGMT') or ''
            return st.split('T')[0] if 'T' in st else st[:10] if len(st) >= 10 else '0000'
        
        existing.sort(key=get_date, reverse=True)
        existing = existing[:100]  # Max 100 atividades
        
        # Salvar
        with open(activities_path, 'w') as f:
            json.dump(existing, f, indent=2)
        
        print(f"💾 Activities: +{added} novas ({len(existing)} total)")
        
    except Exception as e:
        print(f"❌ Erro ao salvar atividades: {e}")

def save_data(target_date: date, hrv_data: dict, sleep_data: dict, stats_data: dict):
    """Guarda os dados em arquivos JSON organizados por data"""
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
    
    return filename

def update_consolidated_file():
    """Atualiza arquivo consolidado"""
    try:
        consolidated_path = os.path.join(DATA_DIR, "garmin_data_consolidated.json")
        all_data = []
        
        for filename in sorted(os.listdir(DATA_DIR)):
            if filename.startswith("garmin_data_") and filename.endswith(".json") and filename != "garmin_data_consolidated.json":
                filepath = os.path.join(DATA_DIR, filename)
                try:
                    with open(filepath, 'r') as f:
                        data = json.load(f)
                        if isinstance(data, dict):
                            all_data.append(data)
                        elif isinstance(data, list) and len(data) > 0:
                            if isinstance(data[0], dict):
                                all_data.append(data[0])
                except Exception as e:
                    print(f"⚠️  Erro ao ler {filename}: {e}")
        
        all_data.sort(key=lambda x: x.get('date', ''), reverse=True)
        all_data = all_data[:30]
        
        with open(consolidated_path, 'w') as f:
            json.dump(all_data, f, indent=2)
        
        print(f"💾 Consolidado: {len(all_data)} dias")
    except Exception as e:
        print(f"⚠️  Erro ao consolidar: {e}")

def import_historical_data(days: int = 7):
    """
    Importa dados históricos dos últimos N dias + atividades.
    """
    try:
        client = get_client()
        today = date.today()
        
        print(f"\n📊 Importação histórica: {days} dias")
        print(f"   Período: {(today - timedelta(days=days)).isoformat()} até {today.isoformat()}\n")
        
        success_count = 0
        error_count = 0
        
        # Importar biometria dia a dia
        for i in range(days):
            target_date = today - timedelta(days=i)
            date_str = target_date.isoformat()
            
            filename = os.path.join(DATA_DIR, f"garmin_data_{date_str}.json")
            if os.path.exists(filename):
                print(f"⏭️  {date_str}: Já existe")
                continue
            
            print(f"📥 {date_str}: A buscar...", end=' ')
            
            try:
                hrv_data = fetch_hrv_data(client, target_date)
                sleep_data = fetch_sleep_data(client, target_date)
                stats_data = fetch_daily_stats(client, target_date)
                
                save_data(target_date, hrv_data, sleep_data, stats_data)
                print(f"✅")
                success_count += 1
                
                time.sleep(2)  # Rate limit
                
            except Exception as e:
                print(f"❌ {e}")
                error_count += 1
        
        # Atualizar consolidado
        print("\n📦 A consolidar dados...")
        update_consolidated_file()
        
        # NOVO: Buscar atividades
        print(f"\n🏃 A buscar atividades dos últimos {days} dias...")
        activities = fetch_activities(client, days)
        if activities:
            save_activities(activities)
        
        print(f"\n✅ Importação concluída!")
        print(f"   Biometria: {success_count} dias OK, {error_count} erros")
        print(f"   Atividades: {len(activities)} encontradas")
        
        return True
        
    except GarminConnectAuthenticationError as e:
        print(f"❌ Erro de autenticação: {e}")
        return False
    except Exception as e:
        print(f"❌ Erro inesperado: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Função principal - execução via CLI"""
    days = 7
    
    if len(sys.argv) > 1:
        try:
            days = int(sys.argv[1])
            if days < 1 or days > 365:
                print("❌ Dias deve estar entre 1 e 365")
                sys.exit(1)
        except ValueError:
            print("❌ Argumento inválido")
            print("   Uso: python historical_import.py [dias]")
            sys.exit(1)
    
    print("🚀 Garmin Historical Import v2.1")
    print(f"   Email: {GARMIN_EMAIL}")
    print(f"   Data dir: {DATA_DIR}")
    
    success = import_historical_data(days)
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
